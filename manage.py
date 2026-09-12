"""Local start: python manage.py demo, then python manage.py serve."""
import argparse
import base64
import getpass
import os
from pathlib import Path
from app.model import Model
from app.server import Server
from app.service import Service
from app.store import Store

ROOT = Path(__file__).resolve().parent


def seed(store):
    with store.db() as db:
        if db.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            raise SystemExit("이미 초기화된 환경입니다. 기존 데이터를 유지합니다.")
    project = "synthetic-advertiser-project"
    owner_id = store.create_user("owner@example.test", "Owner-demo-2026!", "owner", project)
    store.create_user("po@example.test", "Planner-demo-2026!", "po", project)
    user = {"id": owner_id, "role": "owner", "project_id": project}
    service = Service(store, Model())
    raw = (ROOT / "samples" / "research.md").read_bytes()
    source = service.post(user, "/api/research/upload", {"title": "가상 리테일미디어 리서치", "filename": "research.md", "content_base64": base64.b64encode(raw).decode()})
    claims = [
        ("소재 비교의 조건", "가상 연구 가설: 소재 성과를 비교할 때 지면·기간·타깃이 다르면 합계 CTR만으로 인과 효과를 판단하기 어렵다. 광고주는 비교 조건과 해석 근거를 함께 보고 싶어 할 수 있다.", "creative_test"),
        ("소규모 광고주의 해석 부담", "가상 연구 가설: 전담 분석가가 없는 소규모 광고주에게는 복잡한 통계보다 다음 행동과 판단 보류 이유를 설명하는 리포트가 유용할 수 있다. 실제 고객 검증이 필요하다.", "reporting"),
        ("대행사의 승인 흐름", "가상 연구 가설: 대행사 운영자는 소재 변경 제안을 고객에게 설명하고 승인을 받아야 한다. 근거 내보내기와 승인 이력이 도움이 될 수 있다.", "workflow"),
    ]
    evidence_ids = []
    for title, claim, feature in claims:
        insight = service.post(user, "/api/insights", {"source_id": source["id"], "title": title, "text": claim, "feature": feature})
        service.post(user, "/api/insights/release", {"insight_id": insight["id"], "published": True})
        store.update(project, "insight", insight["id"], {"evidence_type": "synthetic"})
        evidence_ids.append(insight["id"])
    baseline = service.post(user, "/api/research/upload", {"title": "가상 기존 리포트 기획서 v0.1", "filename": "existing_service_prd.md", "content_base64": base64.b64encode((ROOT / "samples/existing_service_prd.md").read_bytes()).decode()})
    summary = service.post(user, "/api/insights", {"source_id": baseline["id"], "title": "기존 리포트의 가상 기획 공백", "text": "합성 기획서 SYN-BASE-PRD-001 v0.1에서는 기간·캠페인·소재 필터, 성과 지표와 CSV를 제공한다고 가정한다. 비교 조건 해석·판단 보류·다음 실험 제안은 신규 기획 대상이다. 실제 쿠팡애즈 기능을 설명하는 자료가 아니다.", "feature": "reporting"})
    service.post(user, "/api/insights/release", {"insight_id": summary["id"], "published": True})
    store.update(project, "insight", summary["id"], {"evidence_type": "synthetic"})
    service.post(user, "/api/voc/upload", {"filename": "voc.csv", "source_name": "synthetic-fixture", "content_base64": base64.b64encode((ROOT / "samples/voc.csv").read_bytes()).decode()})
    for name, segment, goals, constraints, eid in [
        ("소규모광고주", "전담 분석가가 없는 소규모 광고주", "광고비를 낭비하지 않고 효과가 있는 소재를 이해하고 싶다.", "분석 시간과 표본이 적고 통계 해석 경험이 적다.", evidence_ids[1]),
        ("대행사운영자", "복수 광고주를 관리하는 대행사 운영자", "추천 근거를 고객에게 설명하고 승인을 빠르게 받고 싶다.", "여러 계정의 자료를 모아 보고하며 변경 승인 절차가 필요하다.", evidence_ids[2]),
    ]:
        service.post(user, "/api/personas", {"name": name, "segment": segment, "goals": goals, "constraints": constraints, "assumptions": "공개 가상 데이터로 직접 설정한 테스트 페르소나. 실제 인터뷰 대상 아님.", "evidence_ids": [eid]})
    service.post(user, "/api/conversations", {"title": "소재 분석 서비스 기획", "mode": "research"})
    (store.directory / "demo-mode").write_text("local synthetic demo only")
    print("가상 샘플 초기화 완료. 로컬 데모 전용 계정:")
    print("owner@example.test / Owner-demo-2026!")
    print("po@example.test / Planner-demo-2026!")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["demo", "init", "serve"])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", default=os.environ.get("NPD_DATA_DIR", str(ROOT / "runtime")))
    args = parser.parse_args()
    store = Store(args.data_dir)
    if args.command == "demo":
        return seed(store)
    if args.command == "init":
        project = input("프로젝트 ID: ").strip()
        if not project:
            raise SystemExit("프로젝트 ID가 필요합니다.")
        for role in ("owner", "po"):
            email = input(f"{role} 이메일: ")
            password = getpass.getpass(f"{role} 비밀번호 (12자 이상): ")
            store.create_user(email, password, role, project)
        print("계정 초기화 완료")
        return
    with store.db() as db:
        if not db.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            raise SystemExit("먼저 python manage.py demo 또는 python manage.py init을 실행하세요.")
    service = Service(store, Model())
    server = Server(("127.0.0.1", args.port), service)
    print(f"NPD Assistant: {server.origin} | AI 모델: {'연결 설정됨' if service.model.configured else '미설정'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
