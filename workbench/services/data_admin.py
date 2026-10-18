"""Export and explicitly-confirmed deletion operations for the local workbench.

This module deliberately owns the destructive boundary.  Views can render an
impact preview, then pass its signature back to ``delete_record``.  The
signature is rebuilt inside the write transaction, so a preview cannot be
reused after related records changed.
"""

import hashlib
import io
import json
import zipfile
from pathlib import Path

from django.core import serializers
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.text import get_valid_filename

from workbench.models import (
    Case,
    Decision,
    Message,
    Observation,
    Operation,
    Principle,
    PrincipleVersion,
    Source,
    Suggestion,
    UserSettings,
)
from .common import Conflict, ConfirmationRequired


ALLOWED_KINDS = {'source', 'principle', 'case', 'decision', 'observation', 'suggestion'}
CORE_MODELS = (Source, Principle, PrincipleVersion, Case, Message, Decision, Observation, Suggestion, Operation, UserSettings)
_REF_FIELD_TYPES = {
    'source': 'source', 'source_id': 'source', 'source_ids': 'source', 'sources': 'source', 'source_snapshot': 'source', 'source_snapshot_id': 'source',
    'principle': 'principle', 'principle_id': 'principle', 'principle_ids': 'principle', 'principles': 'principle',
    'version': 'version', 'version_id': 'version', 'version_ids': 'version', 'versions': 'version', 'value': 'version', 'value_id': 'version', 'values': 'version',
    'principle_version_id': 'version', 'principle_version_ids': 'version',
    'observation': 'observation', 'observation_id': 'observation', 'observation_ids': 'observation', 'observations': 'observation',
    'case': 'case', 'case_id': 'case', 'case_ids': 'case', 'cases': 'case',
    'decision': 'decision', 'decision_id': 'decision', 'decision_ids': 'decision', 'decisions': 'decision',
}


def _json_dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


def _signature(payload):
    return hashlib.sha256(_json_dump(payload).encode('utf-8')).hexdigest()


def _target_key(kind):
    return {
        'source': 'source',
        'principle': 'principle',
        'case': 'case',
        'decision': 'decision',
        'observation': 'observation',
        'suggestion': 'suggestion',
    }[kind]


def _field_type(key):
    return _REF_FIELD_TYPES.get(str(key).lower())


def _typed_mentions(value, ids):
    """Match an ID only inside a field already typed by its surrounding key."""
    ids = set(ids)
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value in ids
    if isinstance(value, str) and value.isdigit():
        return int(value) in ids
    if isinstance(value, list):
        return any(_typed_mentions(item, ids) for item in value)
    if isinstance(value, dict):
        return _typed_mentions(value.get('id'), ids)
    return False


def _source_value_mentions(value, ids, fragments):
    if _typed_mentions(value, ids):
        return True
    if isinstance(value, str):
        return any(fragment and value == fragment for fragment in fragments)
    if isinstance(value, dict):
        for key in ('id', 'text', 'transcript', 'url'):
            item = value.get(key)
            if key == 'id' and _typed_mentions(item, ids):
                return True
            if key != 'id' and isinstance(item, str) and any(fragment and item == fragment for fragment in fragments):
                return True
    return False


def _json_mentions(value, ids, *, entity=None, text_fragments=()):
    """Find IDs in a field explicitly typed as ``entity``; never mix entity IDs."""
    if entity is None or value is None:
        return False
    if isinstance(value, list):
        return any(_json_mentions(item, ids, entity=entity, text_fragments=text_fragments) for item in value)
    if not isinstance(value, dict):
        return False
    for key, item in value.items():
        normalized = str(key).lower()
        field_type = _field_type(normalized)
        if field_type == entity:
            if entity == 'source' and _source_value_mentions(item, ids, text_fragments):
                return True
            if _typed_mentions(item, ids):
                return True
        if isinstance(item, (dict, list)) and _json_mentions(item, ids, entity=entity, text_fragments=text_fragments):
            return True
    return False


_MISSING = object()


def _strip_typed_ids(value, ids):
    ids = set(ids)
    if _typed_mentions(value, ids) and not isinstance(value, list):
        return _MISSING
    if isinstance(value, list):
        output = []
        for item in value:
            cleaned = _strip_typed_ids(item, ids)
            if cleaned is not _MISSING:
                output.append(cleaned)
        return output
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            cleaned = _strip_typed_ids(item, ids) if key == 'id' else item
            if cleaned is not _MISSING:
                output[key] = cleaned
        return output
    return value


def _strip_ids(value, references):
    """Remove IDs only from fields whose entity type is explicitly known."""
    if isinstance(value, list):
        return [_strip_ids(item, references) for item in value]
    if not isinstance(value, dict):
        return value
    output = {}
    for key, item in value.items():
        entity = _field_type(key)
        if entity in references:
            cleaned = _strip_typed_ids(item, references[entity])
            if cleaned is not _MISSING:
                output[key] = cleaned
        else:
            output[key] = _strip_ids(item, references)
    return output


def _strip_fragments(value, fragments):
    """Drop source snapshots from metadata, never from a user's message body."""
    fragments = set(fragment for fragment in fragments if fragment)
    if isinstance(value, list):
        return [_strip_fragments(item, fragments) for item in value]
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in {'source', 'source_text', 'source_url', 'text', 'snapshot', 'original', 'original_text', 'url'} and isinstance(item, str) and item in fragments:
                continue
            output[key] = _strip_fragments(item, fragments)
        return output
    return value


def _object_title(kind, obj):
    if kind == 'source':
        return obj.title or obj.url or f'素材 #{obj.pk}'
    if kind == 'principle':
        current = obj.current
        return current.title if current else f'原则 #{obj.pk}'
    if kind == 'case':
        return obj.situation
    if kind == 'decision':
        return obj.situation
    if kind == 'observation':
        return obj.facts
    return f'建议 #{obj.pk}'


def _get_object(kind, pk):
    if kind not in ALLOWED_KINDS:
        raise ValidationError('不支持删除这种记录。')
    models = {
        'source': Source,
        'principle': Principle,
        'case': Case,
        'decision': Decision,
        'observation': Observation,
        'suggestion': Suggestion,
    }
    try:
        return models[kind].objects.get(pk=pk)
    except models[kind].DoesNotExist:
        raise ValidationError('要操作的记录不存在。')


def _source_related(source):
    source_ids = {source.pk}
    fragments = tuple(fragment for fragment in (source.text, source.url, source.transcript) if fragment)
    suggestion_ids = set()
    for suggestion in Suggestion.objects.all():
        if suggestion.source_id == source.pk or _json_mentions(suggestion.context, source_ids, entity='source', text_fragments=fragments) or _json_mentions(suggestion.payload, source_ids, entity='source', text_fragments=fragments):
            suggestion_ids.add(suggestion.pk)
    message_ids = set()
    for message in Message.objects.filter(role='assistant'):
        snapshot_in_message = any(
            fragment and (message.content.strip() == fragment or (len(fragment) >= 8 and fragment in message.content))
            for fragment in fragments
        )
        if _json_mentions(message.references, source_ids, entity='source') or snapshot_in_message:
            message_ids.add(message.pk)
    return {
        'source_ids': source_ids,
        'version_ids': set(source.principle_versions.values_list('pk', flat=True)),
        'suggestion_ids': suggestion_ids,
        'message_ids': message_ids,
    }


def _principle_related(principle):
    version_ids = set(principle.versions.values_list('pk', flat=True))
    suggestion_ids = set(Suggestion.objects.filter(principle_id=principle.pk).values_list('pk', flat=True))
    suggestion_ids.update(Suggestion.objects.filter(result_type='principle', result_id=principle.pk).values_list('pk', flat=True))
    message_ids = set()
    for suggestion in Suggestion.objects.exclude(pk__in=suggestion_ids):
        if (_json_mentions(suggestion.context, version_ids, entity='version') or
                _json_mentions(suggestion.payload, version_ids, entity='version') or
                _json_mentions(suggestion.context, {principle.pk}, entity='principle') or
                _json_mentions(suggestion.payload, {principle.pk}, entity='principle')):
            suggestion_ids.add(suggestion.pk)
    for message in Message.objects.filter(role='assistant'):
        if (_json_mentions(message.references, version_ids, entity='version') or
                _json_mentions(message.references, {principle.pk}, entity='principle')):
            message_ids.add(message.pk)
    return {
        'principle_ids': {principle.pk},
        'version_ids': version_ids,
        'case_ids': set(Case.objects.filter(versions__in=version_ids).values_list('pk', flat=True).distinct()) if version_ids else set(),
        'decision_ids': set(Decision.objects.filter(versions__in=version_ids).values_list('pk', flat=True).distinct()) if version_ids else set(),
        'observation_ids': set(Observation.objects.filter(versions__in=version_ids).values_list('pk', flat=True).distinct()) if version_ids else set(),
        'suggestion_ids': suggestion_ids,
        'message_ids': message_ids,
    }


def _case_related(case):
    suggestion_ids = set(Suggestion.objects.filter(case_id=case.pk).values_list('pk', flat=True))
    for suggestion in Suggestion.objects.exclude(pk__in=suggestion_ids):
        if (_json_mentions(suggestion.context, {case.pk}, entity='case') or
                _json_mentions(suggestion.payload, {case.pk}, entity='case')):
            suggestion_ids.add(suggestion.pk)
    return {
        'case_ids': {case.pk},
        'message_ids': set(case.entries.values_list('pk', flat=True)),
        'decision_ids': set(case.decisions.values_list('pk', flat=True)),
        'observation_ids': set(case.experiences.values_list('pk', flat=True)),
        'suggestion_ids': suggestion_ids,
        'version_ids': set(case.versions.values_list('pk', flat=True)),
    }


def _decision_related(decision):
    observation_ids = set(decision.observations.values_list('pk', flat=True))
    suggestion_ids = set(Suggestion.objects.filter(observation_id__in=observation_ids).values_list('pk', flat=True))
    suggestion_ids.update(Suggestion.objects.filter(result_type='decision', result_id=decision.pk).values_list('pk', flat=True))
    for suggestion in Suggestion.objects.exclude(pk__in=suggestion_ids):
        if (_json_mentions(suggestion.context, {decision.pk}, entity='decision') or
                _json_mentions(suggestion.payload, {decision.pk}, entity='decision') or
                _json_mentions(suggestion.context, observation_ids, entity='observation') or
                _json_mentions(suggestion.payload, observation_ids, entity='observation')):
            suggestion_ids.add(suggestion.pk)
    case_ids = {decision.case_id} if decision.case_id else set()
    if observation_ids:
        case_ids.update(Case.objects.filter(experiences__in=observation_ids).values_list('pk', flat=True).distinct())
    return {
        'decision_ids': {decision.pk},
        'observation_ids': observation_ids,
        'version_ids': set(decision.versions.values_list('pk', flat=True)),
        'suggestion_ids': suggestion_ids,
        'case_ids': case_ids,
        'message_ids': {
            message.pk for message in Message.objects.filter(role='assistant')
            if (_json_mentions(message.references, {decision.pk}, entity='decision') or
                _json_mentions(message.references, observation_ids, entity='observation'))
        },
    }


def _observation_related(observation):
    suggestion_ids = set(Suggestion.objects.filter(observation_id=observation.pk).values_list('pk', flat=True))
    for suggestion in Suggestion.objects.exclude(pk__in=suggestion_ids):
        if (_json_mentions(suggestion.context, {observation.pk}, entity='observation') or
                _json_mentions(suggestion.payload, {observation.pk}, entity='observation')):
            suggestion_ids.add(suggestion.pk)
    return {
        'observation_ids': {observation.pk},
        'decision_ids': {observation.decision_id},
        'case_ids': set(observation.cases.values_list('pk', flat=True)),
        'version_ids': set(observation.versions.values_list('pk', flat=True)),
        'suggestion_ids': suggestion_ids,
        'message_ids': {
            message.pk for message in Message.objects.filter(role='assistant')
            if _json_mentions(message.references, {observation.pk}, entity='observation')
        },
    }


def _related(kind, obj):
    if kind == 'source':
        return _source_related(obj)
    if kind == 'principle':
        return _principle_related(obj)
    if kind == 'case':
        return _case_related(obj)
    if kind == 'decision':
        return _decision_related(obj)
    if kind == 'observation':
        return _observation_related(obj)
    return {'suggestion_ids': {obj.pk}}


def _impact_payload(kind, obj, related):
    payload = {
        'kind': kind,
        'id': obj.pk,
        'title': _object_title(kind, obj),
        'related': {key: sorted(value) for key, value in sorted(related.items())},
    }
    if kind == 'source':
        payload['attachment'] = obj.image.name if obj.image else ''
        payload['revision'] = obj.revision
    if kind == 'principle':
        payload['revision'] = obj.revision
    if kind == 'case':
        payload['revision'] = obj.revision
    return payload


def _impact_lines(kind, obj, related):
    lines = []
    if kind == 'source':
        lines.extend([
            f"删除素材“{_object_title(kind, obj)}”",
            f"解除 {len(related['version_ids'])} 个原则版本的素材关联",
            f"删除 {len(related['suggestion_ids'])} 份引用它的 AI 建议",
            f"清理 {len(related['message_ids'])} 条受影响的 AI 消息；用户自述文本保留",
        ])
        if obj.image:
            lines.append('删除素材原图附件（在数据库事务提交后执行）')
    elif kind == 'principle':
        lines.extend([
            f"删除原则“{_object_title(kind, obj)}”及其 {len(related['version_ids'])} 个历史版本",
            f"解除 {len(related['case_ids'])} 个情境、{len(related['decision_ids'])} 个选择和 {len(related['observation_ids'])} 个观察的版本引用",
            f"删除 {len(related['suggestion_ids'])} 份引用这些版本的 AI 建议",
            f"清理 {len(related['message_ids'])} 条受影响的 AI 消息；正式选择和观察记录保留",
        ])
    elif kind == 'case':
        lines.extend([
            f"删除情境“{_object_title(kind, obj)}”及其 {len(related['message_ids'])} 条聊天消息",
            f"删除 {len(related['suggestion_ids'])} 份与该情境有关的 AI 建议",
            f"保留 {len(related['decision_ids'])} 个正式选择，并解除它们与情境的关联",
        ])
    elif kind == 'decision':
        lines.extend([
            f"删除选择“{_object_title(kind, obj)}”及其 {len(related['observation_ids'])} 条观察记录",
            f"删除 {len(related['suggestion_ids'])} 份与选择或观察有关的 AI 建议",
            f"清理 {len(related['message_ids'])} 条受影响的 AI 消息",
        ])
    elif kind == 'observation':
        lines.extend([
            f"删除观察“{_object_title(kind, obj)}”",
            f"解除 {len(related['case_ids'])} 个情境与它的关联",
            f"删除 {len(related['suggestion_ids'])} 份与该观察有关的 AI 建议",
            f"清理 {len(related['message_ids'])} 条受影响的 AI 消息",
        ])
    else:
        lines.append(f"删除 AI 建议“{_object_title(kind, obj)}”")
    return lines


def delete_impact(kind, pk):
    with transaction.atomic():
        obj = _get_object(kind, pk)
        related = _related(kind, obj)
        payload = _impact_payload(kind, obj, related)
        return {
            'title': payload['title'],
            'lines': _impact_lines(kind, obj, related),
            'signature': _signature(payload),
        }


def _clean_message_references(references, fragments=()):
    if not references and not fragments:
        return
    for message in Message.objects.filter(role='user'):
        cleaned = _strip_ids(message.references, references)
        cleaned = _strip_fragments(cleaned, fragments)
        if cleaned != message.references:
            Message.objects.filter(pk=message.pk).update(references=cleaned)


def _delete_impacted_ai_messages(message_ids):
    if message_ids:
        Message.objects.filter(pk__in=message_ids, role='assistant').delete()


def _delete_impacted_suggestions(suggestion_ids):
    if suggestion_ids:
        Suggestion.objects.filter(pk__in=suggestion_ids).delete()


def _bump_cases(case_ids):
    if case_ids:
        Case.objects.filter(pk__in=case_ids).update(revision=F('revision') + 1)


def _delete_source(source, related):
    filename = source.image.name if source.image else ''
    storage = source.image.storage if source.image else None
    _delete_impacted_ai_messages(related['message_ids'])
    _delete_impacted_suggestions(related['suggestion_ids'])
    _clean_message_references({'source': {source.pk}}, (source.text, source.url, source.transcript))
    source.principle_versions.clear()
    source.delete()
    if filename and storage:
        transaction.on_commit(lambda name=filename, store=storage: store.delete(name))


def _delete_principle(principle, related):
    _delete_impacted_ai_messages(related['message_ids'])
    _delete_impacted_suggestions(related['suggestion_ids'])
    _clean_message_references({'version': related['version_ids'], 'principle': {principle.pk}})
    _bump_cases(related['case_ids'])
    versions = list(principle.versions.all())
    for version in versions:
        version.sources.clear()
        version.delete()
    principle.delete()


def _delete_case(case, related):
    _delete_impacted_suggestions(related['suggestion_ids'])
    case.delete()


def _delete_decision(decision, related):
    _delete_impacted_ai_messages(related.get('message_ids', set()))
    _delete_impacted_suggestions(related['suggestion_ids'])
    _bump_cases(set(related['case_ids']))
    _clean_message_references({'observation': related['observation_ids'], 'decision': related['decision_ids']})
    decision.delete()


def _delete_observation(observation, related):
    _delete_impacted_ai_messages(related.get('message_ids', set()))
    _delete_impacted_suggestions(related['suggestion_ids'])
    _bump_cases(related['case_ids'])
    _clean_message_references({'observation': related['observation_ids']})
    observation.delete()


def delete_record(kind, pk, *, signature, confirmed):
    if confirmed is not True:
        raise ConfirmationRequired('请先确认删除影响后再继续。')
    with transaction.atomic():
        obj = _get_object(kind, pk)
        related = _related(kind, obj)
        payload = _impact_payload(kind, obj, related)
        if signature != _signature(payload):
            raise Conflict('删除影响已经变化，请重新打开预览后确认。')
        if kind == 'source':
            _delete_source(obj, related)
        elif kind == 'principle':
            _delete_principle(obj, related)
        elif kind == 'case':
            _delete_case(obj, related)
        elif kind == 'decision':
            _delete_decision(obj, related)
        elif kind == 'observation':
            _delete_observation(obj, related)
        else:
            obj.delete()


def _attachment_entry(source, used_names):
    if not source.image:
        return None
    storage = source.image.storage
    name = source.image.name
    if not storage.exists(name):
        raise FileNotFoundError(f'素材 #{source.pk} 的原图附件不存在：{name}')
    with storage.open(name, 'rb') as handle:
        raw = handle.read()
    base = get_valid_filename(Path(name).name) or 'attachment'
    zip_name = f'attachments/source-{source.pk}-{base}'
    stem = Path(zip_name).stem
    suffix = Path(zip_name).suffix
    index = 2
    while zip_name in used_names:
        zip_name = f'attachments/{stem}-{index}{suffix}'
        index += 1
    used_names.add(zip_name)
    return {
        'zip_path': zip_name,
        'source_id': source.pk,
        'original_name': Path(name).name,
        'size': len(raw),
        'sha256': hashlib.sha256(raw).hexdigest(),
        'bytes': raw,
    }


def export_archive():
    """Return a complete ZIP snapshot without reading runtime secrets."""
    with transaction.atomic():
        objects = []
        for model in CORE_MODELS:
            objects.extend(model.objects.all().order_by('pk'))
        data = serializers.serialize('json', objects, indent=2)
        attachments = []
        used_names = set()
        for source in Source.objects.all().order_by('pk'):
            entry = _attachment_entry(source, used_names)
            if entry:
                attachments.append(entry)
        exported_at = timezone.now().isoformat()
        manifest = {
            'schema_version': 1,
            'exported_at': exported_at,
            'attachments': [
                {key: value for key, value in entry.items() if key != 'bytes'}
                for entry in attachments
            ],
        }
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('data.json', data.encode('utf-8'))
            archive.writestr('manifest.json', _json_dump(manifest).encode('utf-8'))
            for entry in attachments:
                archive.writestr(entry['zip_path'], entry['bytes'])
        return output.getvalue()
