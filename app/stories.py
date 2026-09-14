"""User stories: manual drafting, source comparison, explicit version confirmation."""
import copy
import json
import uuid
from .contracts import text, optional, strings, revision, dependency_map, validate_citations
from .story_contracts import STORY_FIELDS, objects, story_fields
from .store import AppError, timestamp
from .story_recovery import StoryRecovery


def lineage(records):
    deps = dependency_map(records)
    for row in records:
        if row.get('kind'):
            deps.append({'kind': row['kind'], 'id': row['id'], 'version': row['version']})
    return list({(d['kind'],d['id'],d['version']):d for d in deps}.values())


class Stories(StoryRecovery):
    def story(self, user, rid, version=None):
        if version is None:
            row = self.store.get(user['project_id'], 'user_story', rid)
        else:
            row = next((r for r in self.store.history(user['project_id'],'user_story',rid) if r['version']==version),None)
            if row is None:
                raise AppError('스토리 버전을 찾을 수 없습니다.',404)
        if not self.accessible(user['project_id'],row):
            raise AppError('스토리의 원본 또는 근거가 변경됐습니다. 새 자료로 재검토하세요.',409)
        return row

    def story_list(self, user):
        return [r if self.accessible(user['project_id'],r) else self.redacted(r)
                for r in self.store.list(user['project_id'],'user_story')]

    def story_history(self, user, rid):
        return [r if self.accessible(user['project_id'],r) else self.redacted(r)
                for r in self.store.history(user['project_id'],'user_story',rid)]

    def story_inputs(self, user, data):
        p=user['project_id']
        knowledge={e['id']:e for e in self.knowledge(p)}
        ids=strings(data.get('evidence_ids',[]),100,80)
        if any(rid not in knowledge for rid in ids):
            raise AppError('현재 프로젝트에서 사용할 수 있는 근거를 선택하세요.',409)
        refs=[]
        for ref in objects(data.get('source_refs',[]),40,'기획 원본'):
            if type(ref.get('version')) is not int:
                raise AppError('기획 원본 버전을 지정하세요.')
            refs.append(self.asset(user,text(ref,'id',80),ref['version']))
        people=[]
        for ref in objects(data.get('persona_refs',[]),10,'페르소나'):
            if type(ref.get('version')) is not int:
                raise AppError('페르소나 버전을 지정하세요.')
            people.append(self.get_persona(p,text(ref,'id',80),ref['version']))
        documents=[]
        for ref in objects(data.get('document_refs',[]),40,'기획 연결'):
            if ref.get('kind') not in ('definition','research_result','debrief') or type(ref.get('version')) is not int:
                raise AppError('연결 문서의 종류·버전을 확인하세요.')
            row=self.store.get(p,ref['kind'],text(ref,'id',80))
            if row['version']!=ref['version'] or not self.accessible(p,row):
                raise AppError('연결 문서가 변경됐습니다.',409)
            documents.append(row)
        records=[knowledge[rid] for rid in ids]+refs+people+documents
        return records, {'evidence_ids':ids,'source_refs':[{'id':r['id'],'version':r['version']} for r in refs],
            'persona_refs':[{'id':r['id'],'version':r['version']} for r in people],
            'document_refs':[{'kind':r['kind'],'id':r['id'],'version':r['version']} for r in documents]}

    def save_story(self, user, body):
        p=user['project_id']; epoch=self.store.epoch(p)
        old=self.story(user,body['story_id']) if body.get('story_id') else None
        if any(k in body for k in ('definition_status','confirmed_version','customer_validation','reviewed_by','provenance')):
            raise AppError('확정·검증 상태와 출처는 전용 동작으로 변경하세요.')
        data={**(old or {}),**body}
        fields=story_fields(data)
        if old and old.get('source_review'):
            required = 'source-review-' + old['source_review']['id']
            if not any(q['id'] == required and q['critical'] for q in fields['questions']):
                raise AppError('원본 변경 확인 질문은 삭제할 수 없습니다. 답변하거나 사유와 함께 제외하세요.', 409)
        records, links=self.story_inputs(user,data)
        provenance=copy.deepcopy(old.get('provenance',{}) if old else {})
        for key in STORY_FIELDS + ('scenarios','acceptance_criteria','questions','assumptions','mvp','new_problem'):
            if not old or fields[key]!=old.get(key):
                prior=provenance.get(key,{})
                provenance[key]={**prior,'origin':'po_edited','edited_by':user['id'],
                    'original_value':prior.get('original_value',old.get(key) if old else fields[key])}
        allowed={r['id'] for r in records}
        validate_citations(json.dumps(fields,ensure_ascii=False),allowed)
        fields.update(**links,dependencies=(old.get("dependencies",[]) if old else [])+lineage(records),provenance=provenance,
            definition_status='draft',customer_validation=('planned' if old and old.get('customer_validation')=='actual_results' and any(
                fields[k]!=old.get(k) for k in ('actor','action','value','situation','problem','scenarios','acceptance_criteria'))
                else old.get('customer_validation','unverified') if old else 'unverified'),
            confirmed_version=old.get('confirmed_version') if old else None,
            created_by=old.get('created_by',user['id']) if old else user['id'],updated_by=user['id'])
        self.planning_actor(user)
        if old:
            return self.store.write(p,updates=[('user_story',old['id'],fields,revision(body))],expected_epoch=epoch)[0]
        return self.store.write(p,inserts=[('user_story',fields,'STORY-'+uuid.uuid4().hex)],expected_epoch=epoch)[0]

    def review_story(self, user, body):
        p=user['project_id']; epoch=self.store.epoch(p)
        row=self.story(user,text(body,'story_id',80))
        status=body.get('state')
        if status not in ('in_review','held','excluded','confirmed'):
            raise AppError('검토 상태를 확인하세요.')
        reason=optional(body,'reason',3000)
        if status in ('held','excluded') and not reason:
            raise AppError('보류 또는 범위 제외 사유를 기록하세요.')
        if status=='confirmed':
            if row.get('source_review') and not any(q['id'] == 'source-review-' + row['source_review']['id'] and q['critical'] and q['status'] in ('answered', 'excluded') and q['answer'] for q in row['questions']):
                raise AppError('원본 변경 확인 질문의 답변 또는 제외 사유가 필요합니다.', 409)
            if not row['actor'] or not row['action'] or not row['value'] or not row['acceptance_criteria']:
                raise AppError('사용자·행동·가치와 수용 기준을 검토하세요.',409)
            if any(q['critical'] and q['status'] not in ('answered','excluded') for q in row['questions']):
                raise AppError('핵심 확인 질문을 해소하거나 사유와 함께 범위에서 제외하세요.',409)
            if not row['problem'] or (row['new_problem'] and not row['validation_task']):
                raise AppError('관련 문제를 기록하고, 새 문제 가설이면 검증 과제를 지정하세요.',409)
            if not row['new_problem'] and not row['evidence_ids'] and not row['document_refs']:
                raise AppError('기존 문제의 근거를 연결하거나 새 문제 가설로 지정하세요.',409)
        expected=revision(body)
        if row['version']!=expected:
            raise AppError('스토리가 변경됐습니다. 검토한 버전을 다시 확인하세요.',409)
        update={'definition_status':status}
        if status=='confirmed':
            update.update(confirmed_version=expected+1,confirmed_at=timestamp(),confirmed_by=user['id'])
        self.planning_actor(user)
        return self.store.write(p,updates=[('user_story',row['id'],update,expected)],
            inserts=[('story_review',{'story_id':row['id'],'target_version':expected,'result_version':expected+1,
                'state':status,'reason':reason,'reviewed_by':user['id']},None)],expected_epoch=epoch)[-1]

    def story_validation(self, user, body):
        p=user['project_id']; epoch=self.store.epoch(p); row=self.story(user,text(body,'story_id',80))
        state=body.get('state')
        if state not in ('unverified','planned','actual_results'):
            raise AppError('고객 검증 상태를 확인하세요.')
        note=text(body,'note',3000)
        evidence_ids=strings(body.get('evidence_ids',[]),50,80)
        evidence=[e for e in self.knowledge(p) if e['id'] in evidence_ids]
        result_ids=strings(body.get('result_ids',[]),30,80)
        results=[r for r in self.research_results(user) if r['id'] in result_ids and not r.get('redacted')]
        if len(results)!=len(result_ids) or any(r.get('category')!='fgi_actual' or r.get('state')!='reviewed' or not r.get('actual_customer_data') for r in results):
            raise AppError('실제 조사 여부를 PO가 확인한 현재 FGI 검토본을 선택하세요.',409)
        if len(evidence)!=len(evidence_ids) or (state=='actual_results' and ((not evidence and not results) or any(e['evidence_type']!='real' for e in evidence))):
            raise AppError('실제 검증 결과에는 현재 공개된 실제 고객 근거를 연결하세요. 가상 FGI는 사용할 수 없습니다.',409)
        self.planning_actor(user)
        return self.store.write(p,updates=[('user_story',row['id'],{'customer_validation':state,
            'validation_note':note,'validation_evidence_ids':evidence_ids,'validation_result_ids':result_ids,
            'dependencies':row['dependencies']+lineage(evidence+results), 'definition_status':'draft'},revision(body))],expected_epoch=epoch)[0]

    def generate_stories(self, user, body):
        p=user['project_id']; epoch=self.store.epoch(p)
        prompt=text(body,'prompt',5000)
        run_ids=strings(body.get('extraction_ids',[]),3,80)
        if len(run_ids) != len(body.get('extraction_ids', [])):
            raise AppError('분석 입력을 중복 없이 선택하세요.')
        runs=[self.extraction(user,rid) for rid in run_ids]
        if len({r['asset_id'] for r in runs}) != len(runs):
            raise AppError('한 원본에서 사용할 분석은 하나만 선택하세요.')
        if any(r['status']!='completed' for r in runs):
            raise AppError('완료한 원본 분석을 선택하세요.',409)
        base=self.story(user,body['base_story_id']) if body.get('base_story_id') else None
        records,links=self.story_inputs(user,body)
        assets=[self.asset(user,r['asset_id'],r['asset_version']) for r in runs]
        for asset in assets:
            if asset['id'] not in {r['id'] for r in records}:
                records.append(asset);links['source_refs'].append({'id':asset['id'],'version':asset['version']})
        evidence=[r for r in records if r['kind'] in ('insight','voc')]
        prior=[r for r in self.store.list(p,'story_draft') if self.accessible(p,r) and not (base and any(
            d['kind']=='user_story' and d['id']==base['id'] for d in r.get('dependencies',[])))][-3:]
        previous=[{'id':r['id'],'stories':r['stories']} for r in prior]
        context=self.definition_context(user)
        if base:
            # A revision cannot depend on itself, including through downstream documents.
            context={k:[r for r in rows if r['id']!=base['id'] and not any(
                d['kind']=='user_story' and d['id']==base['id'] for d in r.get('dependencies',[]))]
                for k,rows in context.items()}
        context_records=[r for rows in context.values() for r in rows]
        dependencies=lineage(records+context_records)+dependency_map(prior)+(base.get('dependencies',[]) if base else [])
        result=self.generate('story_drafts',{'prompt':prompt,'extractions':runs,'evidence':evidence,
            'context':context,
            'base_story':base,'previous_variants':previous})
        candidates=[]
        for generated in objects(result['stories'],12,'스토리 후보'):
            fields=story_fields(generated)
            for run in runs:
                for question in run['questions']:
                    if question['text'] not in {q['text'] for q in fields['questions']}:
                        fields['questions'].append({'id':'source-question-'+str(len(fields['questions'])+1),
                            'text':question['text'],'critical':question['critical'],'status':'unanswered','answer':''})
                for issue in run['quality_issues']:
                    fields['questions'].append({'id':'quality-'+str(len(fields['questions'])+1),'text':issue,
                        'critical':True,'status':'unanswered','answer':''})
            ids=strings(generated.get('evidence_ids',[]),100,80)
            if not set(ids).issubset({e['id'] for e in evidence}):
                raise AppError('스토리 후보의 근거를 확인하지 못했습니다.',502)
            provenance={key:{'origin':'ai_proposed','original_value':copy.deepcopy(fields[key])} for key in fields}
            for ref in generated['provenance']:
                if ref['field'] not in STORY_FIELDS:
                    raise AppError('스토리 출처 필드를 확인하세요.',502)
                if ref['origin']=='extracted':
                    run=next((r for r in runs if r['asset_id']==ref['asset_id']),None)
                    region=next((r for r in run['regions'] if r['id']==ref['region_id']),None) if run else None
                    if not region or not ref['quote'] or ref['quote'] not in region['text']:
                        raise AppError('추출 필드가 원본 전사·영역과 일치하지 않습니다.',502)
                    provenance[ref['field']]={**ref,'asset_version':run['asset_version'],'extraction_id':run['id'],'bbox':region['bbox'],'original_value':fields[ref['field']]}
            fields.update(provenance=provenance,evidence_ids=ids)
            validate_citations(json.dumps(fields,ensure_ascii=False),{r['id'] for r in records})
            candidates.append(fields)
        if not candidates:
            raise AppError('스토리 후보가 없습니다. 입력을 보충하세요.',422)
        self.planning_actor(user)
        checks=[('user_story',base['id'],base['version'])] if base else []
        return self.store.write(p,inserts=[('story_draft',{'prompt':prompt,'prompt_version':'story-drafts-v1','model':self.model.model,
            'stories':candidates,'extraction_ids':run_ids,'links':links,'dependencies':dependencies,
            'base_story':{'id':base['id'],'version':base['version']} if base else None,
            'assumptions':result['assumptions'],'created_by':user['id']},None)],expected_epoch=epoch,checks=checks)[0]

    def apply_story_draft(self, user, body):
        p=user['project_id'];epoch=self.store.epoch(p)
        draft=self.store.get(p,'story_draft',text(body,'draft_id',80))
        if not self.accessible(p,draft):
            raise AppError('초안의 원본·근거가 변경됐습니다.',409)
        index=body.get('candidate_index')
        if type(index) is not int or not 0<=index<len(draft['stories']):
            raise AppError('스토리 후보를 선택하세요.')
        candidate=copy.deepcopy(draft['stories'][index])
        base=self.story(user,body['story_id']) if body.get('story_id') else None
        allowed=set(STORY_FIELDS)|{'mvp','new_problem','scenarios','acceptance_criteria','questions','assumptions'}
        chosen=strings(body.get('fields',list(allowed)),50,80)
        if any(key not in allowed for key in chosen):
            raise AppError('채택할 스토리 필드를 확인하세요.')
        if base:
            expected=revision(body)
            if not draft.get('base_story') or draft['base_story']!={'id':base['id'],'version':expected} or base['version']!=expected:
                raise AppError('재분석 기준과 현재 스토리 버전이 다릅니다. 새로 비교하세요.',409)
            if not chosen:
                raise AppError('채택할 변경 필드를 선택하세요.')
            fields={k:copy.deepcopy(base.get(k)) for k in allowed}
            provenance=copy.deepcopy(base['provenance'])
            for key in chosen:
                fields[key]=candidate[key];provenance[key]=candidate['provenance'][key]
            if base.get('source_review'):
                required = 'source-review-' + base['source_review']['id']
                fields['questions'] = [q for q in fields['questions'] if q['id'] != required] + [copy.deepcopy(q) for q in base['questions'] if q['id'] == required]
            for question in candidate['questions']:
                if question['critical'] and question['text'] not in {q['text'] for q in fields['questions']}:
                    fields['questions'].append(question)
            links={key:base[key] for key in ('evidence_ids','source_refs','persona_refs','document_refs')}
            for key in links:
                for ref in draft['links'][key]:
                    if ref not in links[key]:links[key].append(ref)
            records,links=self.story_inputs(user,links)
            deps=base['dependencies']+draft['dependencies']+lineage(records)
        else:
            fields={k:candidate[k] for k in allowed}
            provenance=candidate['provenance'];links=draft['links'];deps=draft['dependencies']
        fields.update(**links,provenance=provenance,dependencies=deps,definition_status='draft',
            customer_validation=('planned' if base and base.get('customer_validation')=='actual_results' else base.get('customer_validation','unverified') if base else 'unverified'),
            confirmed_version=base.get('confirmed_version') if base else None,
            draft_id=draft['id'],created_by=base.get('created_by') if base else user['id'])
        self.planning_actor(user)
        if base:
            return self.store.write(p,updates=[('user_story',base['id'],fields,revision(body))],expected_epoch=epoch,
                checks=[('story_draft',draft['id'],draft['version'])])[0]
        return self.store.write(p,inserts=[('user_story',fields,'STORY-'+uuid.uuid4().hex)],expected_epoch=epoch,
            checks=[('story_draft',draft['id'],draft['version'])])[0]

    def restructure_stories(self, user, body):
        p=user['project_id'];epoch=self.store.epoch(p)
        refs=objects(body.get('sources'),10,'원본 스토리')
        sources=[self.story(user,text(r,'id',80)) for r in refs]
        if not sources or len({r['id'] for r in sources})!=len(sources):
            raise AppError('분할·병합할 서로 다른 스토리를 선택하세요.')
        for row,ref in zip(sources,refs):
            if type(ref.get('version')) is not int or row['version']!=ref['version']:
                raise AppError('원본 스토리 버전이 변경됐습니다.',409)
        children=objects(body.get('stories'),10,'새 스토리')
        if not children or (len(sources)==1 and len(children)<2):
            raise AppError('분할은 새 스토리 둘 이상, 병합은 원본 둘 이상이 필요합니다.')
        origin=sources[0]
        links={k:[] for k in ('evidence_ids','source_refs','persona_refs','document_refs')}
        for source in sources:
            for key in links:
                for ref in source[key]:
                    if ref not in links[key]:links[key].append(ref)
        records,links=self.story_inputs(user,links)
        prepared=[]
        for child in children:
            fields=story_fields({**origin,**child})
            validate_citations(json.dumps(fields,ensure_ascii=False),{r['id'] for r in records})
            prepared.append(('user_story',{**fields,**links,'provenance':{k:{'origin':'po_edited','original_value':v} for k,v in fields.items()},
                'definition_status':'draft','customer_validation':'unverified','confirmed_version':None,
                'derived_from':[{'id':r['id'],'version':r['version']} for r in sources],
                'dependencies':lineage(records)+[d for source in sources for d in source.get('dependencies',[])],'created_by':user['id']},'STORY-'+uuid.uuid4().hex))
        self.planning_actor(user)
        return self.store.write(p,inserts=prepared,expected_epoch=epoch,checks=[('user_story',r['id'],r['version']) for r in sources])

    def story_package(self, user, body):
        refs=objects(body.get('stories'),100,'확정 스토리')
        stories=[]
        for ref in refs:
            row=self.story(user,text(ref,'id',80),ref.get('version'))
            if row['definition_status']!='confirmed' or type(ref.get('version')) is not int:
                raise AppError('확정된 스토리의 명시적 버전을 선택하세요.',409)
            keep=set(STORY_FIELDS)|{'id','version','scenarios','acceptance_criteria','questions','assumptions','evidence_ids',
                'source_refs','persona_refs','document_refs','mvp','new_problem','definition_status','customer_validation',
                'confirmed_at','confirmed_by','dependencies'}
            stories.append({k:v for k,v in row.items() if k in keep})
        if not stories:
            raise AppError('내보낼 확정 스토리를 선택하세요.')
        package={'schema_version':'npd.story-package.v1','stories':stories,
            'disclosure':'설계안 확정은 실제 고객 검증이 아닙니다. 기획 원본·전사·이미지는 제외했습니다. AXIOM 수신 규격 검증은 별도입니다.'}
        lines=['# 사용자 스토리 설계안',package['disclosure']]
        for row in stories:
            lines.extend(['','## '+row['title'],row['id']+' v'+str(row['version']),
                '사용자: '+row['actor'],'행동: '+row['action'],'가치: '+row['value'],'문제: '+row['problem'],
                '고객 검증: '+row['customer_validation']])
            for s in row['scenarios']:lines.append(s['type']+' · '+s['title']+': '+' → '.join(s['steps'])+' '+s['branch'])
            for a in row['acceptance_criteria']:lines.append('Given '+a['given']+' / When '+a['when']+' / Then '+a['then'])
            lines+=['가정: '+a for a in row['assumptions']]
            lines+=['근거: ['+rid+']' for rid in row['evidence_ids']]
            lines+=['원본 추적 ID: '+r['id']+' v'+str(r['version']) for r in row['source_refs']]
        return {'package':package,'markdown':'\n'.join(lines)}
