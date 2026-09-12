"""Private pack importer contracts; only independent synthetic fixtures live here."""
import csv
import json
import tempfile
import unittest
from pathlib import Path
from app.demo_assets import DemoPack, initialize_pack, load_pack
from app.service import Service
from app.store import AppError, Store
from tests.helpers import CANARY, RecordingModel

CREDENTIALS = {"owner": ("owner@example.test", "Owner-demo-2026!"),
               "po": ("po@example.test", "Planner-demo-2026!")}


def fixture_pack(root):
    root.mkdir()
    def write(name, data):
        (root / name).write_text(json.dumps(data, ensure_ascii=False))
    (root / "research.md").write_text(CANARY + "\n가상 소재 리포트 연구 원문")
    write("features.json", {"features": [{"key": "comparability", "name": "비교 조건", "terms": ["동일 조건"],
        "service_id": "advertiser_portal", "parent_feature_id": "creative_test"}]})
    write("insights.json", [{"key": "I1", "title": "합성 비교 가설", "text": "소재 리포트의 비교 조건을 확인한다는 합성 가설.",
        "feature": "comparability", "evidence_type": "synthetic"}])
    rows, people = [], []
    for i in range(8):
        for channel in range(3):
            rows.append({"external_id": f"SYN-{i}-{channel}", "text": "합성 소재 리포트에서 동일 조건이 필요합니다.",
                "feature": "comparability", "segment": "테스트 광고주", "evidence_type": "synthetic", "occurred_at": "2026-08-15"})
        people.append({"key": f"P{i}", "name": f"테스트광고주{i}", "segment": "테스트 광고주", "goals": "소재 이해", "constraints": "시간 부족",
            "assumptions": ["합성 프로필"], "insights": ["I1"], "voc_refs": [f"SYN-{i}-0"]})
    with (root / "voc.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys()); writer.writeheader(); writer.writerows(rows)
    write("personas.json", people)
    write("prd.json", {"title": "가상 기준 기획", "sections": [{"id": "requirements", "title": "요구사항", "text": "소재 비교를 제공한다."}]})
    write("manifest.json", {"schema_version": "npd.demo-pack.v1", "pack_id": "public-test-fixture", "synthetic_voc_only": True,
        "features": "features.json", "insights": "insights.json", "personas": "personas.json", "prd": "prd.json",
        "research": {"file": "research.md", "title": "비공개 원문"}, "voc": [{"file": "voc.csv", "source_name": "합성 테스트"}]})


class DemoAssetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.assets, self.target = self.root / "assets", self.root / "data"
        fixture_pack(self.assets)
        self.model = RecordingModel()

    def tearDown(self):
        self.temp.cleanup()

    def install(self, publish=True):
        result = initialize_pack(self.assets, self.target, self.model, CREDENTIALS, publish)
        store = Store(self.target)
        service = Service(store, self.model)
        users = {}
        for role in CREDENTIALS:
            token, _ = store.login(*CREDENTIALS[role]); users[role] = store.authenticate(token)
        return result, store, service, users

    def change_json(self, name, mutate):
        path = self.assets / name
        value = json.loads(path.read_text()); mutate(value)
        path.write_text(json.dumps(value, ensure_ascii=False))

    def test_default_drafts_never_release_research_or_create_grounded_personas(self):
        result, store, service, users = self.install(False)
        self.assertFalse(result["insights_published"])
        self.assertEqual(result["personas_deferred"], 8)
        self.assertEqual(len(service.knowledge(users["po"]["project_id"])), 24)
        self.assertEqual(self.model.calls, [])
        self.assertNotIn(CANARY, json.dumps(service.get(users["po"], "/api/bootstrap")))
        with self.assertRaises(AppError): service.get(users["po"], "/api/research/raw/" + result["source_id"])
        self.assertIn(CANARY, service.get(users["owner"], "/api/research/raw/" + result["source_id"])["text"])

    def test_published_pack_runs_eight_persona_fgi_and_applies_actual_prd_version(self):
        result, store, service, users = self.install()
        self.assertEqual(self.model.calls, [])
        self.assertEqual(len(result["persona_ids"]), 8)
        po, cid = users["po"], result["interview_conversation_id"]
        tags = " ".join("@" + p["name"] for p in store.list(po["project_id"], "persona"))
        conv = service.chat(po, {"conversation_id": cid, "message": tags + " 소재 리포트의 비교 조건은?"})
        self.assertEqual(len([m for m in conv["messages"] if m.get("is_synthetic")]), 8)
        service.conversation_state(po, {"conversation_id": cid, "round_type": "challenge"})
        for i in range(3):
            service.chat(po, {"conversation_id": cid, "message": "앞선 소재 리포트 제안을 거절할 조건은?", "request_id": "followup-" + str(i)})
        service.make_debrief(po, {"conversation_id": cid})
        service.decision(po, {"conversation_id": cid, "text": "자동 집행은 제외한다."})
        proposal = service.make_proposal(po, {"conversation_id": cid})
        applied = service.decide_proposal(po, {"proposal_id": proposal["id"], "state": "accepted"})
        self.assertEqual(applied["applied_prd_version"], 2)
        md = service.get(po, "/api/export/" + cid + "?format=markdown")["text"]
        self.assertNotIn(CANARY, md)
        self.assertNotIn(CANARY, json.dumps(self.model.calls))
        self.assertIn("[" + next(iter(result["insight_ids"].values())) + "]", md)

    def test_existing_environment_and_repeated_load_are_preserved(self):
        result, store, service, users = self.install()
        before = self.target.joinpath("npd.sqlite3").read_bytes()
        with self.assertRaises(AppError): self.install()
        self.assertEqual(before, self.target.joinpath("npd.sqlite3").read_bytes())
        with self.assertRaises(AppError): load_pack(service, users["owner"], DemoPack(self.assets), True)
        self.assertEqual(len(store.list(users["owner"]["project_id"], "voc")), 24)

    def test_po_cannot_import_even_into_empty_project(self):
        store = Store(self.target)
        with self.assertRaises(AppError) as caught:
            load_pack(Service(store, self.model), {"id": "po", "role": "po", "project_id": "empty"}, DemoPack(self.assets), True)
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(store.list("empty", "feature"), [])

    def test_bad_persona_reference_is_rolled_back_without_installing_any_database(self):
        self.change_json("personas.json", lambda rows: rows[0].update(voc_refs=["MISSING"]))
        with self.assertRaises(AppError): self.install()
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.root.glob("npd-demo-staging-*")), [])

    def test_invalid_input_loop_leaves_no_partial_database(self):
        mutations = [
            ("personas.json", lambda rows: rows[1].update(name=rows[0]["name"])),
            ("insights.json", lambda rows: rows[0].update(feature="invented-feature")),
            ("manifest.json", lambda obj: obj["research"].update(file="../outside.md")),
            ("manifest.json", lambda obj: obj.update(synthetic_voc_only=False)),
            ("manifest.json", lambda obj: obj.update(voc=obj["voc"] * 2)),
            ("prd.json", lambda obj: obj.update(sections=[])),
        ]
        (self.root / "outside.md").write_text("not an asset")
        for name, mutate in mutations:
            with self.subTest(file=name):
                path = self.assets / name; original = path.read_bytes()
                self.change_json(name, mutate)
                with self.assertRaises(AppError): self.install()
                self.assertFalse(self.target.exists())
                self.assertEqual(list(self.root.glob("npd-demo-staging-*")), [])
                path.write_bytes(original)

    def test_real_voc_and_duplicate_external_ids_are_rejected(self):
        path = self.assets / "voc.csv"; original = path.read_text()
        for modified in (original.replace("synthetic", "real", 1), original.replace("SYN-1-0", "SYN-0-0")):
            path.write_text(modified)
            with self.assertRaises(AppError): self.install()
            self.assertFalse(self.target.exists())
        path.write_text(original)

    def test_symlink_escape_and_invalid_json_are_rejected(self):
        outside = self.root / "outside.json"; outside.write_text("[]")
        link = self.assets / "link.json"; link.symlink_to(outside)
        with self.assertRaises(AppError): DemoPack(self.assets).json_file("link.json")
        (self.assets / "manifest.json").write_text("{")
        with self.assertRaises(AppError): self.install()
        self.assertFalse(self.target.exists())
