"""Explicitly imported recorded demo; never used as an AI/provider fallback."""
import json
import uuid
from pathlib import Path
from .store import AppError
from .studies import GUIDE_SECTIONS
from .stories import lineage


class ResearchDemo:
    def import_targeting_demo(self,user,body):
        p=user['project_id'];epoch=self.store.epoch(p)
        sample=json.loads((Path(__file__).resolve().parent.parent/'samples'/'targeting-fgi-demo.json').read_text(encoding='utf-8'))
        marker=sample['scenario_version']
        old=next((r for r in self.store.list(p,'study') if r.get('demo_scenario')==marker),None)
        if old:
            if not self.accessible(p,old):raise AppError('시연 자료의 참조가 변경됐습니다. 기존 시연을 먼저 검토하세요.',409)
            return old
        rid='RSC-'+uuid.uuid4().hex;cid=str(uuid.uuid4());sid=str(uuid.uuid4());did=str(uuid.uuid4())
        disclosure=sample['disclosure']
        nature={'is_synthetic':True,'contains_synthetic':True,'evidence_nature':'SYNTHETIC','evidence_type':'synthetic','customer_validation':'unverified','demo_scenario':marker}
        source={'id':rid,'kind':'research_result','version':1,'title':'합성 FGI 시연 가설','text':disclosure+'\n'+sample['objective'],
            'category':'benchmark','state':'reviewed','dependencies':[],'source_refs':[],'evidence_ids':[],
            'sections':[],'questions':sample['questions'],'authorship':'recorded_demo','reviewed_by':user['id'],**nature}
        evidence={**source,'dependencies':[{'kind':'research_result','id':rid,'version':1}]}
        people=[self.save_persona(user,{**person,'assumptions':[disclosure],'evidence_ids':[rid],
            'observations':[{'evidence_id':rid,'quote':disclosure}]},[evidence],persist=False) for person in sample['people']]
        refs=[{'id':r['id'],'version':1} for r in people];deps=lineage(people)+evidence['dependencies']
        guide={'sections':[{'title':t,'text':v,'evidence_ids':[rid]} for t,v in zip(GUIDE_SECTIONS,sample['guide'])],
            'assumptions':[disclosure],'authorship':'po_reviewed','recorded_demo':True,'dependencies':evidence['dependencies']}
        prd_inserts=self.create_conversation(user,{'title':sample['title']},persist=False)
        conv=prd_inserts[-1][1]
        conv.update(mode='interview',persona_ids=[r['id'] for r in people],participant_persona_versions=refs,
            objective=sample['objective'],study_id=sid,active_debrief={'id':did,'version':1},planning_revision=1,**nature)
        messages=[]
        for speaker,content in sample['turns']:
            person=next((r for r in people if r['name']==speaker),None)
            messages.append({'id':str(uuid.uuid4()),'conversation_id':cid,'speaker':speaker,'text':content,'dependencies':deps,
                'evidence_ids':[rid],'assumptions':[disclosure],'observations':[],
                'role':'assistant' if person else 'user','status':'recorded_demo','model':None,
                **({'persona_id':person['id'],'persona_version':1} if person else {}),**nature})
        groups={key:[{'text':value,'evidence_ids':[rid],'message_ids':[m['id'] for m in messages]}] for key,value in sample['debrief'].items()}
        summary=disclosure+'\n\n'+'\n\n'.join(sample['debrief'].values())
        debrief={**groups,'text':summary,'summary':summary,'conversation_id':cid,'source_message_ids':[m['id'] for m in messages],
            'dependencies':deps,'review_status':'po_reviewed','reviewed_by':user['id'],'authorship':'recorded_demo',
            'evidence_ids':[rid],'assumptions':[disclosure],'observations':[],'model':None,**nature}
        study={'title':sample['title'],'objective':sample['objective'],'moderation_brief':disclosure,'research_questions':sample['questions'],
            'recruitment_criteria':'역할 차이를 비교하는 합성 시연 프로필 · 실제 리크루팅 아님','persona_ids':[r['id'] for r in people],
            'participants':refs,'target_prd_id':conv['prd_id'],'stage':'debrief','status':'completed','guide':guide,
            'conversation_id':cid,'completed_debrief':{'id':did,'version':1},'dependencies':deps,
            'created_by':user['id'],**nature}
        inserts=[('research_result',source,rid)]+[('persona',r,r['id']) for r in people]+prd_inserts[:-1]
        inserts+=[('conversation',conv,cid)]+[('message',m,m['id']) for m in messages]+[('debrief',debrief,did),('study',study,sid)]
        self.planning_actor(user)
        return self.store.write(p,inserts=inserts,expected_epoch=epoch)[-1]
