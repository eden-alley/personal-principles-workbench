from django.core.exceptions import ValidationError
from workbench.models import Case, Decision, Observation, PrincipleVersion, Message
from .common import run_once, claim, require_confirmation, references, clean_text


def create_case(situation, *, operation_key, depth='daily'):
    situation = clean_text({'situation': situation}, 'situation', required=True)
    if depth not in ['daily', 'deep']:
        raise ValidationError('对话深度无效。')
    def action():
        c = Case.objects.create(situation=situation, depth=depth)
        from .retrieval import search_related
        versions, observations = search_related(situation)
        c.versions.set(versions)
        c.experiences.set(observations)
        return c
    return run_once('case', operation_key, {'situation': situation, 'depth': depth}, Case, action)


def update_references(case, version_ids, observation_ids, *, depth, expected_revision, operation_key):
    if depth not in ['daily', 'deep']:
        raise ValidationError('对话深度无效。')
    def action():
        versions = references(PrincipleVersion, version_ids)
        observations = references(Observation, observation_ids)
        claim(case, expected_revision, depth=depth)
        case.versions.set(versions)
        case.experiences.set(observations)
        return case
    return run_once('references', operation_key, {'id': case.pk, 'versions': version_ids, 'observations': observation_ids, 'depth': depth, 'expected': expected_revision}, Case, action)


def add_message(case, content, *, expected_revision, operation_key):
    content = clean_text({'content': content}, 'content', required=True)
    def action():
        claim(case, expected_revision)
        return Message.objects.create(case=case, role='user', content=content)
    return run_once('message', operation_key, {'id': case.pk, 'content': content, 'expected': expected_revision}, Message, action)


def save_decision(case, data, version_ids, *, operation_key, confirmed, is_demo=False):
    require_confirmation(confirmed)
    values = {f: clean_text(data, f, required=f in ['situation', 'choice']) for f in ['situation', 'choice', 'reason', 'expectation']}
    values['review_date'] = data.get('review_date') or None
    def action():
        versions = references(PrincipleVersion, version_ids)
        obj = Decision(case=case, is_demo=is_demo, **values)
        obj.full_clean()
        obj.save()
        obj.versions.set(versions)
        return obj
    return run_once('decision', operation_key, {'case': case.pk if case else None, 'data': values, 'versions': version_ids, 'demo': is_demo}, Decision, action)


def save_observation(decision, data, version_ids, *, operation_key, confirmed):
    require_confirmation(confirmed)
    values = {f: clean_text(data, f, required=f == 'facts') for f in ['facts', 'interpretation', 'uncertainty', 'difference']}
    values['effect'] = data.get('effect', 'uncertain')
    if values['effect'] not in ['supports', 'challenges', 'uncertain']:
        raise ValidationError('经历与原则的关系无效。')
    def action():
        versions = references(PrincipleVersion, version_ids)
        obj = Observation.objects.create(decision=decision, is_demo=decision.is_demo, **values)
        obj.versions.set(versions)
        Decision.objects.filter(pk=decision.pk).update(reviewed=True)
        return obj
    return run_once('observation', operation_key, {'decision': decision.pk, 'data': values, 'versions': version_ids}, Observation, action)
