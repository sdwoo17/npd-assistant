# NPD Assistant 0.2

상품 기획자를 위한 **보호된 리서치 → VoC 분석 → @페르소나 인터뷰 → 근거가 연결된 PRD 변경** 워크스페이스입니다. 기본 AI 제공자는 AWS Bedrock Converse이며 OpenAI Responses API도 명시적으로 선택할 수 있습니다.

이 저장소는 기존 CloudFront NPD-assistant와 분리된 프로젝트입니다. **현재 완료 범위는 단일 서버에서 실행되는 세 기능과 회귀 검증**입니다. AXIOM 실연동, 기존 AWS 앱 이식, 실제 광고주 UAT, 모델 답변 품질 검증까지 완료했다는 의미는 아닙니다. [수용 기준과 남은 검증](docs/VERIFICATION.md)을 확인하세요.

2026-09-13 추가 구현: 필드별 가중 검색, 승인 후 가상 프로필 활성화, 기존 자산 팩의 프로필 등록, PPTX 텍스트와 DOCX 순서 보존, 운영자 계정 관리, 암호화 백업·복구, 배포 주소 전체 흐름 검사. [명세 대비 구현 결과](docs/IMPLEMENTATION_DELTA_20260913.md)와 [고객 파일럿 운영 절차](docs/PILOT_OPERATIONS.md)를 참고하세요.

2026-09-14 추가 구현: 5단계 FGI 스터디, 모집 버전 고정, 공개 근거를 사용한 가이드 작성·PO 검토, 스터디 완료 및 설계 포함 내보내기, 외부 PRD 인용 검사. 기존 PRD 재사용 시 근거 누락, 검토 요약 불일치, 오래된 제안 채택도 보완했습니다. [이번 구현 범위와 남은 조건](docs/IMPLEMENTATION_DELTA_20260914.md)을 확인하세요.

2026-09-15 추가 구현: 17개 연구 작업의 구조화 산출물, 7종 근거 카드, 제품별 연구 묶음의 명시적 버전 선택, 12영역 PRD 준비 검사, 근거→요구→검증 추적과 보호된 연구 인계를 제공합니다. [사용 방법·요건별 구현·검증 경계](docs/stage1-research-contracts-2026-09-15.md)를 확인하세요.

## Bedrock으로 바로 실행

지원 기준은 Ubuntu/Linux 또는 WSL2의 Python 3.12이며 아래 설치 후 실행합니다. `MODEL_OR_PROFILE_ID`는 실제 계정·리전에서 접근 가능한 모델 또는 추론 프로필 ID로 바꿉니다.

```bash
python scripts/start_bedrock.py --region ap-northeast-2 --model-id MODEL_OR_PROFILE_ID --profile YOUR_AWS_PROFILE --data-dir /path/to/new-private-data
```

기존 AWS 역할·환경 인증을 사용하면 `--profile`을 생략합니다. Bedrock API 키로 실행할 때는 `--profile` 대신 `--prompt-api-key`를 사용합니다. 키와 초기 계정 비밀번호는 숨김 입력하며 파일이나 GitHub에 저장하지 않습니다. 실제 Bedrock 응답이 없으면 서버가 시작되지 않습니다. 새 폴더에는 비공개 계정과 합성 기본 자산을 만들며, `--assets /path/to/private-pack`으로 별도 도메인 팩을 적재할 수 있습니다. 독점 인사이트는 기본 비공개이며 소유자의 공유 승인이 필요합니다.

이 런처의 원자적 디렉터리 초기화와 AWS 배포는 Linux 환경에서 검증합니다. Windows에서는 WSL을 사용하세요. AWS 인증·모델이 없는 환경의 실패를 연결 성공으로 표시하지 않습니다. **HTTPS 고객 파일럿 배포와 실제 Bedrock 전체 흐름 검사는 [Bedrock 실행·배포 안내](docs/BEDROCK_PILOT.md)를 따르세요.**

## 설치 및 로컬 화면 확인

검증 기준은 Ubuntu/Linux 또는 WSL2의 Python 3.12 / Node 22입니다. 화면은 별도 번들 빌드 없이 서버가 제공합니다. Windows 네이티브·다른 Python 버전의 전체 지원을 보장하지 않습니다.

```bash
git clone --branch main https://github.com/sdwoo17/npd-assistant.git
cd npd-assistant
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py demo
python manage.py serve
```

Windows에서는 WSL2 터미널에서 위 Linux 절차를 실행합니다. 접속 주소: **http://127.0.0.1:8765**. 실제 파일럿은 [main의 CI](https://github.com/sdwoo17/npd-assistant/actions/workflows/ci.yml?query=branch%3Amain)가 성공한 커밋을 확인하고 40자리 SHA로 고정합니다.

| 역할 | 합성 데모 계정 | 공개 데모 비밀번호 |
| --- | --- | --- |
| 자료 소유자 | owner@example.test | Owner-demo-2026! |
| 상품 기획자 | po@example.test | Planner-demo-2026! |

데모에는 직접 작성한 합성 VoC 30건, 공개 인사이트 4개, 추가 기능 3개, 페르소나 3명, 실제 편집 가능한 기준 PRD가 있습니다. 합성 자료는 실제 쿠팡애즈 기능이나 광고주 조사 결과가 아닙니다. 사용자가 별도로 제공한 180건 CSV 원문은 이 공개 저장소에 포함하지 않습니다.

별도 리서치·VoC·페르소나 자산 팩은 [비공개 자산 가져오기](docs/PRIVATE_DEMO_PACK.md)를 따릅니다. 원문을 공개 저장소에 복제하지 않고 새 환경에 적재하며, 공유 승인된 인사이트와 실제 페르소나 레코드를 연결합니다.

실제 자료에는 별도의 비공개 데이터 디렉터리와 계정을 사용합니다.

```bash
python manage.py init --data-dir /path/to/private-data
python manage.py serve --data-dir /path/to/private-data
```

`init`이 소유자·PO 계정을 대화형으로 등록합니다. 로그인한 사용자는 프로젝트를 만들고 참여 프로젝트를 전환할 수 있습니다. 새 프로젝트 생성자는 그 빈 프로젝트의 소유자가 되며, 기존 프로젝트의 역할은 바뀌지 않습니다. 소유자는 이미 등록된 계정을 프로젝트에 추가할 수 있습니다. 서버 운영자는 `scripts/pilot_ops.py`로 추가 계정 생성·비밀번호 변경·비활성화·재활성화를 수행합니다. 웹 셀프 가입·메일 초대·셀프 비밀번호 재설정·Cognito는 포함되지 않습니다.

## AI 연결

서버 프로세스 환경변수를 설정합니다. `.env.example`은 안내용이며 `manage.py`가 자동으로 읽지 않습니다.

| 제공자 | 필수 설정 | 출력 계약 |
| --- | --- | --- |
| Bedrock (기본) | `BEDROCK_MODEL_ID`, `AWS_REGION`, 사용 가능한 AWS 인증/실행 역할 또는 `AWS_BEARER_TOKEN_BEDROCK` | `BEDROCK_OUTPUT_MODE=json_schema`, `tool`, `strict_tool` |
| OpenAI (선택) | `NPD_MODEL_PROVIDER=openai`, `OPENAI_MODEL`, `OPENAI_API_KEY` | Responses API strict JSON Schema |

Bedrock `json_schema`와 `strict_tool`은 해당 모델의 structured output 지원이 필요합니다. `tool`은 강제 도구 선택을 지원하는 모델용 응답 형식이며 실제 외부 도구를 실행하지 않습니다. 계정·리전에서 접근 가능한 모델 또는 inference profile ID를 지정하세요. 자동 제공자 전환이나 샘플 응답 폴백은 없습니다. 배지는 설정 유무와 이 서버 프로세스의 마지막 유효 응답 수신을 구분하며 답변 품질을 보증하지 않습니다. 소유자 화면의 **Bedrock 연결 테스트**는 고객 자료 없는 소량의 실제 호출을 보내며, PO는 이 진단 API를 실행할 수 없습니다.

소유자의 인사이트 추출은 **원문을 설정된 제공자로 전달**합니다. PO 채팅은 승인된 인사이트와 가명화된 VoC만 전달합니다. AWS IAM 권한·제공자 데이터 처리 설정은 실제 실행 환경에서 별도 확인합니다. OpenAI에는 `store: false`를 사용하지만 이것이 제공자의 모든 로그 보존을 해제한다는 의미는 아닙니다.

```bash
# 실제 제공자 호출: 합성 입력을 사용하며 API 비용이 발생할 수 있습니다.
python scripts/bedrock_check.py --live
python scripts/live_validation.py --run
```

전체 흐름 검증은 모델 미설정 또는 `--run` 미지정이면 `NOT_RUN`과 종료 코드 2를 반환합니다. 연결 검사에서 `--live`를 지정해도 모델이 없으면 `FAIL`과 종료 코드 1을 반환합니다. 자동 회귀 테스트는 이를 실제 연결 성공으로 계산하지 않습니다.

## 세 기능의 동작

**1. 독점 리서치 보호와 활용**

소유자 전용 원문·원본 파일 암호화, PDF/DOCX/PPTX/MD/TXT 추출, 최대 150,000자 문서의 청크 처리, 비공개 인사이트 추출, 수정·버전·일괄 공개, 실패 작업 재시도를 제공합니다. DOCX 본문·표 순서와 PPTX 슬라이드 순서를 보존하며 PPTX의 이미지·차트·발표자 노트는 추출하지 않습니다. PO에게 원문·파일명·비공개 초안을 보내지 않습니다. 공개 인사이트에는 적용 조건·한계·경쟁사·관찰일·공유 가능한 URL·연결 기능을 저장합니다.

검색은 공개 지식만 대상으로 필드 가중치와 길이 정규화를 적용합니다. 기능 키워드는 약한 힌트로 취급하며, 다수 VoC가 리서치를 가리지 않도록 관련 인사이트와 VoC를 각각 최대 8개 확보하고 나머지 순위를 채웁니다. 의미 임베딩 검색은 포함되지 않습니다. `scripts/evaluate_retrieval.py`로 제공한 정답셋의 후보 회수율·순위·반복 안정성을 측정할 수 있습니다.

공개 철회 또는 근거 버전 변경 시 그 근거를 입력으로 사용한 대화, 페르소나, 디브리프, 제안 및 반영된 PRD를 재확인 상태로 처리합니다. 최종 인용에서 빠졌던 입력 근거와 과거 대화의 간접 의존성도 추적합니다. 이미 사람이 읽거나 다운로드한 내용을 회수할 수는 없습니다. 소유자가 승인한 인사이트 자체의 공개 범위는 소유자가 검토해야 합니다.

**2. VoC 수집과 서비스 기능 분석**

CSV 필수 열은 `external_id,text`, 선택 열은 `feature,segment,occurred_at,evidence_type`입니다. UTF-8, 한 번에 최대 1,000건, 파일 3MB를 지원합니다. 동일 출처 이름+외부 ID의 재수집은 중복 처리하며 행별 결과를 반환합니다. 원본 CSV는 암호화하고 PO 데이터에는 원천 ID·출처·시각·실제/합성 구분을 보존합니다. ID 자체에 식별정보가 있으면 가명 ID를 사용하고 원본은 소유자 데이터에 보관합니다.

프로젝트별 서비스/기능 트리를 JSON으로 가져오고, 복수 기능 연결·초기 규칙 분류·AI 분류·PO 수정이 가능합니다. AI 분류는 25건씩 처리하고 저신뢰 결과는 미분류로 남깁니다. PO가 보정한 분류는 AI가 덮어쓰지 않습니다. 날짜·기능·서비스·광고주 유형·자료 유형·출처별 분석과 동일 필터의 채팅을 지원합니다. 건수와 분모는 서버에서 계산하며 복수 기능 건수의 합은 전체 VoC보다 클 수 있습니다.

Apple 공개 RSS와 App Store Connect 리뷰 수집 어댑터가 있습니다. 공개 RSS 최대 10페이지, 관리 앱 최대 20페이지이며 제공 범위를 넘는 전체 수집은 보장하지 않습니다. 관리 앱에는 `APP_STORE_CONNECT_TOKEN`이 필요합니다. 실제 공개 RSS 2페이지에서 100건 적재 및 재수집 중복 처리를 검증했습니다. Google Play는 아직 없습니다. 일반 쇼핑 앱 리뷰를 실제 광고주 표본으로 간주하지 않습니다.

가명화는 이메일·한국 휴대전화·식별번호 패턴을 처리합니다. 이름·주소·계좌 등 모든 개인정보 탐지는 아니므로 실제 상담 로그는 업로드 전에 추가 정제해야 합니다.

**3. 근거 기반 페르소나와 인터뷰**

채팅의 페르소나 생성 동작 또는 관리 화면에서 실제 레코드를 생성합니다. 관찰 발췌·출처·가정·근거 유형을 저장하고 편집 이력을 유지합니다. `@` 자동완성으로 최대 8명을 순서대로 지정할 수 있습니다. 태그 없는 후속 질문은 당시 참여자와 버전을 유지하고, 명시적 태그는 해당 페르소나의 최신 버전을 선택합니다.

비공개 자산 팩의 작성된 프로필은 암호화된 초안으로 보관합니다. 소유자가 연결된 인사이트를 공개한 뒤 리서치 화면에서 프로필을 검토·활성화하면 PO가 @태그로 선택할 수 있습니다. 재실행은 기존 프로필의 PO 편집을 보존하며, 철회된 근거에 의존하는 프로필은 자동 복구하지 않습니다.

탐색/반박 라운드, PO 결정 고정, 전체 대화 맥락, 공통 요구·의견 차이·가설·근거 부족·실제 고객 확인 질문의 디브리프를 지원합니다. 답변의 근거 ID·직접 인용·수치/단위를 검증하지만 의미적 진실이나 광고주 대표성을 자동 보증하지 않습니다. 모든 페르소나 발언은 가상 인터뷰로 표시합니다.

PRD 변경안은 실제 기준 PRD/문단/버전과 연결됩니다. PO가 문구를 편집하고 채택하면 새 PRD 버전을 트랜잭션으로 저장합니다. 오래된 기준 버전의 경쟁 제안은 거절합니다. Markdown/JSON 내보내기에 `[근거 ID]`, 메시지·자료 버전·PO 결정·변경 전후를 유지합니다. AXIOM API 연결이나 붙여넣기 후 수신 측 인용 보존 검증은 별도입니다.

## 회귀 검증

```bash
npm ci --ignore-scripts
npx playwright install chromium
python scripts/regression.py --passes 3 --browser
python -m compileall -q app manage.py scripts
node --check static/app.js
```

회귀 루프는 독립 임시 DB에서 Python, DOM+HTTP, 실제 Chromium 테스트를 각각 반복합니다. 20개 시드의 CSV 1,000행·상태 전환 300단계, 권한·철회·다국어·동시성·버전·오류·공급자 SDK 계약을 검증합니다. 모델 부분은 명시적인 test double입니다. 자세한 수치와 제한은 [검증 기록](docs/VERIFICATION.md), 직접 재현하는 순서는 [PO 시연 안내](docs/DEMO.md)에 있습니다.

## 컨테이너와 운영 범위

```bash
docker compose build
docker compose run --rm npd demo
docker compose up -d
```

Compose는 로컬 `127.0.0.1`에만 포트를 공개하고 데이터는 별도 볼륨에 저장합니다. 실제 자료는 `demo` 대신 `init`을 사용합니다. 초기화가 끝난 볼륨에 `demo`를 반복하면 기존 데이터를 유지하고 중단합니다.

이 버전은 단일 프로세스 HTTP 서버+SQLite+로컬 암호화 키입니다. 프로세스 재시작 때 중단된 작업을 실패 상태로 복구합니다. 다중 복제본, 관리형 DB/KMS, Cognito, 비동기 작업 큐, 대규모 벡터 검색, OCR, 클라우드 공개 운영 인증은 포함되지 않습니다. **기존 CloudFront 사이트가 이 코드로 자동 갱신되지 않습니다.** 기존 AWS 앱으로의 이식 경계는 [통합 지침](docs/INTEGRATION.md)에 정리했습니다.

`infra/pilot.json`은 지정 사무실/VPN IP에서만 접근하는 HTTPS 단일 EC2 파일럿 구성입니다. IAM 실행 역할, 암호화 EBS, 비공개 초기 계정과 실제 Bedrock 시작 검사를 포함합니다. 이 템플릿을 추가한 것과 AWS 리소스 생성·고객 UAT 완료는 다릅니다. 최초 생성 전용이며 기존 스택의 자동 업데이트를 거절합니다.
