"""Opt-in real Bedrock HTTP workflow gate with synthetic input and separate PO login."""
import argparse
import base64
import hashlib
import http.cookiejar
import json
import secrets
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.model import BedrockModel
from app.server import Server
from app.service import Service
from app.store import Store, AppError


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AppError('Validation endpoints must not redirect credentials or requests.', 400)


class Client:
    def __init__(self, origin):
        self.origin=origin;self.csrf=''
        self.cookies = http.cookiejar.CookieJar()
        self.opener=urllib.request.build_opener(NoRedirect(), urllib.request.HTTPCookieProcessor(self.cookies))

    def call(self, route, body=None):
        req=urllib.request.Request(self.origin+route, data=None if body is None else json.dumps(body).encode(),
            headers={'Content-Type':'application/json','Origin':self.origin,'X-CSRF-Token':self.csrf})
        with self.opener.open(req,timeout=1800) as response:
            return json.load(response)

    def login(self, email, password):
        self.csrf=self.call('/api/login',{'email':email,'password':password})['csrf']


class GuardedModel:
    """Observe only whether private markers reach a PO-facing task, never log inputs."""
    def __init__(self, model, marker):
        self.model_delegate=model;self.marker=marker;self.tasks=[]
    def __getattr__(self,name):return getattr(self.model_delegate,name)
    def generate(self,task,payload):
        if task!='insights' and self.marker in json.dumps(payload,ensure_ascii=False):
            raise AppError('Private input reached a PO-facing model task.',500)
        result=self.model_delegate.generate(task,payload);self.tasks.append(task);return result


def require(condition):
    if not condition:
        raise AppError('Workflow validation condition failed.', 500)


def exercise_http(owner, po, marker, steps):
    def call(client,route,body=None):
        t=time.perf_counter();result=client.call(route,body)
        steps.append({'route':route,'role':'owner' if client is owner else 'po','seconds':round(time.perf_counter()-t,3)})
        return result
    def file(name,**extra):
        return {'filename':name,'content_base64':base64.b64encode((ROOT/'samples'/name).read_bytes()).decode(),**extra}
    call(owner,'/api/model/test',{})
    call(owner,'/api/features/import',json.loads((ROOT/'samples/features.json').read_text()))
    raw=(ROOT/'samples/research.md').read_text()+'\nOwner-only control marker; never include in insights: '+marker
    source=call(owner,'/api/research/upload',{'filename':'synthetic-private.md','title':'합성 검증 리서치','content_base64':base64.b64encode(raw.encode()).decode()})
    try:po.call('/api/research/raw/'+source['id'])
    except urllib.error.HTTPError as exc:require(exc.code==403)
    else:raise AssertionError('PO raw access allowed')
    drafts=call(owner,'/api/research/extract',{'source_id':source['id'],'evidence_type':'synthetic'})
    require(drafts and marker not in json.dumps(drafts,ensure_ascii=False))
    require(not any(e['kind']=='insight' for e in call(po,'/api/evidence')))
    call(owner,'/api/insights/release',{'insight_ids':[d['id'] for d in drafts],'published':True})
    call(owner,'/api/voc/upload',file('voc.csv',source_name='synthetic-live-validation'))
    voc=call(po,'/api/voc')['records']
    call(po,'/api/voc/classify',{'voc_ids':[v['id'] for v in voc[:6]]})
    prd=call(po,'/api/prds/import',file('existing_service_prd.md',title='소재 분석 기준 기획'))
    research=call(po,'/api/conversations',{'title':'소재 비교 리서치','mode':'research','prd_id':prd['id']})
    call(po,'/api/chat',{'conversation_id':research['id'],'message':'소재 성과 비교를 위한 요구사항과 확인할 가설을 근거와 함께 설명해 주세요.','request_id':'research'})
    people=[]
    for name,segment in [('소형셀러검증','전담 분석가 없는 소규모 광고주 소재 리포트'),('대행사검증','고객 승인과 소재 변경 근거가 필요한 대행사 운영자')]:
        people.append(call(po,'/api/personas/generate',{'name':name,'segment':segment}))
    conv=call(po,'/api/conversations',{'title':'가상 광고주 FGI','mode':'interview','persona_ids':[p['id'] for p in people],'prd_id':prd['id']})
    tags=' '.join('@'+p['alias'] for p in people)
    first=call(po,'/api/chat',{'conversation_id':conv['id'],'message':tags+' 소재 추천 근거에서 어떤 비교 조건과 승인 절차가 필요합니까?','request_id':'first'})
    call(po,'/api/conversations/state',{'conversation_id':conv['id'],'round_type':'challenge'})
    second=call(po,'/api/chat',{'conversation_id':conv['id'],'message':'앞선 제안에 반대하는 조건과 판단을 보류해야 할 경우를 설명해 주세요.','request_id':'challenge'})
    require(len([m for m in second['messages'] if m.get('is_synthetic')])==4)
    require(len(second['messages'])>len(first['messages']))
    call(po,'/api/conversations/decisions',{'conversation_id':conv['id'],'text':'자동 광고 집행을 제외하고 비교 조건과 승인 근거를 우선한다.'})
    call(po,'/api/debriefs',{'conversation_id':conv['id']})
    proposal=call(po,'/api/proposals',{'conversation_id':conv['id']})
    applied=call(po,'/api/proposals/decision',{'proposal_id':proposal['id'],'state':'accepted'})
    require(applied['applied_prd_version']==2)
    export=call(po,'/api/export/'+conv['id']+'?format=markdown')
    require(export['text'] and marker not in export['text'])
    require(marker not in json.dumps(call(po,'/api/bootstrap'),ensure_ascii=False))
    require('['+drafts[0]['id']+']' in export['text'] or any('['+e['id']+']' in export['text'] for e in call(po,'/api/evidence')))
    before=len(call(po,'/api/evidence'))
    call(owner,'/api/insights/release',{'insight_ids':[d['id'] for d in drafts],'published':False})
    require(len(call(po,'/api/evidence'))==before-len(drafts))
    require(not call(po,'/api/proposals'))


def run_workflow(model):
    steps=[];started=time.perf_counter();marker='OWNER_PRIVATE_GATE_'+secrets.token_hex(12)
    guarded=GuardedModel(model,marker)
    try:
        with tempfile.TemporaryDirectory(prefix='npd-live-') as tmp:
            store=Store(tmp);project='synthetic-live-validation'
            passwords={r:secrets.token_urlsafe(24) for r in ('owner','po')}
            for role in passwords:store.create_user(role+'@live.example.test',passwords[role],role,project)
            service=Service(store,guarded);server=Server(('127.0.0.1',0),service)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                owner,po=Client(server.origin),Client(server.origin)
                for role,client in [('owner',owner),('po',po)]:client.login(role+'@live.example.test',passwords[role])
                exercise_http(owner, po, marker, steps)
                return {'status':'PASS','steps':steps,'model_tasks':guarded.tasks,'seconds':round(time.perf_counter()-started,2),
                    'http_authentication':True,'personas':2,'interview_rounds':2,'persona_responses':4,'prd_version':2,
                    'raw_protection_checked':True,'revocation_checked':True,
                    'scope':'Actual supplied provider through HTTP, separate owner/PO sessions; semantic accuracy and customer usefulness still need PO review.'}
            finally:
                server.shutdown();server.server_close();thread.join(timeout=5)
    except urllib.error.HTTPError as exc:
        try:error=json.load(exc)
        except Exception:error={}
        return {'status':'FAIL','completed_steps':steps,'http_status':exc.code,'error_code':error.get('code','domain_or_model_validation'),'reason':'No customer/model input or generated output is logged.'}
    except Exception:
        return {'status':'FAIL','completed_steps':steps,'reason':'Workflow or privacy assertion failed. No input contents logged.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true',help='Send synthetic fixtures to the real, paid Bedrock provider')
    parser.add_argument('--passes',type=int,default=1,choices=range(1,4))
    args=parser.parse_args();model=BedrockModel()
    if not args.run or not model.configured:
        print(json.dumps({'status':'NOT_RUN','reason':'Use --run with BEDROCK_MODEL_ID, region and AWS authentication. No live verification performed.'}));return 2
    for i in range(args.passes):
        result=run_workflow(model);result['pass']=i+1;print(json.dumps(result,ensure_ascii=False),flush=True)
        if result['status']!='PASS':return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
