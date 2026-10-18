import tempfile
import uuid
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.core.exceptions import ValidationError
from workbench.models import Principle, PrincipleVersion, Source
from workbench.services import library


class LibraryTests(TestCase):
    def setUp(self):
        self.owner = 'a' * 24
        self.other = 'b' * 24
        self.temp = tempfile.TemporaryDirectory()
        self.settings = override_settings(MEDIA_ROOT=self.temp.name)
        self.settings.enable()

    def tearDown(self):
        self.settings.disable()
        self.temp.cleanup()

    def save(self, **kwargs):
        return library.collect(self.owner, operation_key=str(uuid.uuid4()), **kwargs)

    def test_collection_is_not_a_principle_and_is_owner_scoped(self):
        result = self.save(text='独处有助于独立思考。')
        self.assertEqual(Principle.objects.count(), 0)
        self.assertEqual(Source.objects.count(), 1)
        self.assertEqual(library.list_entries(self.other), [])
        with self.assertRaises(ValidationError):
            library.detail(self.other, result['id'])
        self.assertEqual(result['parse_state'], 'ready')
        self.assertEqual(result['index_state'], 'pending')

    def test_idempotent_save_rejects_different_content(self):
        key = str(uuid.uuid4())
        a = library.collect(self.owner, text='原文', operation_key=key)
        b = library.collect(self.owner, text='原文', operation_key=key)
        self.assertEqual(a['id'], b['id'])
        with self.assertRaises(ValidationError):
            library.collect(self.owner, text='另一份', operation_key=key)

    def test_confirmed_principle_version_and_evidence_are_immutable(self):
        source = self.save(text='独处有助于独立思考。')
        first = library.save_principle(self.owner, statement='我应保留独处时间。',
            source_ids=[source['id']], confirmed=True, operation_key=str(uuid.uuid4()))
        exp = library.save_experience(self.owner, facts='主动聚会收获很多。',
            confirmed=True, operation_key=str(uuid.uuid4()))
        second = library.save_principle(self.owner, entry_id=first['id'], expected_revision=1,
            statement='独处和高质量社交都可以服务于自主安排。', reason='结合聚会经历修订。',
            experience_ids=[exp['id']], confirmed=True, operation_key=str(uuid.uuid4()))
        self.assertEqual(second['revision'], 2)
        old = library.detail(self.owner, first['id'], version=1)
        self.assertEqual(old['text'], '我应保留独处时间。')
        self.assertEqual(old['source_ids'], [source['id']])
        self.assertEqual(old['experience_ids'], [])
        self.assertEqual(second['source_ids'], [source['id']])
        self.assertEqual(second['experience_ids'], [exp['id']])
        self.assertEqual(PrincipleVersion.objects.count(), 2)
        with self.assertRaises(ValidationError):
            library.save_principle(self.owner, entry_id=first['id'], expected_revision=1,
                statement='迟到更新', confirmed=True, operation_key=str(uuid.uuid4()))

    def test_approval_requires_save_intent_and_specific_draft(self):
        draft = library.propose(self.owner, 'conversation-a', 'message-a',
            {'kind': 'principle', 'text': '我重视自主安排时间。'})
        self.assertEqual(Principle.objects.count(), 0)
        self.assertIsNotNone(draft)
        with self.assertRaises(ValidationError):
            library.confirm(self.owner, draft['id'], confirmed=False, operation_key=str(uuid.uuid4()))
        saved = library.confirm(self.owner, draft['id'], confirmed=True, operation_key=str(uuid.uuid4()))
        again = library.confirm(self.owner, draft['id'], confirmed=True, operation_key=str(uuid.uuid4()))
        self.assertEqual(saved['id'], again['id'])
        self.assertEqual(Principle.objects.count(), 1)

    def test_proposals_do_not_repeat_when_ignored_or_dismissed(self):
        draft = library.propose(self.owner, 'conversation-a', 'm1', {'kind':'principle','text':'保留自主时间。'})
        self.assertIsNone(library.propose(self.owner, 'conversation-a', 'm2', {'kind':'principle','text':'又一条认识。'}))
        library.dismiss(self.owner, draft['id'])
        self.assertIsNone(library.propose(self.owner, 'conversation-a', 'm3', {'kind':'principle','text':'保留自主时间。'}))

    def test_cross_owner_evidence_and_confirmation_are_rejected(self):
        source = self.save(text='个人素材')
        with self.assertRaises(ValidationError):
            library.save_principle(self.other, statement='错误引用', source_ids=[source['id']],
                confirmed=True, operation_key=str(uuid.uuid4()))
        draft = library.propose(self.owner, 'c', 'm', {'kind':'experience','text':'一次实际经历'})
        with self.assertRaises(ValidationError):
            library.confirm(self.other, draft['id'], confirmed=True, operation_key=str(uuid.uuid4()))

    def test_removal_and_restoration_require_matching_revision(self):
        item = self.save(text='收藏内容')
        with self.assertRaises(ValidationError):
            library.set_removed(self.owner, item['id'], True, expected_revision=1, confirmed=False)
        library.set_removed(self.owner, item['id'], True, expected_revision=1, confirmed=True)
        self.assertEqual(library.list_entries(self.owner), [])
        library.set_removed(self.owner, item['id'], False, expected_revision=2, confirmed=True)
        self.assertEqual(len(library.list_entries(self.owner)), 1)

    def test_index_failure_does_not_claim_retrievability(self):
        item = self.save(text='有文字但索引失败')
        def broken(_):
            raise RuntimeError('internal diagnostic')
        library.index_entry(self.owner, item['id'], embed=broken)
        updated = library.detail(self.owner, item['id'])
        self.assertEqual(updated['parse_state'], 'ready')
        self.assertEqual(updated['index_state'], 'failed')
        self.assertNotIn('internal diagnostic', str(updated))
