"""Explicit fictional design archetypes; not generated answers or customer observations."""
from .contracts import strings
from .store import AppError

PERSONA_POOL_LIMIT = 100
CATALOG_VERSION = '2026-09-14.1'
ARCHETYPES = [
 ('초기셀러','소규모 셀러','첫 광고의 성과 근거를 이해한다','광고 경험 부족'),
 ('일인운영셀러','소규모 셀러','적은 작업으로 예산과 소재를 점검한다','운영 시간 부족'),
 ('계절상품셀러','계절형 셀러','성수기 전후의 비교 조건을 확인한다','시즌 변동'),
 ('신상품셀러','신상품 셀러','신상품의 학습 자료 부족을 이해한다','과거 이력 부족'),
 ('다품목셀러','다품목 셀러','품목별 소재와 우선순위를 비교한다','관리 대상 다양성'),
 ('브랜드직영','브랜드 광고주','브랜드 원칙과 광고 성과를 함께 확인한다','브랜드 가이드'),
 ('예산민감셀러','소규모 셀러','예산 소진과 불확실성을 확인한다','예산 제약'),
 ('모바일셀러','모바일 운영자','이동 중 주요 변화와 승인 요청을 확인한다','작은 화면'),
 ('대행사운영','대행사 운영자','고객별 소재와 광고 설정을 점검한다','복수 고객 관리'),
 ('대행사리더','대행사 팀장','팀 판단과 고객 보고의 근거를 확인한다','승인 책임'),
 ('대행사신입','대행사 운영자','지표와 비교 조건을 학습한다','경험 부족'),
 ('대행사분석','대행사 분석가','귀속과 표본 조건을 점검한다','데이터 접근 제약'),
 ('대행사보고','고객 보고 담당','고객에게 변경 이유를 설명한다','보고 형식'),
 ('대행사크리에이티브','소재 기획자','콘셉트와 관찰된 반응을 비교한다','제작 자원'),
 ('인하우스운영','브랜드 운영자','자사 캠페인 변경을 검토한다','내부 정책'),
 ('인하우스분석','브랜드 분석가','지표 정의와 원천 데이터를 확인한다','분석 시간'),
 ('인하우스승인','광고 승인 담당','적용 전 변경 내용과 책임을 확인한다','권한 분리'),
 ('브랜드마케터','마케팅 기획자','고객 가설과 캠페인 목표를 연결한다','다중 목표'),
 ('소재디자이너','디자이너','개선할 소재 표현을 이해한다','성과 맥락 부족'),
 ('카피기획자','카피 기획자','문구별 해석과 표현 한계를 확인한다','정책 검토'),
 ('영상기획자','영상 기획자','영상 비교 조건과 개선 가설을 확인한다','제작 비용'),
 ('퍼포먼스리더','성과 마케팅 리더','투입과 기대 효과의 근거를 확인한다','불확실한 귀속'),
 ('재무담당','예산 승인 담당','비용 변경과 승인 이력을 확인한다','감사 가능성'),
 ('데이터담당','데이터 담당','누락과 집계 정의를 검토한다','데이터 품질'),
 ('정책검토자','광고 정책 담당','정책 충돌과 예외를 확인한다','규정 해석'),
 ('접근성검토자','접근성 담당','보조 기술에서도 지표와 행동을 이해한다','대체 표현 필요'),
 ('비기술담당','비기술 운영자','전문 용어 없이 판단 근거를 이해한다','통계 지식 부족'),
 ('전문분석가','전문 분석가','비교 설계와 가설의 한계를 확인한다','원천 검증 필요'),
 ('보수적운영자','보수적 광고주','자동 변경 전 검토권을 유지한다','위험 회피 성향은 가정'),
 ('실험지향운영자','실험 지향 광고주','작은 실험의 다음 행동을 설계한다','실험 자원'),
 ('협업운영자','협업 담당','결정 근거와 담당자를 공유한다','인수인계 비용'),
 ('복귀운영자','재사용 광고주','이전 활동과 현재 정책의 차이를 확인한다','업무 공백'),
]


class PersonaCatalog:
    def persona_catalog(self, user, query=''):
        query=query.casefold().strip()
        existing={r.get('catalog_key'):r for r in self.store.list(user['project_id'],'persona')}
        rows=[]
        for i,(name,segment,goals,constraints) in enumerate(ARCHETYPES,1):
            row={'catalog_key':'ADS-'+str(i).zfill(2),'name':name,'segment':segment,'goals':goals,'constraints':constraints,
                'catalog_version':CATALOG_VERSION,'is_synthetic':True,
                'assumptions':['사람이 작성한 가상 설계 프로필입니다. 고객 관찰 결과나 AI 생성 결과가 아닙니다. 목표·선호는 실제 고객 검증이 필요합니다.']}
            if query and query not in ' '.join((name,segment,goals,constraints)).casefold():continue
            current=existing.get(row['catalog_key'])
            row['registered_id']=current['id'] if current else None
            rows.append(row)
        return {'catalog':rows,'defined_count':len(ARCHETYPES),'active_limit':PERSONA_POOL_LIMIT}

    def register_persona_catalog(self, user, body):
        p=user['project_id'];epoch=self.store.epoch(p)
        keys=strings(body.get('catalog_keys',[]),len(ARCHETYPES),80)
        if not keys:raise AppError('등록할 가상 설계 프로필을 선택하세요.')
        catalog={r['catalog_key']:r for r in self.persona_catalog(user)['catalog']}
        if any(k not in catalog for k in keys):raise AppError('프로필 목록이 변경됐습니다.')
        ids=strings(body.get('evidence_ids',[]),100,80)
        prepared=[];existing=[]
        for key in keys:
            template=catalog[key]
            if template['registered_id']:
                existing.append(template['registered_id']);continue
            person=self.save_persona(user,{**template,'evidence_ids':ids},persist=False)
            person.update(catalog_key=key,catalog_version=CATALOG_VERSION,authorship='explicit_design_template')
            prepared.append(('persona',person,person['id']))
        self.planning_actor(user)
        rows=self.store.write(p,inserts=prepared,expected_epoch=epoch)
        return {'personas':rows,'already_registered':existing,'model_called':False}
