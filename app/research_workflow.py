"""Stage 1 outputs consumed as reviewed planning context by Stage 2."""
import html
import json
import os
import re
import urllib.parse
import urllib.request
import uuid
from .contracts import text, optional, strings, revision, public_url, validate_citations, validate_answer, inline_text
from .model import NoRedirect
from .stories import lineage
from .store import AppError
from .research_provenance import derived_nature, synthetic_record


def internal_amazon_request(value):
    return bool(re.search(r'(?:amazon|아마존).{0,35}(?:내부|기밀|비공개|confidential|internal)|(?:confidential|internal).{0,20}amazon',value,re.I))


class ResearchWorkflow:
    def research_results(self, user):
        return [r if self.accessible(user['project_id'],r) else self.redacted(r)
            for r in self.store.list(user['project_id'],'research_result')]

    def analyze_existing_service(self, user, body):
        p=user['project_id'];epoch=self.store.epoch(p)
        prompt=text(body,'prompt',5000)
        refs=body.get('asset_refs',[])
        if not isinstance(refs,list) or not 1<=len(refs)<=10:raise AppError('기획 문서 1~10개를 선택하세요.')
        assets=[self.asset(user,text(r,'id',80),revision({'expected_version':r.get('version')})) for r in refs]
        if any(r['media_type']!='document' or r['purpose']!='existing_service' for r in assets):
            raise AppError('기존 서비스 PRD·매뉴얼 문서를 선택하세요.')
        documents=[{'asset_id':r['id'],'version':r['version'],'source_nature':r.get('source_nature','UNVERIFIED'),'text':self.store.decrypt(r['encrypted_text'])} for r in assets]
        if sum(len(r['text']) for r in documents)>120000:raise AppError('분석 문서를 120,000자 이하로 나눠 주세요.')
        if internal_amazon_request(prompt):raise AppError('아마존의 비공개 내부 정보에 대해서는 답변하기 어렵습니다. 공개 자료를 기준으로 질문해 주세요.',403)
        result=self.generate('service_baseline',{'prompt':prompt,'documents':documents})
        sections=[]
        if not isinstance(result['sections'],list) or not 1<=len(result['sections'])<=16:raise AppError('서비스 분석 문단을 확인하세요.',502)
        allowed={a['id'] for a in assets}
        for section in result['sections']:
            ids=strings(section['asset_ids'],10,80)
            if not ids or not set(ids).issubset(allowed):raise AppError('기존 서비스 분석의 원본 연결을 확인하지 못했습니다.',502)
            content=text(section,'text',15000);validate_citations(content,ids)
            sections.append({'title':text(section,'title',200),'text':content,'asset_ids':ids})
        lines=['# '+text(result,'title',200)]
        for s in sections:lines.extend(['','## '+s['title'],s['text'],' '.join('['+rid+']' for rid in s['asset_ids'])])
        unknowns=strings(result['unknowns'],40,3000)
        lines+=['확인 필요: '+q for q in unknowns]
        self.planning_actor(user)
        return self.store.write(p,inserts=[('research_result',{'title':result['title'],'category':'existing_service',
            'text':'\n'.join(lines),'sections':sections,'questions':unknowns,'state':'draft','evidence_ids':[],
            'source_refs':refs,'dependencies':lineage(assets),'prompt':prompt,'model':self.model.model,
            'evidence_type':'planning_context',**derived_nature(assets),'created_by':user['id']},'RSC-'+uuid.uuid4().hex)],expected_epoch=epoch)[0]

    def save_research_result(self, user, body):
        p=user['project_id'];epoch=self.store.epoch(p)
        category=body.get('category')
        if category not in ('voc','fgi_actual','benchmark'):raise AppError('리서치 결과 유형을 확인하세요.')
        assets=[];messages=[]
        if body.get('asset_id'):
            asset=self.asset(user,body['asset_id'],revision({'expected_version':body.get('asset_version')}))
            if category=='fgi_actual' and asset['purpose']!='actual_fgi':raise AppError('실제 FGI 결과로 제출한 기획 문서를 선택하세요.')
            assets.append(asset)
        if body.get('conversation_id'):
            conv=self.conversation(user,body['conversation_id'])
            ids=strings(body.get('message_ids',[]),100,80)
            messages=[m for m in conv['messages'] if m['id'] in ids and not m.get('redacted') and m.get('evidence_ids')]
            if len(messages)!=len(ids) or not messages:raise AppError('근거가 있는 분석 답변을 선택하세요.',409)
        if category=='fgi_actual' and (not assets or messages):raise AppError('실제 FGI 등록에는 실제 조사 결과 문서를 연결하세요. 가상 대화를 실제 결과로 등록할 수 없습니다.')
        if not assets and not messages:raise AppError('리서치 원본 또는 분석 답변을 연결하세요.')
        if category=='voc':self.require_actual_voc_messages(user,messages)
        content=text(body,'text',50000)
        evidence_ids=sorted({rid for m in messages for rid in m['evidence_ids']})
        validate_citations(content,set(evidence_ids)|{a['id'] for a in assets})
        fields={'title':text(body,'title',200),'category':category,'text':content,'state':'draft',
            'evidence_ids':evidence_ids,'dependencies':lineage(assets)+[d for m in messages for d in m['dependencies']],
            'source_refs':[{'id':a['id'],'version':a['version']} for a in assets],
            'message_ids':[m['id'] for m in messages],'evidence_type':'submitted_fgi_report' if category=='fgi_actual' else 'analysis',
            'customer_validation':'unverified','created_by':user['id']}
        fields.update(derived_nature(assets+messages+[e for e in self.knowledge(p) if e['id'] in evidence_ids]))
        self.planning_actor(user)
        return self.store.write(p,inserts=[('research_result',fields,'RSC-'+uuid.uuid4().hex)],expected_epoch=epoch)[0]

    def review_research_result(self, user, body):
        p=user['project_id'];epoch=self.store.epoch(p)
        row=self.store.get(p,'research_result',text(body,'result_id',80))
        if row['category']=='voc':
            messages=[self.store.get(p,'message',rid) for rid in row.get('message_ids',[])]
            self.require_actual_voc_messages(user,messages)
        if not self.accessible(p,row):raise AppError('분석 결과의 원본·근거가 변경됐습니다.',409)
        content=text(body,'text',50000)
        validate_citations(content,set(row['evidence_ids'])|{r['id'] for r in row.get('source_refs',[])})
        actual=body.get('actual_customer_data',row.get('actual_customer_data',False))
        if type(actual) is not bool or (actual and row['category']!='fgi_actual'):
            raise AppError('실제 고객 조사 여부를 확인하세요.')
        sources=[self.asset(user,r['id'],r['version']) for r in row.get('source_refs',[])]
        nature=derived_nature([row]+sources)
        if actual and nature['contains_synthetic']:raise AppError('합성 자료를 실제 고객 조사로 확인할 수 없습니다.',409)
        changes={**nature,'text':content,'state':'reviewed','actual_customer_data':actual,
            'reviewed_by':user['id'],'review_reason':optional(body,'reason',3000),
            'canonical_version':'research-result.v2'}
        if row['category']=='existing_service':
            if content!=row['text']:changes['context_snapshot']=None
            # The editable text is canonical. Never send superseded generated
            # sections alongside the PO's replacement text to another model.
            changes['sections']=[{'title':'PO 검토본','text':content,
                'asset_ids':[r['id'] for r in row.get('source_refs',[])]}]
            changes['questions']=strings(body.get('questions',row.get('questions',[])),40,3000)
        self.planning_actor(user)
        return self.store.write(p,updates=[('research_result',row['id'],changes,revision(body))],expected_epoch=epoch)[0]

    def public_research(self, user, body):
        query=text(body,'query',600)
        if len(query.split())>75:raise AppError('검색어를 75단어 이하로 줄여 주세요.')
        if internal_amazon_request(query):
            raise AppError('아마존의 비공개 내부 정보에 대해서는 답변하기 어렵습니다. 공개 자료를 기준으로 질문해 주세요.',403)
        key=os.getenv('NPD_PUBLIC_SEARCH_KEY','')
        if not key:raise AppError('공개 웹 검색 API가 설정되지 않았습니다. 서버에 NPD_PUBLIC_SEARCH_KEY를 설정하세요.',503)
        request=urllib.request.Request('https://api.search.brave.com/res/v1/web/search?'+urllib.parse.urlencode({'q':query,'count':10}),
            headers={'Accept':'application/json','X-Subscription-Token':key})
        try:
            with urllib.request.build_opener(NoRedirect()).open(request,timeout=20) as response:
                raw=response.read(2000001)
                if len(raw)>2000000:raise ValueError()
                payload=json.loads(raw)
            results=[]
            for row in payload.get('web',{}).get('results',[])[:10]:
                url=public_url(row.get('url',''))
                if not url:continue
                clean=lambda value:html.unescape(re.sub('<[^>]+>','',str(value)))[:3000]
                results.append({'id':str(uuid.uuid4()),'kind':'public_candidate','version':1,'title':clean(row.get('title','')),'text':clean(row.get('description','')),'url':url,
                    'evidence_type':'public_search_candidate','review_status':'not_reviewed'})
            self.planning_actor(user)
            saved=self.store.put(user['project_id'],'public_search',{'query':query,'results':results,'dependencies':[]})
            return {'search_id':saved['id'],'version':saved['version'],'query':query,'results':results,'stored':True,
                'disclosure':'공개 웹 검색 후보입니다. 원문·시점·적용 조건을 검토한 후 리서치 근거로 등록하세요.'}
        except AppError:raise
        except Exception:raise AppError('공개 웹 검색을 완료하지 못했습니다. 연결·API 설정을 확인하세요.',502)


    def analyze_public_research(self, user, body):
        p=user['project_id'];epoch=self.store.epoch(p)
        prompt=text(body,'prompt',5000)
        if internal_amazon_request(prompt):
            raise AppError('아마존의 비공개 내부 정보에 대해서는 답변하기 어렵습니다. 공개 자료를 기준으로 질문해 주세요.',403)
        search=self.store.get(p,'public_search',text(body,'search_id',80))
        if search['version']!=revision(body):raise AppError('검색 후보 버전이 변경됐습니다.',409)
        ids=strings(body.get('candidate_ids',[]),10,80)
        evidence=[r for r in search['results'] if r['id'] in ids]
        if not evidence or len(evidence)!=len(ids):raise AppError('분석할 공개 검색 후보를 선택하세요.')
        result=validate_answer(self.generate('public_research_analysis',{'question':prompt,'evidence':evidence,
            'limits':'Search snippets only. Full pages have not been read; sources, date, scope and conflicts require PO review.'}),evidence)
        content=inline_text(result)+'\n\n검색 요약문 기반 초안입니다. 원문·시점·적용 조건 검토가 필요합니다.'
        for item in evidence:content+='\n'+item['title']+' — '+item['url']
        self.planning_actor(user)
        return self.store.write(p,inserts=[('research_result',{'title':search['query'],'category':'benchmark','text':content,
            'state':'draft','evidence_ids':result['evidence_ids'],'source_refs':[], 'public_sources':evidence,
            'dependencies':lineage([search]),'evidence_type':'public_search_analysis','customer_validation':'unverified',
            'prompt':prompt,'assumptions':result['assumptions'],'model':self.model.model,'created_by':user['id']},
            'RSC-'+uuid.uuid4().hex)],expected_epoch=epoch)[0]
