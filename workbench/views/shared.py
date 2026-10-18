from django.contrib import messages
from django.shortcuts import render, redirect
from django.core.exceptions import ValidationError
from django.db import OperationalError, IntegrityError
from django.core.paginator import Paginator
from workbench.ai.provider import ProviderError
from workbench.services.common import Conflict


def attempt(form, action):
    if not form.is_valid():
        return None, 400
    try:
        return action(form.cleaned_data), 200
    except (ValidationError, ProviderError) as error:
        form.add_error(None, '；'.join(error.messages) if isinstance(error, ValidationError) else str(error))
        return None, 409 if isinstance(error, Conflict) else 400
    except (OperationalError, IntegrityError):
        form.add_error(None, '数据正在被另一个操作更新。输入仍在，请稍后重试或重新打开最新记录。')
        return None, 409


def form_page(request, form, heading, cancel_url, *, intro='', original=None, status=200, submit_label='确认保存'):
    return render(request, 'workbench/form.html', locals(), status=status)


def success(request, label, route, pk=None):
    messages.success(request, label)
    return redirect(route, pk=pk) if pk is not None else redirect(route)


def listing(request, template, queryset, **context):
    page_obj = Paginator(queryset, 30).get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)
    context.update(items=page_obj.object_list, page_obj=page_obj, query_params=params.urlencode(), q=request.GET.get('q', ''))
    return render(request, f'workbench/{template}.html', context)
