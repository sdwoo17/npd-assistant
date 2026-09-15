"""Deterministic research calculations and review of structured records."""
from decimal import Decimal, localcontext
import math
from datetime import date
from collections import Counter


def detail_errors(row, contracts):
    kind=row['output_type'];f=row['fields'];errors=[]
    for key,spec in contracts[kind].items():
        if spec['type']!='rows':continue
        entries=f.get(key,[]);seen=set()
        for index,entry in enumerate(entries,1):
            rid=entry.get('id')
            if not rid or rid in seen:errors.append(spec['label']+': 행 ID는 비어 있지 않고 고유해야 합니다.')
            seen.add(rid)
            for name,col in spec['columns'].items():
                if col['required'] and entry.get(name) in (None,'',[]):
                    errors.append(spec['label']+' '+str(index)+'행: '+col['label']+'을 작성하세요.')
    if kind=='service_baseline' and f.get('detail_level')=='STRUCTURED':
        for key in ('service_version','document_as_of','data_as_of'):
            if not f.get(key):errors.append(contracts[kind][key]['label']+'을 작성하세요.')
    if kind=='po_interview':
        for a in f['answers']:
            if a['status']=='ANSWERED':
                if f['run_state']!='EXECUTED':errors.append('미실행 인터뷰의 답변을 확인 완료로 기록할 수 없습니다.')
                for key in ('answer','respondent_role','confirmed_at','effective_at','scope','evidence_note'):
                    if not a.get(key):errors.append(a['id']+': 답변·역할·날짜·적용일·범위·근거 유무를 기록하세요.');break
                try:date.fromisoformat(a['confirmed_at'])
                except (ValueError,TypeError):errors.append(a['id']+': 확인 날짜는 YYYY-MM-DD 형식이어야 합니다.')
                if a['classification']=='UNVERIFIED':errors.append(a['id']+': 확인된 답변의 문서와의 관계를 선택하세요.')
            elif a['answer'] or a['proposed_text']:
                errors.append(a['id']+': 미결·보류 답변을 현재 사실로 저장할 수 없습니다. 확인 후 답변 상태를 바꾸세요.')
        if f['run_state']=='EXECUTED' and not any(a['status']=='ANSWERED' for a in f['answers']):
            errors.append('인터뷰 실행에는 확인한 답변이 하나 이상 필요합니다.')
    if kind in ('calculation','funnel'):
        values=f['terms'] if kind=='calculation' else f['stages']
        keys=('numerator','denominator') if kind=='calculation' else ('count',)
        if f['measurement_state']=='UNKNOWN' and (not f['unknown_reason'] or any(v[k] is not None for v in values for k in keys)):
            errors.append('미측정은 숫자를 비우고 이유를 기록하세요.')
        if f['measurement_state'] in ('OBSERVED','SYNTHETIC') and any(v[k] is None for v in values for k in keys):
            errors.append('계산 입력이 누락됐습니다. 0으로 대신할 수 없습니다.')
        if any(v[k] is not None and v[k]<0 for v in values for k in keys):errors.append('집계 입력은 음수일 수 없습니다.')
    if kind=='funnel':
        if len(f['stages'])<2:errors.append('퍼널 단계가 두 개 이상 필요합니다.')
        if not f['nesting_confirmed']:errors.append('같은 집단의 누적 부분집합인지 확인하세요.')
        counts=[s['count'] for s in f['stages']]
        if any(v is not None and type(v) is not int for v in counts):errors.append('퍼널 대상 수는 정수여야 합니다.')
        if any(a is not None and b is not None and a<b for a,b in zip(counts,counts[1:])):
            errors.append('뒤 단계의 누적 대상 수가 앞 단계보다 클 수 없습니다.')
    if kind=='voc_coding':
        participants={}
        for v in f['records']:
            if v['disposition']=='INCLUDED' and v['participant_key']:
                old=participants.setdefault(v['participant_key'],v['nature'])
                if old!=v['nature']:errors.append('한 참여자 키를 실제·합성 표본에 동시에 사용할 수 없습니다.')
    return errors


def computed_details(kind, f):
    def divide(n,d,multiplier=1):
        if d==0:return None
        with localcontext() as ctx:
            ctx.prec=28
            result=float(Decimal(str(n))/Decimal(str(d))*multiplier)
            return result if math.isfinite(result) else None
    if kind=='calculation':
        entries=f['terms'];state=f['measurement_state']
        if state=='UNKNOWN' or not entries or any(r[k] is None for r in entries for k in ('numerator','denominator')):
            return {'status':'UNKNOWN','value':None,'reason':f['unknown_reason'] or '계산 입력 미완성'}
        if any(r[k]<0 for r in entries for k in ('numerator','denominator')):
            return {'status':'INVALID','value':None,'reason':'음수 입력을 수정하세요.'}
        n=sum((Decimal(str(r['numerator'])) for r in entries),Decimal(0))
        d=sum((Decimal(str(r['denominator'])) for r in entries),Decimal(0))
        scale=100 if f['formula']=='PERCENT_OF_SUMS' else 1
        value=divide(n,d,scale)
        if not all(math.isfinite(float(v)) for v in (n,d)) or (d and value is None):
            return {'status':'NOT_COMPUTABLE','value':None,'reason':'표현 가능한 수치 범위를 초과했습니다. 단위를 조정하고 다시 검토하세요.'}
        return {'status':'NOT_COMPUTABLE' if not d else state,'value':value,
            'numerator_sum':float(n),'denominator_sum':float(d),'formula':'sum(numerator) / sum(denominator)'+(' * 100' if scale==100 else ''),
            'reason':'분모 0은 산출 불가이며 0%가 아닙니다.' if not d else '개별 비율의 평균을 사용하지 않습니다.',
            'causal_effect':'NOT_ESTABLISHED','unit':f['unit']}
    if kind=='funnel':
        counts=[r['count'] for r in f['stages']]
        if any(n is not None and (n<0 or type(n) is not int) for n in counts) or any(a is not None and b is not None and b>a for a,b in zip(counts,counts[1:])) or not f['nesting_confirmed']:
            return {'status':'INVALID','steps':[],'reason':'동일 시작 집단의 누적 단계·정수 입력을 확인하세요.'}
        first=f['stages'][0]['count'] if f['stages'] else None;previous=None;out=[]
        for i,r in enumerate(f['stages']):
            n=r['count'];valid=f['measurement_state']!='UNKNOWN' and n is not None
            out.append({'id':r['id'],'count':n,'of_start_percent':divide(n,first,100) if valid and first is not None else None,
                'of_previous_percent':divide(n,previous,100) if valid and previous is not None else None,
                'dropoff_from_previous':previous-n if valid and previous is not None else None})
            previous=n
        return {'status':f['measurement_state'],'steps':out,'interpretation':'동일 시작 집단의 누적 도달입니다. 이탈 원인이나 전체 고객 비율은 입증하지 않습니다.'}
    if kind=='voc_coding':
        values=f['records'];included=[v for v in values if v['disposition']=='INCLUDED']
        missing=sum(not v['participant_key'] for v in included)
        return {'included_utterances':len(included),'excluded':sum(v['disposition']=='EXCLUDED' for v in values),
            'duplicates':sum(v['disposition']=='DUPLICATE' for v in values),
            'real_utterances':sum(v['nature']=='REAL' for v in included),'synthetic_utterances':sum(v['nature']=='SYNTHETIC' for v in included),
            'independent_participants':None if missing else len({v['participant_key'] for v in included}),
            'participant_keys_missing':missing,'code_counts':dict(Counter(v['code'] for v in included)),
            'codebook_version':f['codebook_version'],'population_inference':'NOT_ESTABLISHED'}
    return None
