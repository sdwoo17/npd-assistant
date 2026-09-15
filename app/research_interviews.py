"""PO interview records and explicitly applied, versioned service revisions."""
import copy
from .contracts import text, strings, revision
from .store import AppError
from .research_provenance import synthetic_record


class ResearchInterviews:
    def start_po_interview(self,user,body):
        epoch=self.store.epoch(user['project_id'])
        baseline=self.research_ref(user,{'kind':'research_item','id':text(body,'baseline_id',80),'version':revision(body)},True)
        if baseline['output_type']!='service_baseline':raise AppError('검토한 기존 서비스 분석을 선택하세요.')
        answers=[{**q,'status':'OPEN','answer':'','respondent_role':'','confirmed_at':'','effective_at':'','scope':'',
            'classification':'UNVERIFIED','evidence_note':'','target_field':'unknowns','proposed_text':''}
            for q in baseline['fields'].get('open_topics',[])]
        if not answers:raise AppError('기존 서비스 분석에 PO 확인 질문을 먼저 작성하세요.')
        return self.save_research_item(user,{'research_id':baseline['research_id'],'output_type':'po_interview',
            'title':text(body,'title',200),'input_refs':[{'kind':baseline['kind'],'id':baseline['id'],'version':baseline['version']}],
            'fields':{'run_state':'NOT_RUN','nature':'SYNTHETIC' if synthetic_record(baseline) else 'PO_REPORTED',
                'purpose':'기존 서비스 문서와 PO 설명의 현재 운영·예외·향후 희망 구분',
                'limitations':'질문만 준비한 미실행 인터뷰입니다. 고객 수요 검증과 별도입니다.','answers':answers},
            'change_reason':'검토한 문서의 미결 질문으로 인터뷰 준비'},expected_epoch=epoch)

    def apply_po_interview(self,user,body):
        epoch=self.store.epoch(user['project_id'])
        interview=self.research_ref(user,{'kind':'research_item','id':text(body,'interview_id',80),'version':revision(body)},True)
        if interview['output_type']!='po_interview':raise AppError('검토한 PO 인터뷰를 선택하세요.')
        baseline=self.research_ref(user,{'kind':'research_item','id':text(body,'baseline_id',80),'version':body.get('baseline_version')},True)
        if baseline['output_type']!='service_baseline' or baseline['research_id']!=interview['research_id']:
            raise AppError('같은 연구의 서비스 분석을 선택하세요.')
        if not any(r['kind']=='research_item' and r['id']==baseline['id'] and r['version']==baseline['version'] for r in interview['input_refs']):
            raise AppError('인터뷰에서 검토한 기존 서비스 버전과 다릅니다.',409)
        ids=strings(body.get('answer_ids',[]),100,80)
        answers=[a for a in interview['fields']['answers'] if a['id'] in ids]
        if not ids or len(answers)!=len(ids):raise AppError('반영할 답변을 선택하세요.')
        fields=copy.deepcopy(baseline['fields']);changes=[];used=set()
        for a in answers:
            if a['status']!='ANSWERED' or a['classification']=='UNVERIFIED' or not a['proposed_text']:
                raise AppError('확인한 답변과 반영 문장이 필요합니다.',409)
            key=a['target_field']
            if a['classification']=='FUTURE_REQUEST' and key!='proposed':
                raise AppError('향후 희망사항은 제안 흐름에만 반영할 수 있습니다.',409)
            if a['classification']=='EXCEPTION' and key!='exceptions':
                raise AppError('예외 상황은 예외 항목에 범위를 명시해 반영하세요.',409)
            if key in used and key not in ('proposed','exceptions'):raise AppError('같은 항목의 여러 답변은 문장을 통합해 다시 검토하세요.')
            used.add(key);before=fields.get(key,'')
            after=(before+'\n향후 희망사항: '+a['proposed_text']).strip() if a['classification']=='FUTURE_REQUEST' else a['proposed_text']
            if a['classification']=='EXCEPTION':after=(before+'\n예외: '+a['proposed_text']+' · 적용 범위: '+a['scope']).strip()
            if a['classification']=='OPERATION_CHANGE':after+='\n적용일: '+a['effective_at']+' · 적용 범위: '+a['scope']
            fields[key]=after
            changes.append({'question_id':a['id'],'field':key,'before':before,'after':after,
                'classification':a['classification'],'confirmed_at':a['confirmed_at'],'effective_at':a['effective_at'],
                'scope':a['scope'],'respondent_role':a['respondent_role'],'evidence_note':a['evidence_note']})
        # Scalar corrections invalidate related structured tables until the PO reconciles them.
        affected={'as_is':('feature_matrix',),'roles':('role_matrix',),'permissions':('role_matrix',),
            'states':('state_models',),'exceptions':('state_models','data_contracts')}
        invalidated=sorted({table for key in used for table in affected.get(key,())})
        for table in invalidated:fields[table]=[]
        if any(a['classification']=='OPERATION_CHANGE' and a['target_field']!='proposed' for a in answers):
            fields['document_as_of']=''  # PO must reconcile the new operating date explicitly.
        # Keep unanswered topics and don't infer that an answer resolves other risks.
        fields['open_topics']=[q for q in fields.get('open_topics',[]) if q['id'] not in ids]
        for answer in interview['fields']['answers']:
            if answer['status']!='ANSWERED' and answer['id'] not in {q['id'] for q in fields['open_topics']}:
                fields['open_topics'].append({k:answer[k] for k in ('id','question','owner','needed_stage','next_action')})
        refs=[{'kind':r['kind'],'id':r['id'],'version':r['version']} for r in (baseline,interview)]
        return self.save_research_item(user,{'research_id':baseline['research_id'],'output_type':'service_baseline','task_id':'SV-02',
            'title':text(body,'title',200),'fields':fields,'input_refs':refs,'change_reason':text(body,'reason',3000)},
            expected_epoch=epoch,provenance={'baseline':refs[0],'interview':refs[1],'changes':changes,'tables_to_reconcile':invalidated,'applied_by':user['id']})

    def research_item_history(self,user,rid):
        rows=self.store.history(user['project_id'],'research_item',rid)
        return [r if self.accessible(user['project_id'],r) and not r.get('withdrawn') else self.redacted(r) for r in rows]
