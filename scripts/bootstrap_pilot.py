"""Create a new pilot DB atomically using private credentials; never reset users."""
import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.demo_assets import initialize_pack
from app.model import create_model
from app.store import Store, AppError
from manage import seed


def credentials_from_secret(value):
    try:
        data = json.loads(value)
        result = {role: (data[role]["email"], data[role]["password"]) for role in ("owner", "po")}
        for email, password in result.values():
            if (not isinstance(email, str) or "@" not in email or len(email) > 300
                    or not isinstance(password, str) or not 16 <= len(password) <= 1000
                    or password in ("Owner-demo-2026!", "Planner-demo-2026!")):
                raise ValueError()
        if result["owner"][0].strip().lower() == result["po"][0].strip().lower():
            raise ValueError()
        return result
    except (ValueError, KeyError, TypeError):
        raise AppError("초기 계정 Secret은 서로 다른 owner/po 이메일과 16자 이상 비밀번호가 필요합니다.") from None


def initialize(destination, credentials, assets=None, publish=False):
    if assets:
        return initialize_pack(assets, destination, create_model(), credentials, publish)
    if publish:
        raise AppError("공유 옵션은 별도 자산을 지정했을 때만 사용하세요.")
    target = Path(destination).absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.mkdir(mode=0o700)
    except FileExistsError:
        raise AppError("기존 데이터 폴더는 덮어쓰지 않습니다.", 409)
    staging = Path(tempfile.mkdtemp(prefix="pilot-staging-", dir=target.parent))
    installed = False
    try:
        seed(Store(staging), credentials, public_demo=False)
        (staging / "pilot-mode").write_text("Restricted pilot; synthetic starter fixtures; private account credentials")
        os.rename(staging, target)
        installed = True
        return {"status": "initialized", "data": "synthetic starter", "credentials_logged": False}
    finally:
        if not installed:
            shutil.rmtree(staging)
            try:
                target.rmdir()
            except OSError:
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--secret-id", required=True, help="Secrets Manager ARN; never the actual password")
    parser.add_argument("--region", required=True)
    parser.add_argument("--assets", help="Optional private pack directory on this host")
    parser.add_argument("--publish-insights", action="store_true")
    args = parser.parse_args()
    try:
        if Path(args.data_dir).exists():
            raise AppError("기존 데이터 폴더는 덮어쓰지 않습니다.", 409)
        import boto3
        value = boto3.client("secretsmanager", region_name=args.region).get_secret_value(SecretId=args.secret_id)["SecretString"]
        credentials = credentials_from_secret(value)
        initialize(args.data_dir, credentials, args.assets, args.publish_insights)
        print(json.dumps({"status": "initialized", "private_pack": bool(args.assets), "model_called": False}))
        return 0
    except AppError as exc:
        print(str(exc), file=sys.stderr)
    except Exception:
        print("초기화 실패. Secret 형식·IAM 권한·리전·폴더 상태를 확인하세요. 비밀번호는 출력하지 않습니다.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
