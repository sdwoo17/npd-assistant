"""Six PO-authored PRD stages with independent prompt-driven draft variants."""
import json
import re
import uuid
from .contracts import text, optional, strings, revision, validate_citations, dependency_map
from .story_contracts import DEFINITION_STAGES, objects
from .stories import lineage
from .store import AppError, timestamp
from .research_contracts import PRD_SECTIONS
from .research_provenance import derived_nature


class Definitions:
    def definition_context(self, user):
        p=user['project_id']
        flow_selection,flow_rows=self.selected_flow_inputs(user)
        pack,items=(None,[]) if flow_selection else self.selected_research(user)
        linked=list(flow_rows)
        if pack:
            for item in items:
                linked.extend(self.research_ref(user,r,True) for r in item['input_refs'] if r['kind']!='research_item')
        elif not flow_selection and (self.store.list(p,'research_item') or any(r.get('state')=='reviewed' for r in self.store.list(p,'research_result'))):
            raise AppError('연구 작업·PRD 준비에서 사용할 연구 묶음과 버전을 먼저 선택하세요.',409)
        linked_ids={r['id'] for r in linked}
        definitions=[]
        for selected in self.store.list(p,'planning_selection'):
            row=self.store.get(p,'definition',selected['document_id'])
            if flow_selection and row.get('research_input_ref')!={'id':flow_selection['id'],'version':flow_selection['version']}:continue
            if pack and row.get('research_id')!=pack['research_id'] and row['id'] not in linked_ids:continue
            if row['version']!=selected['document_version'] or not self.accessible(p,row):
                raise AppError('선택한 기준 문서가 변경됐습니다. 수정안을 검토·확정한 뒤 기획을 이어가세요.',409)
            definitions.append(row)
        stories=[r for r in self.story_list(user) if not r.get('redacted') and r['definition_status']=='confirmed'
            and (not (pack or flow_selection) or r['id'] in linked_ids)]
        research=[r for r in linked if r['kind']=='research_result']
        # Normalize legacy reviewed results at this boundary too: old secondary
        # generated sections must never contradict their canonical PO text.
        research=[{**r,'sections':[{'title':'PO 검토본','text':r['text'],'asset_ids':[]}]}
            if r.get('category')=='existing_service' else r for r in research]
        debriefs=[]
        for conv in self.store.list(p,'conversation'):
            ref=conv.get('active_debrief')
            if ref:
                row=self.store.get(p,'debrief',ref['id'])
                if row['version']==ref['version'] and self.accessible(p,row) and (not (pack or flow_selection) or row['id'] in linked_ids):debriefs.append(row)
        records=definitions+stories+research+debriefs+items+([pack] if pack else [])
        if len(records)>100 or len(json.dumps(records,ensure_ascii=False))>140000:
            raise AppError('기획 맥락이 큽니다. 사용하지 않는 문서를 보류하거나 범위를 나눠 주세요.',409)
        return {'definitions':definitions,'stories':stories,'research_results':research,'reviewed_fgi':debriefs,
            'research_items':items,'research_packs':[pack] if pack else [],'research_inputs':[flow_selection] if flow_selection else []}

    def definition_evidence(self, user, context, query):
        if context.get('research_inputs'):
            selected={r['id'] for r in context['research_results']+context['reviewed_fgi']}
            return [e for e in self.knowledge(user['project_id']) if e['id'] in selected]
        evidence=self.search(user['project_id'],query)[0]
        packs=context.get('research_packs',[])
        if packs:
            allowed={(d['kind'],d['id'],d['version']) for d in packs[0]['dependencies']}
            evidence=[e for e in evidence if (e['kind'],e['id'],e['version']) in allowed]
            evidence+=context['research_items']
        return evidence

    def definition(self, user, rid):
        row=self.store.get(user['project_id'],'definition',rid)
        if not self.accessible(user['project_id'],row):
            raise AppError('기획 문서의 참조 자료가 변경됐습니다. 현재 자료로 재검토하세요.',409)
        return row

    def definition_list(self, user):
        return {'stages':DEFINITION_STAGES,'documents':[r if self.accessible(user['project_id'],r) else self.redacted(r)
            for r in self.store.list(user['project_id'],'definition')]}

    def definition_fields(self, user, body, input_evidence=None):
        stage=body.get('stage')
        if stage not in DEFINITION_STAGES or stage=='stories':
            raise AppError('기획 단계를 선택하세요. 사용자 스토리는 스토리 편집기에서 작성합니다.')
        sections=[];seen=set()
        for row in objects(body.get('sections',[]),80,'문서 문단'):
            rid=text(row,'id',80)
            if rid in seen or not re.fullmatch(r'[\w-]+',rid):raise AppError('문단 ID를 확인하세요.')
            seen.add(rid)
            sections.append({'id':rid,'title':text(row,'title',200),'text':optional(row,'text',20000),
                'evidence_ids':strings(row.get('evidence_ids',[]),100,80),
                'coverage_status':row.get('coverage_status','written' if row.get('text','').strip() else 'needs_work'),
                'na_reason':optional(row,'na_reason',3000)})
            if sections[-1]['coverage_status'] not in ('written','needs_work','not_applicable'):
                raise AppError('문단 작성 상태를 확인하세요.')
        if sum(len(s['text']) for s in sections)>100000:raise AppError('문서는 100,000자 이하로 작성하세요.')
        evidence={e['id']:e for e in self.knowledge(user['project_id'])}
        evidence.update({r['id']:r for r in self.research_results(user) if not r.get('redacted') and r.get('state')=='reviewed'})
        pack=None;pack_rows=[]
        if body.get('research_pack_ref'):
            ref=body['research_pack_ref']
            if not isinstance(ref,dict) or type(ref.get('version')) is not int:raise AppError('연구 묶음 버전을 확인하세요.')
            pack=self.store.get(user['project_id'],'research_pack',ref.get('id'))
            if pack['version']!=ref['version']:raise AppError('연구 묶음의 버전이 변경됐습니다.',409)
            pack_rows=self.pack_rows(user,pack)
            allowed={d['id'] for d in pack['dependencies']}
            evidence={rid:r for rid,r in evidence.items() if rid in allowed}
            evidence.update({r['id']:r for r in pack_rows})
        if input_evidence is not None:evidence={r['id']:r for r in input_evidence}
        ids={rid for s in sections for rid in s['evidence_ids']}
        if not ids.issubset(evidence):raise AppError('현재 공개 근거를 연결하세요.',409)
        for s in sections:validate_citations(s['text'],s['evidence_ids'])
        questions=[]
        for q in objects(body.get('questions',[]),40,'확인 질문'):
            answer=optional(q,'answer',3000);status=q.get('status','unanswered')
            if status not in ('unanswered','answered','excluded') or (status!='unanswered' and not answer):
                raise AppError('확인 질문의 답변 또는 제외 사유를 기록하세요.')
            needed=q.get('needed_stage','DEVELOPMENT')
            if needed not in ('RESEARCH','DEVELOPMENT','RELEASE'):raise AppError('질문의 해결 필요 시점을 확인하세요.')
            questions.append({'needed_stage':needed,'text':text(q,'text',3000),'answer':answer,'status':status,
                'owner':optional(q,'owner',200),'next_action':optional(q,'next_action',3000)})
        stories=[]
        for ref in objects(body.get('story_refs',[]),100,'스토리 연결'):
            if type(ref.get('version')) is not int:raise AppError('스토리 버전을 지정하세요.')
            story=self.story(user,text(ref,'id',80),ref['version'])
            if story['definition_status']!='confirmed':raise AppError('확정된 스토리를 연결하세요.',409)
            if pack and story['id'] not in allowed:raise AppError('선택한 연구 묶음에 포함된 스토리를 연결하세요.',409)
            stories.append(story)
        fields={'stage':stage,'title':text(body,'title',200),'sections':sections,'questions':questions,
            'assumptions':strings(body.get('assumptions',[]),40,3000),
            'story_refs':[{'id':r['id'],'version':r['version']} for r in stories]}
        fields.update(derived_nature(stories+[evidence[rid] for rid in ids]+pack_rows))
        if pack:fields.update(research_pack_ref={'id':pack['id'],'version':pack['version']},research_id=pack['research_id'])
        return fields,lineage(stories+[evidence[rid] for rid in ids]+([pack] if pack else []))

    def save_definition(self, user, body):
        p=user['project_id'];epoch=self.store.epoch(p)
        old=self.definition(user,body['document_id']) if body.get('document_id') else None
        if any(key in body for key in ('state','confirmed_version','confirmed_by','dependencies')):
            raise AppError('문서 상태는 명시적 검토 동작으로 변경하세요.')
        data={**(old or {}),**body}
        flow,flow_rows=self.selected_flow_inputs(user)
        if not flow and not data.get('research_pack_ref'):
            pack,_=self.selected_research(user)
            if pack:data['research_pack_ref']={'id':pack['id'],'version':pack['version']}
        selected_evidence=[e for e in self.knowledge(p) if e['id'] in {r['id'] for r in flow_rows}] if flow else None
        fields,deps=self.definition_fields(user,data,input_evidence=selected_evidence)
        if flow:
            fields['research_input_ref']={'id':flow['id'],'version':flow['version']}
            deps+=lineage([flow]+flow_rows)
        fields.update(derived_nature([fields]+([old] if old else [])))
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
        evidence=self.definition_evidence(user,context,prompt)
        pack=context['research_packs'][0] if context['research_packs'] else None
        flow_selection=context.get('research_inputs',[])
        prior=[r for r in self.store.list(p,'definition') if r.get('stage')==stage and self.accessible(p,r)
            and (not pack or r.get('research_id')==pack['research_id'])
            and (not flow_selection or r.get('research_input_ref')=={'id':flow_selection[0]['id'],'version':flow_selection[0]['version']})][-3:]
        result=self.generate('definition_draft',{'stage':DEFINITION_STAGES[stage],'prompt':prompt,
            'context':context,'evidence':evidence,'previous_variants':prior,
            'required_sections':PRD_SECTIONS if stage=='prd' else {},
            'limits':'Preserve UNKNOWN/PROPOSED/SYNTHETIC/NOT_RUN, conflicts and scope. Do not certify validation.'})
        fields,deps=self.definition_fields(user,{**result,'stage':stage,
            **({'research_pack_ref':{'id':pack['id'],'version':pack['version']}} if pack else {}),
            'questions':[{'text':q,'status':'unanswered','answer':''} for q in result['questions']],
            'story_refs':[{'id':r['id'],'version':r['version']} for r in context['stories']]},input_evidence=evidence)
        if flow_selection:fields['research_input_ref']={'id':flow_selection[0]['id'],'version':flow_selection[0]['version']}
        records=[r for group in context.values() for r in group]+evidence
        fields.update(derived_nature(records))
        fields.update(state='draft',dependencies=lineage(records)+deps+dependency_map(prior),prompt=prompt,prompt_version='stage2-draft-v1',
            model=self.model.model,authorship='ai_proposed',authored_by=user['id'],confirmed_version=None)
        self.planning_actor(user)
        return self.store.write(p,inserts=[('definition',fields,'DOC-'+uuid.uuid4().hex)],expected_epoch=epoch)[0]

    def link_definition_research(self,user,body):
        p=user['project_id'];epoch=self.store.epoch(p)
        row=self.definition(user,text(body,'document_id',80))
        if row['version']!=revision(body) or row['state']!='draft':raise AppError('연구 검토 기준을 연결할 현재 초안을 선택하세요.',409)
        pack,items=self.selected_research(user)
        if not pack:raise AppError('연구 작업·PRD 준비에서 검토할 연구 묶음을 먼저 선택하세요.',409)
        selected,results=self.selected_flow_inputs(user)
        if selected:
            included={(d['kind'],d['id'],d['version']) for d in pack['dependencies'] if d['kind'] in ('research_result','debrief')}
            wanted={(r['kind'],r['id'],r['version']) for r in results}
            if included!=wanted:raise AppError('연구 묶음의 분석 결과 버전과 PRD 참고 선택이 다릅니다. 동일한 결과를 연결하세요.',409)
        self.planning_actor(user)
        return self.store.write(p,updates=[('definition',row['id'],{'research_pack_ref':{'id':pack['id'],'version':pack['version']},
            'research_id':pack['research_id'],'dependencies':row['dependencies']+lineage([pack])},row['version'])],expected_epoch=epoch)[0]

    def definition_readiness(self, user, body):
        row=self.definition(user,text(body,'document_id',80))
        if row['version']!=revision(body):raise AppError('문서 버전을 확인하세요.',409)
        stage=body.get('stage','research')
        if stage not in ('research','development'):raise AppError('준비 검사 단계를 확인하세요.')
        errors=[];coverage=[];research=None
        for sid,title in PRD_SECTIONS.items():
            section=next((s for s in row['sections'] if s['id']==sid),None)
            complete=bool(section and ((section.get('coverage_status','written')=='written' and section['text'].strip())
                or (section.get('coverage_status')=='not_applicable' and section.get('na_reason','').strip()
                    and sid not in ('decision','evidence','users','requirements','validation','questions'))))
            coverage.append({'id':sid,'title':title,'complete':complete})
            if not complete:errors.append(title+' 내용을 보완하세요.')
        ref=row.get('research_pack_ref')
        if not ref:errors.append('검토할 연구 묶음을 연결하세요.')
        else:
            research=self.research_gate(user,{'pack_id':ref['id'],'expected_version':ref['version'],
                'stage':body.get('stage','research')})
            errors+=research['errors']
        for q in row['questions']:
            if stage=='development' and q['status']=='unanswered' and q.get('needed_stage','DEVELOPMENT') in ('RESEARCH','DEVELOPMENT'):
                errors.append('개발 전 미결 질문: '+q['text'])
            if q['status']=='unanswered' and not (q.get('owner') and q.get('next_action')):
                errors.append('미결 질문의 책임자·후속 조사를 지정하세요.')
        return {'document_id':row['id'],'version':row['version'],'ready':not errors,
            'errors':list(dict.fromkeys(errors)),'coverage':coverage,'research':research,
            'scope':'PRD 연구 검토 준비. 실제 고객 검증·개발 승인·UAT 완료와 별도입니다.'}

    def confirm_definition(self, user, body):
        p=user['project_id'];epoch=self.store.epoch(p)
        row=self.definition(user,text(body,'document_id',80));expected=revision(body)
        if row['version']!=expected:raise AppError('문서가 변경됐습니다. 검토한 버전을 확인하세요.',409)
        if row['state']=='confirmed':return row
        if not row['sections'] or not any(s['text'].strip() for s in row['sections']):
            raise AppError('확정할 문서 내용을 작성하세요.',409)
        readiness=None
        if row['stage']=='prd':
            readiness=self.definition_readiness(user,{'document_id':row['id'],'expected_version':expected})
            if not readiness['ready']:raise AppError('PRD 준비 조건 미충족: '+' / '.join(readiness['errors']),409)
        if row['stage']!='prd' and any(q['status']=='unanswered' for q in row['questions']):
            raise AppError('확인 질문을 답변하거나 사유와 함께 범위에서 제외하세요.',409)
        updates=[('definition',row['id'],{'state':'confirmed','confirmed_version':expected+1,
            'confirmed_by':user['id'],'confirmed_at':timestamp(),'readiness':readiness},expected)]
        inserts=[('definition_review',{'document_id':row['id'],'target_version':expected,
            'reviewed_by':user['id'],'reason':optional(body,'reason',3000)},None)]
        if row['stage']=='prd':
            # Native PRD remains a separate versioned artifact with the same evidence closure.
            prd_id=str(uuid.uuid4())
            inserts.append(('prd',{'title':row['title'],'sections':[{k:s[k] for k in ('id','title','text')} for s in row['sections']],
                **derived_nature([row]),'dependencies':row['dependencies'],'definition_id':row['id'],'definition_version':expected+1,
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
        document={k:row[k] for k in ('id','version','stage','title','sections','assumptions','questions','story_refs','confirmed_at')}
        document.update(derived_nature([row]))
        if row.get('research_pack_ref'):document['research_pack_ref']=row['research_pack_ref']
        if row.get('research_input_ref'):document['research_input_ref']=row['research_input_ref']
        package={'schema_version':'npd.definition-package.v1','document':document,
            'stories':story_export['package']['stories'] if story_export else [],
            'disclosure':'PO가 확정한 설계안입니다. 고객 검증 상태는 각 스토리에 별도로 기록합니다. 비공개 기획 원본은 제외했으며 AXIOM 수신 규격 검증은 별도입니다.'}
        package['readiness']=row.get('readiness') or {'status':'not_evaluated_against_research_contract'}
        if row.get('research_pack_ref'):
            ref=row['research_pack_ref']
            package['research']=self.research_handoff(user,{'pack_id':ref['id'],'expected_version':ref['version']})['package']
        lines=['# '+row['title'],'자료 성격: '+document['evidence_nature'],package['disclosure'],row['id']+' v'+str(row['version'])]
        for section in row['sections']:
            lines.extend(['','## '+section['title'],section['text'],' '.join('['+rid+']' for rid in section['evidence_ids'])])
        lines+=['가정: '+s for s in row['assumptions']]
        if story_export:lines.append(story_export['markdown'])
        return {'package':package,'markdown':'\n'.join(lines)}
