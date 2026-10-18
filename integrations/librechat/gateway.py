"""Loopback authenticated library API and provider-neutral OpenAI context gateway."""
import argparse
import base64
import hmac
import http.client
import json
import os
import re
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def main():
    root=Path(__file__).resolve().parents[2]
    os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
    os.environ.setdefault('WORKBENCH_DATA_DIR',str(root/'data'/'librechat-library'))
    import django
    django.setup()
    from django.core.management import call_command
    call_command('migrate',verbosity=0,interactive=False)
    from workbench.services.library_processing import IndexWorker
    from workbench.services.library_organization import OrganizationWorker
    from .organizer import OrganizerModel
    from .provider_config import load_provider_config
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8768)
    args=parser.parse_args()
    token=os.environ.get('LIBRECHAT_BRIDGE_TOKEN','')
    if len(token)<32:
        raise SystemExit('Missing internal service token')
    provider_config=load_provider_config()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    server.token=token
    server.provider_config=provider_config
    server.worker=IndexWorker()
    server.worker.start()
    model=OrganizerModel(provider_config)
    server.organizer=OrganizationWorker(model)
    server.organizer.start()
    try:
        server.serve_forever()
    finally:
        server.organizer.stopped.set()
        model.close()
        server.server_close()


def encoded(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf-8')


class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'

    def log_message(self,*args):
        pass

    def respond(self,status,value,*,mime='application/json',filename=None):
        data=value if isinstance(value,bytes) else encoded(value)
        self.send_response(status)
        self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        if filename:
            self.send_header('Content-Disposition',f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def dispatch(self):
        from django.db import close_old_connections
        from django.core.exceptions import ValidationError
        from workbench.services.common import Conflict
        close_old_connections()
        self.streaming=False
        try:
            host=urlsplit('//'+self.headers.get('Host','')).hostname
            if host not in {'127.0.0.1','localhost'} or self.headers.get('Origin'):
                self.respond(403,{'error':{'code':'forbidden'}})
                return
            auth=self.headers.get('Authorization','')
            if not hmac.compare_digest(auth,'Bearer '+self.server.token):
                self.respond(401,{'error':{'code':'unauthorized'}})
                return
            self.owner=self.headers.get('X-Library-Owner','')
            if not re.fullmatch(r'[a-fA-F0-9]{24}',self.owner):
                self.respond(401,{'error':{'code':'invalid_owner'}})
                return
            parsed=urlsplit(self.path)
            self.path_only=parsed.path
            self.query={key:values[0] for key,values in parse_qs(parsed.query).items()}
            body={}
            if self.command=='POST':
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=30*1024*1024:
                    self.respond(413,{'error':{'code':'invalid_size'}})
                    self.close_connection=True
                    return
                self.connection.settimeout(30)
                body=json.loads(self.rfile.read(size))
                if not isinstance(body,dict):
                    raise ValidationError('请求无效。')
            if self.path_only=='/v1/chat/completions' and self.command=='POST':
                self.chat(body)
            elif self.path_only.startswith('/library'):
                self.library(body)
            else:
                self.respond(404,{'error':{'code':'not_found'}})
        except (BrokenPipeError,ConnectionResetError):
            self.close_connection=True
        except Conflict:
            if not self.streaming:
                self.respond(409,{'error':{'code':'revision_conflict'}})
        except (ValidationError,ValueError,TypeError,KeyError):
            if not self.streaming:
                self.respond(400,{'error':{'code':'invalid_request'}})
        except Exception:
            if not self.streaming:
                self.respond(503,{'error':{'code':'library_unavailable'}})
            self.close_connection=True
        finally:
            if getattr(self,'owns_turn',False):
                from workbench.services.library_gateway import release
                release(self.owner,self.headers.get('X-Library-Conversation',''),self.headers.get('X-Library-Message',''),self.turn_lease)
            close_old_connections()

    def library(self,body):
        from django.core.exceptions import ValidationError
        from workbench.models import LibraryAsset,LibraryEntry,LibraryConversation,LibraryTurn,LibraryDraft
        from workbench.services import library,library_gateway,library_organization
        path=self.path_only.removeprefix('/library').rstrip('/')
        owner=self.owner
        post=self.command=='POST'
        result=None
        if not post and path=='':
            result={'entries':library.list_entries(owner,include_removed=True)}
        elif not post and re.fullmatch(r'/entries/\d+',path):
            result=library.detail(owner,int(path.split('/')[-1]),version=int(self.query['version']) if 'version' in self.query else None)
        elif not post and path=='/turn':
            result=library_gateway.turn_detail(owner,self.query['conversation_id'],self.query['message_id'])
        elif path=='/conversation':
            data=body if post else self.query
            state=library.conversation(owner,data['conversation_id'])
            if post:
                for field in ['selected_ids','excluded_ids']:
                    if field not in body:
                        continue
                    ids=body[field]
                    if not isinstance(ids,list) or len(ids)>(5 if field=='selected_ids' else 200) or any(type(i) is not int for i in ids):
                        raise ValidationError('参考记录无效。')
                    for entry_id in ids:
                        library._entry(owner,entry_id,include_removed=True)
                    setattr(state,field,list(dict.fromkeys(ids)))
                if 'proposals_enabled' in body:
                    if type(body['proposals_enabled']) is not bool:
                        raise ValidationError('设置无效。')
                    state.proposals_enabled=body['proposals_enabled']
                state.save()
            result={field:getattr(state,field) for field in ['selected_ids','excluded_ids','proposals_enabled']}
        elif not post and path=='/export':
            # Unbounded account export; the interactive list's page cap must not truncate backups.
            entries=[library.detail(owner,i) for i in LibraryEntry.objects.filter(owner=owner).values_list('pk',flat=True)]
            for entry in entries:
                for asset in entry['assets']:
                    original=LibraryAsset.objects.get(pk=asset['id'],entry__owner=owner)
                    with original.original.open('rb') as file:
                        asset['data_base64']=base64.b64encode(file.read()).decode('ascii')
            result={'schema_version':1,'entries':entries,
                'conversations':list(LibraryConversation.objects.filter(owner=owner).values(
                    'conversation_id','selected_ids','excluded_ids','proposals_enabled')),
                'turns':list(LibraryTurn.objects.filter(owner=owner).values('conversation_id','message_id',
                    'references','notice','retrieval_ms','completed','answer_text')),
                'drafts':[library.draft_detail(d) for d in LibraryDraft.objects.filter(owner=owner).select_related('suggestion')]}
        elif not post and re.fullmatch(r'/assets/\d+',path):
            asset=LibraryAsset.objects.filter(pk=int(path.split('/')[-1]),entry__owner=owner).first()
            if not asset:
                raise ValidationError('附件不存在。')
            with asset.original.open('rb') as file:
                extension={'image/png':'png','image/jpeg':'jpg','image/webp':'webp','text/plain':'txt','text/markdown':'md'}[asset.mime]
                self.respond(200,file.read(),mime=asset.mime,filename=f'original-{asset.pk}.{extension}')
            return
        elif post and path=='/collect':
            result=library.collect(owner,**{k:v for k,v in body.items() if k in {'text','title','url','assets','conversation_id','message_id','operation_key'}})
        elif post and path=='/sources':
            for key in ('text','title','url'):
                if key in body and not isinstance(body[key],str):
                    raise ValidationError('素材内容无效。')
            result=library.collect(owner,**{k:v for k,v in body.items() if k in {'text','title','url','assets','operation_key'}})
        elif post and path=='/principles':
            result=library.save_principle(owner,**{k:v for k,v in body.items() if k in {'statement','title','operation_key','confirmed','entry_id','expected_revision','source_ids','experience_ids','reason','when_to_use','boundaries','status'}})
        elif post and path=='/experiences':
            result=library.save_experience(owner,**{k:v for k,v in body.items() if k in {'facts','operation_key','confirmed','interpretation','uncertainty','conversation_id','message_id'}})
        elif post and re.fullmatch(r'/entries/\d+/remove',path):
            if type(body.get('removed')) is not bool:
                raise ValidationError('状态无效。')
            result=library.set_removed(owner,int(path.split('/')[2]),body['removed'],
                expected_revision=body['expected_revision'],confirmed=body.get('confirmed'))
        elif post and re.fullmatch(r'/entries/\d+/retry',path):
            item=library._entry(owner,int(path.split('/')[2]))
            if item.parse_state=='failed':
                item.parse_state='pending'
            item.index_state='pending'
            item.error_code=''
            item.save(update_fields=['parse_state','index_state','error_code'])
            if item.kind=='source':
                library_organization.request(owner,item.pk)
            self.server.worker.enqueue(owner,item.pk)
            result=library.detail(owner,item.pk)
        elif post and re.fullmatch(r'/entries/\d+/organize',path):
            entry_id=int(path.split('/')[2])
            library_organization.request(owner,entry_id)
            result=library.detail(owner,entry_id)
        elif post and re.fullmatch(r'/drafts/\d+/confirm',path):
            result=library.confirm(owner,int(path.split('/')[2]),confirmed=body.get('confirmed'),
                operation_key=body['operation_key'],text=body.get('text'))
        elif post and re.fullmatch(r'/drafts/\d+/dismiss',path):
            library.dismiss(owner,int(path.split('/')[2]))
            result={}
        else:
            self.respond(404,{'error':{'code':'not_found'}})
            return
        if post and isinstance(result,dict) and 'index_state' in result and not result.get('removed'):
            self.server.worker.enqueue(owner,result['id'])
        self.respond(200,result)

    def event(self,payload):
        data=b'data: '+(payload if isinstance(payload,bytes) else encoded(payload))+b'\n\n'
        self.wfile.write(data)
        self.wfile.flush()

    def start_stream(self):
        self.send_response(200)
        self.send_header('Content-Type','text/event-stream; charset=utf-8')
        self.send_header('Cache-Control','no-cache')
        self.send_header('Connection','close')
        self.end_headers()
        self.streaming=True
        self.close_connection=True

    def chat(self,body):
        from workbench.services import library_gateway,library_context
        from .provider_config import ProviderConfigError, load_provider_config
        prepared=library_gateway.prepare(self.owner,self.headers.get('X-Library-Conversation',''),
            self.headers.get('X-Library-Message',''),body.get('messages'),
            source_message_id=self.headers.get('X-Library-Parent') or None)
        self.owns_turn=not prepared.get('replayed',False)
        self.turn_lease=prepared.get('lease_token','')
        if 'receipt' in prepared:
            if prepared.get('saved_entry_id'):
                self.server.worker.enqueue(self.owner,prepared['saved_entry_id'])
            receipt=prepared['receipt']
            result={'id':'chatcmpl-'+uuid.uuid4().hex,'object':'chat.completion',
                'created':int(time.time()),'model':body.get('model','gpt-6.1-sol'),
                'choices':[{'index':0,'message':{'role':'assistant','content':receipt},'finish_reason':'stop'}]}
            if not body.get('stream'):
                self.respond(200,result)
                return
            self.start_stream()
            result['object']='chat.completion.chunk'
            result['choices']=[{'index':0,'delta':{'role':'assistant','content':receipt},'finish_reason':None}]
            self.event(result)
            result['choices']=[{'index':0,'delta':{},'finish_reason':'stop'}]
            self.event(result)
            self.event(b'[DONE]')
            return
        try:
            config=getattr(self.server,'provider_config',None) or load_provider_config()
        except ProviderConfigError as error:
            self.respond(503,{'error':{'code':error.code,'message':str(error)}})
            return
        if config.provider=='api':
            # The foreground lane requires provider SSE so cancellation and
            # ProposalFilter retain the same behavior as the SDK bridge.
            forwarded=config.api_payload(prepared['messages'],stream=True)
            endpoint=config.api_endpoint_path
            headers=config.api_headers
            upstream=config.api_connection()
        else:
            forwarded={**body,'messages':prepared['messages'],'stream':True}
            endpoint='/v1/chat/completions'
            headers=config.codex_headers(self.server.token)
            upstream=http.client.HTTPConnection('127.0.0.1',8767,timeout=150)
        cancelled=threading.Event()
        # Detect downstream cancellation even while upstream is thinking without emitting tokens.
        def monitor():
            import select
            while not cancelled.wait(.25):
                try:
                    ready,_,_=select.select([self.connection],[],[],0)
                    if ready and self.connection.recv(1,socket.MSG_PEEK)==b'':
                        if upstream.sock:
                            upstream.sock.shutdown(socket.SHUT_RDWR)
                        upstream.close()
                        return
                except OSError:
                    return
        threading.Thread(target=monitor,daemon=True).start()
        try:
            upstream.request('POST',endpoint,body=encoded(forwarded),headers=headers)
            response=upstream.getresponse()
            if response.status!=200:
                self.respond(response.status,{'error':{'code':'provider_unavailable','message':'模型服务暂不可用，请重试。'}})
                return
            streaming=bool(body.get('stream'))
            if streaming:
                self.start_stream()
            filtering=library_context.ProposalFilter()
            answer=''
            completed=False
            last=None
            for raw in response:
                if not raw.startswith(b'data:'):
                    continue
                data=raw[5:].strip()
                if data==b'[DONE]':
                    completed=True
                    break
                chunk=json.loads(data)
                if 'error' in chunk:
                    if streaming:
                        self.event({'error':{'code':'provider_failed','message':'回答未完成，请重试。'}})
                    return
                choices=chunk.get('choices',[])
                if not choices:
                    continue
                last=chunk
                delta=choices[0].get('delta',{})
                content=delta.get('content','')
                visible=filtering.feed(content) if isinstance(content,str) else ''
                answer+=visible
                if streaming and visible:
                    self.event({**chunk,'choices':[{'index':0,'delta':{'content':visible},'finish_reason':None}]})
            if not completed or last is None:
                if streaming:
                    self.event({'error':{'code':'incomplete_response','message':'回答中断，未保存草稿。'}})
                else:
                    self.respond(502,{'error':{'code':'incomplete_response'}})
                return
            tail,proposal=filtering.finish()
            answer+=tail
            library_gateway.finish(prepared,answer,proposal if last['choices'][0].get('finish_reason')=='stop' else None)
            if streaming:
                self.event({**last,'choices':[{'index':0,'delta':{'content':tail},'finish_reason':'stop'}]})
                self.event(b'[DONE]')
            else:
                self.respond(200,{**last,'object':'chat.completion','choices':[{'index':0,
                    'message':{'role':'assistant','content':answer},'finish_reason':'stop'}]})
        finally:
            cancelled.set()
            upstream.close()


if __name__=='__main__':
    main()
