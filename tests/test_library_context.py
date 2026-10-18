import uuid
from unittest.mock import patch
from django.test import TestCase
from workbench.services import library, library_context
from workbench.models import LibraryEntry


class ContextTests(TestCase):
    def setUp(self):
        self.owner = 'a'*24

    def indexed(self, text, kind='source', owner=None, vector=None):
        owner = owner or self.owner
        key = str(uuid.uuid4())
        if kind == 'principle':
            item = library.save_principle(owner, statement=text, confirmed=True, operation_key=key)
        else:
            item = library.collect(owner, text=text, operation_key=key)
        library.index_entry(owner, item['id'], embed=lambda _: vector or [1.0,0.0])
        return item

    @patch('workbench.services.library_context.semantic.encode_query', return_value=[1.,0.])
    def test_prioritizes_principles_but_allows_unlinked_material_and_counterexample(self, _):
        principle = self.indexed('已确认原则', 'principle')
        material = self.indexed('未提炼素材')
        self.indexed('无关材料', vector=[0.,1.])
        self.indexed('别人资料', owner='b'*24)
        refs = library_context.retrieve(self.owner,'换种表达')['references']
        self.assertEqual([r['id'] for r in refs], [principle['id'], material['id']])
        self.assertEqual(refs[1]['status'], 'collected')

    @patch('workbench.services.library_context.semantic.encode_query', return_value=[1.,0.])
    def test_exclusion_removal_and_stale_index_never_return(self, _):
        first = self.indexed('排除内容')
        second = self.indexed('旧索引')
        LibraryEntry.objects.filter(pk=second['id']).update(revision=2)
        refs = library_context.retrieve(self.owner,'问题',excluded_ids=[first['id']])['references']
        self.assertEqual(refs, [])

    @patch('workbench.services.library_context.semantic.encode_query', return_value=[0.,1.])
    def test_unrelated_question_has_no_forced_personal_context(self, _):
        for _ in range(12):
            self.indexed('独处素材')
        self.assertEqual(library_context.retrieve(self.owner,'Python 语法')['references'], [])

    def test_plain_agreement_is_not_save_intent(self):
        for text in ['这个说法挺有道理','对','以后再说','记住这个观点','你应该说“记住：某事”']:
            self.assertIsNone(library_context.explicit_command(text))
        self.assertEqual(library_context.explicit_command('请记住这个观点：我重视自主安排时间。'),
                         ('principle','我重视自主安排时间。'))

    def test_stream_hides_draft_json_and_only_complete_valid_payload_can_be_saved(self):
        parser = library_context.ProposalFilter()
        output = ''.join(parser.feed(x) for x in ['自然讨论。\n<mem','ory-proposal>{"kind":"principle",','"text":"一个新认识"}</memory-proposal>'])
        tail, draft = parser.finish()
        self.assertEqual(output+tail, '自然讨论。\n')
        self.assertEqual(draft['text'],'一个新认识')
        bad = library_context.ProposalFilter()
        text = bad.feed('讨论<memory-proposal>{"kind":')
        tail, draft = bad.finish()
        self.assertIsNone(draft)
        self.assertEqual(text+tail,'讨论')

    def test_no_untrusted_history_claims_for_excluded_or_changed_records(self):
        cleaned = library_context.clean_history([
            {'role':'user','content':'之前的问题'},
            {'role':'assistant','content':'你以前收藏过相关材料，所以应该拒绝。'},
            {'role':'user','content':'现在的问题'}], discard_assistant=True)
        self.assertEqual([m['role'] for m in cleaned], ['user','user'])
