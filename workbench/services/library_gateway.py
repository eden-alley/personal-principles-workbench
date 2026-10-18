"""Provider-independent context preparation and completed-turn bookkeeping."""
import hashlib
import json
import re
import time
import uuid
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from workbench.models import LibraryDraft, LibraryEntry, LibraryTurn
from . import library, library_context
from .common import Conflict


def identity(owner, conversation_id, message_id):
    if not isinstance(owner,str) or not re.fullmatch(r'[a-fA-F0-9]{24}',owner):
        raise ValidationError('登录身份无效。')
    for value in [conversation_id,message_id]:
        if not isinstance(value,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',value):
            raise ValidationError('对话身份无效。')


def text_content(message):
    content=message.get('content','')
    if isinstance(content,str):
        return content
    if isinstance(content,list):
        return '\n'.join(part.get('text','') for part in content
            if isinstance(part,dict) and part.get('type')=='text' and isinstance(part.get('text'),str))
    return ''


def answer_hash(text):
    return hashlib.sha256(' '.join(text.split()).encode()).hexdigest()


def prepare(owner, conversation_id, message_id, messages, *, source_message_id=None):
    claimed=[]
    try:
        return _prepare(owner,conversation_id,message_id,messages,source_message_id=source_message_id,claimed=claimed)
    except Exception:
        if claimed:
            LibraryTurn.objects.filter(pk=claimed[0][0],lease_token=claimed[0][1],completed=False).update(processing_until=None)
        raise


def _prepare(owner, conversation_id, message_id, messages, *, source_message_id, claimed):
    identity(owner,conversation_id,message_id)
    if not isinstance(messages,list) or not messages or len(messages)>100 or any(not isinstance(m,dict) for m in messages):
        raise ValidationError('消息格式无效。')
    if messages[-1].get('role')!='user':
        raise ValidationError('缺少当前用户消息。')
    if source_message_id:
        identity(owner,conversation_id,source_message_id)
    request_hash=hashlib.sha256(json.dumps(messages,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    with transaction.atomic():
        existing,_=LibraryTurn.objects.get_or_create(owner=owner,conversation_id=conversation_id,message_id=message_id)
        if existing.request_sha256 and existing.request_sha256!=request_hash:
            raise Conflict('消息编号已被另一段内容使用。')
        if existing.completed and existing.answer_text:
            return dict(owner=owner,conversation_id=conversation_id,message_id=message_id,
                references=existing.references,receipt=existing.answer_text,replayed=True)
        if existing.processing_until and existing.processing_until>timezone.now():
            raise Conflict('这条消息仍在处理，请等待完成。')
        existing.request_sha256=request_hash
        existing.processing_until=timezone.now()+timedelta(minutes=5)
        existing.lease_token=str(uuid.uuid4())
        existing.save(update_fields=['request_sha256','processing_until','lease_token'])
        claimed.append((existing.pk,existing.lease_token))
    query=text_content(messages[-1])
    command=library_context.explicit_command(query)
    source_message_id=source_message_id or message_id
    operation_key=str(uuid.uuid5(uuid.NAMESPACE_URL,f'library:{owner}:{conversation_id}:{source_message_id}'))
    state=library.conversation(owner,conversation_id)
    prepared=dict(owner=owner,conversation_id=conversation_id,message_id=message_id, references=[],
        turn_id=existing.pk,lease_token=existing.lease_token)
    result=None
    if re.fullmatch(r'(?:请)?(?:确认保存|保存这条|记住这条|记住这个观点)[。！!\s]*',query):
        previous=next((m for m in reversed(messages[:-1]) if m.get('role')=='assistant'),None)
        if previous:
            turn=LibraryTurn.objects.filter(owner=owner,conversation_id=conversation_id,completed=True,
                answer_sha256=answer_hash(text_content(previous))).order_by('-pk').first()
            if turn:
                draft=LibraryDraft.objects.filter(owner=owner,conversation_id=conversation_id,
                    message_id=turn.message_id,suggestion__discarded=False).order_by('-pk').first()
                if draft:
                    result=library.confirm(owner,draft.pk,confirmed=True,operation_key=operation_key)
                    command=(result['kind'],result['text'])
    if re.fullmatch(r'(?:请)?(?:先收着|收藏|保存为素材|收藏这张图)[。！!\s]*',query):
        content=messages[-1].get('content')
        assets=[]
        for part in content if isinstance(content,list) else []:
            if not isinstance(part,dict) or part.get('type')!='image_url':
                continue
            image=part.get('image_url',{})
            url=image.get('url','') if isinstance(image,dict) else image
            match=re.fullmatch(r'data:(image/(?:png|jpeg|webp));base64,(.+)',url)
            if not match:
                raise ValidationError('收藏图片需要本地上传的原件。')
            assets.append({'name':f'image-{len(assets)+1}.{match[1].split("/")[1]}','mime':match[1],'data':match[2]})
        if assets:
            result=library.collect(owner,assets=assets,conversation_id=conversation_id,message_id=source_message_id,
                operation_key=operation_key)
            command=('source','图片原件已保存，文字解析和索引将继续在本机处理。')
    if command:
        kind,content=command
        if result is not None:
            pass
        elif kind=='principle':
            result=library.save_principle(owner,statement=content,confirmed=True,operation_key=operation_key,
                conversation_id=conversation_id,message_id=source_message_id)
        elif kind=='experience':
            result=library.save_experience(owner,facts=content,confirmed=True,operation_key=operation_key,
                conversation_id=conversation_id,message_id=source_message_id)
        else:
            result=library.collect(owner,text=content,operation_key=operation_key,
                conversation_id=conversation_id,message_id=source_message_id)
        label={'principle':'原则','experience':'经历','source':'素材'}[kind]
        prepared.update(receipt=f'已保存为{label}：\n\n{content}',saved_entry_id=result['id'])
        if kind=='source':
            prepared['receipt']+='\n\n后台会自动整理摘要、要点和主题，可在资料库查看进度与结果。'
        if not LibraryTurn.objects.filter(pk=existing.pk,lease_token=existing.lease_token).update(
                notice=f'已保存为{label}，记录 {result["id"]}。',completed=True,
                answer_text=prepared['receipt'],answer_sha256=answer_hash(prepared['receipt']),processing_until=None):
            raise Conflict('这次回答已被新的请求接管。')
        return prepared
    started=time.monotonic()
    notice=''
    try:
        refs=library_context.retrieve(owner,query,excluded_ids=state.excluded_ids,
            selected_ids=state.selected_ids)['references']
    except Exception:
        refs=[]
        notice='个人资料检索暂不可用，本轮按普通对话回答。'
        if state.selected_ids:
            receipt='本轮指定的资料暂时无法读取。请在资料库检查解析状态或调整所选记录，再发送问题；这次没有调用模型分析这些资料。'
            if not LibraryTurn.objects.filter(pk=existing.pk,lease_token=existing.lease_token).update(notice=receipt,completed=True,
                    answer_text=receipt,answer_sha256=answer_hash(receipt),processing_until=None):
                raise Conflict('这次回答已被新的请求接管。')
            prepared.update(receipt=receipt)
            return prepared
    elapsed=int((time.monotonic()-started)*1000)
    history=library_context.clean_history(messages)
    # Only remove assistant answers actually derived from references invalidated since that turn.
    stale=set()
    entries={row.pk:row for row in LibraryEntry.objects.filter(owner=owner)}
    for turn in LibraryTurn.objects.filter(owner=owner,conversation_id=conversation_id,completed=True).exclude(answer_sha256=''):
        if any(ref['id'] in state.excluded_ids or ref['id'] not in entries or
            entries[ref['id']].removed or entries[ref['id']].revision!=ref['revision'] for ref in turn.references):
            stale.add(turn.answer_sha256)
    history=[m for m in history if m.get('role')!='assistant' or answer_hash(text_content(m)) not in stale]
    requested=bool(re.search(r'(?:帮我|请|把|将).{0,50}(?:提炼|整理|拟定|修订).{0,30}(?:原则|经历)',query)
        or re.fullmatch(r'(?:请)?(?:确认保存|保存这条|记住这条|记住这个观点)[。！!\s]*',query))
    allowed=library.may_propose(owner,conversation_id)
    history.insert(0,{'role':'system','content':library_context.context_instruction(refs,
        allow_proposal=allowed,requested_draft=requested)})
    if not LibraryTurn.objects.filter(pk=existing.pk,lease_token=existing.lease_token).update(
            references=refs,retrieval_ms=elapsed,notice=notice,completed=False):
        raise Conflict('这次回答已被新的请求接管。')
    prepared.update(messages=history,references=refs,allow_proposal=allowed or requested,
        automatic=not requested)
    return prepared


@transaction.atomic
def finish(prepared, answer, proposal):
    if 'turn_id' not in prepared:
        return
    if not LibraryTurn.objects.filter(pk=prepared['turn_id'],lease_token=prepared['lease_token'],completed=False).exists():
        raise Conflict('这次回答已被新的请求接管。')
    refs={ref['id']:ref for ref in prepared['references']}
    if proposal and prepared['allow_proposal']:
        try:
            for field,kind in [('source_ids','source'),('experience_ids','experience')]:
                ids=proposal.get(field,[])
                if not isinstance(ids,list) or any(type(i) is not int or i not in refs or refs[i]['kind']!=kind for i in ids):
                    raise ValidationError('草稿引用无效。')
            target=proposal.get('entry_id')
            if target and (type(target) is not int or target not in refs or refs[target]['kind']!='principle'
                    or proposal.get('expected_revision')!=refs[target]['revision']):
                raise ValidationError('草稿版本无效。')
            library.propose(prepared['owner'],prepared['conversation_id'],prepared['message_id'],proposal,
                automatic=prepared['automatic'])
        except (ValidationError,Conflict,TypeError,ValueError):
            pass  # A malformed model suggestion must never become a confirmed record.
    LibraryTurn.objects.filter(pk=prepared['turn_id'],lease_token=prepared['lease_token']).update(completed=True,answer_sha256=answer_hash(answer),
        answer_text=answer,processing_until=None)


def release(owner,conversation_id,message_id,lease_token):
    LibraryTurn.objects.filter(owner=owner,conversation_id=conversation_id,message_id=message_id,
        completed=False,lease_token=lease_token).update(processing_until=None)


def turn_detail(owner, conversation_id, message_id):
    identity(owner,conversation_id,message_id)
    turn=LibraryTurn.objects.filter(owner=owner,conversation_id=conversation_id,message_id=message_id).first()
    return {'references':turn.references if turn else [],'notice':turn.notice if turn else '',
        'completed':turn.completed if turn else False,'retrieval_ms':turn.retrieval_ms if turn else 0,
        'drafts':[library.draft_detail(d) for d in LibraryDraft.objects.filter(owner=owner,
            conversation_id=conversation_id,message_id=message_id).select_related('suggestion')]}
