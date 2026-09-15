"""Versioned research contracts shared by forms, review and PRD handoff.

These are NPD contracts, not a claim of compatibility with an external PRD API.
Unknown measurements and planned studies must remain distinguishable from facts.
"""
import math
from .contracts import optional, strings
from .store import AppError

SCHEMA_VERSION = 'npd.research.v1'
EVIDENCE_TYPES = ('PUBLIC_FACT','ATTACHMENT_STATEMENT','INTERNAL_MEASUREMENT','REAL_VOC',
                  'SYNTHETIC_FGI','ASSUMPTION','USER_REQUIREMENT')
PRD_SECTIONS = {
    'decision':'결정 요약','evidence':'근거와 문제','users':'사용자와 과업',
    'metrics':'목표와 지표','scope':'대안과 범위','stories':'사용자 스토리',
    'requirements':'요구사항과 인수 기준','data':'화면과 데이터',
    'constraints':'비기능 요구사항','validation':'검증과 출시',
    'economics':'경제성과 의존성','questions':'질문과 결정',
}
TASKS = {
    'RS-00':('조사 브리프·범위','brief',['decision','scope','questions']),
    'SV-01':('문서·원문 위치·읽기 범위','source_manifest',['evidence','data','questions']),
    'SV-02':('현재 업무·문제','service_baseline',['decision','users','stories','data']),
    'SV-03':('데이터·API·비기능 제약','constraint',['requirements','data','constraints','economics']),
    'VC-01':('VoC 수집·표본','voc_collection',['evidence','users','validation']),
    'VC-02':('VoC 코딩·반대 사례','voc_finding',['evidence','users','scope','stories']),
    'VC-03':('내부 실측·기준선','measurement',['metrics','validation','economics']),
    'FG-01':('가설·조사 가이드','study_plan',['evidence','users','validation','questions']),
    'FG-02':('가상 FGI 디브리프','synthetic_debrief',['evidence','scope','stories','validation']),
    'FG-03':('실제 조사 결과','actual_study',['evidence','users','validation']),
    'BM-01':('벤치마크 원문 확인','benchmark_source',['evidence','constraints']),
    'BM-02':('범위·수치·비교 가능성','benchmark_comparison',['evidence','metrics','scope']),
    'BM-03':('적용 판단·대안','alternative',['scope','economics','questions']),
    'SY-01':('문제·가설 종합','hypothesis',['decision','evidence','scope','stories']),
    'SY-02':('지표·검증·경제성','validation_plan',['metrics','validation','economics']),
    'SY-03':('PRD 초안·연결 계획','synthesis',['requirements','stories','questions']),
    'SY-04':('검토·변경·인계','handoff_plan',['validation','questions']),
}

# Each field is rendered as a labelled input. Drafts may omit required content;
# review validates the same contract again and reports precise missing fields.
def field(label, kind='text', required=True, options=None):
    return {'label':label,'type':kind,'required':required, **({'options':options} if options else {})}
def enum(label, options, required=True):
    return field(label,'select',required,options)
def fields(**values):
    return {k: field(v) if isinstance(v,str) else v for k,v in values.items()}

TYPES = {
 'brief': fields(product_scope='제품·시장·대상 범위',decision_question='지금 결정할 질문',
    exclusions='제외 범위와 이유',owner='결정 책임자',deadline='필요 시점·단계',
    required_tasks=field('수행할 조사 작업','lines')),
 'source_manifest': fields(source_family_id='동일 출처 계열 ID',relationship='원본·번역·요약 관계',
    publisher='발행자 또는 미확인 이유',language='언어',media='자료 유형',
    source_locator='원문 문단·표/셀·페이지·슬라이드 위치',read_scope='읽은 범위',unread_scope='읽지 못한 범위·이유',
    source_event_date=field('사건 시점 또는 미확인 이유'),published_at='발행 시점 또는 미확인 이유',
    accessed_at='확인 시점',source_hash=field('원본 해시',required=False)),
 'service_baseline': fields(roles='사용자·역할',jobs='상황·JTBD',as_is='현재 업무 흐름',
    proposed='제안 흐름·현재와의 차이',states='상태·전이',exceptions='실패·빈값·재시도',
    permissions='권한·정책',pain_points='관찰한 문제·다른 원인',unknowns='운영 여부가 미확인인 항목'),
 'constraint': fields(capability='데이터·이벤트·API·정책',support_status=enum('지원 상태',['UNKNOWN','DOCUMENTED','TESTED','UNSUPPORTED']),
    contract_version='명세 버전·출처',owner='확인 책임자',event_time='발생·수신·유효 시점',
    data_definition='필드·상태·식별자·중복 기준',permission='실행·조회 권한',failure='실패·미확인·복구',
    evaluation='부하·지연·접근성 평가 조건',verification='실행 증거 또는 다음 확인 작업'),
 'voc_collection': fields(method='수집 경로·방법',population='대상 집단·모집 기준',period='수집 기간',
    included='포함 기준',excluded='제외 기준',deduplication='중복·독립 참여자 판정',
    utterances=field('유효 발언 수','number'),participants=field('독립 참여자 수','number'),
    excluded_count=field('제외 수','number'),real_count=field('실제 발언 수','number'),synthetic_count=field('합성 발언 수','number'),
    privacy='동의·비식별 처리',unknown_reason=field('알 수 없는 집계와 이유',required=False)),
 'voc_finding': fields(codebook_version='코드북 버전',role_context='역할·상황·과업',observation='관찰 사실',
    interpretation='해석·니즈',impact='영향·현재 대처',counterexamples='반대 사례 또는 미확인 이유',
    alternatives='다른 원인',frequency='빈도·분모·필터',limitations='표본 한계'),
 'measurement': fields(metric_name='측정 지표',measurement_origin=enum('측정 대상 구분',['INTERNAL','EXTERNAL','SYNTHETIC']),value=field('측정값','number',False),
    measurement_state=enum('기준선 상태',['UNKNOWN','OBSERVED','SYNTHETIC']),unknown_reason=field('미측정 이유',required=False),
    numerator='분자·계산 정의',denominator='분모·계산 정의',unit='단위',population='대상·세그먼트',period='기간·시간대',
    data_version='데이터·쿼리 버전',filters='필터·중복·제외',missing='결측·지연·품질',
    verification='검증 방법·근거 위치',owner='검증 책임자',next_action='다음 측정·확인 과업'),
 'study_plan': fields(hypothesis='검증할 가설',segments='역할별 모집·표본 계획',method='가상 탐색·실제 관찰 구분',
    tasks='관찰 과업',questions='연구 질문·비유도 프로브',guide='8개 항목 가이드 또는 연결한 가이드 요약',
    rejection='지지·기각·보류 조건',bias='편향·한계',owner='실행 책임자',privacy='동의·비식별 계획'),
 'synthetic_debrief': fields(personas='페르소나·프로필 버전',common_needs='공통 니즈',disagreements='의견 차이',
    hypotheses='가설',unsupported_claims='근거 없는 주장',followup_questions='실제 확인할 질문·과업',
    limitations='합성 탐색의 한계'),
 'actual_study': fields(run_state=enum('실행 상태',['NOT_RUN','EXECUTED']),method='조사 방법',
    planned_sample='계획 표본·모집 기준',actual_sample=field('실제 표본·역할별 구성',required=False),
    independent_participants=field('독립 참여자 수','number',False),executed_at=field('실행 시점',required=False),
    executor=field('실행 확인자',required=False),attestation=field('실제 실행을 확인함','checkbox',False),
    privacy='동의·비식별 처리',observations='발언·관찰 위치와 내용',counterexamples='반대 사례·한계',
    outcome='가설 판정 또는 아직 필요한 조사',synthetic_difference='가상 예상과 실제 결과 차이'),
 'benchmark_source': fields(source_family_id='출처 계열',publisher='발행자',language='언어',
    verification_state=enum('원문 확인',['SNIPPET_ONLY','PASSAGE_REVIEWED']),source_locator='확인 위치',
    accessed_at='확인 시점',feature_scope='기능·시장·계정·형식·UI/API',effective_date='기능 적용 시점·미확인 이유',
    availability=enum('기능 상태',['UNKNOWN','BETA','GA','ROADMAP','RETIRED']),unread_scope='미확인 범위·한계'),
 'benchmark_comparison': fields(comparison_axes='비교 축·조건',population='대상·관찰/배정 단위',period='기간·시장',
    budget_fees='예산·수수료 차이',attribution='귀속·추적 범위',comparability=enum('비교 가능성',['UNKNOWN','COMPARABLE','NOT_COMPARABLE']),
    design=enum('비교 설계',['OBSERVATIONAL','RANDOMIZED','UNKNOWN']),differences='비교 불가 차이·충돌',next_action='추가 확인'),
 'alternative': fields(approach='선택지·현상 유지',decision=enum('판단',['ADOPT','TEST','DEFER','EXCLUDE']),
    comparison='현상 유지와 다른 선택지 비교',compared_options=field('현상 유지 외 비교 선택지','lines'),
    fewer_options_reason=field('비교 선택지가 둘 미만인 이유',required=False),rationale='선택·보류·제외 이유',value='기대 가치',
    costs='비용·재작업·위험',conditions='우리 제품에 적용할 전제',owner='결정 책임자'),
 'hypothesis': fields(problem='문제·JTBD',claim='가설·원인 후보',support='지지 근거',counterevidence='반대 근거·다른 원인',
    method='검증 방법',decision_rule='지지·기각·보류 조건',owner='검증 책임자',
    outcome=enum('판정',['UNVERIFIED','SUPPORTED','REFUTED','INCONCLUSIVE']),change_reason='기획 변경 또는 후속 조사'),
 'validation_plan': fields(outcome_metric='성공 지표·관찰 단위',baseline='기준선 상태·출처 또는 미측정 이유',
    proposed_target='제안 목표·근거',guardrails='오류·피해·중단 기준',design='실험 가능성·대안 설계',
    event_coverage='배정·관찰·이벤트·귀속·성숙도',uat_plan='UAT 과업·기대 결과·미실행 상태',
    economics='비용·수익·ROI 식·검토·재작업 가정',dependencies='권리·운영·API 의존성',owner='검증·출시 책임자'),
 'synthesis': fields(decision='문제·결정 요약',scope='채택·제외·보류 범위',trace_plan='스토리·요구·AC·UAT·지표 연결 계획',
    uncertainties='남은 가정·충돌·질문',owner='검토 책임자'),
 'handoff_plan': fields(approval_scope='PO 확정 범위·개발 승인과의 구별',blocking_questions='개발 착수 차단 질문',
    next_research='담당자·후속 조사·필요 단계',changes='변경 전후·이유·기준 버전',
    receiving_contract='수신 규격 또는 미확인 이유',owner='인계 확인 책임자'),
 'evidence': fields(evidence_type=enum('근거 유형',list(EVIDENCE_TYPES)),claim='주장',
    source_locator='원문 위치·확인 범위',source_family_id='동일 출처 계열',publisher='발행자·실행자',language='언어',
    published_at='발행 시점·미확인 이유',accessed_at='확인 시점',source_event_date='실제 사건 시점·미확인 이유',
    scope_notes='기능·시장·계정·시점·적용 조건',claim_status=enum('주장 상태',['UNVERIFIED','SUPPORTED','CONFLICTED','REFUTED']),
    confidence_reason='판단 근거',limitations='한계',supports_demand_validation=field('실제 수요 검증에 사용','checkbox',False)),
 'conflict': fields(dimension='날짜·수치·정책·기능 범위',left_claim='첫 주장·원문 위치',right_claim='상대 주장·원문 위치',
    severity=enum('중요도',['BLOCKING','NONBLOCKING']),scope='같은 조건인지·다른 범위인지',
    resolution_status=enum('판단 상태',['UNRESOLVED','RESOLVED','SCOPED_OUT']),provisional='잠정 판단',
    needed_evidence='해결에 필요한 증거',owner='결정 책임자',resolution=field('해결·제외 근거와 이유',required=False)),
 'numeric_claim': fields(source_display='원문 수치 표현',value=field('정규화 값','number'),unit='단위',
    source_kind=enum('원문 수치 의미',['LEVEL','RELATIVE_CHANGE','PERCENTAGE_POINT','MULTIPLE','SHARE']),
    claim_kind=enum('사용할 의미',['LEVEL','RELATIVE_CHANGE','PERCENTAGE_POINT','MULTIPLE','SHARE']),
    provenance=enum('수치 성격',['OBSERVED','PREDICTED','PROPOSED','SYNTHETIC']),
    numerator='분자',denominator='분모',comparator='비교 대상',period='기간·시장',population='대상·수집 경로',
    calculation='원문과의 변환·계산식 또는 변환 없음'),
 'metric': fields(numerator='분자',denominator='분모',unit='단위',population='대상·세그먼트',period='기간·시간대',
    deduplication='중복·제외·결측',attribution='귀속·성숙도·N/A 이유',baseline_state=enum('기준선',['UNKNOWN','OBSERVED','SYNTHETIC']),
    baseline=field('기준선 값','number',False),unknown_reason=field('미측정 이유',required=False),target=field('제안 목표','number',False),
    target_reason='목표 제안 근거 또는 미정 이유',guardrail='가드레일',owner='측정 책임자'),
 'problem': fields(claim='문제·인사이트',counterevidence='반대 근거·한계'),
 'story_candidate': fields(actor='사용자·역할',situation='상황',action='필요한 행동',value='기대 결과'),
 'requirement': fields(behavior='관찰할 동작',priority=enum('우선순위',['P0','P1','P2']),exceptions='오류·권한·예외'),
 'acceptance': fields(given='주어진 조건',when='행동',then='관찰할 결과'),
 'uat': fields(task='실행 과업',expected='기대 결과',status=enum('실행 상태',['NOT_RUN','PASS','FAIL']),
    actual=field('실제 결과',required=False),executed_at=field('실행 시점',required=False),executor=field('실행자',required=False),
    evidence=field('실행 증거 위치',required=False),defect=field('결함·재검증 기록',required=False)),
 'question': fields(question='확인할 질문',owner='책임자',needed_stage=enum('필요 단계',['RESEARCH','DEVELOPMENT','RELEASE']),
    status=enum('상태',['OPEN','ANSWERED','EXCLUDED']),next_action='확인 방법·필요 시점',answer=field('답변·제외 사유',required=False)),
 'utility': fields(task_context='제품·과업·난이도·평가 설계',measurement_state=enum('측정 상태',['UNKNOWN','OBSERVED','SYNTHETIC']),
    before_minutes=field('이전 작성 시간(분)','number',False),after_minutes=field('현재 작성 시간(분)','number',False),
    before_retrieval_minutes=field('이전 근거 재탐색 시간(분)','number',False),after_retrieval_minutes=field('현재 근거 재탐색 시간(분)','number',False),
    omissions=field('누락 수','number',False),errors=field('의미·수치 오류 수','number',False),
    rework='PO 수정·재작업·검토 비용',privacy='동의·비식별 범위',limitations='비교 한계·미측정 이유',owner='평가 책임자'),
 'input_policy': fields(decision=enum('상한 결정 상태',['PROPOSED','CONFIRMED_CURRENT']),
    file_bytes=field('파일당 바이트 상한','number'),story_extractions=field('스토리 분석 입력 개수','number'),
    pixel_limit=field('이미지 픽셀 수 상한','number'),evaluation='모델·전처리·지연·품질·비용 평가',
    owner='결정 책임자',reason='유지·변경 제안 이유',limitations='미검증 조건·후속 시험'),
}

GRAPH_TYPES = {'evidence':'E','problem':'I','story_candidate':'US','requirement':'R',
               'acceptance':'AC','uat':'T','metric':'M'}

def clean_fields(output_type, data):
    if output_type not in TYPES or not isinstance(data,dict):
        raise AppError('조사 산출물 유형·내용을 확인하세요.')
    if set(data)-set(TYPES[output_type]):
        raise AppError('지원하지 않는 산출물 필드가 있습니다.')
    result={}
    for key,spec in TYPES[output_type].items():
        value=data.get(key)
        if spec['type']=='number':
            if value is not None and (type(value) not in (int,float) or not math.isfinite(value)):
                raise AppError(spec['label']+'은 유한한 숫자 또는 미측정이어야 합니다.')
            result[key]=value
        elif spec['type']=='checkbox':
            if value is not None and type(value) is not bool:raise AppError(spec['label']+'을 확인하세요.')
            result[key]=bool(value)
        elif spec['type']=='lines':result[key]=strings(data.get(key,[]),100,3000)
        else:
            result[key]=optional(data,key,10000)
            if spec['type']=='select' and result[key] and result[key] not in spec['options']:
                raise AppError(spec['label']+' 선택을 확인하세요.')
    return result

def content_errors(row):
    if row.get('applicability')=='not_applicable':
        if row['output_type']=='brief':return ['조사 범위와 책임자는 해당 없음으로 생략할 수 없습니다.']
        return [] if row.get('na_reason','').strip() else ['해당 없음의 이유가 필요합니다.']
    data=row['fields'];kind=row['output_type'];errors=[]
    for key,spec in TYPES[kind].items():
        if spec['required'] and (data.get(key) is None or data.get(key)=='' or data.get(key)==[]):
            errors.append(spec['label']+'을 작성하세요.')
    if kind=='brief' and (not data['required_tasks'] or any(t not in TASKS for t in data['required_tasks'])):
        errors.append('유효한 필수 조사 작업을 선택하세요.')
    if kind=='numeric_claim' and data.get('source_kind')!=data.get('claim_kind'):
        errors.append('원문 수치의 수준·증가율·%p·배수·비중 의미를 변경할 수 없습니다.')
    if kind in ('measurement','metric'):
        status=data.get('measurement_state',data.get('baseline_state'));value=data.get('value',data.get('baseline'))
        if status=='UNKNOWN' and (value is not None or not data.get('unknown_reason')):
            errors.append('미측정은 값 없이 이유를 기록하세요. 0으로 대체할 수 없습니다.')
        if status in ('OBSERVED','SYNTHETIC') and value is None:errors.append('측정값이 필요합니다.')
    if kind=='voc_collection':
        keys=('utterances','participants','excluded_count','real_count','synthetic_count')
        if any(type(data.get(k)) is not int or data[k]<0 for k in keys):errors.append('표본 수는 0 이상의 정수여야 합니다.')
        elif data['real_count']+data['synthetic_count']!=data['utterances'] or data['participants']>data['utterances']:
            errors.append('실제·합성 발언 합계와 참여자 분모를 확인하세요.')
    if kind=='alternative' and len(set(data['compared_options']))<2 and not data.get('fewer_options_reason'):
        errors.append('현상 유지 외 선택지 두 개를 비교하거나 불가능한 이유를 기록하세요.')
    if kind=='utility':
        numbers=('before_minutes','after_minutes','before_retrieval_minutes','after_retrieval_minutes','omissions','errors')
        if any(data.get(k) is not None and data[k]<0 for k in numbers):errors.append('시간·오류 수는 음수일 수 없습니다.')
        if data.get('measurement_state')=='UNKNOWN' and any(data.get(k) is not None for k in numbers):
            errors.append('효과 미측정은 값 없이 한계와 측정 계획을 기록하세요.')
        if data.get('measurement_state')=='OBSERVED' and any(data.get(k) is None for k in ('before_minutes','after_minutes')):
            errors.append('효과 실측에는 같은 평가 조건의 전후 시간을 기록하세요.')
    if kind=='actual_study' and data.get('run_state')=='EXECUTED':
        if type(data.get('independent_participants')) is not int or data['independent_participants']<=0:
            errors.append('실제 독립 참여자 수는 양의 정수여야 합니다.')
        if not all(data.get(k) for k in ('actual_sample','executed_at','executor','attestation','independent_participants')):
            errors.append('실제 표본·시점·실행자 확인이 필요합니다.')
    if kind=='uat' and data.get('status')!='NOT_RUN':
        if not all(data.get(k) for k in ('actual','executed_at','executor','evidence')):
            errors.append('PASS/FAIL에는 실제 결과·시점·실행자·증거가 필요합니다.')
    if kind=='conflict' and data.get('resolution_status')!='UNRESOLVED' and not data.get('resolution'):
        errors.append('충돌 해결·범위 제외의 근거와 이유가 필요합니다.')
    if kind=='question' and data.get('status')!='OPEN' and not data.get('answer'):
        errors.append('질문 답변 또는 제외 이유가 필요합니다.')
    if kind=='input_policy':
        if any(type(data.get(k)) is not int or data[k]<=0 for k in ('file_bytes','story_extractions','pixel_limit')):
            errors.append('입력 상한은 양의 정수여야 합니다.')
        if data.get('decision')=='CONFIRMED_CURRENT':
            from .story_contracts import IMAGE_LIMIT, IMAGE_PIXELS
            if (data.get('file_bytes'),data.get('story_extractions'),data.get('pixel_limit'))!=(IMAGE_LIMIT,3,IMAGE_PIXELS):
                errors.append('제안 상한은 현재 구현의 지원 상한으로 확정할 수 없습니다.')
    return errors

def render_item(row):
    if row.get('applicability')=='not_applicable':return '해당 없음: '+row.get('na_reason','')
    lines=[]
    if row.get('conflict_ids'):
        lines.append('연결된 충돌: '+', '.join(row['conflict_ids'])+' · 적용 주장 상태: '+row['effective_claim_status'])
    for key,spec in TYPES[row['output_type']].items():
        value=row['fields'].get(key)
        if value is None:value='미측정'
        if isinstance(value,list):value=' / '.join(value)
        if isinstance(value,bool):value='예' if value else '아니오'
        lines.append(spec['label']+': '+str(value))
    return '\n'.join(lines)
