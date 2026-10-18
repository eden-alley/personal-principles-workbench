from django.test import TestCase, override_settings
from django.urls import reverse
from workbench.models import Source, Principle, Decision, Observation
from workbench.services.sources import save_source
from workbench.services.principles import save_principle
from workbench.services.records import create_case, save_decision, save_observation
from tests.test_domain import key, principle_data


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class ViewTests(TestCase):
    def test_home_and_empty_libraries(self):
        for name in ['home', 'source_list', 'principle_list', 'case_list', 'decision_list', 'settings', 'demo']:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_primary_navigation_has_named_links(self):
        response = self.client.get(reverse('home'))
        self.assertContains(response, 'href="/sources/"')
        self.assertContains(response, 'href="/principles/"')
        self.assertContains(response, '历史对话')
        self.assertContains(response, 'href="/sources/new/"')

    def test_source_post_and_link_only_label(self):
        response = self.client.post(reverse('source_create'), {'operation_key': key(), 'url': 'https://example.com', 'kind': 'link', 'verification': 'unverified'})
        self.assertEqual(response.status_code, 302)
        source = Source.objects.get()
        self.assertContains(self.client.get(reverse('source_detail', args=[source.pk])), '正文未获取')
        response = self.client.get(reverse('ai_preview', args=['extract', source.pk]))
        self.assertContains(response, '正文未获取')

    def test_core_detail_pages_render_with_real_records(self):
        s = save_source({'text': '阅读材料'}, operation_key=key())
        p = save_principle(principle_data(), source_ids=[s.pk], operation_key=key(), confirmed=True)
        c = create_case('阅读安排', operation_key=key())
        d = save_decision(c, {'situation': '邀请冲突', 'choice': '改约'}, [p.current.pk], operation_key=key(), confirmed=True)
        o = save_observation(d, {'facts': '双方同意改约'}, [p.current.pk], operation_key=key(), confirmed=True)
        for name, pk in [('source_detail', s.pk), ('principle_detail', p.pk), ('version_detail', p.current.pk), ('case_detail', c.pk), ('decision_detail', d.pk), ('observation_detail', o.pk), ('principle_edit', p.pk), ('source_edit', s.pk), ('case_references', c.pk), ('decision_create', c.pk), ('observation_create', d.pk)]:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name, args=[pk])).status_code, 200)

    def test_csrf_and_post_only_enforced(self):
        from django.test import Client
        csrf_client = Client(enforce_csrf_checks=True)
        self.assertEqual(csrf_client.post(reverse('source_create'), {'text': 'test'}).status_code, 403)
        p = save_principle(principle_data(), operation_key=key(), confirmed=True)
        self.assertEqual(self.client.get(reverse('principle_focus', args=[p.pk])).status_code, 405)

    def test_same_origin_forms_keep_origin_for_csrf(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        response = client.get(reverse('source_create'))
        self.assertEqual(response['Referrer-Policy'], 'same-origin')
        response = client.post(reverse('source_create'), {'operation_key': key(), 'text': '本机同源表单', 'kind': 'text', 'verification': 'unverified', 'csrfmiddlewaretoken': client.cookies['csrftoken'].value}, HTTP_ORIGIN='http://testserver')
        self.assertEqual(response.status_code, 302)

    def test_stale_form_keeps_input_with_409(self):
        p = save_principle(principle_data(), operation_key=key(), confirmed=True)
        save_principle(principle_data(), principle=p, expected_revision=1, operation_key=key(), confirmed=True)
        data = principle_data()
        data.update(operation_key=key(), revision=1, confirmed='on', statement='我还没有保存的输入')
        response = self.client.post(reverse('principle_edit', args=[p.pk]), data)
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, '我还没有保存的输入', status_code=409)

    def test_http_end_to_end_preserves_old_choice_basis(self):
        self.client.post(reverse('source_create'), {'operation_key': key(), 'text': '为值得投入的阅读保留时间。', 'kind': 'text', 'verification': 'unverified'})
        source = Source.objects.get()
        from unittest.mock import patch
        with patch.dict('os.environ', {'AI_API_KEY': ''}):
            preview = self.client.get(reverse('ai_preview', args=['extract', source.pk]))
            f = preview.context['form']
            response = self.client.post(reverse('ai_preview', args=['extract', source.pk]), {'operation_key': f.initial['operation_key'], 'revision': f.initial['revision'], 'context_token': f.initial['context_token']})
        self.assertEqual(response.status_code, 302)
        from workbench.models import Suggestion
        s = Suggestion.objects.get(task='extract')
        self.assertEqual(self.client.get(reverse('suggestion_detail', args=[s.pk])).status_code, 200)
        data = principle_data()
        data.update(operation_key=key(), confirmed='on')
        self.assertEqual(self.client.post(reverse('suggestion_apply', args=[s.pk]), data).status_code, 302)
        p = Principle.objects.get()
        v1 = p.current
        self.client.post(reverse('case_create'), {'operation_key': key(), 'situation': '阅读时间与邀请冲突', 'depth': 'daily'})
        from workbench.models import Case
        c = Case.objects.get()
        self.client.post(reverse('decision_create', args=[c.pk]), {'operation_key': key(), 'situation': c.situation, 'choice': '改约', 'reason': '保留阅读空间', 'expectation': '自在', 'versions': [v1.pk], 'confirmed': 'on'})
        d = Decision.objects.get()
        self.client.post(reverse('observation_create', args=[d.pk]), {'operation_key': key(), 'facts': '朋友同意改约', 'interpretation': '协商可能有帮助', 'uncertainty': '其他邀请是否如此未知', 'difference': '比预想顺利', 'effect': 'supports', 'versions': [v1.pk], 'confirmed': 'on'})
        o = Observation.objects.get()
        with patch.dict('os.environ', {'AI_API_KEY': ''}):
            preview = self.client.get(reverse('ai_preview', args=['revise', o.pk]))
            f = preview.context['form']
            response = self.client.post(reverse('ai_preview', args=['revise', o.pk]), {'operation_key': f.initial['operation_key'], 'revision': f.initial['revision'], 'context_token': f.initial['context_token']})
        self.assertEqual(response.status_code, 302)
        suggestion = Suggestion.objects.get(task='revise')
        p.refresh_from_db()
        self.assertEqual(p.revision, 1)
        data.update(operation_key=key(), statement='先协商时间，再决定是否拒绝。', reason='这次协商有帮助，但仍需更多经历')
        self.assertEqual(self.client.post(reverse('suggestion_apply', args=[suggestion.pk]), data).status_code, 302)
        p.refresh_from_db()
        self.assertEqual(p.revision, 2)
        self.assertEqual(d.versions.get().pk, v1.pk)
        self.assertContains(self.client.get(reverse('decision_detail', args=[d.pk])), '保留阅读空间')
