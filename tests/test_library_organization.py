import json
import uuid
from types import SimpleNamespace
from datetime import timedelta
from unittest.mock import Mock

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from workbench.models import LibraryEntry, Observation, Principle, Source
from workbench.services import library, library_organization as organization


class OrganizationTests(TestCase):
    owner = 'a' * 24
    other = 'b' * 24
    text = '独处能让我恢复精力。有共同话题的小范围交流也能带来启发。'

    def collect(self, text=None, owner=None):
        result = library.collect(owner or self.owner, text=text or self.text,
                                 operation_key=str(uuid.uuid4()))
        library.index_entry(owner or self.owner, result['id'], embed=lambda text: [1., 0.])
        return result

    def result(self, **updates):
        value = {'title': '独处与交流', 'summary': '材料讨论独处和交流各自的价值。',
                 'key_points': [{'text': '独处有助于恢复精力。', 'quote': '独处能让我恢复精力。'}],
                 'topics': ['精力管理', '人际交往'], 'relations': [], 'limitations': '这是一份观点材料，未验证普遍适用性。'}
        value.update(updates)
        return json.dumps(value, ensure_ascii=False)

    def test_collect_queues_once_and_does_not_process_old_sources_implicitly(self):
        key = str(uuid.uuid4())
        first = library.collect(self.owner, text=self.text, operation_key=key)
        again = library.collect(self.owner, text=self.text, operation_key=key)
        self.assertEqual(first['id'], again['id'])
        self.assertEqual(first['organization']['state'], 'pending')
        self.assertEqual(organization.Job.objects.count(), 1)
        source = Source.objects.create(text='旧素材')
        old = LibraryEntry.objects.create(owner=self.owner, kind='source', object_id=source.pk)
        self.assertEqual(library.detail(self.owner, old.pk)['organization']['state'], 'not_started')

    def test_background_summary_is_separate_grounded_and_idempotent(self):
        entry = self.collect()
        model = Mock(return_value=self.result())
        organization.process(self.owner, entry['id'], generate=model)
        organization.process(self.owner, entry['id'], generate=model)
        value = library.detail(self.owner, entry['id'])
        self.assertEqual(value['organization']['state'], 'ready')
        self.assertEqual(value['organization']['topics'], ['精力管理', '人际交往'])
        self.assertEqual(value['text'], self.text)
        self.assertEqual(value['revision'], 1)
        self.assertEqual(Principle.objects.count(), 0)
        self.assertEqual(Observation.objects.count(), 0)
        self.assertEqual(model.call_count, 1)

    def test_organization_records_selected_api_model(self):
        entry = self.collect()
        def generate(prompt):
            return self.result()
        generate.config = SimpleNamespace(model='compatible-test-model')
        organization.process(self.owner, entry['id'], generate=generate)
        value = library.detail(self.owner, entry['id'])['organization']
        self.assertEqual(value['state'], 'ready')
        self.assertEqual(value['model'], 'compatible-test-model')

    def test_relation_requires_sent_same_owner_source_and_exact_quotes(self):
        related = self.collect('小范围交流能带来启发。')
        foreign = self.collect('另一个账号的私有资料', owner=self.other)
        entry = self.collect()
        captured = []
        def model(prompt):
            captured.append(prompt)
            return self.result(relations=[{'entry_id': related['id'], 'kind': 'complements',
                'explanation': '两份材料都谈到小范围交流的启发。',
                'quote': '小范围交流也能带来启发。', 'related_quote': '小范围交流能带来启发。'}])
        organization.process(self.owner, entry['id'], generate=model)
        value = library.detail(self.owner, entry['id'])['organization']
        self.assertEqual(value['state'], 'ready')
        self.assertEqual(value['relations'][0]['entry_id'], related['id'])
        self.assertNotIn('另一个账号的私有资料', captured[0])
        organization.request(self.owner, entry['id'])
        organization.process(self.owner, entry['id'], generate=lambda p:self.result(relations=[{
            'entry_id': foreign['id'], 'kind':'similar', 'explanation':'伪造关联',
            'quote':self.text, 'related_quote':'另一个账号的私有资料'}]))
        failed = library.detail(self.owner, entry['id'])['organization']
        self.assertEqual(failed['state'], 'failed')
        self.assertEqual(failed['relations'][0]['entry_id'], related['id'])

    def test_invalid_quote_fails_without_changing_original(self):
        entry = self.collect()
        organization.process(self.owner, entry['id'], generate=lambda p:self.result(
            key_points=[{'text':'虚构经历', 'quote':'我曾经获得全国冠军'}]))
        value = library.detail(self.owner, entry['id'])
        self.assertEqual(value['organization']['state'], 'failed')
        self.assertEqual(value['organization']['error_code'], 'invalid_output')
        self.assertEqual(value['text'], self.text)

    def test_parse_failure_and_url_only_do_not_call_model(self):
        entry = self.collect()
        LibraryEntry.objects.filter(pk=entry['id']).update(parse_state='failed', index_state='blocked')
        model = Mock(return_value=self.result())
        organization.process(self.owner, entry['id'], generate=model)
        self.assertEqual(library.detail(self.owner, entry['id'])['organization']['state'], 'blocked')
        url = library.collect(self.owner, url='https://example.invalid/article', operation_key=str(uuid.uuid4()))
        organization.process(self.owner, url['id'], generate=model)
        self.assertEqual(library.detail(self.owner, url['id'])['organization']['state'], 'blocked')
        model.assert_not_called()

    def test_timeout_is_visible_and_only_manual_retry_calls_again(self):
        entry = self.collect()
        model = Mock(side_effect=TimeoutError('private error detail'))
        organization.process(self.owner, entry['id'], generate=model)
        organization.process(self.owner, entry['id'], generate=model)
        state = library.detail(self.owner, entry['id'])['organization']
        self.assertEqual(state['state'], 'failed')
        self.assertNotIn('private', json.dumps(state))
        self.assertEqual(model.call_count, 1)
        organization.request(self.owner, entry['id'])
        organization.process(self.owner, entry['id'], generate=lambda p:self.result())
        self.assertEqual(library.detail(self.owner, entry['id'])['organization']['state'], 'ready')

    def test_removed_or_revised_during_model_call_cannot_commit(self):
        entry = self.collect()
        def model(prompt):
            library.set_removed(self.owner, entry['id'], True, expected_revision=1, confirmed=True)
            return self.result()
        organization.process(self.owner, entry['id'], generate=model)
        self.assertNotEqual(library.detail(self.owner, entry['id'])['organization']['state'], 'ready')

    def test_manual_organization_is_owner_scoped_and_does_not_repeat_running_job(self):
        entry = self.collect()
        with self.assertRaises(ValidationError):
            organization.request(self.other, entry['id'])
        claim = organization.claim(self.owner, entry['id'])
        self.assertIsNotNone(claim)
        self.assertIsNone(organization.claim(self.owner, entry['id']))
        organization.request(self.owner, entry['id'])
        self.assertIsNone(organization.claim(self.owner, entry['id']))

    def test_expired_lease_cannot_overwrite_newer_result(self):
        entry = self.collect()
        first = organization.claim(self.owner, entry['id'])
        organization.Job.objects.filter(entry_id=entry['id']).update(lease_until=timezone.now()-timedelta(seconds=1))
        second = organization.claim(self.owner, entry['id'])
        self.assertNotEqual(first['lease_token'], second['lease_token'])
        self.assertFalse(organization.finish(first, json.loads(self.result())))
        self.assertTrue(organization.finish(second, json.loads(self.result())))

    def test_restored_source_marks_previous_organization_stale(self):
        entry = self.collect()
        organization.process(self.owner, entry['id'], generate=lambda p:self.result())
        library.set_removed(self.owner, entry['id'], True, expected_revision=1, confirmed=True)
        restored=library.set_removed(self.owner, entry['id'], False, expected_revision=2, confirmed=True)
        self.assertEqual(restored['organization']['state'],'stale')
        self.assertEqual(restored['organization']['summary'],'材料讨论独处和交流各自的价值。')
        organization.request(self.owner,entry['id'])
        self.assertEqual(library.detail(self.owner,entry['id'])['organization']['state'],'pending')

    def test_chat_context_contains_derived_summary_as_labeled_background(self):
        from unittest.mock import patch
        from workbench.services import library_context
        entry = self.collect()
        organization.process(self.owner, entry['id'], generate=lambda p:self.result())
        with patch('workbench.services.semantic.encode_query',return_value=[1.,0.]):
            refs=library_context.retrieve(self.owner,'独处')['references']
        self.assertEqual(refs[0]['organization']['summary'],'材料讨论独处和交流各自的价值。')
        prompt=library_context.context_instruction(refs,allow_proposal=False)
        self.assertIn('AI整理',prompt)
        self.assertIn('自动整理',prompt)
