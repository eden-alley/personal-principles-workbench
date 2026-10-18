import json
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import OperationalError, IntegrityError
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_POST
from workbench.ai.base import ProviderError
from workbench.forms import ChatForm
from workbench.models import Case
from workbench.services.chat import send_chat, needs_reply
from workbench.services.ai_settings import provider_config


def chat_context(case=None, form=None, send_url=None):
    context = {'case': case, 'chat_form': form or ChatForm(initial={'revision': case.revision if case else None, 'depth': case.depth if case else 'daily', 'config_token': signing.dumps(provider_config(), salt='direct-chat')}),
               'send_url': send_url or (reverse('chat_send', args=[case.pk]) if case else reverse('chat_start')),
               'needs_reply': needs_reply(case) if case else False}
    if case:
        context.update(entries=case.entries.all(), references=case.versions.all(), experiences=case.experiences.all(),
                       suggestions=case.suggestion_set.filter(applied=False, discarded=False).exclude(task='chat'))
    return context


@require_POST
def chat_send(request, pk=None):
    case = get_object_or_404(Case, pk=pk) if pk else None
    form = ChatForm(request.POST)

    def events():
        nonlocal case
        try:
            if not form.is_valid():
                raise ValidationError([str(message) for errors in form.errors.values() for message in errors])
            d = form.cleaned_data
            if d['action'] == 'send':
                try:
                    config = signing.loads(d['config_token'], salt='direct-chat', max_age=86400)
                except signing.BadSignature:
                    raise ValidationError('页面已过期，请先复制输入，刷新后再发送。')
                if config != provider_config():
                    raise ValidationError('模型设置已变化，请先复制输入，刷新后再发送。')
            if case and d['revision'] is None:
                raise ValidationError('对话信息缺失，请刷新后继续。')
            stream = send_chat(content=d['content'], operation_key=d['operation_key'], action=d['action'],
                               case=case, revision=d['revision'], depth=d['depth'])
            try:
                for event in stream:
                    if event['type'] in {'saved', 'complete'}:
                        case = event['case']
                        result = {'type': event['type'], 'url': reverse('case_detail', args=[case.pk]),
                                  'send_url': reverse('chat_send', args=[case.pk]), 'revision': case.revision}
                        if event['type'] == 'complete':
                            result['html'] = render_to_string('workbench/_chat_content.html', chat_context(case), request=request)
                        yield result
                    else:
                        yield event
            finally:
                stream.close()
        except (ValidationError, ProviderError) as error:
            yield {'type': 'error', 'code': getattr(error, 'code', None) or 'request_failed',
                   'message': '；'.join(error.messages) if isinstance(error, ValidationError) else str(error)}
        except (OperationalError, IntegrityError):
            yield {'type': 'error', 'code': 'conflict', 'message': '对话正在更新，请稍后重试。'}
        except Exception:
            yield {'type': 'error', 'code': 'request_failed', 'message': '回复未完成，请重试。已保存的原话仍保留。'}

    if 'application/x-ndjson' in request.headers.get('Accept', ''):
        def lines():
            stream = events()
            try:
                for event in stream:
                    yield json.dumps(event, ensure_ascii=False) + '\n'
            finally:
                stream.close()
        response = StreamingHttpResponse(lines(), content_type='application/x-ndjson; charset=utf-8')
        response['Cache-Control'] = 'no-store'
        response['X-Accel-Buffering'] = 'no'
        return response
    for event in events():
        if event['type'] == 'complete':
            return redirect(event['url'])
        if event['type'] == 'error':
            form.add_error(None, event['message'])
    return render(request, 'workbench/case_detail.html', chat_context(case, form, request.path), status=400)
