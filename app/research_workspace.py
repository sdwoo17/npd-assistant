"""Project-private research work, explicit input selection and review gates."""
import hashlib
import json
import uuid
from .contracts import text, optional, strings, revision, dependency_map
from .store import AppError, timestamp
from .stories import lineage
from .research_contracts import (SCHEMA_VERSION, TYPES, TASKS, GRAPH_TYPES, PRD_SECTIONS,
                                 clean_fields, content_errors, render_item)

from .research_interviews import ResearchInterviews
from .research_details import computed_details
from .research_provenance import derived_nature, synthetic_record

REF_KINDS=('insight','voc','research_result','debrief','planning_asset','user_story','definition','research_item')


class ResearchWorkspace(ResearchInterviews):
    def research_ref(self, user, ref, reviewed=False):
        if not isinstance(ref,dict) or ref.get('kind') not in REF_KINDS or type(ref.get('version')) is not int:
            raise AppError('참조 자료의 종류·ID·버전이 필요합니다.')
        p=user['project_id']
        if ref['kind'] in ('insight','voc'):
            row=next((e for e in self.knowledge(p) if e['id']==ref.get('id') and e['kind']==ref['kind']),None)
            if not row:raise AppError('공유 가능한 현재 근거를 선택하세요.',409)
        else:
            row=self.store.get(p,ref['kind'],ref.get('id'))
            if row.get('withdrawn') or not self.accessible(p,row):raise AppError('참조 자료를 재검토하세요.',409)
        if row['version']!=ref['version']:raise AppError('참조 자료의 버전이 변경됐습니다.',409)
        if reviewed:
            state_key={'research_item':'state','research_result':'state','definition':'state','user_story':'definition_status'}.get(row['kind'])
            if state_key and row.get(state_key) not in ('reviewed','confirmed'):
                raise AppError('검토한 자료 버전을 선택하세요.',409)
            if row['kind']=='debrief' and row.get('review_status')!='po_reviewed':
                raise AppError('검토한 디브리프를 선택하세요.',409)
        return row

    def research_item(self, user, rid):
        row=self.store.get(user['project_id'],'research_item',rid)
        if row.get('withdrawn') or not self.accessible(user['project_id'],row):
            raise AppError('연구 산출물의 근거·버전이 변경됐습니다. 현재 자료로 다시 검토하세요.',409)
        return row

    def research_workspace(self, user):
        p=user['project_id']
        rows=self.store.list(p,'research_item');packs=self.store.list(p,'research_pack')
        safe=lambda r:r if not r.get('withdrawn') and self.accessible(p,r) else self.redacted(r)
        selection=self.store.list(p,'research_selection')
        return {'schema_version':SCHEMA_VERSION,'types':TYPES,'tasks':TASKS,'prd_sections':PRD_SECTIONS,
            'items':self.annotate_research_conflicts([safe(r) for r in rows]), 'packs':[safe(r) for r in packs],
            'selection':selection[0] if selection else None,
            'input_capabilities':{'file_bytes':3*1024*1024,'image_pixels':16000000,'image_edge':8000,
                'story_extractions':3,'status':'IMPLEMENTED_LIMITS','sample_limit_decision':'NOT_VALIDATED',
                'reason':'현재 처리 상한입니다. 더 큰 예시 상한은 실제 모델·비용·품질 시험 후 결정합니다.'}}

    def save_research_item(self, user, body, *, authorship='po_authored', expected_epoch=None, provenance=None):
        p=user['project_id'];epoch=self.store.epoch(p) if expected_epoch is None else expected_epoch
        old=self.research_item(user,body['item_id']) if body.get('item_id') else None
        data={**(old or {}),**body};kind=data.get('output_type')
        if old and kind!=old['output_type']:raise AppError('산출물 유형은 변경할 수 없습니다.')
        cleaned=clean_fields(kind,data.get('fields',{}))
        task=data.get('task_id','')
        if task and (task not in TASKS or TASKS[task][1]!=kind):raise AppError('작업과 산출물 유형이 다릅니다.')
        if kind=='brief':task='RS-00'
        research_id=data.get('research_id','')
        if kind!='brief':
            brief=self.research_item(user,research_id)
            if brief['output_type']!='brief':raise AppError('조사 브리프를 선택하세요.')
            if old and research_id!=old['research_id']:raise AppError('연구 범위 변경은 새 산출물로 작성하세요.')
        refs=data.get('input_refs',[]);links=data.get('links',[])
        if not isinstance(refs,list) or len(refs)>100 or not isinstance(links,list) or len(links)>100:
            raise AppError('참조 자료는 100개 이하로 선택하세요.')
        records=[self.research_ref(user,r,True) for r in refs]
        normalized_links=[]
        for link in links:
            if not isinstance(link,dict) or link.get('relation') not in ('supports','refutes','derived_from','validates','measures'):
                raise AppError('근거 연결의 의미를 선택하세요.')
            target=self.research_ref(user,link,True)
            if old and target['id']==old['id']:raise AppError('자기 자신을 참조할 수 없습니다.')
            if target['kind']=='research_item' and target.get('research_id')!=research_id:
                raise AppError('다른 연구의 산출물을 연결할 수 없습니다.')
            records.append(target)
            normalized_links.append({'kind':target['kind'],'id':target['id'],'version':target['version'],
                'relation':link['relation'],'reason':optional(link,'reason',3000)})
        if old and any(r['id']==old['id'] or any(d['kind']=='research_item' and d['id']==old['id'] for d in r.get('dependencies',[])) for r in records):
            raise AppError('자신 또는 후속 산출물을 원인으로 연결할 수 없습니다.')
        if any(r['kind']=='research_item' and r.get('research_id')!=research_id for r in records):
            raise AppError('같은 연구 범위의 산출물을 선택하세요.')
        applicability=data.get('applicability','applicable')
        if applicability not in ('applicable','not_applicable'):raise AppError('적용 여부를 확인하세요.')
        rid=old['id'] if old else 'RITEM-'+uuid.uuid4().hex
        fields={'title':text(data,'title',200),'output_type':kind,'task_id':task,'fields':cleaned,
            'research_id':research_id if kind!='brief' else rid,'schema_version':SCHEMA_VERSION,
            'applicability':applicability,'na_reason':optional(data,'na_reason',3000),
            'input_refs':[{'kind':r['kind'],'id':r['id'],'version':r['version']} for r in records],
            'links':normalized_links,'dependencies':lineage(records)+(old.get('dependencies',[]) if old else []),
            'state':'draft','authorship':authorship,'authored_by':user['id'],
            'change_reason':optional(data,'change_reason',3000),'reviewed_by':None,'reviewed_at':None,
            'access_scope':'project_private','withdrawn':False}
        fields['evidence_type']=cleaned.get('evidence_type','synthetic' if kind=='synthetic_debrief' else 'research')
        fields.update(derived_nature(records+([old] if old else []),cleaned))
        computed=computed_details(kind,cleaned)
        if computed:fields['computed']=computed
        if provenance:fields['interview_application']=provenance
        fields['text']=render_item(fields)
        self.planning_actor(user)
        if old:return self.store.write(p,updates=[('research_item',rid,fields,revision(body))],expected_epoch=epoch)[0]
        return self.store.write(p,inserts=[('research_item',fields,rid)],expected_epoch=epoch)[0]

    def generate_research_item(self, user, body):
        p=user['project_id'];epoch=self.store.epoch(p)
        kind=body.get('output_type')
        if kind not in TYPES or kind=='brief':raise AppError('브리프 작성 후 분석할 산출물을 선택하세요.')
        brief=self.research_item(user,text(body,'research_id',80))
        if brief['output_type']!='brief':raise AppError('조사 브리프를 선택하세요.')
        refs=body.get('input_refs',[])
        if not isinstance(refs,list) or not 1<=len(refs)<=100:raise AppError('분석할 검토 근거를 선택하세요.')
        sources=[self.research_ref(user,r,True) for r in refs]
        if any(r['kind']=='research_item' and r['research_id']!=brief['id'] for r in sources):raise AppError('같은 연구의 근거를 선택하세요.')
        safe=[{k:r[k] for k in ('id','kind','version','title','text','evidence_type','evidence_nature','contains_synthetic') if k in r} for r in sources]
        if len(json.dumps(safe,ensure_ascii=False))>100000:raise AppError('분석 범위를 줄여 주세요. 입력을 임의로 생략하지 않습니다.')
        result=self.generate('research_task',{'prompt':text(body,'prompt',5000),'brief':brief['fields'],
            'output_type':kind,'fields':TYPES[kind],'evidence':safe,
            'limits':'Draft only. Unknown numeric values are empty. Never certify actual observations, execution or approval.'})
        data={}
        for value in result['values']:
            key=value['key']
            if key not in TYPES[kind] or key in data:raise AppError('AI 산출물 필드가 계약과 다릅니다.',502)
            spec=TYPES[kind][key];v=value['text']
            if spec['type']=='rows':
                try:v=json.loads(v) if v.strip() else []
                except (ValueError,TypeError):raise AppError('AI 표 형식을 확인하지 못했습니다.',502)
            elif spec['type']=='number':
                try:v=float(v) if v.strip() else None
                except ValueError:raise AppError('AI 측정값 형식을 확인하지 못했습니다.',502)
                if v is not None and v.is_integer():v=int(v)
            elif spec['type']=='checkbox':v=False
            elif spec['type']=='lines':v=[line.strip() for line in v.splitlines() if line.strip()]
            data[key]=v
        for key,reset in (('run_state','NOT_RUN'),('claim_status','UNVERIFIED'),('measurement_state','UNKNOWN'),('baseline_state','UNKNOWN')):
            if key in TYPES[kind]:data[key]=reset
        if kind in ('measurement','metric'):
            data['value' if kind=='measurement' else 'baseline']=None
            data['unknown_reason']='AI 초안입니다. 실측 자료와 계산을 PO가 확인해야 합니다.'
        if kind=='service_baseline':data['detail_level']='STRUCTURED'
        if kind=='po_interview':
            data['answers']=clean_fields(kind,{'answers':data.get('answers',[])})['answers']
            for a in data['answers']:a.update(status='OPEN',answer='',proposed_text='',confirmed_at='',respondent_role='',effective_at='',scope='',evidence_note='',classification='UNVERIFIED')
        if kind=='uat':data['status']='NOT_RUN'
        if kind=='actual_study':data.update(actual_sample='',independent_participants=None,executed_at='',executor='',attestation=False)
        return self.save_research_item(user,{'title':result['title'],'output_type':kind,'task_id':body.get('task_id',''),
            'research_id':brief['id'],'input_refs':refs,'fields':data,
            'change_reason':'AI 후보 · '+text(body,'prompt',5000)},authorship='ai_proposed',expected_epoch=epoch)

    def review_research_item(self, user, body):
        p=user['project_id'];epoch=self.store.epoch(p)
        row=self.research_item(user,text(body,'item_id',80))
        errors=content_errors(row)
        refs=[self.research_ref(user,r,True) for r in row['input_refs']]
        f=row['fields'];kind=row['output_type']
        if row['applicability']=='applicable':
            synthetic=synthetic_record(row) or any(synthetic_record(r) for r in refs)
            if synthetic and (f.get('measurement_state')=='OBSERVED' or f.get('baseline_state')=='OBSERVED' or f.get('provenance')=='OBSERVED'
                    or kind=='actual_study' and f.get('run_state')=='EXECUTED'
                    or kind=='uat' and f.get('status') in ('PASS','FAIL')
                    or kind=='po_interview' and f.get('nature')=='PO_REPORTED'
                    or kind=='evidence' and (f.get('evidence_type') in ('REAL_VOC','INTERNAL_MEASUREMENT','PUBLIC_FACT') or f.get('supports_demand_validation'))):
                errors.append('합성 입력은 실제 관측·공개 사실·고객 검증·UAT 실행으로 확정할 수 없습니다.')
            if kind=='calculation':
                allowed={r['id'] for r in refs if r['kind'] in ('insight','voc','research_item')}
                if any(t['source_id'] not in allowed for t in f['terms']):errors.append('계산 입력마다 선택한 공유 근거 카드·VoC를 연결하세요.')
            if kind=='funnel' and f['measurement_state']=='OBSERVED' and not refs:errors.append('관측 퍼널의 검토 근거를 연결하세요.')
            if kind=='voc_coding':
                allowed={r['id']:r for r in refs if r['kind'] in ('insight','voc','research_item')}
                for v in f['records']:
                    source=allowed.get(v['source_id'])
                    if not source:errors.append('코딩 발언마다 선택한 공유 근거를 연결하세요.')
                    elif v['nature']=='REAL' and (synthetic_record(source) or not (source['kind']=='voc' and source.get('evidence_type')=='real')):
                        errors.append('실제 발언 코딩에는 현재 공유된 실제 VoC가 필요합니다.')
            if kind=='po_interview' and not any(r['kind']=='research_item' and r['output_type']=='service_baseline' for r in refs):
                errors.append('인터뷰에서 대조한 서비스 분석 버전을 연결하세요.')
            needs_sources=kind in ('source_manifest','evidence','numeric_claim','voc_finding','benchmark_source')
            if kind=='evidence' and f['evidence_type'] in ('ASSUMPTION','USER_REQUIREMENT'):needs_sources=False
            if needs_sources and not refs:errors.append('검토한 원문·근거를 연결하세요.')
            if kind=='evidence':
                real=any(r['kind']=='voc' and r.get('evidence_type')=='real' or r['kind']=='research_item' and
                    r['output_type']=='actual_study' and r['fields'].get('run_state')=='EXECUTED' for r in refs)
                if f['evidence_type']=='REAL_VOC' and not real:errors.append('실제 VoC 또는 실행을 확인한 조사 결과가 필요합니다.')
                measured=any(r['kind']=='research_item' and r['output_type']=='measurement' and
                    r['fields'].get('measurement_state')=='OBSERVED' and r['fields'].get('measurement_origin')=='INTERNAL' for r in refs)
                if f['evidence_type']=='INTERNAL_MEASUREMENT' and not measured:
                    errors.append('검토한 내부 실측 결과를 연결하세요.')
                if f['supports_demand_validation'] and (f['evidence_type'] not in ('REAL_VOC','INTERNAL_MEASUREMENT') or not refs):
                    errors.append('합성·가정·외부 사례는 실제 수요 검증으로 사용할 수 없습니다.')
                if f['claim_status']=='CONFLICTED' and not any(r['kind']=='research_item' and r['output_type']=='conflict' for r in refs):
                    errors.append('충돌 상태에는 양쪽 근거와 판단 기록을 연결하세요.')
            if kind=='conflict' and len({r['id'] for r in refs})<2:errors.append('충돌하는 양쪽 근거를 연결하세요.')
            if kind in ('measurement','metric') and f.get('measurement_state',f.get('baseline_state'))=='OBSERVED' and not refs:
                errors.append('실측 기준선의 근거가 필요합니다.')
            if kind=='metric' and f['baseline_state']=='OBSERVED' and not any(
                    r['kind']=='research_item' and r['output_type']=='measurement' and r['fields']['measurement_state']=='OBSERVED' and r['fields']['measurement_origin']=='INTERNAL'
                    and all(r['fields'][source]==f[target] for source,target in (('value','baseline'),('unit','unit'),('population','population'),('period','period')))
                    for r in refs):
                errors.append('우리 제품의 동일 대상·기간·단위·값을 검토한 실측 기준선을 연결하세요.')
            if kind=='utility' and f['measurement_state']=='OBSERVED' and not refs:
                errors.append('작성 효과의 실측 기록을 연결하세요.')
            if kind=='actual_study' and f['run_state']=='EXECUTED' and not any(r['kind']=='planning_asset' and r.get('purpose')=='actual_fgi' or r['kind']=='research_result' and r.get('actual_customer_data') for r in refs):
                errors.append('실제 조사 제출 문서를 연결하세요.')
            if kind=='uat' and f['status']!='NOT_RUN' and not refs:errors.append('실제 실행 증거를 연결하세요.')
            if any(not l['reason'] for l in row['links']):errors.append('연결한 근거가 주장을 지원·반박하는 이유를 기록하세요.')
        if errors:raise AppError(' / '.join(errors),409)
        self.planning_actor(user)
        return self.store.write(p,updates=[('research_item',row['id'],{'state':'reviewed',
            'reviewed_by':user['id'],'reviewed_at':timestamp(),'review_reason':text(body,'reason',3000)},revision(body))],expected_epoch=epoch)[0]

    def withdraw_research_item(self, user, body):
        self.planning_actor(user)
        row=self.research_item(user,text(body,'item_id',80))
        return self.store.update(user['project_id'],'research_item',row['id'],{'withdrawn':True},revision(body))

    def save_research_pack(self, user, body):
        p=user['project_id'];epoch=self.store.epoch(p)
        old=self.store.get(p,'research_pack',body['pack_id']) if body.get('pack_id') else None
        brief=self.research_item(user,text(body,'research_id',80))
        if brief['output_type']!='brief' or brief['state']!='reviewed':raise AppError('검토한 조사 브리프를 선택하세요.',409)
        refs=body.get('item_refs',[])
        if not isinstance(refs,list) or not refs or len(refs)>100 or any(not isinstance(r,dict) for r in refs):raise AppError('산출물 1~100개를 선택하세요.')
        rows=[self.research_ref(user,dict(r,kind='research_item'),True) for r in refs]
        if any(r['research_id']!=brief['id'] for r in rows):raise AppError('다른 제품·연구의 산출물이 섞여 있습니다.')
        rows=list({r['id']:r for r in [brief]+rows}.values())
        if len(rows)>100:raise AppError('브리프를 포함하여 산출물 100개 이하로 묶으세요.')
        fields={'title':text(body,'title',200),'research_id':brief['id'],
            'item_refs':[{'kind':r['kind'],'id':r['id'],'version':r['version']} for r in rows],
            'dependencies':lineage(rows),'schema_version':SCHEMA_VERSION,'created_by':user['id'],
            'change_reason':optional(body,'reason',3000),'state':'draft'}
        if old and old['research_id']!=brief['id']:raise AppError('묶음의 연구 범위는 변경할 수 없습니다.')
        self.planning_actor(user)
        if old:return self.store.write(p,updates=[('research_pack',old['id'],fields,revision(body))],expected_epoch=epoch)[0]
        return self.store.write(p,inserts=[('research_pack',fields,'RPACK-'+uuid.uuid4().hex)],expected_epoch=epoch)[0]

    @staticmethod
    def annotate_research_conflicts(rows):
        """Backlinks are derived in a view, not circular freshness dependencies."""
        conflicts=[r for r in rows if r.get('output_type')=='conflict' and r.get('state')=='reviewed'
            and r.get('applicability')=='applicable']
        result=[]
        for row in rows:
            if row.get('output_type')=='evidence':
                linked=[c for c in conflicts if c.get('research_id')==row.get('research_id')
                    and any(ref['id']==row['id'] for ref in c['input_refs'])]
                status='CONFLICTED' if any(c['fields']['resolution_status']=='UNRESOLVED' for c in linked) else row['fields']['claim_status']
                row={**row,'conflict_ids':[c['id'] for c in linked],'effective_claim_status':status}
                row['text']=render_item(row)
            result.append(row)
        return result

    def pack_rows(self, user, pack):
        if not self.accessible(user['project_id'],pack):raise AppError('연구 묶음의 근거·버전이 변경됐습니다. 다시 선택하세요.',409)
        return self.annotate_research_conflicts([self.research_ref(user,r,True) for r in pack['item_refs']])

    def select_research_pack(self, user, body):
        p=user['project_id'];epoch=self.store.epoch(p)
        pack=self.store.get(p,'research_pack',text(body,'pack_id',80))
        if pack['version']!=revision(body):raise AppError('연구 묶음 버전이 변경됐습니다.',409)
        self.pack_rows(user,pack)
        old=next(iter(self.store.list(p,'research_selection')),None)
        fields={'pack_id':pack['id'],'pack_version':pack['version'],'selected_by':user['id']}
        self.planning_actor(user)
        if old:return self.store.write(p,updates=[('research_selection',old['id'],fields,old['version'])],expected_epoch=epoch)[0]
        return self.store.write(p,inserts=[('research_selection',fields,None)],expected_epoch=epoch)[0]

    def selected_research(self, user):
        selected=next(iter(self.store.list(user['project_id'],'research_selection')),None)
        if not selected:return None,[]
        pack=self.store.get(user['project_id'],'research_pack',selected['pack_id'])
        if pack['version']!=selected['pack_version']:raise AppError('연구 묶음의 현재 버전을 다시 선택하세요.',409)
        return pack,self.pack_rows(user,pack)

    def research_gate(self, user, body):
        pack=self.store.get(user['project_id'],'research_pack',text(body,'pack_id',80))
        if pack['version']!=revision(body):raise AppError('검토할 묶음 버전을 확인하세요.',409)
        stage=body.get('stage','research')
        if stage not in ('research','development'):raise AppError('검토 단계를 확인하세요.')
        rows=self.pack_rows(user,pack);by_id={r['id']:r for r in rows}
        brief=by_id[pack['research_id']];checks=[];errors=[];warnings=[]
        for conflict in self.store.list(user['project_id'],'research_item'):
            if (conflict.get('output_type')=='conflict' and conflict.get('research_id')==pack['research_id']
                    and conflict.get('state')=='reviewed' and conflict.get('applicability')=='applicable'
                    and not conflict.get('withdrawn') and self.accessible(user['project_id'],conflict)
                    and conflict['id'] not in by_id and any(r['id'] in by_id for r in conflict['input_refs'])):
                errors.append('선택한 근거와 관련된 충돌 기록을 묶음에 포함하세요: '+conflict['id'])
        for task in brief['fields']['required_tasks']:
            candidates=[r for r in rows if r.get('task_id')==task]
            valid=bool(candidates) and all(not content_errors(r) for r in candidates)
            checks.append({'task_id':task,'title':TASKS[task][0],'complete':valid,
                'item_ids':[r['id'] for r in candidates]})
            if not valid:errors.append(TASKS[task][0]+' 산출물 검토가 필요합니다.')
            if task=='SV-02' and any(r['applicability']=='applicable' and r['fields'].get('detail_level')!='STRUCTURED' for r in candidates):
                errors.append('기존 서비스 분석에 기능·권한·상태·데이터·지표 구조를 보완하세요.')
        active=[r for r in rows if r['applicability']=='applicable']
        nodes=[r for r in active if r['output_type'] in GRAPH_TYPES]
        by_type={t:[r for r in nodes if r['output_type']==t] for t in GRAPH_TYPES}
        parent={'problem':'evidence','story_candidate':'problem','requirement':'story_candidate',
                'acceptance':'requirement','uat':'acceptance','metric':'uat'}
        graph=[]
        graph_labels={'evidence':'근거 카드 (E)','problem':'문제·인사이트 (I)','story_candidate':'사용자 스토리 (US)',
            'requirement':'요구사항 (R)','acceptance':'인수 기준 (AC)','uat':'UAT 과업 (T)','metric':'지표 (M)'}
        for row in nodes:
            for link in row['links']:
                target=by_id.get(link['id'])
                if target is None:errors.append(row['title']+': 연결 노드를 같은 묶음에 포함하세요.');continue
                graph.append({'from':target['id'],'to':row['id'],'relation':link['relation'],
                    'source_version':target['version'],'target_version':row['version'],'reason':link['reason'],
                    'identity_check':'CURRENT_VERSION','semantic_review':'PO_REVIEWED',
                    'reviewed_by':row['reviewed_by'],'reviewed_at':row['reviewed_at']})
            expected=parent.get(row['output_type'])
            if expected and not any(l['id'] in by_id and by_id[l['id']]['output_type']==expected and l['relation']!='refutes' for l in row['links']):
                errors.append(row['title']+': '+graph_labels[expected]+' 연결이 필요합니다.')
        for kind,tag in GRAPH_TYPES.items():
            if not by_type[kind]:errors.append(graph_labels[kind]+'가 없습니다.')
        for kind,child in (('requirement','acceptance'),('acceptance','uat'),('uat','metric')):
            for row in by_type[kind]:
                if not any(any(l['id']==row['id'] and l['relation']!='refutes' for l in c['links']) for c in by_type[child]):
                    errors.append(row['title']+': '+graph_labels[child]+' 검증 연결이 없습니다.')
        if stage=='development':
            def supporting_evidence(row):
                pending=[row];seen=set();evidence=[]
                while pending:
                    current=pending.pop()
                    if current['id'] in seen:continue
                    seen.add(current['id'])
                    if current['output_type']=='evidence':evidence.append(current);continue
                    pending.extend(by_id[link['id']] for link in current['links'] if link['relation']!='refutes' and link['id'] in by_id)
                return evidence
            for requirement in by_type['requirement']:
                evidence=supporting_evidence(requirement)
                if evidence and all(synthetic_record(e) or e['fields']['evidence_type'] in ('SYNTHETIC_FGI','ASSUMPTION') for e in evidence):
                    errors.append(requirement['title']+': 합성·가정만 연결된 요구는 파일럿 제안이며 개발 준비 완료로 승격할 수 없습니다.')
        for row in active:
            f=row['fields'];kind=row['output_type']
            claim_status=row.get('effective_claim_status',f.get('claim_status'))
            if kind=='evidence' and claim_status in ('UNVERIFIED','CONFLICTED','REFUTED'):
                warnings.append(row['title']+': '+claim_status+' 근거. 단정된 사실로 사용하지 마세요.')
            if kind=='conflict' and f['resolution_status']=='UNRESOLVED':
                warnings.append(row['title']+': 충돌 미해결 · '+f['owner'])
                if stage=='development' and f['severity']=='BLOCKING':errors.append(row['title']+': 개발 차단 충돌')
            topics=f.get('open_topics',[]) if kind=='service_baseline' else [a for a in f.get('answers',[]) if a['status']!='ANSWERED'] if kind=='po_interview' else []
            for topic in topics:
                warnings.append(row['title']+': '+topic['id']+' · '+topic['next_action'])
                if stage=='development' and topic['needed_stage'] in ('RESEARCH','DEVELOPMENT'):errors.append(topic['id']+': 개발 전 PO 확인 필요')
            if row.get('computed',{}).get('status') in ('UNKNOWN','NOT_COMPUTABLE','INVALID'):
                warnings.append(row['title']+': 계산 미확인 · '+row['computed'].get('reason','입력을 확인하세요.'))
            if kind=='question' and f['status']=='OPEN':
                warnings.append(row['title']+': '+f['next_action'])
                if stage=='development' and f['needed_stage'] in ('RESEARCH','DEVELOPMENT'):errors.append(row['title']+': 개발 전 확인 필요')
            if stage=='development' and kind=='actual_study' and f['run_state']=='NOT_RUN':
                errors.append(row['title']+': 필요한 실제 조사가 미실행입니다.')
        families={r['fields']['source_family_id'] for r in active if r['output_type'] in ('source_manifest','benchmark_source','evidence')
            and r['fields'].get('source_family_id') and r['fields'].get('evidence_type') not in ('ASSUMPTION','SYNTHETIC_FGI','USER_REQUIREMENT')}
        return {'pack_id':pack['id'],'pack_version':pack['version'],'stage':stage,'ready':not errors,
            'status':('research_review_ready' if stage=='research' else 'development_review_ready') if not errors else 'needs_review',
            'checks':checks,'errors':list(dict.fromkeys(errors)),'warnings':list(dict.fromkeys(warnings)),
            'graph':graph,'source_family_count':len(families),'customer_validation':'NOT_CERTIFIED',
            'uat':'NOT_CERTIFIED','external_receipt':'NOT_VERIFIED','computed_at':timestamp()}

    def research_impact(self, user):
        p=user['project_id'];result=[]
        for kind in ('research_item','research_pack','definition','user_story'):
            for row in self.store.list(p,kind):
                if not self.accessible(p,row):
                    # No stale body/title or protected source metadata is disclosed.
                    result.append({'id':row['id'],'kind':kind,'version':row['version'],'state':'requires_review'})
        return {'affected':result,'action':'현재 근거로 산출물을 다시 검토한 뒤 묶음의 버전을 다시 선택하세요.'}

    def compose_research_prd(self, user, body):
        p=user['project_id'];epoch=self.store.epoch(p)
        pack=self.store.get(p,'research_pack',text(body,'pack_id',80))
        if pack['version']!=revision(body):raise AppError('묶음 버전이 변경됐습니다.',409)
        rows=self.pack_rows(user,pack);sections=[]
        areas={'evidence':['evidence'],'conflict':['evidence','questions'],'numeric_claim':['metrics'],
            'metric':['metrics'],'calculation':['metrics','economics'],'funnel':['metrics','validation'],'voc_coding':['evidence','users'],'po_interview':['decision','questions'],'problem':['evidence'],'story_candidate':['stories'],'requirement':['requirements'],
            'acceptance':['requirements'],'uat':['validation'],'question':['questions'],'utility':['economics']}
        for section_id,title in PRD_SECTIONS.items():
            selected=[r for r in rows if section_id in (TASKS[r['task_id']][2] if r.get('task_id') else areas.get(r['output_type'],[]))]
            content='\n\n'.join(r['title']+'\n'+render_item(r)+'\n['+r['id']+']' for r in selected)
            sections.append({'id':section_id,'title':title,'text':content,'evidence_ids':[r['id'] for r in selected],
                'coverage_status':'written' if selected else 'needs_work','na_reason':''})
        fields={**derived_nature(rows),'stage':'prd','title':text(body,'title',200),'sections':sections,'questions':[],
            'assumptions':['선택한 검토 산출물을 배치한 연구 초안. 고객 검증·개발 승인·UAT 실행을 뜻하지 않습니다.'],
            'story_refs':[],'research_pack_ref':{'id':pack['id'],'version':pack['version']},'research_id':pack['research_id'],
            'state':'draft','dependencies':lineage(rows)+[{'kind':'research_pack','id':pack['id'],'version':pack['version']}],
            'authored_by':user['id'],'authorship':'research_compilation','confirmed_version':None}
        if len(json.dumps(fields,ensure_ascii=False))>140000:raise AppError('필수 항목을 유지하면서 연구 묶음을 나누세요.',409)
        self.planning_actor(user)
        return self.store.write(p,inserts=[('definition',fields,'DOC-'+uuid.uuid4().hex)],expected_epoch=epoch)[0]

    def research_handoff(self, user, body):
        p=user['project_id'];epoch=self.store.epoch(p)
        pack=self.store.get(p,'research_pack',text(body,'pack_id',80))
        if pack['version']!=revision(body):raise AppError('인계할 묶음 버전을 확인하세요.',409)
        rows=self.pack_rows(user,pack);previous=None
        if body.get('previous_export_id'):
            previous=self.store.get(p,'research_export',body['previous_export_id'])
            if not self.research_history_visible(user,previous):raise AppError('이전 인계의 권한·근거를 다시 확인하세요.',409)
            if previous['research_id']!=pack['research_id']:raise AppError('같은 연구의 이전 인계를 선택하세요.')
        # Input references can name private planning assets. Export only reviewed
        # research bodies and links between included items, never raw assets or
        # their identifiers, transcripts, filenames, snippets or owner metadata.
        ids={r['id'] for r in rows};safe=[]
        for r in rows:
            item={k:r[k] for k in ('id','version','title','output_type','task_id','fields','applicability','na_reason','reviewed_by','reviewed_at','change_reason')}
            for key in ('conflict_ids','effective_claim_status','evidence_nature','contains_synthetic','computed','interview_application'):
                if key in r:item[key]=r[key]
            item['links']=[l for l in r['links'] if l['kind']=='research_item' and l['id'] in ids]
            safe.append(item)
        old={r['id']:r for r in previous['package']['items']} if previous else {}
        current={r['id']:r for r in safe}
        changes=[{'id':rid,'before':old.get(rid),'after':current.get(rid),'reason':body.get('reason','')}
            for rid in sorted(set(old)|set(current)) if old.get(rid)!=current.get(rid)]
        if previous and changes and not optional(body,'reason',3000):raise AppError('변경분 인계 이유를 기록하세요.')
        gate=self.research_gate(user,{'pack_id':pack['id'],'expected_version':pack['version']})
        package={'schema_version':'npd.research-handoff.v1','research_id':pack['research_id'],
            'pack_ref':{'id':pack['id'],'version':pack['version']},'items':safe,'changes':changes,
            'base_export_id':previous['id'] if previous else None,'review':gate,
            'customer_validation':'NOT_CERTIFIED','external_receipt':'NOT_VERIFIED',
            'disclosure':'PO 검토 자료입니다. 원본과 비공개 입력 참조는 제외했습니다. AXIOM 수신·개발 승인·고객 검증은 별도입니다.'}
        digest=hashlib.sha256(json.dumps(package,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        self.planning_actor(user)
        saved=self.store.write(p,inserts=[('research_export',{'research_id':pack['research_id'],'package':package,
            'hash':digest,'created_by':user['id'],'dependencies':lineage(rows)+[{'kind':'research_pack','id':pack['id'],'version':pack['version']}],
            'receipt_status':'NOT_VERIFIED'},'REX-'+uuid.uuid4().hex)],expected_epoch=epoch)[0]
        return {'export_id':saved['id'],'hash':digest,'package':package,
            'markdown':'# '+pack['title']+'\n\n'+package['disclosure']+'\n\n'+'\n\n'.join('## '+r['title']+'\n'+render_item(r) for r in rows)}

    def research_history_visible(self, user, record):
        """Old export versions may be diffed, but current revocations still win.

        Freshness is checked separately for new input. Here we retain a labelled
        old snapshot only while every original source remains authorized.
        """
        p=user['project_id'];allowed={(e['kind'],e['id']) for e in self.knowledge(p)}
        pending=list(record.get('dependencies',[]));seen=set()
        while pending:
            dep=pending.pop();key=(dep['kind'],dep['id'])
            if key in seen:continue
            seen.add(key)
            if len(seen)>2000:return False
            if dep['kind'] in ('insight','voc'):
                if key not in allowed:return False
            elif dep['kind'] in REF_KINDS+('research_pack','persona'):
                try:current=self.store.get(p,dep['kind'],dep['id'])
                except AppError:return False
                if current.get('withdrawn'):return False
                pending.extend(current.get('dependencies',[]))
            else:return False
        return True

    def record_research_receipt(self, user, body):
        p=user['project_id'];epoch=self.store.epoch(p)
        exported=self.store.get(p,'research_export',text(body,'export_id',80))
        if not self.research_history_visible(user,exported):raise AppError('인계 자료의 접근 범위를 다시 확인하세요.',409)
        if body.get('package_hash')!=exported['hash']:raise AppError('수신한 패키지 해시가 다릅니다.')
        mapping=body.get('mapping',[])
        if not isinstance(mapping,list) or len(mapping)>100:raise AppError('수신 ID 대조 목록을 확인하세요.')
        expected={r['id']:r['version'] for r in exported['package']['items']}
        seen=set();destinations=set();clean=[]
        for m in mapping:
            if not isinstance(m,dict) or type(m.get('source_version')) is not int or m.get('source_id') in seen or expected.get(m.get('source_id'))!=m.get('source_version'):
                raise AppError('수신 대조의 원본 ID·버전이 다릅니다.')
            target=text(m,'destination_id',200)
            if target in destinations:raise AppError('수신 ID가 중복되었습니다.')
            seen.add(m['source_id']);destinations.add(target);clean.append({'source_id':m['source_id'],
                'source_version':m['source_version'],'destination_id':target})
        if seen!=set(expected):raise AppError('모든 산출물의 수신 ID를 대조하세요.')
        self.planning_actor(user)
        return self.store.write(p,inserts=[('research_receipt',{'export_id':exported['id'],'package_hash':exported['hash'],
            'destination':text(body,'destination',500),'receipt_reference':text(body,'receipt_reference',3000),
            'mapping':clean,'recorded_by':user['id'],'recorded_at':timestamp(),
            'status':'PO_RECONCILED','verification_method':'manual_receipt_attestation','live_transport_verified':False,
            'dependencies':exported['dependencies']},None)],updates=[('research_export',exported['id'],
                {'receipt_status':'PO_RECONCILED'},exported['version'])],expected_epoch=epoch)[0]
