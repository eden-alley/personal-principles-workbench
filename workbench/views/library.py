import uuid
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.db.models import Q, F, Max
from django.core import signing
from django.core.exceptions import ValidationError
from django.views.decorators.http import require_POST, require_http_methods
from django.utils import timezone
from workbench.models import Source, Principle, PrincipleVersion, Decision, Observation, Suggestion, Case
from .chat import chat_context
from workbench.forms import SourceForm, PrincipleForm, SourceLinkForm
from workbench.services.sources import save_source
from workbench.services.principles import save_principle, set_focus, FIELDS
from workbench.services.common import Conflict, require_confirmation
from .shared import attempt, form_page, success, listing


def home(request):
    conversations = Case.objects.all()[:4]
    focused = Principle.objects.filter(focused=True)
    due = Decision.objects.filter(review_date__lte=timezone.localdate(), reviewed=False)
    return render(request, 'workbench/home.html', {**chat_context(), 'conversations': conversations, 'focused': focused, 'due': due})


def source_list(request):
    q = request.GET.get('q', '').strip()
    items = Source.objects.all()
    if q:
        items = items.filter(Q(title__icontains=q) | Q(text__icontains=q) | Q(transcript__icontains=q) | Q(tags__icontains=q) | Q(attribution__icontains=q))
    return listing(request, 'source_list', items)


@require_http_methods(['GET', 'POST'])
def source_edit(request, pk=None):
    source = get_object_or_404(Source, pk=pk) if pk else None
    initial = {f: getattr(source, f) for f in ['title', 'text', 'url', 'kind', 'transcript', 'attribution', 'verification', 'tags', 'revision']} if source else {'kind': 'text', 'verification': 'unverified'}
    form = SourceForm(request.POST or None, request.FILES or None, initial=initial, source=source)
    status = 200
    if request.method == 'POST':
        result, status = attempt(form, lambda d: save_source(d, operation_key=d['operation_key'], image=d.get('image'), source=source, expected_revision=d.get('revision')))
        if result:
            return success(request, '素材已保存。可以先存着，也可以继续提炼或关联。', 'source_detail', result.pk)
    return form_page(request, form, '补充素材信息' if source else '存一个想法', reverse('source_detail', args=[pk]) if pk else reverse('source_list'), intro='先留下原始内容。收藏不代表认同，也不需要马上变成原则。', status=status, submit_label='保存素材')


def source_detail(request, pk):
    source = get_object_or_404(Source, pk=pk)
    linked = source.principle_versions.select_related('principle').all()
    suggestions = source.suggestion_set.filter(applied=False, discarded=False)
    return render(request, 'workbench/source_detail.html', locals())


@require_http_methods(['GET', 'POST'])
def source_link(request, pk):
    source = get_object_or_404(Source, pk=pk)
    revisions = {str(p.pk): p.revision for p in Principle.objects.all()}
    initial = {'revision': source.revision, 'revisions_token': signing.dumps(revisions, salt='source-link')}
    form = SourceLinkForm(request.POST or None, initial=initial)
    status = 200
    def action(d):
        require_confirmation(d['confirmed'])
        if Source.objects.get(pk=pk).revision != d['revision']:
            raise Conflict('素材已被另一个页面修改，请重新查看后关联。')
        try:
            expected = signing.loads(d['revisions_token'], salt='source-link', max_age=86400)[str(d['principle'].pk)]
        except (signing.BadSignature, KeyError):
            raise Conflict('关联页面已过期，请重新打开。')
        p = d['principle']
        version = p.current
        data = {f: getattr(version, f) for f in FIELDS}
        data['reason'] = '补充启发来源：' + str(source)
        return save_principle(data, principle=p, expected_revision=expected, source_ids=sorted(set(version.sources.values_list('pk', flat=True)) | {source.pk}), operation_key=d['operation_key'], confirmed=True)
    if request.method == 'POST':
        result, status = attempt(form, action)
        if result:
            return success(request, '已关联启发来源，并保留一个新版本。', 'principle_detail', result.pk)
    return form_page(request, form, '关联已有原则', reverse('source_detail', args=[pk]), intro='关联会创建一个新版本记录来源；旧版本保持原样。', status=status)


def principle_list(request):
    q, status, kind, tag = (request.GET.get(k, '').strip() for k in ['q', 'status', 'kind', 'tag'])
    current = PrincipleVersion.objects.filter(number=F('principle__revision'))
    if q:
        current = current.filter(Q(title__icontains=q) | Q(statement__icontains=q) | Q(tags__icontains=q) | Q(when_to_use__icontains=q))
    if tag:
        current = current.filter(tags__icontains=tag)
    if status:
        current = current.filter(status=status)
    if kind:
        current = current.filter(kind=kind)
    return listing(request, 'principle_list', Principle.objects.filter(pk__in=current.values('principle_id')), status=status, kind=kind, tag=tag)


@require_http_methods(['GET', 'POST'])
def principle_edit(request, pk=None):
    principle = get_object_or_404(Principle, pk=pk) if pk else None
    original = principle.current if principle else None
    initial = {f: getattr(original, f) for f in FIELDS} if original else {'status': 'candidate', 'kind': 'strategy'}
    if principle:
        initial.update(revision=principle.revision, source_ids=list(original.sources.values_list('pk', flat=True)))
    form = PrincipleForm(request.POST or None, initial=initial)
    status = 200
    if request.method == 'POST':
        result, status = attempt(form, lambda d: save_principle({f: d[f] for f in FIELDS}, principle=principle, expected_revision=d['revision'], source_ids=list(d['source_ids'].values_list('pk', flat=True)), operation_key=d['operation_key'], confirmed=d['confirmed']))
        if result:
            return success(request, '原则版本已保存，历史内容保持不变。', 'principle_detail', result.pk)
    return form_page(request, form, '修订原则' if principle else '手动创建原则', reverse('principle_detail', args=[pk]) if pk else reverse('principle_list'), intro='只有你确认后，内容和状态才会保存。可以先作为候选。', original=original, status=status)


def principle_detail(request, pk):
    principle = get_object_or_404(Principle, pk=pk)
    version = principle.current
    versions = principle.versions.all()
    decisions = Decision.objects.filter(versions__principle=principle).distinct()
    observations = Observation.objects.filter(versions__principle=principle).distinct()
    focus_key = uuid.uuid4()
    return render(request, 'workbench/principle_detail.html', locals())


def version_detail(request, pk):
    version = get_object_or_404(PrincipleVersion, pk=pk)
    principle = version.principle
    return render(request, 'workbench/version_detail.html', locals())


@require_POST
def principle_focus(request, pk):
    from workbench.forms import OperationForm
    form = OperationForm(request.POST)
    p = get_object_or_404(Principle, pk=pk)
    def action(d):
        if request.POST.get('focused') not in ['true', 'false']:
            raise ValidationError('关注状态无效。')
        return set_focus(p, request.POST['focused'] == 'true', expected_revision=d['revision'], operation_key=d['operation_key'])
    result, status = attempt(form, action)
    if result:
        return success(request, '关注状态已更新。', 'principle_detail', pk)
    return form_page(request, form, '关注状态未保存', reverse('principle_detail', args=[pk]), status=status)
