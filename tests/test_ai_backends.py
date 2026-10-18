import json
from unittest.mock import patch
from django.test import TestCase, override_settings, Client
from django.urls import reverse
from workbench.models import Source, Suggestion, Principle, Message, Operation
from workbench.ai.base import ProviderError
from workbench.ai.provider import generate
from workbench.services.ai import build_context, generate_suggestion_stream
from workbench.services.ai_settings import provider_config
from workbench.services.records import create_case, update_references
from workbench.services.principles import save_principle
from tests.test_domain import key, principle_data


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class BackendIntegrationTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(text='【测试素材】保留阅读安排，并考虑家庭急需。')
        self.url = reverse('ai_preview', args=['extract', self.source.pk])

    def preview_data(self):
        f = self.client.get(self.url).context['form']
        return {**f.initial, 'confirmed_send': 'on'}

    def events(self, data):
        response = self.client.post(self.url, data, HTTP_ACCEPT='application/x-ndjson')
        return [json.loads(line) for line in b''.join(response.streaming_content).decode().splitlines()]

    def test_default_is_codex_and_absent_key_does_not_fall_back(self):
        with patch.dict('os.environ', {'AI_PROVIDER': '', 'AI_API_KEY': ''}):
            self.assertEqual(provider_config()['provider'], 'codex')
        with patch('workbench.ai.codex_provider.CodexProvider.stream', side_effect=ProviderError('未登录', 'not_logged_in')):
            with self.assertRaises(ProviderError) as error:
                generate('extract', build_context('extract', self.source), config={'provider': 'codex'})
        self.assertEqual(error.exception.code, 'not_logged_in')

    def test_codex_context_uses_native_capacity_without_character_truncation(self):
        self.source.text = '素材正文' * 6000
        self.source.save()
        with patch.dict('os.environ', {'AI_PROVIDER': 'codex'}):
            context = build_context('extract', self.source)
            self.assertEqual(context['source']['text'], self.source.text)
            self.assertEqual(provider_config()['model'], 'gpt-6.1-sol')
            self.assertEqual(provider_config()['reasoning_effort'], 'high')
        with patch.dict('os.environ', {'AI_PROVIDER': 'api'}):
            limited = build_context('extract', self.source)
            self.assertLess(len(limited['source']['text']), len(self.source.text))

    def test_stream_failure_keeps_source_and_no_partial_rows(self):
        def fail(*args, **kwargs):
            yield {'type': 'delta', 'text': '{"principle":'}
            raise ProviderError('额度不足', 'quota_exhausted')
        data = self.preview_data()
        with patch('workbench.services.ai.stream_generate', side_effect=fail):
            events = self.events(data)
        self.assertEqual(events[-1]['code'], 'quota_exhausted')
        self.assertFalse(Suggestion.objects.exists())
        self.assertFalse(Principle.objects.exists())
        self.source.refresh_from_db()
        self.assertIn('测试素材', self.source.text)

    def test_stream_success_is_suggestion_only_and_replay_does_not_call_again(self):
        data = self.preview_data()
        events = self.events(data)
        self.assertEqual(events[-1]['type'], 'complete')
        self.assertFalse(Principle.objects.exists())
        self.assertEqual(Suggestion.objects.get().provider, 'demo')
        with patch('workbench.services.ai.stream_generate') as provider:
            again = self.events(data)
        provider.assert_not_called()
        self.assertEqual(again[-1], events[-1])
        self.assertEqual(Suggestion.objects.count(), 1)

    def test_provider_change_invalidates_preview_before_call(self):
        data = self.preview_data()
        with patch.dict('os.environ', {'AI_PROVIDER': 'codex'}), patch('workbench.services.ai.stream_generate') as provider:
            events = self.events(data)
        self.assertEqual(events[-1]['type'], 'error')
        self.assertIn('设置已变化', events[-1]['message'])
        provider.assert_not_called()

    def test_remote_send_requires_confirmation_even_in_stream(self):
        with patch.dict('os.environ', {'AI_PROVIDER': 'codex'}):
            data = self.preview_data()
            data.pop('confirmed_send')
            with patch('workbench.services.ai.stream_generate') as provider:
                events = self.events(data)
        self.assertEqual(events[-1]['type'], 'error')
        provider.assert_not_called()

    def test_no_login_non_ai_features_work_without_sdk_start(self):
        with patch.dict('os.environ', {'AI_PROVIDER': 'codex'}), patch('workbench.ai.codex_auth.Codex') as sdk:
            for path in ['/', '/sources/', '/principles/', '/cases/', '/settings/']:
                self.assertEqual(self.client.get(path).status_code, 200)
            self.assertEqual(self.client.post('/sources/new/', {'operation_key': key(), 'text': '离线资料', 'kind': 'text', 'verification': 'unverified'}).status_code, 302)
        sdk.assert_not_called()

    def test_login_has_post_and_csrf_boundary(self):
        self.assertEqual(self.client.get('/settings/codex/login/').status_code, 405)
        client = Client(enforce_csrf_checks=True)
        with patch('workbench.ai.codex_auth.start_login') as login:
            self.assertEqual(client.post('/settings/codex/login/').status_code, 403)
        login.assert_not_called()

    def test_multiturn_uses_local_messages_and_drops_outdated_ai_context(self):
        principle = save_principle(principle_data(), operation_key=key(), confirmed=True)
        case = create_case('阅读邀请', operation_key=key())
        first = build_context('chat', case)
        list(generate_suggestion_stream('chat', case, context=first, operation_key=key(), expected_revision=case.revision))
        case.refresh_from_db()
        second = build_context('chat', case)
        self.assertTrue(any(m['role'] == 'assistant' for m in second['messages']))
        update_references(case, [], [], depth='daily', expected_revision=case.revision, operation_key=key())
        case.refresh_from_db()
        third = build_context('chat', case)
        self.assertFalse(any(m['role'] == 'assistant' for m in third['messages']))
        self.assertEqual(third['versions'], [])
