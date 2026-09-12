# 새 GitHub 저장소에 게시하기

대상: https://github.com/sdwoo17/npd-assistant

2026-09-12 GitHub 연결을 다시 확인한 뒤 실제 파일 생성에 성공했다. 이전의 HTTP 403, Resource not accessible by integration 문제는 해결되었으며, 이 커밋에는 실행 가능한 MVP 코드와 테스트, 합성 샘플, GitHub Actions 설정을 포함한다. 현재 원격 검증 결과는 저장소 Actions에서 확인한다.

## ZIP에서 시작

ZIP을 풀고 npd-assistant 폴더로 이동한다. Python 환경을 만들고 README의 설치·demo·serve 명령을 실행할 수 있다. 압축에는 인증 정보, 실행 DB, 암호화 키, 실제 고객 자료가 없다.

## 별도의 빈 저장소에 처음 게시하는 경우

현재 대상 저장소에는 코드가 있으므로 README의 clone 절차로 시작한다. 아래 명령은 ZIP을 별도의 빈 저장소에 처음 게시하는 경우에만 사용하고, origin 주소를 그 저장소 주소로 바꾼다.

~~~bash
git init -b main
git add .
git commit -m "Build NPD Assistant research and persona MVP"
git remote add origin https://github.com/sdwoo17/npd-assistant.git
git push -u origin main
~~~

이미 원격에 다른 커밋이 있다면 먼저 해당 저장소를 clone하고 그 작업 폴더에 패키지 파일을 추가한다. 원격 변경을 덮어쓰는 force push는 사용하지 않는다.

GitHub 앱으로 게시하려면 현재 연결이 이 저장소의 코드 쓰기를 허용해야 한다. 저장소가 PUBLIC인 것은 익명 읽기가 가능하다는 의미이며 쓰기 인증을 대신하지 않는다. 비밀번호나 토큰을 ChatGPT 메시지·소스 파일에 입력하지 않는다.

## 게시 후

1. 저장소의 README와 app/, static/, tests/, samples/가 보이는지 확인한다.
2. Actions의 Build and test 실행 결과를 확인한다.
3. 서버 실행 환경에 모델 키와 모델 이름을 설정해 실제 AI 시연을 진행한다.

원격 업로드는 웹서비스 배포와 별개다. 현재 앱은 로컬 실행 MVP이며 GitHub Pages만으로 Python 서버를 실행할 수 없다.
