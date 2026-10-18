import uuid
import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from django.test import TestCase, TransactionTestCase
from django.core.exceptions import ValidationError
from workbench.services import library, library_gateway as gateway
from workbench.models import LibraryDraft, LibraryEntry, LibraryTurn
from workbench.services.common import Conflict


class GatewayTests(TestCase):
    owner = 'a' * 24

    def test_identity_fails_closed(self):
        for owner in ['', '{{LIBRECHAT_USER_ID}}', 'other']:
            with self.assertRaises(ValidationError):
                gateway.identity(owner, 'chat', 'message')

    def test_explicit_save_is_exact_idempotent_and_needs_no_model(self):
        messages = [{'role':'user','content':'记住：我可以选择独处，不必为了合群参加所有活动。'}]
        first = gateway.prepare(self.owner, 'chat', 'msg', messages)
        second = gateway.prepare(self.owner, 'chat', 'msg', messages)
        self.assertIn('已保存', first['receipt'])
        self.assertEqual(first['receipt'], second['receipt'])
        self.assertEqual(LibraryEntry.objects.count(), 1)

    @patch('workbench.services.library_context.retrieve', return_value={'references':[]})
    def test_completed_turn_replays_and_inflight_or_changed_request_is_rejected(self, retrieve):
        messages=[{'role':'user','content':'普通问题'}]
        first=gateway.prepare(self.owner,'chat','msg',messages)
        with self.assertRaises(Conflict):
            gateway.prepare(self.owner,'chat','msg',messages)
        gateway.finish(first,'完整的回答',None)
        replay=gateway.prepare(self.owner,'chat','msg',messages)
        self.assertEqual(replay['receipt'],'完整的回答')
        self.assertTrue(replay['replayed'])
        self.assertEqual(retrieve.call_count,1)
        with self.assertRaises(Conflict):
            gateway.prepare(self.owner,'chat','msg',[{'role':'user','content':'不同内容'}])

    def test_regenerating_explicit_command_reuses_original_user_operation(self):
        messages=[{'role':'user','content':'记住：确认内容只保存一次。'}]
        gateway.prepare(self.owner,'chat','assistant1',messages,source_message_id='user1')
        gateway.prepare(self.owner,'chat','assistant2',messages,source_message_id='user1')
        self.assertEqual(LibraryEntry.objects.count(),1)

    @patch('workbench.services.library_context.retrieve', return_value={'references':[]})
    def test_expired_request_cannot_release_or_overwrite_new_lease(self, _):
        from django.utils import timezone
        from datetime import timedelta
        messages=[{'role':'user','content':'普通问题'}]
        first=gateway.prepare(self.owner,'chat','msg',messages)
        LibraryTurn.objects.filter(pk=first['turn_id']).update(processing_until=timezone.now()-timedelta(seconds=1))
        second=gateway.prepare(self.owner,'chat','msg',messages)
        gateway.release(self.owner,'chat','msg',first['lease_token'])
        self.assertIsNotNone(LibraryTurn.objects.get(pk=first['turn_id']).processing_until)
        with self.assertRaises(Conflict):
            gateway.finish(first,'旧回答',{'kind':'principle','text':'旧建议'})
        gateway.finish(second,'新回答',None)
        self.assertEqual(LibraryTurn.objects.get(pk=first['turn_id']).answer_text,'新回答')
        self.assertFalse(LibraryDraft.objects.exists())

    @patch('workbench.services.library_context.retrieve', return_value={'references':[]})
    def test_agreement_is_normal_chat_and_hallucinated_evidence_is_rejected(self, _):
        prepared = gateway.prepare(self.owner, 'chat', 'msg', [{'role':'user','content':'有道理'}])
        self.assertNotIn('receipt', prepared)
        entry = library.collect(self.owner,text='虽属于本人，但本轮没有检索到',operation_key=str(uuid.uuid4()))
        gateway.finish(prepared, '正常回答', {'kind':'principle','text':'一个新认识', 'source_ids':[entry['id']]})
        self.assertFalse(LibraryDraft.objects.exists())

    @patch('workbench.services.library_context.retrieve', return_value={'references':[]})
    def test_stale_reference_answer_is_omitted_but_unrelated_history_remains(self, retrieve):
        item = library.collect(self.owner,text='素材',operation_key=str(uuid.uuid4()))
        retrieve.return_value = {'references':[{'id':item['id'],'revision':1,'kind':'source'}]}
        prepared = gateway.prepare(self.owner,'chat','msg',[{'role':'user','content':'讨论'}])
        gateway.finish(prepared,'依据旧材料的回答',None)
        library.set_removed(self.owner,item['id'],True,expected_revision=1,confirmed=True)
        retrieve.return_value = {'references':[]}
        prepared = gateway.prepare(self.owner,'chat','msg2',[
            {'role':'assistant','content':'依据旧材料的回答'},
            {'role':'assistant','content':'普通回答'}, {'role':'user','content':'继续'}])
        contents = [m['content'] for m in prepared['messages']]
        self.assertNotIn('依据旧材料的回答',contents)
        self.assertIn('普通回答',contents)

    @patch('workbench.services.library_context.retrieve', return_value={'references':[]})
    def test_natural_confirmation_binds_to_visible_draft_and_branch(self, _):
        prepared=gateway.prepare(self.owner,'chat','first',[{'role':'user','content':'讨论新认识'}])
        gateway.finish(prepared,'可以减少为了合群而勉强自己的次数。',
            {'kind':'principle','text':'根据自己的精力自主选择参加聚会。'})
        wrong=gateway.prepare(self.owner,'chat','other-branch',[
            {'role':'assistant','content':'另一个分支的无关回答'}, {'role':'user','content':'记住这条'}])
        self.assertNotIn('receipt',wrong)
        correct=gateway.prepare(self.owner,'chat','confirm',[
            {'role':'assistant','content':'可以减少为了合群而勉强自己的次数。'}, {'role':'user','content':'记住这条'}])
        self.assertIn('已保存',correct['receipt'])
        self.assertEqual(LibraryEntry.objects.count(),1)

    def test_image_collect_command_saves_without_a_model(self):
        import base64
        from io import BytesIO
        from PIL import Image
        raw=BytesIO()
        Image.new('RGB',(20,20),'white').save(raw,format='PNG')
        prepared=gateway.prepare(self.owner,'chat','image',[
            {'role':'user','content':[{'type':'text','text':'先收着'},
                {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(raw.getvalue()).decode()}}]}])
        self.assertIn('已保存为素材',prepared['receipt'])
        entry=LibraryEntry.objects.get()
        self.assertEqual(entry.parse_state,'pending')
        self.assertEqual(entry.assets.count(),1)


class GatewayHTTPTests(TransactionTestCase):
    def setUp(self):
        from integrations.librechat.gateway import Handler
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.token='test-internal-token'
        self.server.worker=type('Worker',(),{'enqueue':lambda *args: None})()
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self,method,path,body=None,owner='a'*24,token='test-internal-token',extra=None):
        client=http.client.HTTPConnection('127.0.0.1',self.server.server_port)
        headers={'Authorization':'Bearer '+token,'X-Library-Owner':owner,**(extra or {})}
        client.request(method,path,body=json.dumps(body).encode() if body else None,headers=headers)
        response=client.getresponse()
        result=response.status,json.loads(response.read())
        client.close()
        return result

    def test_auth_origin_and_tenant_isolation(self):
        item=library.collect('a'*24,text='本人的私有素材',operation_key=str(uuid.uuid4()))
        self.assertEqual(self.request('GET','/library',token='wrong')[0],401)
        self.assertEqual(self.request('GET','/library',extra={'Origin':'http://evil.invalid'})[0],403)
        self.assertEqual(self.request('GET',f'/library/entries/{item["id"]}',owner='b'*24)[0],400)
        self.assertEqual(self.request('GET','/library',owner='b'*24)[1]['entries'],[])

    def test_conflict_and_exact_save_receipt(self):
        item=library.collect('a'*24,text='资料',operation_key=str(uuid.uuid4()))
        self.assertEqual(self.request('POST',f'/library/entries/{item["id"]}/remove',
            {'removed':True,'expected_revision':3,'confirmed':True})[0],409)
        status,result=self.request('POST','/v1/chat/completions',{'messages':[
            {'role':'user','content':'记住：每次选择都应考虑自己的真实需要。'}], 'stream':False},
            extra={'X-Library-Conversation':'chat','X-Library-Message':'message'})
        self.assertEqual(status,200)
        self.assertIn('已保存',result['choices'][0]['message']['content'])

    def test_direct_source_and_manual_organization_are_owner_scoped(self):
        from workbench.services.library_organization import Job
        status, item = self.request('POST','/library/sources',{'text':'直接新增素材示例',
            'owner':'b'*24, 'operation_key':str(uuid.uuid4())})
        self.assertEqual(status,200)
        self.assertEqual(item['organization']['state'],'pending')
        self.assertEqual(LibraryEntry.objects.get(pk=item['id']).owner,'a'*24)
        Job.objects.filter(entry_id=item['id']).update(state='failed')
        self.assertEqual(self.request('POST',f'/library/entries/{item["id"]}/organize',
            {'retry':True},owner='b'*24)[0],400)
        status, retry = self.request('POST',f'/library/entries/{item["id"]}/organize',{'retry':True})
        self.assertEqual(status,200)
        self.assertEqual(retry['organization']['state'],'pending')
