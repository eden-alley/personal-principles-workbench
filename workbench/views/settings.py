import os
from django.http import HttpResponse, Http404, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.core.exceptions import ValidationError, ObjectDoesNotExist
from django.views.decorators.http import require_http_methods, require_POST
from workbench.models import UserSettings
from workbench.forms import SettingsForm, DeleteForm
from workbench.services.retrieval import settings_record
from workbench.services.common import claim, run_once, require_confirmation
from workbench.services.data_admin import export_archive, delete_impact, delete_record
from workbench.services.ai_settings import provider_config, provider_status
from workbench.ai import codex_auth
from .shared import attempt, form_page, success


@require_http_methods(['GET', 'POST'])
def settings(request):
    prefs = settings_record()
    form = SettingsForm(request.POST or None, initial={f: getattr(prefs, f) for f in ['revision', 'retrieval_limit', 'context_chars', 'ai_provider', 'codex_model', 'codex_reasoning_effort']})
    ai_status = provider_status()
    selected_provider = provider_config()
    provider_override = os.environ.get('AI_PROVIDER', '').strip()
    model_override = os.environ.get('CODEX_MODEL', '').strip()
    effort_override = os.environ.get('CODEX_REASONING_EFFORT', '').strip()
    status = 200
    def action(d):
        require_confirmation(d['confirmed'])
        values = {f: d[f] for f in ['retrieval_limit', 'context_chars', 'ai_provider', 'codex_model', 'codex_reasoning_effort']}
        def save():
            claim(prefs, d['revision'], **values)
            return prefs
        return run_once('settings', d['operation_key'], {**values, 'revision': d['revision']}, UserSettings, save)
    if request.method == 'POST':
        result, status = attempt(form, action)
        if result:
            return success(request, '单次调用设置已保存。', 'settings')
    return render(request, 'workbench/settings.html', locals(), status=status)


def export_data(request):
    try:
        content = export_archive()
    except (FileNotFoundError, ValidationError):
        return HttpResponse('导出未完成：有附件缺失或数据不可读。请检查 data/attachments 后重试，未生成不完整备份。', status=409, content_type='text/plain; charset=utf-8')
    response = HttpResponse(content, content_type='application/zip')
    response['Content-Disposition'] = 'attachment; filename="principles-workbench-backup.zip"'
    return response


@require_http_methods(['GET', 'POST'])
def delete_preview(request, kind, pk):
    try:
        details = delete_impact(kind, pk)
    except (ObjectDoesNotExist, ValueError, ValidationError):
        raise Http404
    impact, object_title = details['lines'], details['title']
    form = DeleteForm(request.POST or None, initial={'signature': details['signature']})
    heading = '确认永久删除'
    status = 200
    def action(d):
        delete_record(kind, pk, signature=d['signature'], confirmed=d['confirmed'])
        return True
    if request.method == 'POST':
        result, status = attempt(form, action)
        if result:
            return success(request, '已按预览范围删除。', 'home')
    return render(request, 'workbench/delete_preview.html', locals(), status=status)


def demo(request):
    return render(request, 'workbench/demo.html')


@require_http_methods(['GET'])
def ai_status(request):
    response = JsonResponse(provider_status(refresh=request.GET.get('refresh') == '1'))
    response['Cache-Control'] = 'no-store'
    return response


@require_POST
def codex_login(request):
    response = JsonResponse(codex_auth.start_login())
    response['Cache-Control'] = 'no-store'
    return response


@require_http_methods(['GET'])
def codex_login_status(request):
    response = JsonResponse(codex_auth.login_status())
    response['Cache-Control'] = 'no-store'
    return response


@require_POST
def codex_login_cancel(request):
    return JsonResponse(codex_auth.cancel_login())
