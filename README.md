# NPD Assistant

독점 리서치와 고객 VoC를 바탕으로 상품 기획자가 리서치 채팅과 **@페르소나 인터뷰**를 진행하는 새 프로젝트입니다.

**현재 상태: 실행 가능한 로컬 MVP.** 원문 보호·VoC 처리·페르소나·대화·PRD 제안의 서버와 화면을 구현했습니다. 실제 AI 답변에는 사용 가능한 모델 이름과 API 키가 필요합니다. API 키가 없을 때 샘플 문장을 AI 답변처럼 반환하지 않습니다.

이 공개 저장소에는 애플리케이션 코드와 **합성 테스트 자료만** 들어 있습니다. 업로드 자료·DB·암호화 키는 git에서 제외되는 runtime/에 저장됩니다. 사용자의 실제 리서치나 고객 데이터를 저장소에 커밋하지 마세요.

## 빠른 시작

ZIP으로 받은 경우 압축을 푼 npd-assistant 폴더에서 아래 가상 환경 생성부터 진행하세요. 저장소 게시 절차와 현재 게시 상태는 [docs/PUBLISH.md](docs/PUBLISH.md)에 있습니다.

Python 3.11 이상을 사용합니다. 브라우저 화면은 별도 JavaScript 빌드 없이 서버가 제공합니다.

~~~bash
git clone https://github.com/sdwoo17/npd-assistant.git
cd npd-assistant
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py demo
python manage.py serve
~~~

Windows에서는 python3 대신 python, 가상 환경 활성화는 .venv\Scripts\Activate.ps1을 사용합니다.

브라우저 주소: **http://127.0.0.1:8765**

demo 명령으로 생성되는 로컬 테스트 계정:

| 역할 | 이메일 | 데모 비밀번호 |
| --- | --- | --- |
| 지식 소유자 | owner@example.test | Owner-demo-2026! |
| 상품 기획자 | po@example.test | Planner-demo-2026! |

이 계정은 공개된 가상 샘플용입니다. 실제 자료에는 새 데이터 디렉터리와 init 명령으로 별도의 계정을 만드세요.

~~~bash
python manage.py init --data-dir /path/to/private-npd-data
python manage.py serve --data-dir /path/to/private-npd-data
~~~

init은 소유자와 PO의 이메일·비밀번호를 터미널에서 받습니다. runtime/private.key는 암호화 자료 복구에 필요하므로 데이터와 함께 비공개로 관리해야 합니다.

## AI 모델 설정

서버에서 OpenAI Responses API를 호출합니다. API 키와 모델 이름은 서버 프로세스의 환경변수로 설정합니다. .env.example은 항목 안내용이며 자동으로 읽지 않습니다.

- OPENAI_API_KEY: 해당 실행 환경에서 사용할 키
- OPENAI_MODEL: 계정에서 사용할 수 있고 Responses API의 JSON 출력을 지원하는 모델

키를 브라우저 코드나 git 파일에 넣지 마세요. 설정 후 서버를 다시 시작하면 모델 미설정 안내가 해제됩니다. 배지는 **설정 유무**이며 실제 API 호출 성공을 보장하는 표시가 아닙니다.

app/model.py는 실제 HTTP 클라이언트입니다. store: false로 요청하고, 응답 JSON과 근거 ID를 서버에서 검증합니다. 이것이 제공자의 모든 로그·보존 정책을 비활성화한다는 뜻은 아닙니다. 현재 개발 검증에는 실제 모델 키가 없어 테스트용 응답으로 계약과 흐름을 검증했습니다.

## 세 기능

### 1. 독점 리서치 기반 채팅

소유자는 PDF·DOCX·Markdown·TXT 파일을 업로드합니다. 추출 텍스트는 암호화해 저장하고 소유자 전용 API에서만 조회합니다.

소유자는 AI로 인사이트 초안을 추출하거나 직접 작성한 다음 공개 범위를 정합니다. **PO의 검색·채팅 모델 입력에는 공개된 인사이트만** 포함됩니다. 원문·파일명·비공개 초안을 PO에게 보내지 않습니다. AI 초안 추출 시에는 소유자가 선택한 원문이 설정된 모델로 전달됩니다.

공개 철회 시 관련 지식은 검색에서 제외됩니다. 해당 근거를 사용하는 페르소나·PRD 제안은 숨기고 과거 AI 답변은 재확인 상태로 바꿉니다. 이미 사람이 읽거나 다운로드한 정보까지 회수할 수는 없습니다.

### 2. 실제 VoC와 서비스 기능 연결

UTF-8 CSV를 업로드합니다. 필수 열은 external_id,text, 선택 열은 feature,segment,occurred_at,evidence_type입니다.

- 같은 출처 이름과 외부 ID는 중복으로 처리합니다.
- 이메일·휴대전화·한국 식별번호 패턴을 가명화합니다. 이름·주소·계좌 등 모든 개인정보를 자동 탐지하는 기능은 아니므로 실제 자료는 사전 정제가 필요합니다.
- 초기 기능 분류는 규칙 기반이며 PO가 수정할 수 있습니다. AI 채팅은 분류된 VoC와 공개 리서치를 함께 검색해 해석합니다.
- 건수는 서버에서 집계하며 실제 수집 자료와 합성 자료를 구분합니다.

App Store Connect 수집 어댑터도 포함됩니다. 서버에 APP_STORE_CONNECT_TOKEN을 설정하고 **관리 권한이 있는 앱**의 ID를 입력하면 최신 최대 100건을 가져옵니다. 전체 페이지 수집, 경쟁사 앱 크롤링, Google Play 수집은 아직 지원하지 않습니다. 실제 Apple 계정으로 검증하지 않았습니다.

### 3. @페르소나 인터뷰

리서치·VoC 근거로 AI가 페르소나를 생성하거나, PO가 근거를 선택해 직접 정의합니다. 페르소나의 목표·제약·미검증 가정을 함께 저장합니다.

채팅에서 @를 입력해 페르소나를 선택합니다. 한 번에 최대 4개를 지정할 수 있으며 지정한 순서대로 발언합니다. 페르소나 태그와 근거를 서버에서 확인하고 대화 맥락을 다음 질문에 전달합니다. 태그 없는 후속 질문은 마지막으로 지정한 참여자와 발언 순서를 유지합니다.

인터뷰 발언은 항상 **가상 응답**입니다. 대화에서 PRD 변경 제안을 생성하고 채택·보류 상태를 저장할 수 있습니다. JSON 내보내기는 공개 가능한 근거, 가상 인터뷰, 제안 상태를 묶습니다. 실제 AXIOM API 연결이나 AXIOM 호환 규격 검증은 아직 수행하지 않았습니다.

## 권장 데모 흐름

가상 기존 기획서와 신규 서비스 PRD 초안을 samples/에 포함했습니다. 구체적인 질문과 입력 자료는 [PO 시연 안내](docs/DEMO.md)를 참고하세요.

1. 소유자로 로그인 → 지식 관리에서 원문과 공개 인사이트 확인.
2. 인사이트 하나를 철회하고 PO 활용 가능 근거에서 빠지는지 확인.
3. PO로 로그인 → 지식 관리·원문 API 접근이 차단되는지 확인.
4. 고객 VoC에서 기능 분류를 수정하고 집계 확인.
5. 모델 설정 후 “소규모 광고주의 소재 분석 리포트 개선 기회를 설명해 줘”라고 질문.
6. “@소규모광고주 추천 소재의 근거가 어떻게 보여야 적용하시겠어요?”라고 인터뷰.
7. “@대행사운영자 @소규모광고주 같은 리포트를 사용한다면 어떤 차이가 있을까요?”라고 FGI 진행.
8. “이 대화로 PRD 변경 제안 만들기” → 검토·채택 → 근거 패키지 내보내기.

## 테스트

~~~bash
python -m unittest discover -s tests -v
python -m compileall -q app manage.py
node --check static/app.js
npm ci --ignore-scripts
npm test
~~~

테스트의 모델 응답은 명시적인 test double 또는 로컬 가짜 API입니다. 실제 모델의 답변 품질, 실제 광고주 UAT, 외부 리뷰 수집 성공을 검증한 결과와 구분합니다. 자세한 결과는 [docs/VERIFICATION.md](docs/VERIFICATION.md)를 확인하세요.

## 코드 구조

| 경로 | 역할 |
| --- | --- |
| app/store.py | SQLite, 암호화, 비밀번호 해시, 세션·권한 범위 |
| app/ingest.py | 문서 추출, CSV, 가명화, 기능 분류, 검색 |
| app/model.py | 실제 Responses API 연결 |
| app/service.py | 리서치·VoC·페르소나·채팅·PRD 업무 흐름 |
| app/server.py | HTTP API, 쿠키, CSRF, 정적 화면 제공 |
| static/ | 로그인·리서치 채팅·지식 관리·VoC·페르소나·PRD 화면 |
| samples/ | 공개 가능한 합성 입력 |
| tests/ | 기능·권한·HTTP·모델 계약 테스트 |

## 현재 범위

로컬 단일 서버 MVP이며 운영 서비스 완성본이 아닙니다. 검색은 한국어 2글자 토큰을 포함한 어휘 매칭입니다. 벡터 검색·대규모 자료 처리·OCR·SaaS 사용자 관리·스트리밍·운영용 클라우드 배포는 다음 개발 범위입니다. 기본 서버는 loopback에만 바인딩하며 공용 인터넷 서비스로 직접 노출하도록 설계하지 않았습니다.

새 GitHub 프로젝트이며 이전 CloudFront NPD-assistant와 연결된 버전이 아닙니다. 배포 및 기존 사이트 통합은 별도 작업입니다.
