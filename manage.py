"""Local start: python manage.py demo, then python manage.py serve."""
import argparse
import base64
import getpass
import json
import os
from pathlib import Path
from app.model import create_model
from app.server import Server
from app.service import Service
from app.store import Store

ROOT = Path(__file__).resolve().parent


def seed(store, credentials=None, public_demo=True):
    with store.db() as db:
        if db.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            raise SystemExit("이미 초기화된 환경입니다. 기존 데이터를 유지합니다.")
    project = "synthetic-advertiser-project"
    credentials = credentials or {"owner": ("owner@example.test", "Owner-demo-2026!"),
                                  "po": ("po@example.test", "Planner-demo-2026!")}
    owner_id = store.create_user(*credentials["owner"], "owner", project)
    store.create_user(*credentials["po"], "po", project)
    user = {"id": owner_id, "role": "owner", "project_id": project}
    service = Service(store, create_model())
    service.import_features(user, json.loads((ROOT / "samples/features.json").read_text()))
    raw = (ROOT / "samples" / "research.md").read_bytes()
    source = service.post(user, "/api/research/upload", {"title": "가상 리테일미디어 리서치", "filename": "research.md", "content_base64": base64.b64encode(raw).decode()})
    claims = [
        ("소재 비교의 조건", "가상 연구 가설: 소재 성과를 비교할 때 지면·기간·타깃이 다르면 합계 CTR만으로 인과 효과를 판단하기 어렵다. 광고주는 비교 조건과 해석 근거를 함께 보고 싶어 할 수 있다.", "creative_test"),
        ("소규모 광고주의 해석 부담", "가상 연구 가설: 전담 분석가가 없는 소규모 광고주에게는 복잡한 통계보다 다음 행동과 판단 보류 이유를 설명하는 리포트가 유용할 수 있다. 실제 고객 검증이 필요하다.", "reporting"),
        ("대행사의 승인 흐름", "가상 연구 가설: 대행사 운영자는 소재 변경 제안을 고객에게 설명하고 승인을 받아야 한다. 근거 내보내기와 승인 이력이 도움이 될 수 있다.", "workflow"),
    ]
    evidence_ids = []
    for title, claim, feature in claims:
        insight = service.post(user, "/api/insights", {"source_id": source["id"], "title": title, "text": claim, "feature": feature, "evidence_type": "synthetic"})
        service.post(user, "/api/insights/release", {"insight_id": insight["id"], "published": True})
        evidence_ids.append(insight["id"])
    baseline = service.post(user, "/api/research/upload", {"title": "가상 기존 리포트 기획서 v0.1", "filename": "existing_service_prd.md", "content_base64": base64.b64encode((ROOT / "samples/existing_service_prd.md").read_bytes()).decode()})
    summary = service.post(user, "/api/insights", {"source_id": baseline["id"], "title": "기존 리포트의 가상 기획 공백", "text": "합성 기획서 SYN-BASE-PRD-001 v0.1에서는 기간·캠페인·소재 필터, 성과 지표와 CSV를 제공한다고 가정한다. 비교 조건 해석·판단 보류·다음 실험 제안은 신규 기획 대상이다. 실제 쿠팡애즈 기능을 설명하는 자료가 아니다.", "feature": "reporting", "evidence_type": "synthetic"})
    service.post(user, "/api/insights/release", {"insight_id": summary["id"], "published": True})
    service.post(user, "/api/voc/upload", {"filename": "voc.csv", "source_name": "synthetic-fixture", "content_base64": base64.b64encode((ROOT / "samples/voc.csv").read_bytes()).decode()})
    voc = service.voc_records(project)
    for name, segment, goals, constraints, eid in [
        ("소규모광고주", "전담 분석가가 없는 소규모 광고주", "광고비를 낭비하지 않고 효과가 있는 소재를 이해하고 싶다.", "분석 시간과 표본이 적고 통계 해석 경험이 적다.", evidence_ids[1]),
        ("대행사운영자", "복수 광고주를 관리하는 대행사 운영자", "추천 근거를 고객에게 설명하고 승인을 빠르게 받고 싶다.", "여러 계정의 자료를 모아 보고하며 변경 승인 절차가 필요하다.", evidence_ids[2]),
        ("브랜드마케터", "브랜드 마케터", "비교 조건을 검토해 다음 소재 실험을 결정하고 싶다.", "프로모션과 타깃 차이를 분리하기 어렵다.", evidence_ids[0]),
    ]:
        linked = next((v for v in voc if v["segment"] in segment), voc[0])
        insight = next(e for e in service.knowledge(project) if e["id"] == eid)
        service.post(user, "/api/personas", {"name": name, "segment": segment, "goals": goals, "constraints": constraints, "assumptions": "공개 가상 데이터로 직접 설정한 테스트 페르소나. 실제 인터뷰 대상 아님.", "evidence_ids": [eid, linked["id"]], "observations": [{"evidence_id": eid, "quote": insight["text"]}, {"evidence_id": linked["id"], "quote": linked["text"]}]})
    prd = service.import_prd(user, {"title": "소재 분석 리포트 개선 · 기준 PRD", "filename": "existing_service_prd.md", "content_base64": base64.b64encode((ROOT / "samples/existing_service_prd.md").read_bytes()).decode()})
    service.post(user, "/api/conversations", {"title": "소재 분석 서비스 기획", "mode": "research", "prd_id": prd["id"]})
    if public_demo:
        (store.directory / "demo-mode").write_text("local synthetic demo only")
        print("가상 샘플 초기화 완료. 로컬 데모 전용 계정:")
        print("owner@example.test / Owner-demo-2026!")
        print("po@example.test / Planner-demo-2026!")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["demo", "init", "serve"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--origin", help="Browser-visible origin; required behind a reverse proxy")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", default=os.environ.get("NPD_DATA_DIR", str(ROOT / "runtime")))
    parser.add_argument("--require-model", action="store_true", default=os.getenv("NPD_REQUIRE_MODEL") == "1",
                        help="Refuse serving unless a real Bedrock probe succeeds")
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
    service = Service(store, create_model())
    if args.require_model:
        if store.has_public_demo_passwords():
            raise SystemExit("고객 테스트 환경에는 공개 샘플 비밀번호를 사용하지 마세요. bootstrap_pilot.py로 초기화하세요.")
        if getattr(service.model, "provider", None) != "bedrock":
            raise SystemExit("이 실행 모드에는 Bedrock이 필요합니다.")
        from app.store import AppError, timestamp
        try:
            service.model.probe()
            service._last_model_success = timestamp()
        except AppError as exc:
            raise SystemExit(str(exc)) from None
    store.recover_jobs()
    server = Server((args.host, args.port), service, origin=args.origin)
    print(f"NPD Assistant: {server.origin} | AI 모델: {'연결 설정됨' if service.model.configured else '미설정'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
