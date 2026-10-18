import json
import threading
import uuid
from django.core.exceptions import ValidationError
from django.db import transaction
from workbench.models import Source, Case, Principle, PrincipleVersion, Observation, Decision, Suggestion, Message, Operation
from workbench.ai.provider import generate, stream_generate, ProviderError
from workbench.ai.validation import validate_output, ValidationError as OutputError
from .common import run_once, claim, require_confirmation, Conflict
from .retrieval import settings_record
from .ai_settings import provider_config
from .principles import save_principle
from .records import save_decision


def _version(v):
    return {field: getattr(v, field) for field in ['id', 'number', 'title', 'statement', 'kind', 'status', 'when_to_use', 'conditions', 'boundaries', 'questions', 'tags', 'is_demo']}


def _observation(o):
    return {'id': o.id, 'facts': o.facts, 'interpretation': o.interpretation, 'uncertainty': o.uncertainty, 'difference': o.difference, 'effect': o.effect, 'provenance': '用户明确保存的自述；解释不等于客观事实', 'is_demo': o.is_demo}


def _bounded(context, limit):
    # Preserve complete valid JSON and identifiers; shorten text, never slice JSON.
    context = json.loads(json.dumps(context, ensure_ascii=False))
    context['context_note'] = '仅发送本次选中的内容；未发送原始附件。'
    if limit is None:
        context['context_note'] += '上下文容量遵循 Codex 原生模型窗口，不按工作台字符数截短。'
        return context
    def shorten(value, cap):
        if isinstance(value, str):
            return value if len(value) <= cap else value[:cap] + '…[已截短]'
        if isinstance(value, list):
            return [shorten(v, cap) for v in value]
        if isinstance(value, dict):
            return {k: shorten(v, cap) for k, v in value.items()}
        return value
    cap = 2000
    while len(json.dumps(context, ensure_ascii=False)) > limit and cap >= 64:
        context = shorten(context, cap)
        context['context_note'] = '部分文字已按单次上下文预算截短；原始记录未改变。'
        cap //= 2
    while len(json.dumps(context, ensure_ascii=False)) > limit:
        for field in ['messages', 'values', 'observations', 'versions']:
            minimum = 1 if context.get('task') == 'revise' and field in ['versions', 'observations'] else 0
            if len(context.get(field, [])) > minimum:
                context[field].pop()
                break
        else:
            raise ValidationError('上下文预算不足，请提高设置中的字符上限。')
    # Values are selected confirmed value versions only, not a second retrieval path.
    selected = {v['id'] for v in context.get('versions', [])}
    context['values'] = [v for v in context.get('values', []) if v['id'] in selected]
    return context


def build_context(task, obj, target_version=None):
    prefs = settings_record()
    context = {'task': task, 'versions': [], 'observations': [], 'values': []}
    if task == 'extract' and isinstance(obj, Source):
        context['source'] = {field: getattr(obj, field) for field in ['id', 'text', 'transcript', 'url', 'attribution', 'verification', 'kind', 'tags', 'is_demo']}
        context['source']['body_status'] = '已提供文字' if obj.text or obj.transcript else '正文未获取'
    elif task in ['chat', 'decision'] and isinstance(obj, Case):
        obj.refresh_from_db()
        context.update(situation=obj.situation, depth=obj.depth)
        context['versions'] = [_version(v) for v in obj.versions.all()[:prefs.retrieval_limit]]
        context['observations'] = [_observation(o) for o in obj.experiences.all()[:prefs.retrieval_limit]]
        # Replay only local, still-relevant messages. No SDK thread history is resumed.
        context['messages'] = []
        version_ids = sorted(v['id'] for v in context['versions'])
        observation_ids = sorted(o['id'] for o in context['observations'])
        for message in list(obj.entries.order_by('-pk')[:12])[::-1]:
            refs = message.references
            if message.role == 'assistant' and (refs.get('context_versions') != version_ids or refs.get('context_observations') != observation_ids or refs.get('context_depth') != obj.depth):
                continue
            context['messages'].append({'role': message.role, 'content': message.content, 'provenance': '对话自述，非正式选择' if message.role == 'user' else 'AI 建议，非用户确认事实'})
        context['values'] = [v for v in context['versions'] if v['kind'] == 'value' and v['status'] == 'adopted']
    elif task == 'revise' and isinstance(obj, Observation):
        if target_version is None:
            target_version = obj.versions.first()
        if target_version is None:
            raise ValidationError('请先为这条观察关联一条原则，再生成修订建议。')
        if not obj.versions.filter(principle_id=target_version.principle_id).exists():
            raise ValidationError('修订目标必须是这条观察关联的原则。')
        target_version = target_version.principle.current
        context['versions'] = [_version(target_version)]
        context['observations'] = [_observation(obj)]
        d = obj.decision
        basis = d.versions.filter(principle=target_version.principle).order_by('-number')[:prefs.retrieval_limit]
        context['decision'] = {'id': d.id, 'situation': d.situation, 'choice': d.choice, 'reason': d.reason, 'expectation': d.expectation, 'historical_basis': [{'number': v.number, 'statement': v.statement, 'status': v.status} for v in basis], 'basis_note': '这里只保留与修订目标同一原则的历史依据；版本引用列表中的版本才是本次可修订的当前版本。'}
    else:
        raise ValidationError('AI 任务与记录类型不匹配。')
    return _bounded(context, None if provider_config()['provider'] == 'codex' else prefs.context_chars)


def _fresh(task, obj, context, expected_revision):
    target = None
    if task == 'revise':
        ids = [v['id'] for v in context.get('versions', [])]
        target = PrincipleVersion.objects.filter(pk=ids[0]).first() if ids else None
        if target is None or target.principle.revision != expected_revision:
            raise Conflict('原则已经改变，请重新查看要发送的内容。')
    elif not type(obj).objects.filter(pk=obj.pk, revision=expected_revision).exists():
        raise Conflict('内容已经改变，请重新打开 AI 预览。')
    if not type(obj).objects.filter(pk=obj.pk).exists():
        raise Conflict('原始记录已经删除。')
    obj.refresh_from_db()
    if build_context(task, obj, target) != context:
        raise Conflict('参考内容或设置已经变化，请重新预览后再发送。')
    return target


_generation_lock = threading.Lock()


def _check_config(config):
    if config != provider_config():
        raise Conflict('AI 接入方式或设置已变化，请重新预览后发送。')


def _save_result(task, obj, context, expected_revision, result, config, operation_key, fingerprint):
    if not isinstance(result, dict) or type(result.get('is_demo')) is not bool:
        raise ProviderError('模型提供器返回了无效的模式标记。')
    try:
        payload = validate_output(task, result.get('payload'), context)
    except OutputError as error:
        raise ProviderError(f'模型输出未通过校验：{error}') from None
    def action():
        _check_config(config)
        target = _fresh(task, obj, context, expected_revision)
        values = {'source': obj} if task == 'extract' else {'observation': obj, 'principle': target.principle} if task == 'revise' else {'case': obj}
        suggestion = Suggestion.objects.create(task=task, payload=payload, context=context, base_revision=expected_revision, is_demo=result['is_demo'], provider=result.get('provider', config['provider']), model=result.get('model', config['model']), **values)
        if task == 'chat':
            claim(obj, expected_revision)
            lines = [payload['answer']]
            lines.extend(f"可选做法：{o['action']}\n代价与考虑：{o['tradeoff']}" for o in payload['options'])
            if payload['question']:
                lines.append('一个关键问题：' + payload['question'])
            references = dict(payload['citations'], context_versions=sorted(v['id'] for v in context['versions']), context_observations=sorted(o['id'] for o in context['observations']), context_depth=context.get('depth'))
            Message.objects.create(case=obj, role='assistant', content='\n\n'.join(lines), references=references, is_demo=result['is_demo'])
        return suggestion
    return run_once('ai', operation_key, fingerprint, Suggestion, action)


def generate_suggestion_stream(task, obj, *, context, operation_key, expected_revision, remote_allowed=False, config=None, streaming=True):
    config = config or provider_config()
    if task == 'extract' and not (obj.text.strip() or obj.transcript.strip()):
        raise ValidationError('正文未获取：请先补充原文或校对文字，再提炼原则。仅凭链接无法分析正文。')
    if config['provider'] != 'demo' and not remote_allowed:
        raise ValidationError('请先确认将预览的相关内容发送给模型服务。')
    _check_config(config)
    fingerprint = {'task': task, 'id': obj.pk, 'context': context, 'revision': expected_revision, 'config': config}
    # Single-user app: one active generation avoids duplicate account usage on double submit.
    if not _generation_lock.acquire(blocking=False):
        raise ProviderError('已有分析正在进行，请等待完成后重试。', code='busy')
    try:
        prior = Operation.objects.filter(key=operation_key).first()
        if prior:
            yield {'type': 'complete', 'suggestion': run_once('ai', operation_key, fingerprint, Suggestion, lambda: None)}
            return
        _fresh(task, obj, context, expected_revision)
        result = None
        if streaming:
            events = stream_generate(task, context, config=config)
            try:
                for event in events:
                    if event.get('type') == 'final':
                        result = event['result']
                    elif event.get('type') in {'status', 'delta'}:
                        yield event
            finally:
                events.close()
        else:
            result = generate(task, context, config=config)
        if result is None:
            raise ProviderError('模型没有返回完整结果。')
        suggestion = _save_result(task, obj, context, expected_revision, result, config, operation_key, fingerprint)
        yield {'type': 'complete', 'suggestion': suggestion}
    finally:
        _generation_lock.release()


def generate_suggestion(task, obj, **kwargs):
    for event in generate_suggestion_stream(task, obj, **kwargs, streaming=False):
        if event['type'] == 'complete':
            return event['suggestion']


def apply_suggestion(suggestion, data, version_ids, *, operation_key, confirmed):
    require_confirmation(confirmed)
    def action():
        suggestion.refresh_from_db()
        if suggestion.discarded:
            raise Conflict('此建议已放弃，原始记录仍然保留。')
        if suggestion.applied:
            model = Principle if suggestion.result_type == 'principle' else Decision
            result = model.objects.filter(pk=suggestion.result_id).first()
            if result is None:
                raise Conflict('建议对应的记录已经删除。')
            return result
        nested_key = str(uuid.uuid5(uuid.UUID(str(operation_key)), 'confirmed-result'))
        if suggestion.task in ['extract', 'revise']:
            if suggestion.task == 'extract' and not Source.objects.filter(pk=suggestion.source_id, revision=suggestion.base_revision).exists():
                raise Conflict('素材信息或校对文字已改变，请重新提炼后确认；原建议仍保留。')
            p = suggestion.principle if suggestion.task == 'revise' else None
            result = save_principle(data, operation_key=nested_key, confirmed=True, principle=p, expected_revision=suggestion.base_revision if p else None, source_ids=[suggestion.source_id] if suggestion.source_id else None, is_demo=suggestion.is_demo)
            result_type = 'principle'
        elif suggestion.task == 'decision':
            if suggestion.case.revision != suggestion.base_revision:
                raise Conflict('情境或参考内容已经变化，请重新生成选择草稿或手动记录。')
            result = save_decision(suggestion.case, data, version_ids, operation_key=nested_key, confirmed=True, is_demo=suggestion.is_demo)
            result_type = 'decision'
        else:
            raise ValidationError('聊天建议不能直接保存为原则或事实。')
        Suggestion.objects.filter(pk=suggestion.pk, applied=False, discarded=False).update(applied=True, result_type=result_type, result_id=result.pk)
        return result
    model = Principle if suggestion.task in ['extract', 'revise'] else Decision
    return run_once('apply', operation_key, {'suggestion': suggestion.pk, 'data': data, 'versions': version_ids}, model, action)


def discard_suggestion(suggestion):
    with transaction.atomic():
        if not Suggestion.objects.filter(pk=suggestion.pk, applied=False).update(discarded=True):
            raise Conflict('此建议已保存，不能再放弃；可以编辑对应记录。')
