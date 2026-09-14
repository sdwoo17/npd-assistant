"""Six PO-authored PRD stages with independent prompt-driven draft variants."""
import json
import re
import uuid
from .contracts import text, optional, strings, revision, validate_citations, dependency_map
from .story_contracts import DEFINITION_STAGES, objects
from .stories import lineage
from .store import AppError, timestamp


class Definitions:
    def definition_context(self, user):
        p=user['project_id']
        definitions=[]
        for selected in self.store.list(p,'planning_selection'):
            row=self.store.get(p,'definition',selected['document_id'])
            if row['version']!=selected['document_version'] or not self.accessible(p,row):
                raise AppError('선택한 기준 문서가 변경됐습니다. 수정안을 검토·확정한 뒤 기획을 이어가세요.',409)
            definitions.append(row)
        stories=[r for r in self.story_list(user) if not r.get('redacted') and r['definition_status']=='confirmed']
        research=[r for r in self.store.list(p,'research_result') if r.get('state')=='reviewed' and self.accessible(p,r)]
        debriefs=[]
        for conv in self.store.list(p,'conversation'):
            ref=conv.get('active_debrief')
            if ref:
                row=self.store.get(p,'debrief',ref['id'])
                if row['version']==ref['version'] and self.accessible(p,row):debriefs.append(row)
        records=definitions+stories+research+debriefs
        if len(records)>100 or len(json.dumps(records,ensure_ascii=False))>140000:
            raise AppError('기획 맥락이 큽니다. 사용하지 않는 문서를 보류하거나 범위를 나눠 주세요.',409)
        return {'definitions':definitions,'stories':stories,'research_results':research,'reviewed_fgi':debriefs}

    def definition(self, user, rid):
        row=self.store.get(user['project_id'],'definition',rid)
        if not self.accessible(user['project_id'],row):
            raise AppError('기획 문서의 참조 자료가 변경됐습니다. 현재 자료로 재검토하세요.',409)
        return row

    def definition_list(self, user):
        return {'stages':DEFINITION_STAGES,'documents':[r if self.accessible(user['project_id'],r) else self.redacted(r)
            for r in self.store.list(user['project_id'],'definition')]}

    def definition_fields(self, user, body):
        stage=body.get('stage')
        if stage not in DEFINITION_STAGES or stage=='stories':
            raise AppError('기획 단계를 선택하세요. 사용자 스토리는 스토리 편집기에서 작성합니다.')
        sections=[];seen=set()
        for row in objects(body.get('sections',[]),80,'문서 문단'):
            rid=text(row,'id',80)
            if rid in seen or not re.fullmatch(r'[\w-]+',rid):raise AppError('문단 ID를 확인하세요.')
            seen.add(rid)
            sections.append({'id':rid,'title':text(row,'title',200),'text':optional(row,'text',20000),
                'evidence_ids':strings(row.get('evidence_ids',[]),100,80)})
        if sum(len(s['text']) for s in sections)>100000:raise AppError('문서는 100,000자 이하로 작성하세요.')
        evidence={e['id']:e for e in self.knowledge(user['project_id'])}
        evidence.update({r['id']:r for r in self.research_results(user) if not r.get('redacted') and r.get('state')=='reviewed'})
        ids={rid for s in sections for rid in s['evidence_ids']}
        if not ids.issubset(evidence):raise AppError('현재 공개 근거를 연결하세요.',409)
        for s in sections:validate_citations(s['text'],s['evidence_ids'])
        questions=[]
        for q in objects(body.get('questions',[]),40,'확인 질문'):
            answer=optional(q,'answer',3000);status=q.get('status','unanswered')
            if status not in ('unanswered','answered','excluded') or (status!='unanswered' and not answer):
                raise AppError('확인 질문의 답변 또는 제외 사유를 기록하세요.')
            questions.append({'text':text(q,'text',3000),'answer':answer,'status':status})
        stories=[]
        for ref in objects(body.get('story_refs',[]),100,'스토리 연결'):
            if type(ref.get('version')) is not int:raise AppError('스토리 버전을 지정하세요.')
            story=self.story(user,text(ref,'id',80),ref['version'])
            if story['definition_status']!='confirmed':raise AppError('확정된 스토리를 연결하세요.',409)
            stories.append(story)
        fields={'stage':stage,'title':text(body,'title',200),'sections':sections,'questions':questions,
            'assumptions':strings(body.get('assumptions',[]),40,3000),
            'story_refs':[{'id':r['id'],'version':r['version']} for r in stories]}
        return fields,lineage(stories+[evidence[rid] for rid in ids])

    def save_definition(self, user, body):
        p=user['project_id'];epoch=self.store.epoch(p)
        old=self.definition(user,body['document_id']) if body.get('document_id') else None
        if any(key in body for key in ('state','confirmed_version','confirmed_by','dependencies')):
            raise AppError('문서 상태는 명시적 검토 동작으로 변경하세요.')
        fields,deps=self.definition_fields(user,{**(old or {}),**body})
        fields.update(state='draft',dependencies=(old.get('dependencies',[]) if old else [])+deps,
            authored_by=user['id'],confirmed_version=old.get('confirmed_version') if old else None)
        self.planning_actor(user)
        if old:return self.store.write(p,updates=[('definition',old['id'],fields,revision(body))],expected_epoch=epoch)[0]
        return self.store.write(p,inserts=[('definition',fields,'DOC-'+uuid.uuid4().hex)],expected_epoch=epoch)[0]

    def generate_definition(self, user, body):
        p=user['project_id'];epoch=self.store.epoch(p)
        stage=body.get('stage');prompt=text(body,'prompt',5000)
        if stage not in DEFINITION_STAGES or stage=='stories':raise AppError('문서 기획 단계를 확인하세요.')
        context=self.definition_context(user)
        evidence=self.search(p,prompt)[0]
        prior=[r for r in self.store.list(p,'definition') if r.get('stage')==stage and self.accessible(p,r)][-3:]
        result=self.generate('definition_draft',{'stage':DEFINITION_STAGES[stage],'prompt':prompt,
            'context':context,'evidence':evidence,'previous_variants':prior})
        fields,deps=self.definition_fields(user,{**result,'stage':stage,
            'questions':[{'text':q,'status':'unanswered','answer':''} for q in result['questions']],
            'story_refs':[{'id':r['id'],'version':r['version']} for r in context['stories']]})
        records=[r for group in context.values() for r in group]+evidence
        fields.update(state='draft',dependencies=lineage(records)+deps+dependency_map(prior),prompt=prompt,prompt_version='stage2-draft-v1',
            model=self.model.model,authorship='ai_proposed',authored_by=user['id'],confirmed_version=None)
        self.planning_actor(user)
        return self.store.write(p,inserts=[('definition',fields,'DOC-'+uuid.uuid4().hex)],expected_epoch=epoch)[0]

    def confirm_definition(self, user, body):
        p=user['project_id'];epoch=self.store.epoch(p)
        row=self.definition(user,text(body,'document_id',80));expected=revision(body)
        if row['version']!=expected:raise AppError('문서가 변경됐습니다. 검토한 버전을 확인하세요.',409)
        if row['state']=='confirmed':return row
        if not row['sections'] or not any(s['text'].strip() for s in row['sections']):
            raise AppError('확정할 문서 내용을 작성하세요.',409)
        if any(q['status']=='unanswered' for q in row['questions']):
            raise AppError('확인 질문을 답변하거나 사유와 함께 범위에서 제외하세요.',409)
        updates=[('definition',row['id'],{'state':'confirmed','confirmed_version':expected+1,
            'confirmed_by':user['id'],'confirmed_at':timestamp()},expected)]
        inserts=[('definition_review',{'document_id':row['id'],'target_version':expected,
            'reviewed_by':user['id'],'reason':optional(body,'reason',3000)},None)]
        if row['stage']=='prd':
            # Native PRD remains a separate versioned artifact with the same evidence closure.
            prd_id=str(uuid.uuid4())
            inserts.append(('prd',{'title':row['title'],'sections':[{k:s[k] for k in ('id','title','text')} for s in row['sections']],
                'dependencies':row['dependencies'],'definition_id':row['id'],'definition_version':expected+1,
                'created_by':user['id']},prd_id))
            updates[0][2]['prd_id']=prd_id
        selection=next((s for s in self.store.list(p,'planning_selection') if s['stage']==row['stage']),None)
        choice={'stage':row['stage'],'document_id':row['id'],'document_version':expected+1}
        if selection:
            updates.append(('planning_selection',selection['id'],choice,selection['version']))
        else:
            inserts.append(('planning_selection',choice,None))
        self.planning_actor(user)
        saved=self.store.write(p,inserts=inserts,updates=updates,expected_epoch=epoch)
        return next(r for r in saved if r['kind']=='definition')

    def definition_export(self, user, body):
        row=self.definition(user,text(body,'document_id',80))
        if row['state']!='confirmed' or row['version']!=revision(body):
            raise AppError('내보낼 확정 문서 버전을 확인하세요.',409)
        story_export=self.story_package(user,{'stories':row['story_refs']}) if row['story_refs'] else None
        document={k:row[k] for k in ('id','version','stage','title','sections','assumptions','questions','story_refs','dependencies','confirmed_at')}
        package={'schema_version':'npd.definition-package.v1','document':document,
            'stories':story_export['package']['stories'] if story_export else [],
            'disclosure':'PO가 확정한 설계안입니다. 고객 검증 상태는 각 스토리에 별도로 기록합니다. 비공개 기획 원본은 제외했으며 AXIOM 수신 규격 검증은 별도입니다.'}
        lines=['# '+row['title'],package['disclosure'],row['id']+' v'+str(row['version'])]
        for section in row['sections']:
            lines.extend(['','## '+section['title'],section['text'],' '.join('['+rid+']' for rid in section['evidence_ids'])])
        lines+=['가정: '+s for s in row['assumptions']]
        if story_export:lines.append(story_export['markdown'])
        return {'package':package,'markdown':'\n'.join(lines)}
