from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.db.models import Q
from django.views.decorators.http import require_http_methods, require_POST
from workbench.models import Case, Decision, Observation, Suggestion, PrincipleVersion
from workbench.forms import CaseForm, ReferenceForm, MessageForm, DecisionForm, ObservationForm
from workbench.services.records import create_case, update_references, add_message, save_decision, save_observation
from .shared import attempt, form_page, success, listing
from .chat import chat_context


def case_list(request):
    q = request.GET.get('q', '').strip()
    return listing(request, 'case_list', Case.objects.filter(situation__icontains=q))


@require_http_methods(['GET', 'POST'])
def case_create(request):
    if request.method == 'GET':
        return redirect('home')
    form = CaseForm(request.POST or None, initial={'depth': 'daily'})
    status = 200
    if request.method == 'POST':
        result, status = attempt(form, lambda d: create_case(d['situation'], depth=d['depth'], operation_key=d['operation_key']))
        if result:
            return success(request, '情境已保存。先查看检索到的参考内容，也可以直接记录选择。', 'case_detail', result.pk)
    return form_page(request, form, '我遇到一件事', reverse('case_list'), status=status, submit_label='保存情境')


def case_detail(request, pk):
    case = get_object_or_404(Case, pk=pk)
    return render(request, 'workbench/case_detail.html', chat_context(case))


@require_http_methods(['GET', 'POST'])
def case_references(request, pk):
    case = get_object_or_404(Case, pk=pk)
    form = ReferenceForm(request.POST or None, initial={'revision': case.revision, 'versions': list(case.versions.values_list('pk', flat=True)), 'experiences': list(case.experiences.values_list('pk', flat=True)), 'depth': case.depth})
    status = 200
    if request.method == 'POST':
        result, status = attempt(form, lambda d: update_references(case, list(d['versions'].values_list('pk', flat=True)), list(d['experiences'].values_list('pk', flat=True)), depth=d['depth'], expected_revision=d['revision'], operation_key=d['operation_key']))
        if result:
            return success(request, '参考内容已更新；之后的模型调用会使用这次选择。', 'case_detail', pk)
    return form_page(request, form, '本次参考内容', reverse('case_detail', args=[pk]), intro='检索依据是文字与标签的关键词重合。可移除误匹配，或手动补充具体版本。', status=status)


@require_POST
def case_message(request, pk):
    case = get_object_or_404(Case, pk=pk)
    form = MessageForm(request.POST)
    result, status = attempt(form, lambda d: add_message(case, d['content'], expected_revision=d['revision'], operation_key=d['operation_key']))
    if result:
        return success(request, '补充已保存，尚未成为正式选择。', 'case_detail', pk)
    return form_page(request, form, '补充情境', reverse('case_detail', args=[pk]), status=status)


def decision_list(request):
    q = request.GET.get('q', '').strip()
    items = Decision.objects.filter(Q(situation__icontains=q) | Q(choice__icontains=q) | Q(reason__icontains=q))
    return listing(request, 'decision_list', items)


@require_http_methods(['GET', 'POST'])
def decision_create(request, pk):
    case = get_object_or_404(Case, pk=pk)
    form = DecisionForm(request.POST or None, initial={'situation': case.situation, 'versions': list(case.versions.values_list('pk', flat=True))})
    status = 200
    if request.method == 'POST':
        result, status = attempt(form, lambda d: save_decision(case, {f: d[f] for f in ['situation', 'choice', 'reason', 'expectation', 'review_date']}, list(d['versions'].values_list('pk', flat=True)), operation_key=d['operation_key'], confirmed=d['confirmed']))
        if result:
            return success(request, '这次选择已保存，并锁定了参考的历史版本。', 'decision_detail', result.pk)
    return form_page(request, form, '记录这次选择', reverse('case_detail', args=[pk]), intro='这一步由你确认真实选择；无需依赖 AI，也可以不关联任何原则。', status=status)


def decision_detail(request, pk):
    decision = get_object_or_404(Decision, pk=pk)
    observations = decision.observations.all()
    return render(request, 'workbench/decision_detail.html', locals())


@require_http_methods(['GET', 'POST'])
def observation_create(request, pk):
    decision = get_object_or_404(Decision, pk=pk)
    form = ObservationForm(request.POST or None, initial={'versions': list(decision.versions.values_list('pk', flat=True)), 'effect': 'uncertain'})
    status = 200
    if request.method == 'POST':
        result, status = attempt(form, lambda d: save_observation(decision, {f: d[f] for f in ['facts', 'interpretation', 'uncertainty', 'difference', 'effect']}, list(d['versions'].values_list('pk', flat=True)), operation_key=d['operation_key'], confirmed=d['confirmed']))
        if result:
            return success(request, '经历已保存；原选择的理由和期待保持原样。', 'observation_detail', result.pk)
    response = form_page(request, form, '补充后续经历', reverse('decision_detail', args=[pk]), intro=f'当时情境：{decision.situation}\n选择：{decision.choice}\n主要考虑：{decision.reason}\n希望得到：{decision.expectation}\n参考版本：' + '；'.join(str(v) for v in decision.versions.all()), status=status)
    return response


def observation_detail(request, pk):
    observation = get_object_or_404(Observation, pk=pk)
    decision = observation.decision
    versions = observation.versions.all()
    suggestions = observation.suggestion_set.filter(applied=False, discarded=False)
    return render(request, 'workbench/observation_detail.html', locals())
