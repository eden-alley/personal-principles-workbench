from django.core.exceptions import ValidationError
from workbench.models import Principle, PrincipleVersion, Source, KINDS, STATUSES
from .common import run_once, claim, require_confirmation, references, clean_text

FIELDS = ['title', 'statement', 'kind', 'status', 'when_to_use', 'conditions', 'boundaries', 'questions', 'tags', 'reason']


def save_principle(data, *, operation_key, confirmed, principle=None, expected_revision=None, source_ids=None, is_demo=False):
    require_confirmation(confirmed)
    values = {f: clean_text(data, f, required=f in ['title', 'statement', 'kind', 'status'], limit=200 if f == 'title' else 500 if f == 'tags' else 20000) for f in FIELDS}
    if values['kind'] not in dict(KINDS) or values['status'] not in dict(STATUSES):
        raise ValidationError('原则类型或状态无效。')
    def action():
        p = principle or Principle.objects.create()
        previous = p.current
        claim(p, expected_revision if principle else 0, status=values['status'], kind=values['kind'])
        sources = references(Source, source_ids) if source_ids is not None else list(previous.sources.all()) if previous else []
        v = PrincipleVersion.objects.create(principle=p, number=p.revision, is_demo=is_demo or bool(previous and previous.is_demo), **values)
        v.sources.set(sources)
        return p
    return run_once('principle', operation_key, {'data': values, 'id': principle.pk if principle else None, 'expected': expected_revision, 'sources': source_ids, 'demo': is_demo}, Principle, action)


def set_focus(principle, focused, *, expected_revision, operation_key):
    # Focus has its own compare-and-swap on the current content revision, without creating a content version.
    def action():
        if not Principle.objects.filter(pk=principle.pk, revision=expected_revision, focused=not focused).update(focused=focused):
            from .common import Conflict
            raise Conflict('原则或关注状态已变化，请刷新后重试。')
        principle.refresh_from_db()
        return principle
    return run_once('focus', operation_key, {'id': principle.pk, 'focused': focused, 'expected': expected_revision}, Principle, action)
