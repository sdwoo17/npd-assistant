# Bedrock 실제 실행과 제한된 고객 파일럿

검증 상태는 `docs/VERIFICATION.md`를 참조한다. 저장소 게시나 모형 테스트 통과를 실제 Bedrock 호출·AWS 배포 완료로 계산하지 않는다. 여기서 사용하는 자산과 가상 인터뷰도 실제 광고주 인터뷰 결과가 아니다.

## 1. 기존 AWS 인증으로 즉시 로컬 실행

Python 3.11 이상 Linux/WSL에서 README 설치를 완료한다. 사용 계정에서 Bedrock 모델 접근과 필요한 최초 제공자 등록을 완료하고 접근 가능한 모델/추론 프로필 ID 및 리전을 정한다. 실행에 필요한 것은 모델 이름만이 아니라 해당 리전의 호출 권한이다.

```bash
# 기존 로그인된 AWS CLI/SSO 프로필 사용. 값은 본인의 설정으로 변경한다.
python scripts/start_bedrock.py --region ap-northeast-2 --model-id MODEL_OR_PROFILE_ID --profile YOUR_AWS_PROFILE --data-dir /path/to/new-private-data
```

EC2 실행 역할 또는 현재 프로세스의 SDK 인증을 사용하면 `--profile`을 생략한다. Bedrock API 키를 사용한다면 `--profile` 대신 `--prompt-api-key`를 지정한다. 숨김 프롬프트로 읽은 키는 이 서버 프로세스 환경에만 두고 출력·저장하지 않는다. 키를 채팅, 명령줄 인자, 공개 저장소에 넣지 않는다. Bedrock 전용 API 키는 CloudFormation/Secrets Manager 배포 인증을 대신하지 않는다.

런처는 실제 Bedrock 응답 확인 → 새 디렉터리와 서로 다른 소유자/PO 계정 생성 → 재시작 시에도 모델 검사 → `http://127.0.0.1:8765` 순으로 실행한다. 소유자와 PO 비밀번호는 각각 16자 이상을 입력한다. 초기 합성 기본 자산은 VoC 30건과 페르소나 3명이다. 이는 사용자가 제공한 도메인 아카이브가 아니다.

별도 도메인 팩은 같은 명령에 `--assets /path/to/private-pack`을 추가한다. 자료가 동일하다면 기존 확보본을 재사용하며 ZIP을 공개 코드에 넣지 않는다. 원문과 초안 인사이트는 기본 비공개다. 소유자가 화면에서 인사이트를 검토·공유한 뒤 페르소나 화면에서 목표 광고주별 AI 생성 또는 수동 등록을 수행한다. 팩에 미리 작성된 8개 프로필을 그대로 초기화하려면, 팩 전체 인사이트를 검토한 후 새 데이터 폴더에 `--publish-insights`를 명시해 가져온다. 기존 데이터 폴더에 보류된 프로필을 추가 병합하는 CLI는 없다. [비공개 팩 형식과 범위](PRIVATE_DEMO_PACK.md)를 참고한다. 기존 데이터 폴더는 덮어쓰지 않는다.

## 2. 연결 검사와 실제 전체 흐름 검사

일반 `manage.py serve` 경로는 아래 환경을 서버 프로세스에 설정한다. `.env.example`은 자동으로 읽지 않는다.

```bash
export NPD_MODEL_PROVIDER=bedrock
export AWS_REGION=ap-northeast-2
export BEDROCK_MODEL_ID=MODEL_OR_PROFILE_ID
export BEDROCK_OUTPUT_MODE=json_schema
python scripts/bedrock_check.py --live
python scripts/live_validation.py --run --passes 1
python manage.py serve --data-dir /path/to/private-data --require-model
```

| 검사 | 실제 성공 조건 | 성공이 의미하지 않는 것 |
| --- | --- | --- |
| 연결 검사 / 소유자 화면 버튼 | Bedrock에서 지정 JSON 계약의 응답 수신 | 검색 정확도·인터뷰 품질 |
| 전체 흐름 검사 | 실제 HTTP 로그인 두 역할, 원문 차단, AI 추출·공유·VoC 분류·채팅·2명 인터뷰 2라운드·디브리프·PRD v2·인용·철회 | 실제 광고주 대표성·AXIOM API 연계·배포 URL 검증 |
| `--require-model` | 공개 샘플 비밀번호가 없고 실제 Bedrock 시작 검사 성공 | 24시간 가용성·다중 서버 운영 |

전체 흐름 검사는 별도 임시 DB와 합성 자료를 사용하고 종료 시 제거한다. 고객 DB를 수정하지 않는다. `--passes 3`은 실제 유료 모델 호출을 세 번의 전체 흐름으로 반복한다. HTTP 시험 서버도 로컬에서 별도로 실행하므로 HTTPS 배포 주소의 사용자 테스트는 추가로 수행해야 한다. 출력은 단계·시각·건수만 포함하며 원문·비밀번호·모델 답변을 로그에 기록하지 않는다.

`json_schema` 또는 `strict_tool`은 모델이 해당 구조화 출력 계약을 지원해야 한다. 강제 도구 선택만 지원하는 모델은 `tool`을 명시한다. 응답 도구는 스키마를 전달하는 용도이며 광고 집행이나 시스템 명령을 실행하지 않는다. 첫 스키마 처리에 수분이 걸릴 수 있어 SDK 읽기 제한을 300초로 설정했다. SDK 계약 오류, IAM 거절, 만료 인증, 한도 초과, 시간 초과와 잘못된 모델 출력을 구분해 표시한다. 다른 모델이나 가짜 답변으로 자동 전환하지 않는다.

연결 API는 `GET /api/model/status`, `POST /api/model/test`이며 둘 다 소유자만 허용한다. POST는 세션·Origin·CSRF 검사 및 10초 요청 간격을 적용한다. 공개 `/healthz`는 HTTP 프로세스 생존 여부만 반환하며 AI를 호출하거나 자료를 반환하지 않는다. Bedrock 권한이 나중에 철회되면 일반 요청이 오류로 표시되며 healthz만으로 모델 연결을 판정하면 안 된다.

## 3. AWS HTTPS 고객 파일럿 최초 생성

이 구성은 단일 EC2 + SQLite 파일럿이다. VPC의 서로 다른 가용 영역에 있는 인터넷 연결 공용 서브넷 2개 이상, 실제 도메인, 같은 리전에서 발급 완료된 ACM 인증서, 고객 사무실/VPN IPv4 CIDR, 계정 초기화 Secret과 AWS 배포 권한이 필요하다. EC2는 ALB로부터만 앱 트래픽을 받고 SSH 포트는 열지 않는다. 운영 관리는 권한 있는 SSM 세션을 이용한다.

Secrets Manager에 다음 구조의 Secret을 생성한다. 아래 값은 설명용이며 실제로는 서로 다른 이메일과 비공개 비밀번호를 사용한다. AWS 관리형 Secret 암호화 키를 전제로 한다. 별도 고객 관리 KMS 키는 추가 IAM·키 정책 구성이 필요하다.

```json
{
  "owner": {"email": "owner@your-company.example", "password": "REPLACE_WITH_A_PRIVATE_PASSWORD"},
  "po": {"email": "po@customer.example", "password": "REPLACE_WITH_ANOTHER_PRIVATE_PASSWORD"}
}
```

`infra/pilot.parameters.example.json`을 저장소 밖으로 복사해 실제 값으로 작성한다. 예시 계정 번호·IP는 실제 환경이 아니다. `CodeCommit`을 생략하면 실행 체크아웃의 40자리 커밋을 고정한다. 그 커밋은 `sdwoo17/npd-assistant`에 게시되어 있어야 한다. `BedrockResourceArns`에는 호출 모델과 inference profile이 요구하는 모든 대상 리전의 정확한 ARN을 포함한다. 실행 역할은 지정 ARN에 `bedrock:InvokeModel`, 초기 계정 Secret에 `secretsmanager:GetSecretValue`, 해당 스택에 시작 신호 권한을 부여받는다. 계정의 모델 제공자 등록은 미리 완료한다.

```bash
# 파라미터 구문 확인만 수행하며 AWS를 호출하지 않는다.
python scripts/deploy_pilot.py --parameters /private/npd-pilot-parameters.json --region ap-northeast-2

# 실제 AWS 인증 환경에서 유료 리소스를 생성한다.
python scripts/deploy_pilot.py --parameters /private/npd-pilot-parameters.json --region ap-northeast-2 --stack-name npd-assistant-pilot --execute
```

스크립트는 변경 집합을 만든 뒤 신규 스택만 실행한다. 이미 존재하는 스택은 중단하며 기존 데이터를 수정하지 않는다. EC2 user-data에 커밋만 바꾸면 기존 인스턴스 코드가 갱신된다고 보장할 수 없기 때문에 자동 업데이트를 지원한다고 표시하지 않는다. CloudFormation `CREATE_COMPLETE`와 대상 그룹 정상 상태를 확인한 다음, 도메인을 출력 `DnsCnameTarget`으로 연결한다. 첫 서버 시작에서 실제 Bedrock 응답이 확인되지 않으면 성공 신호를 보내지 않는다.

배포 기본 자산은 공개 저장소의 합성 기본 자산이다. 독점 도메인 팩을 자동 전송하지 않는다. 비공개 전송 경로로 서버에 팩을 확보한 다음 새 데이터 디렉터리에 `bootstrap_pilot.py --assets ...`로 초기화하고, 서버를 중지한 상태에서 `NPD_DATA_DIR`와 systemd의 `--data-dir`를 함께 그 디렉터리로 바꾼 뒤 재시작한다. 이전 데이터는 유지한다. 소유자 업로드 화면에서 새 문서를 넣는 방식도 가능하다.

## 4. 고객 테스트 시작 조건

1. 배포 URL에서 HTTPS와 소유자/PO의 별도 로그인을 확인한다.
2. 소유자 연결 검사 성공을 확인하고, 실제 Bedrock 전체 흐름 검사를 같은 서버 환경에서 통과시킨다.
3. 도메인 팩을 적재하고 소유자가 공유 인사이트를 검토한다. 승인되지 않은 자료가 PO에 보이지 않는지 확인한다.
4. PO가 자연어 검색 → 기능별 VoC → `@` 페르소나 탐색/반박 → 디브리프 → PRD v2 → Markdown을 직접 수행한다. 8명 FGI와 긴 질문의 지연도 측정한다.
5. 검색 정답 집합과 사람이 판단한 기능 라벨로 품질을 평가한다. 이전 도메인 팩 어휘 검색 진단의 기대 근거 존재는 4/8 질의였으므로 검색 품질을 이미 충족했다고 표시하지 않는다. 합성 VoC의 라벨 적재 성공은 모델 분류 정확도가 아니다.
6. AXIOM에는 승인된 Markdown/JSON을 전달하고 수신 뒤 인용·버전이 보존되는지 확인한다. AXIOM API 직접 연결은 별도 범위다.

배포 템플릿은 암호화 EBS를 인스턴스 종료 후 보존하지만 자동 백업·재연결·복원을 구현한 것은 아니다. 기존 인스턴스 교체나 업그레이드 전에 DB와 로컬 암호화 키를 함께 백업하고 복구를 검증해야 한다. Cognito, 관리형 DB, KMS 기반 문서별 암호화, HA, 작업 큐, 대규모 부하 검증은 이 파일럿에 포함되지 않는다. 기존 CloudFront 앱도 변경되지 않는다.

## AWS 공식 근거

- [Converse 구조화 출력·strict tool·첫 스키마 처리](https://docs.aws.amazon.com/bedrock/latest/userguide/structured-output.html)
- [추론 프로필 IAM 권한](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-prereq.html)
- [모델 접근 사전 요건](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html)
- [Bedrock API 키와 SDK 환경변수](https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys-use.html)
- [Amazon Linux 2023의 기본 Python 3.9와 별도 버전 설치](https://docs.aws.amazon.com/linux/al2023/ug/python.html): 템플릿은 Python 3.12를 설치한다.
