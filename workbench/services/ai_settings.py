"""Non-secret backend selection; business SQLite remains authoritative."""
import os
from workbench.ai.provider import get_provider
from .retrieval import settings_record


def provider_config():
    prefs = settings_record()
    name = os.environ.get('AI_PROVIDER', '').strip() or prefs.ai_provider
    model = (os.environ.get('CODEX_MODEL', '').strip() or prefs.codex_model) if name == 'codex' else os.environ.get('AI_MODEL', 'gpt-4o-mini') if name == 'api' else ''
    result = {'provider': name, 'model': model, 'settings_revision': prefs.revision}
    if name == 'codex':
        result['reasoning_effort'] = os.environ.get('CODEX_REASONING_EFFORT', '').strip() or prefs.codex_reasoning_effort
        result['context_policy'] = 'codex_native'
        result['timeout'] = os.environ.get('CODEX_TIMEOUT', '120')
    return result


def provider_status(refresh=False):
    config = provider_config()
    result = get_provider(config['provider']).status(refresh=refresh)
    return {**result, 'provider': config['provider'], 'model': config['model'], 'reasoning_effort': config.get('reasoning_effort', ''), 'context_policy': config.get('context_policy', 'character_limit')}
