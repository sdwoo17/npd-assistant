# 고객 파일럿 운영과 검증

이 절차는 단일 서버·SQLite 파일럿을 위한 것이다. 저장소 코드 검사, 실제 Bedrock 호출, 실제 HTTPS 배포, PO 과업 검증은 서로 다른 통과 조건이다. AWS 계정·모델·도메인 설정 없이 뒤의 단계를 완료로 처리하지 않는다.

## 1. AWS 인증 연결

AWS 운영자가 기존 SSO 프로필 또는 실행 역할과 접근 가능한 Bedrock 모델/추론 프로필을 준비한다. 비밀번호·키는 채팅이나 GitHub에 올리지 않는다. 서버 환경에서 `AWS_REGION`, `BEDROCK_MODEL_ID`, 필요 시 `AWS_PROFILE`을 설정한다. 기본 제공자는 Bedrock이며 자동 모델 대체는 없다.

```bash
python scripts/bedrock_check.py --live
```

`connection_verified: true`, `live_call: true`인 실제 응답과 종료 코드 0이 통과 조건이다. 모델 미설정은 `FAIL`, 실제 호출을 요청하지 않은 검사는 `NOT_TESTED`이며 성공이 아니다. 제공자별 구조화 출력 설정과 역할 권한은 [Bedrock 실행 안내](BEDROCK_PILOT.md)를 따른다.

## 2. 실제 Bedrock 전체 흐름 검증

```bash
python scripts/live_validation.py --run --passes 3
```

검증은 별도 임시 HTTP 서버와 합성 입력으로 수행한다. 소유자·PO 로그인, 원문 접근 차단, 실제 추출·VoC 분류·채팅·두 페르소나 생성, 탐색/반박, 디브리프, PRD v2 적용, 인용과 철회를 확인한다. 소유자 추출 외 모델 입력에 원문 카나리가 들어가면 실패한다. 임시 데이터는 종료 시 제거한다. Python 최적화 모드에서도 검증 조건은 제거되지 않는다.

자동 결과가 PASS여도 사람이 실제 응답의 근거 적합성, 반대 의견, 불확실성 표현, PRD 변경의 유용성을 평가한다. 합성 프로필의 FGI는 실제 광고주 인터뷰로 표시하지 않는다. 8명 FGI의 실제 지연·요금·답변 품질도 별도로 측정한다.

## 3. HTTPS 배포와 계정 준비

최초 배포는 [Bedrock 배포 안내](BEDROCK_PILOT.md)의 CloudFormation 절차를 따른다. 실제 VPC·서브넷·ACM·도메인·접근 CIDR·초기 계정 Secret이 필요하다. 기존 스택 업데이트나 CloudFront 앱 자동 통합은 이 배포 도구의 범위가 아니다.

추가 사용자 계정은 서버의 데이터 디렉터리를 읽고 쓸 수 있는 운영자가 만든다. 아래 명령은 저장소 루트와 해당 Python 환경에서 실행한다. 경로와 이메일은 실제 값으로 바꾼다.

```bash
python scripts/pilot_ops.py --data-dir /private/npd-data list-accounts
python scripts/pilot_ops.py --data-dir /private/npd-data create-account --email po@customer.example --project-id EXISTING_PROJECT_ID --role po
python scripts/pilot_ops.py --data-dir /private/npd-data reset-password --email po@customer.example
python scripts/pilot_ops.py --data-dir /private/npd-data disable-account --email po@customer.example
python scripts/pilot_ops.py --data-dir /private/npd-data enable-account --email po@customer.example
```

비밀번호는 숨김 입력과 확인 입력을 사용하며 16자 이상이어야 한다. 기존 계정 중복, 없는 프로젝트, 잘못된 역할은 거부한다. 비밀번호 변경과 비활성화는 모든 기존 세션을 철회하며, 로그인 검증 도중 변경된 비밀번호로 새 세션을 만들 수 없다. 어떤 프로젝트에서도 마지막 활성 소유자를 비활성화하지 못한다. 전역 계정 비활성화는 그 계정의 모든 프로젝트에 적용된다. 웹에서 셀프 가입·비밀번호 복구·SSO를 제공하는 기능은 아니다.

새 팩을 비공개로 가져온 뒤 소유자가 인사이트를 검토·공개하고 리서치 화면에서 프로필 초안을 활성화한다. 예전 적재본의 보류 프로필은 [동일 팩 등록 절차](PRIVATE_DEMO_PACK.md)를 사용한다. 운영 자료와 원문을 공개 저장소에 복사하지 않는다.

## 4. 암호화 백업과 복구 연습

```bash
python scripts/pilot_ops.py --data-dir /private/npd-data backup --output /private/backups/pilot-20260913.npdbackup
python scripts/pilot_ops.py --data-dir /private/restore-test restore --archive /private/backups/pilot-20260913.npdbackup
```

백업은 실행 중인 SQLite를 backup API로 일관되게 복사한다. DB·원문 암호화 키·선택적 적재 영수증을 별도의 암호로 암호화한 단일 파일에 저장한다. 계정 비밀번호와 다른 백업 암호를 보관하고, 백업 파일과 암호를 분리해 관리한다. 파일은 소유자 읽기/쓰기 권한으로 생성하며 기존 백업은 덮어쓰지 않는다. 파일럿 제한은 DB 50MiB, 전체 백업 100MiB이다. 큰 DB는 관리형 백업으로 전환한다.

복구는 새 폴더에서만 수행한다. 암호 인증, DB 무결성, 현재·과거 버전의 암호화 필드 복호화를 검증한 뒤 설치한다. 잘못된 암호, 변조, 키 불일치에는 설치하지 않는다. 기존 세션은 폐기하고 중단된 작업은 재시도 가능한 실패 상태로 바꾼다. 복구 후 소유자·PO 로그인, 원문 권한, PRD 버전과 근거 철회를 확인한다.

예약 백업·S3 업로드·보존 기간·RPO/RTO는 운영자가 정하고 실제 복구 시간을 측정한다. 이 스크립트는 인스턴스 자동 복구나 다중 서버 HA를 구현하지 않는다. EC2 교체 전에 외부 백업과 새 폴더 복구를 먼저 검증한다.

## 5. 배포 URL에서 전체 흐름과 PO 테스트

```bash
python scripts/https_validation.py --url https://YOUR_DOMAIN --run --passes 3 --output /private/https-gate.json
```

서로 다른 소유자·PO 계정으로 로그인한다. URL은 HTTPS 도메인만 허용하며 TLS 인증서 검증을 끄는 옵션은 없다. 요청 리다이렉트는 거부한다. 미인증 접근, Secure·HttpOnly·SameSite 쿠키, Bedrock 제공자, 실제 모델/권한/PRD 흐름을 검사한다.

각 회차는 새 `SYNTHETIC HTTPS validation` 프로젝트를 만들고 별도 PO 멤버십을 추가한다. 기존 프로젝트 자료는 변경하지 않으며 합성 검증 프로젝트는 운영자 검토용으로 남긴다. 종료 시 원래 프로젝트로 돌아간 뒤 검증 세션을 로그아웃한다. 실패 시에도 정리를 시도하고, 세션 정리가 실패하면 전체 게이트를 실패로 표시한다. 원문·로그인 정보·모델 답변은 결과 파일에 넣지 않는다.

원격 HTTP 게이트만으로는 서버 내부 모델 입력을 직접 검사할 수 없으므로 `model_input_guarded: false`를 기록한다. 그 검사는 앞의 로컬 Bedrock 전체 흐름 게이트가 담당한다. 자동 검증 후 실제 PO가 브라우저에서 다음을 수행한다.

1. 자신의 기획 질문으로 공개 리서치를 검색하고 원문 접근이 차단되는지 확인한다.
2. 기능·기간·광고주 유형별 VoC와 분모, 실제/합성 구분을 확인한다.
3. 서로 다른 프로필을 @태그로 지정하고 개별 후속 질문과 반박 라운드를 진행한다.
4. 디브리프를 검토하고 PO 결정을 고정해 PRD 변경 제안을 편집·채택한다.
5. Markdown/JSON을 AXIOM으로 가져간 뒤 인용과 버전 보존을 확인한다.
6. 권한 변경, 근거 철회, 로그인 만료와 실패 후 재시도를 확인한다.

AXIOM의 실제 API 계약을 받기 전에는 파일 교환을 API 연동 완료로 표시하지 않는다. 과업 성공률·검색 관련성·독립 분류 정답셋·실제 응답 지연을 PO가 판정한 뒤 고객 파일럿 통과 여부를 결정한다.

## 검색 품질 재현

```bash
python scripts/evaluate_retrieval.py --data-dir /private/npd-data --project-id PROJECT_ID --cases /private/questions.json --receipt /private/npd-data/demo-import.json --passes 3 --label-origin authored --output /private/retrieval.json
```

질문 파일은 `[{"query":"...","expected_evidence_ids":["..."]}]` 또는 기존 팩의 `question,insight_keys` 배열을 받는다. 뒤 형식에는 적재 영수증이 필요하다. `--minimum-hit-rate 0.8`처럼 명시한 기준에 미달하면 종료 코드 1을 반환한다. 질의와 근거 본문은 로그에 남기지 않는다. `authored`는 작성자 후보셋이며, `independent`는 별도 평가자가 만든 경우에만 지정한다. 선언값을 자동으로 독립 정답셋이라고 검증해 주는 기능은 아니다.
