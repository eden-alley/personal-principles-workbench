import io
import json
import tempfile
import uuid
import zipfile
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from workbench.models import (
    Case,
    Decision,
    Message,
    Observation,
    Principle,
    PrincipleVersion,
    Source,
    Suggestion,
    UserSettings,
)
from workbench.services.common import Conflict, ConfirmationRequired
from workbench.services.data_admin import delete_impact, delete_record, export_archive
from workbench.services.principles import save_principle
from workbench.services.records import create_case, save_decision, save_observation
from workbench.services.sources import save_source


def key():
    return str(uuid.uuid4())


def principle_data(**changes):
    values = {
        'title': '保留阅读时间',
        'statement': '在可以改约时保留阅读时间。',
        'kind': 'strategy',
        'status': 'adopted',
        'when_to_use': '阅读与邀请冲突',
        'conditions': '',
        'boundaries': '重要关系需要回应',
        'questions': '',
        'tags': '阅读,自主',
        'reason': '本人确认',
    }
    values.update(changes)
    return values


def png_upload(name='original.png'):
    output = io.BytesIO()
    Image.new('RGB', (2, 2), '#294e42').save(output, format='PNG')
    return SimpleUploadedFile(name, output.getvalue(), content_type='image/png')


class DataAdminTests(TestCase):
    def make_principle(self):
        return save_principle(principle_data(), operation_key=key(), confirmed=True)

    def test_export_archive_contains_all_core_models_m2m_and_manifest(self):
        source = save_source({'title': '一份素材', 'text': '保留原文。'}, operation_key=key())
        principle = self.make_principle()
        version = principle.current
        version.sources.add(source)
        case = create_case('阅读与邀请冲突', operation_key=key())
        case.versions.add(version)
        decision = save_decision(case, {'situation': '当时', 'choice': '改约'}, [version.id], operation_key=key(), confirmed=True)
        observation = save_observation(decision, {'facts': '后来更自在。'}, [version.id], operation_key=key(), confirmed=True)
        Message.objects.create(case=case, role='user', content='我的记录。', references={'versions': [version.id]})
        Suggestion.objects.create(task='chat', case=case, payload={'citations': {'versions': [version.id]}}, context={'versions': [version.id]})
        UserSettings.objects.get_or_create()

        archive = export_archive()
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            names = set(bundle.namelist())
            data = json.loads(bundle.read('data.json'))
            manifest = json.loads(bundle.read('manifest.json'))

        self.assertIn('data.json', names)
        self.assertIn('manifest.json', names)
        self.assertEqual(manifest['schema_version'], 1)
        self.assertIn('exported_at', manifest)
        models = {item['model'] for item in data}
        self.assertTrue({'workbench.source', 'workbench.principle', 'workbench.principleversion', 'workbench.case', 'workbench.message', 'workbench.decision', 'workbench.observation', 'workbench.suggestion', 'workbench.operation', 'workbench.usersettings'} <= models)
        version_row = next(item for item in data if item['model'] == 'workbench.principleversion')
        self.assertIn(source.id, version_row['fields']['sources'])
        self.assertNotIn('.secret', names)
        self.assertNotIn('.env', names)
        self.assertEqual(observation.decision_id, decision.id)

    def test_export_reports_missing_original_attachment(self):
        with tempfile.TemporaryDirectory() as folder, override_settings(MEDIA_ROOT=folder):
            source = Source.objects.create(title='缺失附件', image='does-not-exist.png')
            with self.assertRaises(FileNotFoundError):
                export_archive()
        self.assertTrue(Source.objects.filter(pk=source.pk).exists())

    def test_export_includes_attachment_hash_and_safe_name(self):
        with tempfile.TemporaryDirectory() as folder, override_settings(MEDIA_ROOT=folder):
            source = save_source({'title': '../截图', 'kind': 'image'}, image=png_upload('../raw.png'), operation_key=key())
            archive = export_archive()
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                manifest = json.loads(bundle.read('manifest.json'))
                attachment = manifest['attachments'][0]
                self.assertTrue(attachment['zip_path'].startswith('attachments/'))
                self.assertNotIn('..', attachment['zip_path'])
                self.assertEqual(bundle.read(attachment['zip_path']), Path(source.image.path).read_bytes())

    def test_delete_source_removes_attachment_after_commit(self):
        with tempfile.TemporaryDirectory() as folder, override_settings(MEDIA_ROOT=folder):
            source = save_source({'title': '带原图的素材', 'kind': 'image'}, image=png_upload(), operation_key=key())
            path = Path(source.image.path)
            self.assertTrue(path.exists())
            preview = delete_impact('source', source.id)
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                delete_record('source', source.id, signature=preview['signature'], confirmed=True)
            self.assertEqual(len(callbacks), 1)
            self.assertFalse(path.exists())

    def test_delete_source_unlinks_versions_and_cleans_ai_references(self):
        source = Source.objects.create(title='要删除的素材', text='原文快照')
        principle = self.make_principle()
        version = principle.current
        version.sources.add(source)
        case = Case.objects.create(situation='相关情境')
        user = Message.objects.create(case=case, role='user', content='我写下的原文快照', references={'sources': [source.id]})
        assistant = Message.objects.create(case=case, role='assistant', content='原文快照', references={'sources': [source.id]})
        direct = Suggestion.objects.create(task='extract', source=source, payload={'source_id': source.id}, context={'source_id': source.id})
        indirect = Suggestion.objects.create(task='chat', case=case, payload={'source_id': source.id}, context={'source_id': source.id})

        preview = delete_impact('source', source.id)
        self.assertIn('title', preview)
        self.assertTrue(preview['lines'])
        delete_record('source', source.id, signature=preview['signature'], confirmed=True)

        self.assertFalse(Source.objects.filter(pk=source.pk).exists())
        self.assertTrue(PrincipleVersion.objects.filter(pk=version.pk).exists())
        self.assertFalse(version.sources.filter(pk=source.pk).exists())
        self.assertFalse(Suggestion.objects.filter(pk__in=[direct.pk, indirect.pk]).exists())
        self.assertFalse(Message.objects.filter(pk=assistant.pk).exists())
        user.refresh_from_db()
        self.assertNotIn(source.id, user.references.get('sources', []))

    def test_delete_principle_keeps_formal_records_but_unlinks_versions_and_bumps_cases(self):
        principle = self.make_principle()
        version = principle.current
        case = Case.objects.create(situation='引用原则的情境')
        case.versions.add(version)
        decision = Decision.objects.create(case=case, situation='当时', choice='暂缓')
        decision.versions.add(version)
        observation = Observation.objects.create(decision=decision, facts='后来观察')
        observation.versions.add(version)
        assistant = Message.objects.create(case=case, role='assistant', content='建议', references={'versions': [version.id]})
        user = Message.objects.create(case=case, role='user', content='我的记录', references={'versions': [version.id]})
        suggestion = Suggestion.objects.create(task='revise', observation=observation, payload={'citations': {'versions': [version.id]}}, context={'versions': [version.id]})
        revision = case.revision

        preview = delete_impact('principle', principle.id)
        delete_record('principle', principle.id, signature=preview['signature'], confirmed=True)

        self.assertFalse(Principle.objects.filter(pk=principle.pk).exists())
        self.assertFalse(PrincipleVersion.objects.filter(pk=version.pk).exists())
        self.assertTrue(Decision.objects.filter(pk=decision.pk).exists())
        self.assertTrue(Observation.objects.filter(pk=observation.pk).exists())
        self.assertFalse(Suggestion.objects.filter(pk=suggestion.pk).exists())
        self.assertFalse(Message.objects.filter(pk=assistant.pk).exists())
        self.assertEqual(Case.objects.get(pk=case.pk).revision, revision + 1)
        user.refresh_from_db()
        self.assertNotIn(version.id, user.references.get('versions', []))
        self.assertFalse(case.versions.filter(pk=version.pk).exists())
        self.assertFalse(decision.versions.filter(pk=version.pk).exists())
        self.assertFalse(observation.versions.filter(pk=version.pk).exists())

    def test_delete_case_keeps_decisions_but_detaches_case(self):
        case = Case.objects.create(situation='要清理的情境')
        decision = Decision.objects.create(case=case, situation='当时', choice='观察')
        Message.objects.create(case=case, role='user', content='我的记录')
        Suggestion.objects.create(task='chat', case=case)
        preview = delete_impact('case', case.id)
        delete_record('case', case.id, signature=preview['signature'], confirmed=True)
        decision.refresh_from_db()
        self.assertIsNone(decision.case_id)
        self.assertFalse(Case.objects.filter(pk=case.pk).exists())
        self.assertFalse(Message.objects.filter(case_id=case.pk).exists())

    def test_delete_requires_confirmation_and_rejects_stale_preview(self):
        source = Source.objects.create(title='需要确认')
        preview = delete_impact('source', source.id)
        with self.assertRaises(ConfirmationRequired):
            delete_record('source', source.id, signature=preview['signature'], confirmed=False)
        Source.objects.filter(pk=source.pk).update(title='已变化')
        with self.assertRaises(Conflict):
            delete_record('source', source.id, signature=preview['signature'], confirmed=True)

    def test_source_delete_matches_source_references_without_cross_type_ids(self):
        source = Source.objects.create(title='素材', text='正文')
        principle = self.make_principle()
        version = principle.current
        case = Case.objects.create(situation='只引用原则版本')
        suggestion = Suggestion.objects.create(
            task='chat',
            case=case,
            context={'versions': [version.id]},
            payload={'citations': {'versions': [version.id], 'observations': []}},
        )
        user = Message.objects.create(
            case=case,
            role='user',
            content='保留我的记录',
            references={'versions': [version.id], 'sources': [source.id]},
        )
        preview = delete_impact('source', source.id)
        delete_record('source', source.id, signature=preview['signature'], confirmed=True)

        self.assertTrue(Suggestion.objects.filter(pk=suggestion.pk).exists())
        user.refresh_from_db()
        self.assertEqual(user.references, {'versions': [version.id], 'sources': []})

    def test_case_delete_does_not_match_versions_with_same_numeric_id(self):
        case = Case.objects.create(situation='待删除情境')
        other_case = Case.objects.create(situation='保留情境')
        principle = self.make_principle()
        version = principle.current
        unrelated = Suggestion.objects.create(
            task='chat',
            case=other_case,
            context={'versions': [case.id]},
            payload={'citations': {'versions': [case.id], 'observations': []}},
        )
        preview = delete_impact('case', case.id)
        delete_record('case', case.id, signature=preview['signature'], confirmed=True)
        self.assertTrue(Suggestion.objects.filter(pk=unrelated.pk).exists())
        self.assertTrue(PrincipleVersion.objects.filter(pk=version.pk).exists())

    def test_source_revision_change_invalidates_delete_preview(self):
        source = Source.objects.create(title='会被校对的素材', text='原始正文')
        preview = delete_impact('source', source.id)
        Source.objects.filter(pk=source.pk).update(revision=2, transcript='校对后的正文')
        with self.assertRaises(Conflict):
            delete_record('source', source.id, signature=preview['signature'], confirmed=True)

    def test_decision_and_observation_previews_count_affected_ai_messages(self):
        case = Case.objects.create(situation='记录情境')
        decision = Decision.objects.create(case=case, situation='当时', choice='观察')
        observation = Observation.objects.create(decision=decision, facts='后来')
        assistant = Message.objects.create(case=case, role='assistant', content='建议', references={'observations': [observation.id]})
        decision_preview = delete_impact('decision', decision.id)
        observation_preview = delete_impact('observation', observation.id)
        self.assertIn('1 条受影响的 AI 消息', '\n'.join(decision_preview['lines']))
        self.assertIn('1 条受影响的 AI 消息', '\n'.join(observation_preview['lines']))
        self.assertTrue(Message.objects.filter(pk=assistant.pk).exists())

    def test_deleting_result_principle_removes_applied_suggestion(self):
        principle = self.make_principle()
        applied = Suggestion.objects.create(
            task='extract',
            applied=True,
            result_type='principle',
            result_id=principle.id,
            payload={},
            context={},
        )
        preview = delete_impact('principle', principle.id)
        delete_record('principle', principle.id, signature=preview['signature'], confirmed=True)
        self.assertFalse(Suggestion.objects.filter(pk=applied.pk).exists())
