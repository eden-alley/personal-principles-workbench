from workbench.services.ai_settings import provider_status
from django.urls import reverse


def app_context(request):
    links = [('home', '新对话'), ('case_list', '历史对话'), ('source_list', '素材'), ('principle_list', '原则'), ('decision_list', '选择记录')]
    nav = [{'url': reverse(name), 'label': label, 'active': request.path == '/' if name == 'home' else request.path.startswith(reverse(name))} for name, label in links]
    status = provider_status()
    label = {'codex': 'Codex', 'api': '普通 API', 'demo': '演示'}.get(status['provider'], 'AI')
    return {'app_mode': label + ' · ' + status['label'], 'ai_status': status, 'nav': nav}
