# GitHub 및 실행 환경

대상 저장소: https://github.com/sdwoo17/npd-assistant

0.2 변경은 `main`의 `946a2a1`에서 분기한 `feature/research-workflow-v2`에 게시하고 PR에서 검토한다. 기존 브랜치의 다른 변경을 덮어쓰거나 force push하지 않는다. 최신 실행 결과는 해당 PR/커밋의 GitHub Actions에서 확인한다.

```bash
git clone --branch feature/research-workflow-v2 https://github.com/sdwoo17/npd-assistant.git
cd npd-assistant
python -m venv .venv
```

이후 설치·초기화·실행은 README를 따른다. GitHub는 소스 보관 및 자동 검증을 수행하며 Python 웹서버를 GitHub Pages로 실행하지 않는다. CloudFront 배포본은 자동 변경되지 않는다.

공개 저장소에는 코드·합성 시드·테스트·본문 없는 집계만 커밋한다. 실제 업로드 자료, 원본 CSV, 암호화 키, DB, 환경변수 파일, 자격 증명은 커밋하지 않는다. 공개 저장소의 읽기 권한과 쓰기 권한은 별개이며 토큰을 채팅이나 소스 파일로 전달하지 않는다.
