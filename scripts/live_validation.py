"""Opt-in live provider gate using only repository synthetic fixtures and an ephemeral DB."""
import argparse
import base64
import json
import sys
import tempfile
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.model import create_model
from app.service import Service
from app.store import Store,AppError


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run',action='store_true',help='Send synthetic fixtures to the configured paid provider');args=parser.parse_args()
    model=create_model()
    if not args.run or not model.configured:
        print(json.dumps({'status':'SKIPPED','reason':'Use --run with provider model and credentials configured. No live verification performed.'}));return 2
    steps=[];started=time.perf_counter()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            service=Service(Store(tmp),model);user={'id':'live-validation','role':'owner','project_id':'synthetic-live-validation'}
            def post(path,body):
                result=service.post(user,path,body);steps.append(path);return result
            def file(name,**extra):return {'filename':name,'content_base64':base64.b64encode((ROOT/'samples'/name).read_bytes()).decode(),**extra}
            post('/api/features/import',json.loads((ROOT/'samples/features.json').read_text()))
            source=post('/api/research/upload',file('research.md',title='합성 검증 리서치'))
            drafts=post('/api/research/extract',{'source_id':source['id'],'evidence_type':'synthetic'})
            post('/api/insights/release',{'insight_ids':[d['id'] for d in drafts],'published':True})
            post('/api/voc/upload',file('voc.csv',source_name='synthetic-live-validation'))
            post('/api/voc/classify',{'voc_ids':[v['id'] for v in service.voc_records(user['project_id'])[:6]]})
            persona=post('/api/personas/generate',{'name':'검증광고주','segment':'소규모 광고주 소재 리포트'})
            conv=post('/api/conversations',{'title':'합성 소재 분석 기획','mode':'interview','persona_ids':[persona['id']]})
            post('/api/chat',{'conversation_id':conv['id'],'message':'@검증광고주 소재 추천 근거에서 확인할 조건은 무엇인가요?','request_id':'live-first-turn'})
            post('/api/debriefs',{'conversation_id':conv['id']})
            proposal=post('/api/proposals',{'conversation_id':conv['id']})
            post('/api/proposals/decision',{'proposal_id':proposal['id'],'state':'accepted'})
            package=service.get(user,'/api/export/'+conv['id']+'?format=markdown')
            assert package['text'] and '[' in package['text']
        print(json.dumps({'status':'PASS','steps':steps,'seconds':round(time.perf_counter()-started,2),'scope':'Live provider contract and workflow; advertiser representativeness and semantic correctness still require PO evaluation.'},ensure_ascii=False));return 0
    except (AppError,AssertionError):
        print(json.dumps({'status':'FAIL','completed_steps':steps,'reason':'Provider or domain validation failed. No input contents logged.'}));return 1

if __name__=='__main__':sys.exit(main())
