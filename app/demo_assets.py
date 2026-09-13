"""Import a private, authored demo pack into an isolated local database.

No model calls or canned chat answers. The CLI installs only a fully constructed
staging database. Never use this helper to overwrite an existing project.
"""
import base64
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from .contracts import text
from .ingest import decode_file, parse_csv
from .research import owner
from .service import Service
from .store import AppError, Store


class DemoPack:
    def __init__(self, directory):
        self.root = Path(directory).resolve(strict=True)
        self.files = {}
        self.manifest = self.json_file("manifest.json")
        if (not isinstance(self.manifest, dict)
                or self.manifest.get("schema_version") != "npd.demo-pack.v1"
                or self.manifest.get("synthetic_voc_only") is not True):
            raise AppError("합성 VoC 전용 npd.demo-pack.v1 매니페스트가 필요합니다.")
        text(self.manifest, "pack_id", 100)

    def read(self, name):
        if not isinstance(name, str) or not name or Path(name).is_absolute():
            raise AppError("자산 파일의 상대 경로를 확인하세요.")
        if name in self.files:
            return self.files[name]
        try:
            path = (self.root / name).resolve(strict=True)
            if not path.is_relative_to(self.root) or not path.is_file():
                raise AppError("자산 폴더 밖의 파일은 읽을 수 없습니다.")
            with path.open("rb") as stream:
                value = stream.read(3 * 1024 * 1024 + 1)
            if not 0 < len(value) <= 3 * 1024 * 1024:
                raise AppError("자산 파일은 3MB 이하이어야 합니다.")
        except (OSError, ValueError):
            raise AppError("자산 파일 경로 또는 읽기 권한을 확인하세요.")
        self.files[name] = value
        return value

    def json_file(self, name):
        try:
            return json.loads(self.read(name).decode("utf-8-sig"))
        except (UnicodeError, ValueError):
            raise AppError("자산 JSON 형식을 확인하세요.")

    def upload(self, name, **extra):
        return {"filename": Path(name).name,
                "content_base64": base64.b64encode(self.read(name)).decode(), **extra}

    def digest(self):
        return hashlib.sha256(json.dumps({k: hashlib.sha256(v).hexdigest()
            for k, v in sorted(self.files.items())}, sort_keys=True).encode()).hexdigest()


def keyed(rows, field, limit):
    if not isinstance(rows, list) or not 1 <= len(rows) <= limit:
        raise AppError("자산 목록의 수량을 확인하세요.")
    result = {}
    for row in rows:
        key = text(row, field, 100)
        if key in result:
            raise AppError("자산의 ID가 중복됩니다.")
        result[key] = row
    return result


def research_sources(pack, manifest, insights):
    """Validate document attribution before creating any source records.

    Legacy packs contain one `research` object. Multiple-source packs must name
    a source_key on every insight; a missing reference never falls back to the
    first document. Locators remain in the owner's private import receipt.
    """
    if ("research" in manifest) == ("research_sources" in manifest):
        raise AppError("research 또는 research_sources 중 하나만 지정하세요.")
    if "research" in manifest:
        sources = {"research": manifest["research"]}
    else:
        sources = keyed(manifest["research_sources"], "key", 64)
    uploads, provenance = {}, {}
    for key, row in sources.items():
        payload = pack.upload(row["file"], title=text(row, "title", 200))
        decode_file(payload)  # reject unsupported or oversized text before writes
        digest = hashlib.sha256(pack.read(row["file"])).hexdigest()
        if "sha256" in row and row["sha256"] != digest:
            raise AppError("리서치 자산의 SHA-256이 일치하지 않습니다.")
        uploads[key] = payload
    for key, row in insights.items():
        source_key = row.get("source_key", "research" if "research" in manifest else None)
        if not isinstance(source_key, str) or source_key not in sources:
            raise AppError("모든 인사이트에 유효한 리서치 source_key가 필요합니다.")
        locator = row.get("source_locator", "")
        if not isinstance(locator, str) or len(locator) > 500:
            raise AppError("인사이트의 원문 위치는 500자 이내의 문자열이어야 합니다.")
        provenance[key] = {"source_key": source_key, "source_locator": locator}
    return uploads, provenance


def load_pack(service, user, pack, publish_insights=False):
    """Populate an EMPTY staging project; caller owns cleanup on any failure."""
    owner(user)
    if type(publish_insights) is not bool:
        raise AppError("공유 승인은 명시적인 true/false여야 합니다.")
    p, store, manifest = user["project_id"], service.store, pack.manifest
    with store.db() as db:
        if db.execute("SELECT 1 FROM records WHERE project_id=? LIMIT 1", (p,)).fetchone():
            raise AppError("기존 프로젝트는 덮어쓰지 않습니다. 새 데이터 폴더를 사용하세요.", 409)
    try:
        service.import_features(user, pack.json_file(manifest["features"]))
        taxonomy = service.features(p)
        specs = keyed(pack.json_file(manifest["insights"]), "key", 200)
        people = keyed(pack.json_file(manifest["personas"]), "key", 8)
        uploads, provenance = research_sources(pack, manifest, specs)
        voc_specs = manifest["voc"]
        if not isinstance(voc_specs, list) or not 1 <= len(voc_specs) <= 10:
            raise AppError("VoC 파일 목록은 1~10개여야 합니다.")
        external_ids, source_names = set(), set()
        for item in voc_specs:
            source_name = text(item, "source_name", 100)
            if source_name in source_names:
                raise AppError("채널별 VoC 출처 이름은 고유해야 합니다.")
            source_names.add(source_name)
            _, content, _ = decode_file(pack.upload(item["file"]))
            rows, errors = parse_csv(content, taxonomy)
            if not rows or errors or any(r["evidence_type"] != "synthetic" for r in rows):
                raise AppError("VoC는 오류 없는 합성 레코드로만 구성되어야 합니다.")
            for r in rows:
                if r["external_id"] in external_ids:
                    raise AppError("데모 자산의 외부 ID는 채널 간에도 고유해야 합니다.")
                external_ids.add(r["external_id"])
        annotations = {}
        if manifest.get("annotations"):
            annotations = keyed(pack.json_file(manifest["annotations"])["records"], "external_id", 10000)
            if set(annotations) != external_ids:
                raise AppError("VoC와 분류 주석의 ID가 일치하지 않습니다.")
            for row in annotations.values():
                linked = row.get("feature_ids")
                if not isinstance(linked, list) or not linked or any(f not in taxonomy for f in linked):
                    raise AppError("주석의 기능 ID를 확인하세요.")
        for person in people.values():
            if (not person.get("insights") or not person.get("voc_refs")
                    or not set(person["insights"]).issubset(specs)
                    or not set(person["voc_refs"]).issubset(external_ids)):
                raise AppError("모든 페르소나는 리서치와 VoC의 유효한 참조가 필요합니다.")
        sources = {key: service.research_upload(user, payload) for key, payload in uploads.items()}
        insights = {key: service.insight_save(user, {**row,
                    "source_id": sources[provenance[key]["source_key"]]["id"]})
                    for key, row in specs.items()}
        for item in voc_specs:
            result = service.voc_upload(user, pack.upload(item["file"], source_name=item["source_name"]))
            if result["errors"] or result["duplicates"]:
                raise AppError("VoC 적재 결과가 사전 검증과 다릅니다.")
        voc = {r["external_id"]: r for r in service.voc_records(p)}
        for eid, row in annotations.items():
            # Explicit, authored seed labels. This is not measured AI accuracy.
            service.voc_edit(user, {"voc_id": voc[eid]["id"], "feature_ids": row["feature_ids"]})
        prd = service.save_prd(user, pack.json_file(manifest["prd"]))
        saved_people = {}
        if publish_insights:
            service.insight_release(user, {"insight_ids": [r["id"] for r in insights.values()], "published": True})
            knowledge = {r["id"]: r for r in service.knowledge(p)}
            for key, person in people.items():
                ids = [insights[k]["id"] for k in person["insights"]] + [voc[k]["id"] for k in person["voc_refs"]]
                saved_people[key] = service.save_persona(user, {**person, "evidence_ids": ids,
                    "observations": [{"evidence_id": rid, "quote": knowledge[rid]["text"][:500]} for rid in ids]})
        research = service.create_conversation(user, {"title": "선도 사례·VoC 기반 기획", "mode": "research", "prd_id": prd["id"]})
        interview = service.create_conversation(user, {"title": "가상 광고주 FGI · 실제 고객 검증 아님", "mode": "interview",
            "prd_id": prd["id"], "persona_ids": [r["id"] for r in saved_people.values()],
            "objective": "소재·타깃 분석과 다음 실험 제안의 필요 조건 및 반대 의견 탐색. 합성 채널 기록은 독립 고객 수가 아님."})
        return {"pack_id": manifest["pack_id"], "content_digest": pack.digest(),
            "source_id": sources["research"]["id"] if "research" in manifest else None,
            "source_ids": {key: row["id"] for key, row in sources.items()}, "insight_provenance": provenance,
            "insight_ids": {k: r["id"] for k, r in insights.items()}, "voc_ids": {k: r["id"] for k, r in voc.items()},
            "persona_ids": {k: r["id"] for k, r in saved_people.items()}, "prd_id": prd["id"],
            "research_conversation_id": research["id"], "interview_conversation_id": interview["id"],
            "insights_published": publish_insights, "personas_deferred": len(people) - len(saved_people),
            "model_called": False}
    except (KeyError, TypeError, AttributeError):
        raise AppError("자산 매니페스트 또는 참조 형식을 확인하세요.")


def initialize_pack(assets, destination, model, credentials, publish_insights=False):
    """Build privately, then atomically install; never overwrite a database."""
    pack = DemoPack(assets)
    target = Path(destination).absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.mkdir(mode=0o700)
    except FileExistsError:
        raise AppError("데이터 폴더가 이미 존재합니다. 기존 환경을 유지합니다.", 409)
    staging = Path(tempfile.mkdtemp(prefix="npd-demo-staging-", dir=target.parent))
    installed = False
    try:
        store = Store(staging)
        project = "synthetic-advertiser-research"
        for role in ("owner", "po"):
            email, password = credentials[role]
            store.create_user(email, password, role, project)
        token, _ = store.login(*credentials["owner"])
        user = store.authenticate(token)
        service = Service(store, model)
        result = load_pack(service, user, pack, publish_insights)
        store.logout(token)
        (staging / "demo-import.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        (staging / "demo-mode").write_text("local demo; synthetic VoC and personas; private owner research")
        os.rename(staging, target)  # target is the empty directory reserved by this invocation
        installed = True
        return result
    finally:
        if not installed:
            shutil.rmtree(staging)
            try:
                target.rmdir()  # never delete content another process might have created
            except OSError:
                pass
