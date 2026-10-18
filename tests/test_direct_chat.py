import json
from unittest.mock import patch
from django.test import TestCase, Client, override_settings
from workbench.models import Case, Message, Suggestion, Principle, Decision
from workbench.ai.base import ProviderError
from tests.test_domain import key


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class DirectChatTests(TestCase):
    def post_turn(self, data, case_id=None):
        url = f'/cases/{case_id}/send/' if case_id else '/chat/send/'
        response = self.client.post(url, data, HTTP_ACCEPT='application/x-ndjson')
        self.assertEqual(response.status_code, 200)
        return [json.loads(line) for line in b''.join(response.streaming_content).decode().splitlines()]

    def data(self, **values):
        token = self.client.get('/').context['chat_form'].initial.get('config_token', '')
        return dict(operation_key=key(), content='【虚构测试】想留一点时间自由阅读。', depth='daily', action='send', config_token=token, **values)

    def test_one_send_saves_input_and_returns_reply_in_place(self):
        events = self.post_turn(self.data())
        self.assertEqual(events[-1]['type'], 'complete')
        self.assertIn('html', events[-1])
        self.assertEqual(Case.objects.get().situation, '【虚构测试】想留一点时间自由阅读。')
        self.assertEqual(Message.objects.get().role, 'assistant')
        self.assertFalse(Principle.objects.exists())
        self.assertFalse(Decision.objects.exists())

    def test_followup_uses_saved_conversation(self):
        self.post_turn(self.data())
        case = Case.objects.get()
        data = self.data(revision=case.revision)
        data['content'] = '如果临时有其他安排呢？'
        events = self.post_turn(data, case.pk)
        self.assertEqual(events[-1]['type'], 'complete')
        self.assertEqual(list(Message.objects.values_list('role', flat=True)), ['assistant', 'user', 'assistant'])
        messages = Suggestion.objects.first().context['messages']
        self.assertEqual(messages[-1]['content'], '如果临时有其他安排呢？')
        self.assertEqual(messages[0]['role'], 'assistant')

    def test_replayed_submission_does_not_repeat_input_or_inference(self):
        data = self.data()
        self.post_turn(data)
        with patch('workbench.services.ai.stream_generate', side_effect=AssertionError('重复调用')):
            events = self.post_turn(data)
        self.assertEqual(events[-1]['type'], 'complete')
        self.assertEqual((Case.objects.count(), Message.objects.count(), Suggestion.objects.count()), (1, 1, 1))

    def test_failure_keeps_input_and_retry_does_not_duplicate_it(self):
        data = self.data()
        def fail(*args, **kwargs):
            yield {'type': 'delta', 'text': '{"answer":"未完成'}
            raise ProviderError('额度不足', 'quota_exhausted')
        with patch('workbench.services.ai.stream_generate', side_effect=fail):
            events = self.post_turn(data)
        self.assertEqual(events[-1]['code'], 'quota_exhausted')
        self.assertEqual(Case.objects.count(), 1)
        self.assertEqual(Message.objects.count(), 0)
        self.assertEqual(Suggestion.objects.count(), 0)
        self.assertEqual(self.post_turn(data)[-1]['type'], 'complete')
        self.assertEqual(Case.objects.count(), 1)

    def test_save_only_never_calls_model_and_can_resume_without_retyping(self):
        data = self.data()
        data['action'] = 'save'
        with patch('workbench.services.ai.stream_generate', side_effect=AssertionError('不应调用')):
            self.assertEqual(self.post_turn(data)[-1]['type'], 'complete')
        case = Case.objects.get()
        self.assertFalse(Message.objects.exists())
        data = self.data(revision=case.revision)
        data['content'] = ''
        self.assertEqual(self.post_turn(data, case.pk)[-1]['type'], 'complete')
        self.assertEqual(Message.objects.count(), 1)

    def test_stale_followup_preserves_other_page_updates(self):
        self.post_turn(self.data())
        case = Case.objects.get()
        with patch('workbench.services.ai.stream_generate', side_effect=AssertionError('不应调用')):
            events = self.post_turn(self.data(revision=case.revision - 1), case.pk)
        self.assertEqual(events[-1]['type'], 'error')
        self.assertEqual(Message.objects.count(), 1)

    def test_explicit_send_or_save_is_required(self):
        data = self.data()
        data.pop('action')
        events = self.post_turn(data)
        self.assertEqual(events[-1]['type'], 'error')
        self.assertFalse(Case.objects.exists())

    def test_input_change_cannot_reuse_submission_key(self):
        data = self.data()
        self.post_turn(data)
        data['content'] = '另一条不同的问题'
        events = self.post_turn(data)
        self.assertEqual(events[-1]['type'], 'error')
        self.assertEqual(Case.objects.count(), 1)

    def test_direct_send_requires_post_and_csrf(self):
        self.assertEqual(self.client.get('/chat/send/').status_code, 405)
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post('/chat/send/', self.data()).status_code, 403)

    def test_resume_key_cannot_be_reused_for_a_new_message(self):
        data = self.data()
        data['action'] = 'save'
        self.post_turn(data)
        case = Case.objects.get()
        resume = self.data(revision=case.revision)
        resume['content'] = ''
        self.post_turn(resume, case.pk)
        case.refresh_from_db()
        resume['revision'] = case.revision
        resume['content'] = '这应该用新的发送编号'
        result = self.post_turn(resume, case.pk)
        self.assertEqual(result[-1]['type'], 'error')
        self.assertEqual(Message.objects.count(), 1)

    def test_provider_change_since_page_open_requires_refresh(self):
        data = self.data()
        with patch.dict('os.environ', {'AI_PROVIDER': 'api'}), patch('workbench.services.ai.stream_generate', side_effect=AssertionError('不能调用新提供商')):
            result = self.post_turn(data)
        self.assertEqual(result[-1]['type'], 'error')
        self.assertFalse(Case.objects.exists())

    def test_browser_disconnect_releases_chat_and_generation_for_next_send(self):
        from tests.test_codex_provider import FakeCodex, FakeHandle, Event
        from workbench.ai import codex_provider
        from workbench.services.chat import _chat_lock
        from workbench.services.ai import _generation_lock
        FakeCodex.instances, FakeCodex.threads, FakeCodex.configs = [], [], []
        FakeCodex.safe_settings = True
        FakeCodex.handles = [FakeHandle([Event('item/agentMessage/delta', {'delta': '{"answer":"未完成'})])]
        data = self.data()
        def real_provider_contract(task, context, **kwargs):
            return codex_provider.CodexProvider().stream(task, context, config={'timeout': 2})
        with patch('workbench.services.ai.stream_generate', side_effect=real_provider_contract), patch.object(codex_provider, 'Codex', FakeCodex), patch.object(codex_provider.codex_auth, 'status', return_value={'code': 'connected'}):
            response = self.client.post('/chat/send/', data, HTTP_ACCEPT='application/x-ndjson')
            events = iter(response.streaming_content)
            for line in events:
                if json.loads(line)['type'] == 'delta':
                    break
            response.close()
        self.assertFalse(_chat_lock.locked())
        self.assertFalse(_generation_lock.locked())
        self.assertTrue(all(instance.closed for instance in FakeCodex.instances))
        self.assertEqual(Case.objects.count(), 1)
        self.assertFalse(Message.objects.exists())
        self.assertFalse(Suggestion.objects.exists())
        self.assertEqual(self.post_turn(data)[-1]['type'], 'complete')
        self.assertEqual(Case.objects.count(), 1)
        self.assertEqual(Message.objects.count(), 1)
