"""Load a separately supplied private pack; no source documents live in GitHub."""
import argparse
import getpass
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.demo_assets import initialize_pack
from app.model import create_model
from app.store import AppError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", required=True, help="Extracted private pack directory")
    parser.add_argument("--data-dir", required=True, help="NEW isolated data directory")
    parser.add_argument("--publish-insights", action="store_true", help="Owner has reviewed the shareable insight JSON and approves its release")
    parser.add_argument("--demo-credentials", action="store_true", help="Use documented local-only sample accounts; never expose publicly")
    args = parser.parse_args()
    credentials = {}
    if args.demo_credentials:
        credentials = {"owner": ("owner@example.test", "Owner-demo-2026!"), "po": ("po@example.test", "Planner-demo-2026!")}
    else:
        for role in ("owner", "po"):
            credentials[role] = (input(role + " email: "), getpass.getpass(role + " password (12+ characters): "))
    try:
        result = initialize_pack(args.assets, args.data_dir, create_model(), credentials, args.publish_insights)
    except (AppError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps({"status": "installed", "pack_id": result["pack_id"], "sources": len(result["source_ids"]), "voc": len(result["voc_ids"]),
        "insights": len(result["insight_ids"]), "personas": len(result["persona_ids"]),
        "insights_published": result["insights_published"], "personas_deferred": result["personas_deferred"],
        "model_called": False, "note": "가상 자료 적재 완료. AI 채팅은 별도 모델 연결이 필요합니다."}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
