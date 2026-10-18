import json
from django.http import StreamingHttpResponse
from django.db import OperationalError, IntegrityError
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.core import signing
from django.core.exceptions import ValidationError
from django.views.decorators.http import require_http_methods, require_POST
from workbench.models import Source, Case, Observation, PrincipleVersion, Suggestion
from workbench.forms import AIPreviewForm, PrincipleForm, DecisionForm
from workbench.services.ai import build_context, generate_suggestion, generate_suggestion_stream, apply_suggestion, discard_suggestion
from workbench.services.principles import FIELDS
from workbench.services.ai_settings import provider_config as get_provider_config
from workbench.ai.provider import ProviderError
from .shared import attempt, form_page, success

HEADINGS = {'extract': '从素材提炼候选原则', 'chat': '整理当前情境', 'decision': '生成选择记录草稿', 'revise': '根据经历提出修订建议'}


@require_http_methods(['GET', 'POST'])
def ai_preview(request, task, pk):
    from django.http import Http404
    model = {'extract': Source, 'chat': Case, 'decision': Case, 'revise': Observation}.get(task)
    if not model:
        raise Http404
    obj = get_object_or_404(model, pk=pk)
    provider_config = get_provider_config()
    is_remote = provider_config['provider'] != 'demo'
    heading = HEADINGS[task]
    context = {}
    error = None
    expected = getattr(obj, 'revision', 0)
    if request.method == 'GET':
        try:
            target = None
            if task == 'revise':
                try:
                    target_id = int(request.GET['target']) if request.GET.get('target') else None
                except ValueError:
                    raise ValidationError('修订目标编号无效。')
                target = get_object_or_404(PrincipleVersion, pk=target_id) if target_id else obj.versions.first()
                if target:
                    expected = target.principle.revision
            context = build_context(task, obj, target)
            if task == 'extract' and not (obj.text.strip() or obj.transcript.strip()):
                error = '正文未获取：先补充原文或手工校对文字，再提炼原则。仅凭 URL 不能分析文章。'
        except ValidationError as e:
            error = '；'.join(e.messages)
        token = signing.dumps({'task': task, 'id': pk, 'revision': expected, 'context': context, 'config': provider_config}, salt='ai-preview', compress=True)
        form = AIPreviewForm(initial={'context_token': token, 'revision': expected}, is_remote=is_remote)
        if error:
            # Force a harmless empty validation pass so non-field errors can render on GET.
            form.is_bound = True
            form.full_clean()
            form._errors.clear()
            form.is_bound = False
            form.add_error(None, error)
        status = 200
    else:
        form = AIPreviewForm(request.POST, is_remote=is_remote)
        def arguments(d):
            nonlocal context
            try:
                signed = signing.loads(d['context_token'], salt='ai-preview', max_age=3600)
            except signing.BadSignature:
                raise ValidationError('预览已过期或无效，请重新打开预览。')
            if signed.get('task') != task or signed.get('id') != pk or signed.get('revision') != d['revision']:
                raise ValidationError('发送内容与此页面不一致，请重新预览。')
            if signed.get('config') != provider_config:
                raise ValidationError('AI 接入方式或设置已变化，请重新预览后发送。')
            context = signed['context']
            return dict(context=context, operation_key=d['operation_key'], expected_revision=d['revision'], remote_allowed=d['confirmed_send'], config=provider_config)
        if 'application/x-ndjson' in request.headers.get('Accept', ''):
            def stream():
                def line(event):
                    return json.dumps(event, ensure_ascii=False) + '\n'
                try:
                    if not form.is_valid():
                        error = '；'.join(str(message) for errors in form.errors.values() for message in errors)
                        yield line({'type': 'error', 'code': 'invalid_request', 'message': error})
                        return
                    args = arguments(form.cleaned_data)
                    yield line({'type': 'status', 'message': '正在分析，请保留此页面。'})
                    events = generate_suggestion_stream(task, obj, **args)
                    try:
                        for event in events:
                            if event['type'] == 'complete':
                                event = {'type': 'complete', 'url': reverse('suggestion_detail', args=[event['suggestion'].pk])}
                            yield line(event)
                    finally:
                        events.close()
                except (ValidationError, ProviderError) as error:
                    yield line({'type': 'error', 'code': getattr(error, 'code', None) or 'request_failed', 'message': '；'.join(error.messages) if isinstance(error, ValidationError) else str(error)})
                except (OperationalError, IntegrityError):
                    yield line({'type': 'error', 'code': 'conflict', 'message': '资料正在更新，请重新打开预览后重试。'})
                except Exception:
                    yield line({'type': 'error', 'code': 'request_failed', 'message': '分析未完成，原始资料仍保留。请重试或查看接入设置。'})
            response = StreamingHttpResponse(stream(), content_type='application/x-ndjson; charset=utf-8')
            response['Cache-Control'] = 'no-store'
            response['X-Accel-Buffering'] = 'no'
            return response
        result, status = attempt(form, lambda d: generate_suggestion(task, obj, **arguments(d)))
        if result:
            return success(request, '建议已生成。请查看、编辑后决定是否保存。', 'suggestion_detail', result.pk)
    context_json = json.dumps(context, ensure_ascii=False, indent=2)
    return render(request, 'workbench/ai_preview.html', locals(), status=status)


def _suggestion_form(suggestion, data=None):
    payload = suggestion.payload
    if suggestion.task in ['extract', 'revise']:
        initial = dict(payload['principle'])
        for field in ['conditions', 'boundaries', 'questions', 'tags']:
            initial[field] = ('，' if field == 'tags' else '\n').join(initial.get(field, []))
        initial.update(reason=payload['reason'], status=suggestion.principle.status if suggestion.principle_id else 'candidate', revision=suggestion.base_revision)
        form = PrincipleForm(data, initial=initial)
        # The original source is bound by the server, not editable AI metadata.
        form.fields.pop('source_ids')
        return form
    if suggestion.task == 'decision':
        initial = {f: payload[f] for f in ['situation', 'choice', 'reason', 'expectation']}
        initial['versions'] = payload['citations']['versions']
        return DecisionForm(data, initial=initial)
    return None


def suggestion_detail(request, pk):
    suggestion = get_object_or_404(Suggestion, pk=pk)
    form = None if suggestion.applied or suggestion.discarded else _suggestion_form(suggestion)
    original = suggestion.principle.current if suggestion.principle_id else None
    return render(request, 'workbench/suggestion_detail.html', {'suggestion': suggestion, 'form': form, 'heading': HEADINGS[suggestion.task], 'original': original})


@require_POST
def suggestion_apply(request, pk):
    suggestion = get_object_or_404(Suggestion, pk=pk)
    form = _suggestion_form(suggestion, request.POST)
    if form is None:
        from django.http import HttpResponseBadRequest
        return HttpResponseBadRequest('聊天不能直接变成正式原则或选择，请使用记录选择入口。')
    def action(d):
        if suggestion.task in ['extract', 'revise']:
            data = {f: d[f] for f in FIELDS}
            ids = []
        else:
            data = {f: d[f] for f in ['situation', 'choice', 'reason', 'expectation', 'review_date']}
            ids = list(d['versions'].values_list('pk', flat=True))
        return apply_suggestion(suggestion, data, ids, operation_key=d['operation_key'], confirmed=d['confirmed'])
    result, status = attempt(form, action)
    if result:
        return success(request, '经你确认的内容已保存。', 'principle_detail' if suggestion.task in ['extract', 'revise'] else 'decision_detail', result.pk)
    return render(request, 'workbench/suggestion_detail.html', {'suggestion': suggestion, 'form': form, 'heading': HEADINGS[suggestion.task], 'original': suggestion.principle.current if suggestion.principle_id else None}, status=status)


@require_POST
def suggestion_discard(request, pk):
    from django.contrib import messages
    suggestion = get_object_or_404(Suggestion, pk=pk)
    try:
        discard_suggestion(suggestion)
        messages.success(request, '已放弃这份建议；原始素材、经历和正式记录仍保留。')
    except ValidationError as error:
        messages.error(request, '；'.join(error.messages))
    return redirect('suggestion_detail', pk=pk)
