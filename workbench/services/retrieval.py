import re
from django.db.models import F
from workbench.models import PrincipleVersion, Observation, UserSettings


def settings_record():
    return UserSettings.objects.get_or_create(pk=1)[0]


def tokens(text):
    result = set(re.findall(r'[a-zA-Z0-9]{2,}', text.lower()))
    for chunk in re.findall(r'[\u4e00-\u9fff]+', text):
        if len(chunk) == 1:
            continue
        result.update(chunk[i:i+2] for i in range(len(chunk)-1))
    return result - {'一个', '这个', '什么', '自己', '可以', '时候', '因为', '所以', '我的', '我们', '是否'}


def search_related(query):
    limit = settings_record().retrieval_limit
    words = tokens(query)
    def ranked(items, text):
        matches = [(len(words & tokens(text(obj))), obj) for obj in items]
        return [obj for score, obj in sorted(matches, key=lambda pair: (-pair[0], -pair[1].pk)) if score > 0][:limit]
    versions = PrincipleVersion.objects.filter(number=F('principle__revision'), status__in=['adopted', 'trial'])
    return ranked(versions, lambda v: f'{v.title} {v.statement} {v.when_to_use} {v.tags}'), ranked(Observation.objects.filter(is_demo=False), lambda o: f'{o.facts} {o.interpretation}')
