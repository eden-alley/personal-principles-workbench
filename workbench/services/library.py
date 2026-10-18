"""Owner-scoped library commands. Models may propose; only explicit commands write."""
import base64
import hashlib
import re
import uuid
from datetime import timedelta
from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from PIL import Image

from workbench.models import (
    LibraryAsset, LibraryConversation, LibraryDraft, LibraryEntry, LibraryEvidence,
    Observation, Principle, Source, Suggestion,
)
from .common import Conflict, clean_text, require_confirmation, run_once
from .principles import save_principle as domain_principle
from .sources import save_source

KINDS = {'source', 'principle', 'experience'}


def owner_key(owner):
    if not isinstance(owner, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', owner):
        raise ValidationError('登录身份无效。')
    return owner


def _entry(owner, entry_id, *, include_removed=False):
    query = LibraryEntry.objects.filter(owner=owner_key(owner), pk=entry_id)
    if not include_removed:
        query = query.filter(removed=False)
    obj = query.first()
    if obj is None:
        raise ValidationError('记录不存在或无权访问。')
    return obj


def _refs(owner, ids, kind):
    if not isinstance(ids, list) or len(ids) > 20 or any(type(i) is not int for i in ids):
        raise ValidationError('来源关联无效。')
    result = []
    for entry_id in dict.fromkeys(ids):
        item = _entry(owner, entry_id)
        if item.kind != kind:
            raise ValidationError('来源类型不匹配。')
        result.append(item)
    return result


def _operation(key, suffix):
    try:
        return str(uuid.uuid5(uuid.UUID(str(key)), suffix))
    except (ValueError, TypeError):
        raise ValidationError('操作编号无效。') from None


def detail(owner, entry_id, version=None):
    item = _entry(owner, entry_id, include_removed=True)
    out = {'id': item.pk, 'kind': item.kind, 'revision': item.revision, 'removed': item.removed,
        'parse_state': item.parse_state, 'index_state': item.index_state,
        'error_code': item.error_code, 'conversation_id': item.conversation_id,
        'message_id': item.message_id, 'created_at': item.created_at.isoformat(),
        'source_ids': [], 'experience_ids': [], 'versions': [], 'reason': '',
        'assets': [{'id': a.pk, 'name': a.name, 'mime': a.mime, 'size': a.size, 'sha256': a.sha256}
            for a in item.assets.all()]}
    if item.kind == 'source':
        from . import library_organization
        source = Source.objects.get(pk=item.object_id)
        out.update(title=source.title or source.text[:60] or source.transcript.split('\n')[0][:60]
                   or (out['assets'][0]['name'] if out['assets'] else '未命名素材'), text=source.text, transcript=source.transcript,
                   url=source.url, status='collected', attribution=source.attribution,
                   organization=library_organization.snapshot(item))
    elif item.kind == 'principle':
        principle = Principle.objects.get(pk=item.object_id)
        selected = principle.versions.filter(number=version).first() if version else principle.current
        if selected is None:
            raise ValidationError('原则版本不存在。')
        evidence = list(selected.library_evidence.select_related('entry'))
        out.update(title=selected.title, text=selected.statement, status=selected.status,
            version=selected.number, reason=selected.reason, when_to_use=selected.when_to_use,
            boundaries=selected.boundaries,
            source_ids=[e.entry_id for e in evidence if e.entry.kind == 'source'],
            experience_ids=[e.entry_id for e in evidence if e.entry.kind == 'experience'],
            evidence=[{'id': e.entry_id, 'kind': e.entry.kind, 'revision': e.entry_revision,
                       'excerpt': e.excerpt, 'removed': e.entry.removed} for e in evidence],
            versions=[{'version': v.number, 'title':v.title, 'text': v.statement, 'reason': v.reason,
                       'status':v.status,'when_to_use':v.when_to_use,'boundaries':v.boundaries,
                       'created_at': v.created_at.isoformat(),
                       'source_ids':[e.entry_id for e in v.library_evidence.all() if e.entry.kind=='source'],
                       'experience_ids':[e.entry_id for e in v.library_evidence.all() if e.entry.kind=='experience'],
                       'evidence':[{'id':e.entry_id,'kind':e.entry.kind,'revision':e.entry_revision,
                           'excerpt':e.excerpt} for e in v.library_evidence.all()]}
                      for v in principle.versions.prefetch_related('library_evidence__entry').all()])
    else:
        exp = Observation.objects.get(pk=item.object_id)
        out.update(title=exp.facts[:60], text=exp.facts, interpretation=exp.interpretation,
                   uncertainty=exp.uncertainty, status='confirmed')
    return out


def list_entries(owner, kind=None, *, include_removed=False):
    query = LibraryEntry.objects.filter(owner=owner_key(owner))
    if not include_removed:
        query = query.filter(removed=False)
    if kind:
        if kind not in KINDS:
            raise ValidationError('记录类型无效。')
        query = query.filter(kind=kind)
    return [detail(owner, obj.pk) for obj in query.order_by('-updated_at')[:200]]


def _validate_assets(assets):
    if not isinstance(assets, list) or len(assets) > 4:
        raise ValidationError('一次最多收藏四个附件。')
    result = []
    total = 0
    for asset in assets:
        try:
            mime = asset['mime']
            raw = base64.b64decode(asset['data'], validate=True)
            name = str(asset.get('name', 'attachment'))[:200].replace('\\', '/').rsplit('/', 1)[-1]
        except (KeyError, ValueError, TypeError):
            raise ValidationError('附件内容无效。') from None
        total += len(raw)
        if not raw or len(raw) > 10 * 1024 * 1024 or total > 20 * 1024 * 1024:
            raise ValidationError('附件大小超出限制。')
        if mime in {'image/png', 'image/jpeg', 'image/webp'}:
            try:
                with Image.open(BytesIO(raw)) as image:
                    if image.width * image.height > 40_000_000 or image.format not in {'PNG', 'JPEG', 'WEBP'}:
                        raise ValueError('image')
                    image.verify()
            except Exception:
                raise ValidationError('图片无法读取。') from None
        elif mime in {'text/plain', 'text/markdown'}:
            try:
                raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                raise ValidationError('文本附件需要 UTF-8 编码。') from None
        else:
            raise ValidationError('暂只支持 PNG、JPEG、WebP、TXT 和 Markdown。')
        result.append((name or 'attachment', mime, raw))
    return result


def collect(owner, *, text='', title='', url='', assets=None, conversation_id='', message_id='', operation_key):
    owner_key(owner)
    files = _validate_assets(assets or [])
    data = {'text': text, 'title': title, 'url': url}
    text = clean_text(data, 'text')
    if not (text or url or files):
        raise ValidationError('没有可收藏的内容。')
    text_files = '\n\n'.join(raw.decode('utf-8-sig') for _, mime, raw in files if mime.startswith('text/'))
    parse_pending = any(mime.startswith('image/') for _, mime, _ in files)
    fingerprint = {'owner': owner, **data, 'conversation': conversation_id, 'message': message_id,
        'assets': [(name, mime, hashlib.sha256(raw).hexdigest()) for name, mime, raw in files]}
    written = []

    def action():
        source = save_source({'text': text or ('' if files else url), 'title': title,
            'url': url, 'transcript': text_files[:20000], 'kind': 'image' if parse_pending else 'text'},
            operation_key=_operation(operation_key, 'source')) if (text or url or text_files) else Source.objects.create(title=title, kind='image')
        item = LibraryEntry.objects.create(owner=owner, kind='source', object_id=source.pk,
            conversation_id=conversation_id[:80], message_id=message_id[:80],
            parse_state='pending' if parse_pending else ('ready' if (text or text_files) else 'empty'),
            index_state='pending' if (parse_pending or text or text_files) else 'blocked',
            searchable_text='\n'.join([title, text, text_files])[:50000])
        for name, mime, raw in files:
            extension = {'image/png':'png','image/jpeg':'jpg','image/webp':'webp','text/plain':'txt','text/markdown':'md'}[mime]
            asset = LibraryAsset(entry=item, name=name, mime=mime, sha256=hashlib.sha256(raw).hexdigest(), size=len(raw))
            asset.original.save(f'original.{extension}', ContentFile(raw), save=False)
            written.append(asset.original)
            asset.save()
        from workbench.models import LibraryOrganization
        LibraryOrganization.objects.create(entry=item)
        return item
    try:
        result = run_once('library.collect', operation_key, fingerprint, LibraryEntry, action)
    except Exception:
        for stored in written:
            stored.delete(save=False)
        raise
    return detail(owner, result.pk)


def save_principle(owner, *, statement, operation_key, confirmed, title='', entry_id=None,
                   expected_revision=None, source_ids=None, experience_ids=None, reason='',
                   when_to_use='', boundaries='', status='adopted', conversation_id='', message_id=''):
    require_confirmation(confirmed)
    owner_key(owner)
    statement = clean_text({'statement': statement}, 'statement', required=True)
    if status not in {'adopted', 'trial', 'retired'}:
        raise ValidationError('原则状态无效。')
    payload = dict(owner=owner, statement=statement, title=title, entry_id=entry_id,
        expected_revision=expected_revision, source_ids=source_ids, experience_ids=experience_ids,
        reason=reason, when_to_use=when_to_use, boundaries=boundaries, status=status,
        conversation_id=conversation_id,message_id=message_id)

    def action():
        item = _entry(owner, entry_id) if entry_id else None
        if item and (item.kind != 'principle' or item.revision != expected_revision):
            raise Conflict('原则已变化，请查看最新版本后再确认。')
        previous = detail(owner, item.pk) if item else {}
        sources = _refs(owner, source_ids if source_ids is not None else previous.get('source_ids', []), 'source')
        experiences = _refs(owner, experience_ids if experience_ids is not None else previous.get('experience_ids', []), 'experience')
        principle = Principle.objects.get(pk=item.object_id) if item else None
        principle = domain_principle({'title': title or statement[:60], 'statement': statement,
            'kind': 'belief', 'status': status, 'reason': reason, 'when_to_use': when_to_use,
            'boundaries': boundaries}, operation_key=_operation(operation_key, 'principle'),
            confirmed=True, principle=principle, expected_revision=principle.revision if principle else None,
            source_ids=[s.object_id for s in sources])
        version = principle.current
        if item:
            item.revision += 1
            item.searchable_text = f'{statement}\n{when_to_use}\n{boundaries}'
            item.embedding = []
            item.index_chunks = []
            item.index_state = 'pending'
            item.save()
        else:
            item = LibraryEntry.objects.create(owner=owner, kind='principle', object_id=principle.pk,
                searchable_text=f'{statement}\n{when_to_use}\n{boundaries}',
                conversation_id=conversation_id[:80],message_id=message_id[:80])
        for evidence in [*sources, *experiences]:
            evidence_detail=detail(owner,evidence.pk)
            LibraryEvidence.objects.create(version=version, entry=evidence, entry_revision=evidence.revision,
                excerpt=(evidence_detail['text'] or evidence_detail.get('transcript',''))[:2000])
        return item
    return detail(owner, run_once('library.principle', operation_key, payload, LibraryEntry, action).pk)


def save_experience(owner, *, facts, operation_key, confirmed, interpretation='', uncertainty='',
                    conversation_id='', message_id=''):
    require_confirmation(confirmed)
    owner_key(owner)
    values = {name: clean_text({'value': value}, 'value', required=name == 'facts')
              for name, value in [('facts', facts), ('interpretation', interpretation), ('uncertainty', uncertainty)]}
    def action():
        exp = Observation.objects.create(**values)
        return LibraryEntry.objects.create(owner=owner, kind='experience', object_id=exp.pk,
            searchable_text=f'{facts}\n{interpretation}', conversation_id=conversation_id[:80], message_id=message_id[:80])
    return detail(owner, run_once('library.experience', operation_key, {'owner':owner, **values,
        'conversation_id':conversation_id, 'message_id':message_id}, LibraryEntry, action).pk)


def index_entry(owner, entry_id, *, embed=None):
    item = _entry(owner, entry_id)
    if item.parse_state != 'ready' or not item.searchable_text.strip():
        return
    try:
        if embed is None:
            from .semantic import encode_chunks, MODEL_NAME
            chunks = encode_chunks(item.searchable_text)
            vector, model = chunks[0]['vector'], MODEL_NAME
        else:
            vector, model = list(embed(item.searchable_text)), 'test'
            chunks = [{'text': item.searchable_text, 'vector': vector}]
        if not vector:
            raise ValueError('empty vector')
        LibraryEntry.objects.filter(pk=item.pk, revision=item.revision, removed=False).update(
            embedding=vector, index_chunks=chunks, index_state='ready', index_revision=item.revision,
            index_model=model, error_code='')
    except Exception:
        LibraryEntry.objects.filter(pk=item.pk, revision=item.revision).update(index_state='failed', error_code='index_failed')


def conversation(owner, conversation_id):
    owner_key(owner)
    if not isinstance(conversation_id, str) or not 1 <= len(conversation_id) <= 80:
        raise ValidationError('对话编号无效。')
    return LibraryConversation.objects.get_or_create(owner=owner, conversation_id=conversation_id)[0]


def draft_detail(draft):
    suggestion = draft.suggestion
    return {'id': draft.pk, 'conversation_id':draft.conversation_id, 'message_id':draft.message_id,
            'payload':suggestion.payload, 'status':'saved' if suggestion.applied else 'dismissed' if suggestion.discarded else 'pending',
            'entry_id':draft.entry_id}


def may_propose(owner, conversation_id):
    state = conversation(owner, conversation_id)
    return state.proposals_enabled and not LibraryDraft.objects.filter(owner=owner, automatic=True,
        conversation_id=conversation_id).exists() and not LibraryDraft.objects.filter(owner=owner,
        automatic=True, created_at__gte=timezone.now()-timedelta(minutes=20)).exists()


def propose(owner, conversation_id, message_id, payload, *, automatic=True):
    with transaction.atomic():
        if automatic and not may_propose(owner, conversation_id):
            return None
        if not isinstance(payload, dict) or payload.get('kind') not in {'principle', 'experience'}:
            raise ValidationError('记忆建议类型无效。')
        text = clean_text(payload, 'text', required=True, limit=8000)
        normalized = {'kind':payload['kind'], 'text':text,
            'reason':clean_text(payload, 'reason', limit=2000), 'source_ids':payload.get('source_ids', []),
            'experience_ids':payload.get('experience_ids', [])}
        _refs(owner, normalized['source_ids'], 'source')
        _refs(owner, normalized['experience_ids'], 'experience')
        if payload.get('entry_id'):
            entry = _entry(owner, payload['entry_id'])
            if entry.kind != 'principle' or payload.get('expected_revision') != entry.revision:
                raise Conflict('待修订原则已变化。')
            normalized.update(entry_id=entry.pk, expected_revision=entry.revision)
        existing = LibraryDraft.objects.filter(owner=owner, conversation_id=conversation_id,
            message_id=message_id, suggestion__applied=False, suggestion__discarded=False).first()
        if existing:
            return draft_detail(existing)
        suggestion = Suggestion.objects.create(task='library', payload=normalized, is_demo=False,
            context={'owner':owner, 'conversation_id':conversation_id, 'message_id':message_id})
        draft = LibraryDraft.objects.create(owner=owner, conversation_id=conversation_id,
            message_id=message_id, suggestion=suggestion, automatic=automatic)
        return draft_detail(draft)


def confirm(owner, draft_id, *, confirmed, operation_key, text=None):
    require_confirmation(confirmed)
    with transaction.atomic():
        draft = LibraryDraft.objects.select_related('suggestion').filter(owner=owner_key(owner), pk=draft_id).first()
        if draft is None or draft.suggestion.discarded:
            raise ValidationError('待保存内容不存在或已忽略。')
        if draft.suggestion.applied:
            return detail(owner, draft.entry_id)
        data = draft.suggestion.payload.copy()
        chosen = text if text is not None else data['text']
        if data['kind'] == 'principle':
            result = save_principle(owner, statement=chosen, operation_key=operation_key, confirmed=True,
                source_ids=data.get('source_ids'), experience_ids=data.get('experience_ids'),
                entry_id=data.get('entry_id'), expected_revision=data.get('expected_revision'), reason=data.get('reason',''),
                conversation_id=draft.conversation_id,message_id=draft.message_id)
        else:
            result = save_experience(owner, facts=chosen, operation_key=operation_key, confirmed=True,
                conversation_id=draft.conversation_id, message_id=draft.message_id)
        draft.suggestion.applied = True
        draft.suggestion.result_type = result['kind']
        draft.suggestion.result_id = result['id']
        draft.suggestion.save()
        draft.entry_id = result['id']
        draft.save()
        return result


def dismiss(owner, draft_id):
    draft = LibraryDraft.objects.filter(owner=owner_key(owner), pk=draft_id).first()
    if draft is None:
        raise ValidationError('待保存内容不存在。')
    Suggestion.objects.filter(pk=draft.suggestion_id, applied=False).update(discarded=True)


def set_removed(owner, entry_id, removed, *, expected_revision, confirmed):
    require_confirmation(confirmed)
    with transaction.atomic():
        item = _entry(owner, entry_id, include_removed=True)
        if item.revision != expected_revision:
            raise Conflict('记录已变化，请刷新后再操作。')
        item.removed = bool(removed)
        item.revision += 1
        item.index_state = 'pending'
        item.embedding = []
        item.index_chunks = []
        item.save()
    return detail(owner, item.pk)
