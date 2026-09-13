"""Safe local configuration report and opt-in real Converse probe; no project data."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.model import BedrockModel
from app.store import AppError


def check(model, live=False):
    status = model.status()
    if not live:
        return {"status": "NOT_TESTED", **status, "live_call": False}, 2
    try:
        result = model.probe()
        return {"status": "PASS", **result, "live_call": True,
                "scope": "Converse connectivity and probe schema only; run live_validation.py for workflows."}, 0
    except AppError as exc:
        return {"status": "FAIL", **model.status(), "live_call": False,
                "error_code": getattr(exc, "code", "bedrock_response_invalid"),
                "error": str(exc), "note": "No successful live response verified."}, 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Send a small paid probe with no customer data")
    args = parser.parse_args()
    result, code = check(BedrockModel(), args.live)
    print(json.dumps(result, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
