import uuid
import tempfile
from django.test import TestCase, override_settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from workbench.models import Source, Principle, PrincipleVersion, Decision, Observation
from workbench.services.principles import save_principle
from workbench.services.records import create_case, save_decision, save_observation, update_references
from workbench.services.sources import save_source
from workbench.services.common import Conflict, ConfirmationRequired


def key():
    return str(uuid.uuid4())


def principle_data(**changes):
    return dict(title='保护阅读时间', statement='在可以改约时保留阅读时间。', kind='strategy', status='adopted', when_to_use='阅读与邀请冲突', conditions='', boundaries='重要关系需要回应', questions='', tags='阅读,自主', reason='本人确认', **changes)


class DomainTests(TestCase):
    def make_principle(self):
        return save_principle(principle_data(), operation_key=key(), confirmed=True)

    def test_manual_workflow_without_model(self):
        source = save_source({'text': '自己的观察', 'kind': 'experience'}, operation_key=key())
        p = self.make_principle()
        case = create_case('阅读与邀请冲突', operation_key=key())
        d = save_decision(case, {'situation': case.situation, 'choice': '改约', 'reason': '保留空间', 'expectation': '自在'}, [p.current.id], operation_key=key(), confirmed=True)
        self.assertEqual(source.text, '自己的观察')
        self.assertEqual(d.versions.get().number, 1)

    def test_explicit_confirmation_required(self):
        with self.assertRaises(ConfirmationRequired):
            save_principle(principle_data(), operation_key=key(), confirmed=False)
        self.assertFalse(Principle.objects.exists())

    def test_revision_keeps_history_and_decision_reference(self):
        p = self.make_principle()
        old = p.current
        c = create_case('阅读安排', operation_key=key())
        d = save_decision(c, {'situation': '当时', 'choice': '阅读', 'reason': '我愿意', 'expectation': '平静'}, [old.id], operation_key=key(), confirmed=True)
        data = principle_data()
        data['statement'] = '重要邀请优先协商时间。'
        save_principle(data, principle=p, expected_revision=1, operation_key=key(), confirmed=True)
        old.refresh_from_db()
        self.assertEqual(old.statement, '在可以改约时保留阅读时间。')
        self.assertEqual(d.versions.get().id, old.id)
        self.assertEqual(PrincipleVersion.objects.count(), 2)

    def test_versions_are_immutable(self):
        v = self.make_principle().current
        v.statement = '覆盖'
        with self.assertRaises(ValidationError):
            v.save()
        with self.assertRaises(ValidationError):
            PrincipleVersion.objects.filter(pk=v.pk).update(statement='覆盖')

    def test_conflict_is_explicit(self):
        p = self.make_principle()
        save_principle(principle_data(), principle=p, expected_revision=1, operation_key=key(), confirmed=True)
        with self.assertRaises(Conflict):
            save_principle(principle_data(), principle=p, expected_revision=1, operation_key=key(), confirmed=True)
        self.assertEqual(PrincipleVersion.objects.count(), 2)

    def test_duplicate_confirmation_is_idempotent(self):
        token = key()
        p1 = save_principle(principle_data(), operation_key=token, confirmed=True)
        p2 = save_principle(principle_data(), operation_key=token, confirmed=True)
        self.assertEqual(p1.id, p2.id)
        c = create_case('一个问题', operation_key=key())
        token = key()
        data = {'situation': '当时', 'choice': '暂缓', 'reason': '', 'expectation': ''}
        d1 = save_decision(c, data, [], operation_key=token, confirmed=True)
        d2 = save_decision(c, data, [], operation_key=token, confirmed=True)
        self.assertEqual(d1.id, d2.id)

    def test_observation_does_not_overwrite_expectation_or_reason(self):
        c = create_case('问题', operation_key=key())
        d = save_decision(c, {'situation': '情况', 'choice': '尝试', 'reason': '原理由', 'expectation': '原期待'}, [], operation_key=key(), confirmed=True)
        o = save_observation(d, {'facts': '发生了不同的事', 'interpretation': '我觉得可能受情境影响', 'uncertainty': '原因未知', 'difference': '与预期不同', 'effect': 'uncertain'}, [], operation_key=key(), confirmed=True)
        d.refresh_from_db()
        self.assertEqual(d.reason, '原理由')
        self.assertEqual(d.expectation, '原期待')
        self.assertEqual(o.uncertainty, '原因未知')

    def test_invalid_reference_rolls_back(self):
        c = create_case('问题', operation_key=key())
        with self.assertRaises(ValidationError):
            save_decision(c, {'situation': '情况', 'choice': '暂缓'}, [99999], operation_key=key(), confirmed=True)
        self.assertFalse(Decision.objects.exists())

    def test_reference_removal_is_persisted(self):
        p = self.make_principle()
        c = create_case('阅读', operation_key=key())
        update_references(c, [p.current.id], [], depth='daily', expected_revision=c.revision, operation_key=key())
        c.refresh_from_db()
        update_references(c, [], [], depth='deep', expected_revision=c.revision, operation_key=key())
        c.refresh_from_db()
        self.assertFalse(c.versions.exists())
        self.assertEqual(c.depth, 'deep')

    def test_source_update_does_not_overwrite_original(self):
        s = save_source({'text': '原始文字', 'url': 'https://example.com', 'kind': 'text'}, operation_key=key())
        save_source({'text': '试图替换', 'transcript': '独立校对'}, source=s, expected_revision=s.revision, operation_key=key())
        s.refresh_from_db()
        self.assertEqual(s.text, '原始文字')
        self.assertEqual(s.transcript, '独立校对')

    def test_invalid_image_rejected(self):
        with tempfile.TemporaryDirectory() as folder, override_settings(MEDIA_ROOT=folder):
            with self.assertRaises(ValidationError):
                save_source({'kind': 'image'}, image=SimpleUploadedFile('bad.png', b'not an image'), operation_key=key())
            self.assertFalse(Source.objects.exists())

    def test_source_keeps_original_whitespace(self):
        original = '  原文第一行\n\n末尾原文  \n'
        s = save_source({'text': original}, operation_key=key())
        self.assertEqual(s.text, original)
