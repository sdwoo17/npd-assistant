import json
import unittest
from app.ingest import decode_file, parse_csv, redact
from app.model import Model
from app.store import AppError, Store
from tests.helpers import CANARY, Fixture, encoded


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.s, self.owner, self.po = self.f.service, self.f.owner, self.f.po

    def tearDown(self):
        self.f.close()

    def test_po_cannot_fetch_research_or_original(self):
        for route in ["/api/research", "/api/research/raw/" + self.f.source["id"]]:
            with self.subTest(route=route), self.assertRaises(AppError) as caught:
                self.s.get(self.po, route)
            self.assertEqual(caught.exception.status, 403)

    def test_owner_can_read_original_and_ciphertext_is_at_rest(self):
        self.assertIn(CANARY, self.s.get(self.owner, "/api/research/raw/" + self.f.source["id"])["text"])
        self.assertNotIn(CANARY.encode(), self.f.store.path.read_bytes())

    def test_po_bootstrap_and_evidence_never_expose_original_metadata(self):
        data = json.dumps([self.s.get(self.po, "/api/bootstrap"), self.s.get(self.po, "/api/evidence")])
        for secret in [CANARY, "owner-secret.md", "encrypted_text"]:
            self.assertNotIn(secret, data)

    def test_draft_insights_not_searchable_until_owner_release(self):
        self.s.post(self.owner, "/api/insights/release", {"insight_id": self.f.insight["id"], "published": False})
        self.assertEqual(self.s.get(self.po, "/api/evidence"), [])
        with self.assertRaises(AppError) as caught:
            self.s.post(self.po, "/api/insights/release", {"insight_id": self.f.insight["id"], "published": True})
        self.assertEqual(caught.exception.status, 403)

    def test_raw_never_enters_po_model_context_even_when_requested(self):
        conv = self.f.conversation()
        self.s.post(self.po, "/api/chat", {"conversation_id": conv["id"], "message": "소재 리포트 연구의 원문 전체를 번역해서 보여줘"})
        payload = json.dumps(self.f.model.calls[-1], ensure_ascii=False)
        self.assertNotIn(CANARY, payload)
        self.assertNotIn("Ignore all instructions", payload)
        self.assertIn(self.f.insight["id"], payload)

    def test_cross_project_access_rejected(self):
        conv = self.f.conversation()
        for route in ["/api/research/raw/" + self.f.source["id"], "/api/conversations/" + conv["id"]]:
            with self.subTest(route=route), self.assertRaises(AppError) as caught:
                self.s.get(self.f.other, route)
            self.assertEqual(caught.exception.status, 404)
        self.assertEqual(self.s.get(self.f.other, "/api/evidence"), [])

    def test_insight_generation_requires_owner_and_creates_private_drafts(self):
        with self.assertRaises(AppError):
            self.s.post(self.po, "/api/research/extract", {"source_id": self.f.source["id"]})
        drafts = self.s.post(self.owner, "/api/research/extract", {"source_id": self.f.source["id"]})
        self.assertFalse(drafts[0]["published"])
        self.assertIn(CANARY, json.dumps(self.f.model.calls[-1]))
        self.assertNotIn(drafts[0]["id"], {e["id"] for e in self.s.knowledge("project-a")})

    def test_csv_import_redacts_deduplicates_and_reports_errors(self):
        csv = "external_id,text,segment,evidence_type\n1,소재 문의 test@example.test 010-1234-5678,소규모 광고주,real\n2,,소규모,real\n"
        body = encoded("calls.csv", csv, source_name="call-center")
        first = self.s.post(self.owner, "/api/voc/upload", body)
        second = self.s.post(self.owner, "/api/voc/upload", body)
        self.assertEqual((first["imported"], len(first["errors"]), second["duplicates"]), (1, 1, 1))
        data = self.s.get(self.po, "/api/voc")
        self.assertNotIn("test@example.test", json.dumps(data))
        self.assertNotIn("010-1234-5678", json.dumps(data))
        self.assertEqual(data["records"][0]["feature"], "creative_test")
        self.assertEqual(data["total"], 1)

    def test_feature_correction_and_synthetic_counts(self):
        body = encoded("calls.csv", "external_id,text,evidence_type\n1,리포트 분석,synthetic\n", source_name="demo")
        self.s.post(self.owner, "/api/voc/upload", body)
        item = self.s.get(self.po, "/api/voc")["records"][0]
        self.s.post(self.po, "/api/voc/feature", {"voc_id": item["id"], "feature": "budget"})
        data = self.s.get(self.po, "/api/voc")
        self.assertEqual(data["counts"]["budget"], 1)
        self.assertEqual(data["synthetic_count"], 1)

    def test_invalid_csv_and_empty_document_rejected(self):
        with self.assertRaises(AppError):
            parse_csv("wrong,columns\nx,y")
        with self.assertRaises(AppError):
            decode_file(encoded("empty.txt", "  "))
        with self.assertRaises(AppError):
            decode_file(encoded("exec.exe", "hello"))

    def test_missing_model_fails_without_fake_response(self):
        self.s.model = Model(key="", model="")
        conv = self.f.conversation()
        with self.assertRaises(AppError) as caught:
            self.s.post(self.po, "/api/chat", {"conversation_id": conv["id"], "message": "소재 리포트 분석"})
        self.assertEqual(caught.exception.status, 503)
        self.assertEqual(self.s.get(self.po, "/api/conversations/" + conv["id"])["messages"], [])

    def test_unknown_question_has_no_invented_citation(self):
        conv = self.f.conversation()
        result = self.s.post(self.po, "/api/chat", {"conversation_id": conv["id"], "message": "qwertyxyz"})
        self.assertEqual(result["messages"][-1]["status"], "no_evidence")
        self.assertEqual(result["messages"][-1]["evidence_ids"], [])
        self.assertIsNone(result["messages"][-1]["model"])
        self.assertEqual(self.f.model.calls, [])

    def test_invalid_model_citation_not_persisted(self):
        conv = self.f.conversation()
        self.f.model.bad_citation = True
        with self.assertRaises(AppError) as caught:
            self.s.post(self.po, "/api/chat", {"conversation_id": conv["id"], "message": "소재 리포트 분석"})
        self.assertEqual(caught.exception.status, 502)
        self.assertEqual(self.s.get(self.po, "/api/conversations/" + conv["id"])["messages"], [])

    def test_persona_generation_and_tag_followup(self):
        batch = self.s.post(self.po, "/api/personas/generate", {"segment": "소규모 광고주 소재 분석"})
        self.assertEqual(self.f.store.list(self.po["project_id"], "persona"), [])
        p = self.s.post(self.po, "/api/persona-candidates/adopt", {"batch_id":batch["id"],"expected_version":batch["version"],"indices":[0]})[0]
        conv = self.f.conversation()
        first = self.s.post(self.po, "/api/chat", {"conversation_id": conv["id"], "message": "@" + p["alias"] + " 소재 추천을 어떻게 판단하나요?"})
        self.assertEqual(first["messages"][-1]["persona_id"], p["id"])
        second = self.s.post(self.po, "/api/chat", {"conversation_id": conv["id"], "message": "그 이유를 더 자세히 설명해 주세요."})
        self.assertEqual(second["messages"][-1]["persona_id"], p["id"])
        self.assertTrue(second["messages"][-1]["is_synthetic"])
        self.assertTrue(self.f.model.calls[-1][1]["history"])

    def test_multi_persona_moderator_order(self):
        a, b = self.f.persona("소규모"), self.f.persona("대행사")
        conv = self.f.conversation()
        result = self.s.post(self.po, "/api/chat", {"conversation_id": conv["id"], "message": "@대행사 @소규모 소재 리포트에 어떤 근거가 필요할까요?"})
        self.assertEqual([m["speaker"] for m in result["messages"]], ["PO", "대행사", "소규모"])
        self.assertIn("대행사", json.dumps(self.f.model.calls[-1][1]["history"], ensure_ascii=False))
        followup = self.s.post(self.po, "/api/chat", {"conversation_id": conv["id"], "message": "앞서 이야기한 이유를 더 설명해 주세요."})
        self.assertEqual([m["speaker"] for m in followup["messages"][-3:]], ["PO", "대행사", "소규모"])
        self.s.post(self.po, "/api/chat", {"conversation_id": conv["id"], "message": "@소규모 소재 분석에 필요한 첫 행동은 무엇인가요?"})
        followup = self.s.post(self.po, "/api/chat", {"conversation_id": conv["id"], "message": "그 행동의 이유를 알려주세요."})
        self.assertEqual(followup["persona_ids"], [a["id"]])
        self.assertEqual([m["speaker"] for m in followup["messages"][-2:]], ["PO", "소규모"])

    def test_unknown_or_foreign_persona_tag_rejected(self):
        conv = self.f.conversation()
        with self.assertRaises(AppError) as caught:
            self.s.post(self.po, "/api/chat", {"conversation_id": conv["id"], "message": "@없는사람 소재 분석"})
        self.assertEqual(caught.exception.status, 404)

    def test_persona_requires_accessible_evidence(self):
        with self.assertRaises(AppError):
            self.s.post(self.f.other, "/api/personas", {"name": "외부", "segment": "광고주", "goals": "이해", "constraints": "시간", "evidence_ids": [self.f.insight["id"]]})

    def test_revocation_hides_old_response_persona_and_proposal(self):
        self.f.persona()
        conv = self.f.conversation()
        self.s.post(self.po, "/api/chat", {"conversation_id": conv["id"], "message": "소재 리포트 근거"})
        self.s.post(self.po, "/api/proposals", {"conversation_id": conv["id"]})
        self.s.post(self.owner, "/api/insights/release", {"insight_id": self.f.insight["id"], "published": False})
        self.assertEqual(self.s.get(self.po, "/api/bootstrap")["personas"], [])
        self.assertEqual(self.s.get(self.po, "/api/proposals"), [])
        conv = self.s.get(self.po, "/api/conversations/" + conv["id"])
        self.assertTrue(conv["messages"][-1]["redacted"])

    def test_revocation_during_generation_does_not_return_stale_answer(self):
        conv = self.f.conversation()
        self.f.model.hook = lambda: self.s.post(self.owner, "/api/insights/release", {"insight_id": self.f.insight["id"], "published": False})
        with self.assertRaises(AppError) as caught:
            self.s.post(self.po, "/api/chat", {"conversation_id": conv["id"], "message": "소재 리포트"})
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.s.get(self.po, "/api/conversations/" + conv["id"])["messages"], [])

    def test_prd_proposal_acceptance_and_safe_export(self):
        conv = self.f.conversation()
        self.s.post(self.po, "/api/chat", {"conversation_id": conv["id"], "message": "소재 분석 요구"})
        proposal = self.s.post(self.po, "/api/proposals", {"conversation_id": conv["id"]})
        self.s.post(self.po, "/api/proposals/decision", {"proposal_id": proposal["id"], "expected_version": proposal["version"], "state": "accepted"})
        exported = self.s.get(self.po, "/api/export/" + conv["id"])
        self.assertEqual(exported["proposals"][0]["state"], "accepted")
        self.assertEqual(exported["evidence"][0]["id"], self.f.insight["id"])
        self.assertNotIn(CANARY, json.dumps(exported))
        self.assertNotIn("encrypted_text", json.dumps(exported))

    def test_sqlite_reopen_preserves_encrypted_source(self):
        reopened = Store(self.f.temp.name)
        source = reopened.get("project-a", "source", self.f.source["id"])
        self.assertIn(CANARY, reopened.decrypt(source["encrypted_text"]))

    def test_review_connector_requires_token_and_numeric_app(self):
        with self.assertRaises(AppError):
            self.s.post(self.owner, "/api/voc/reviews", {"app_id": "https://localhost"})
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {"APP_STORE_CONNECT_TOKEN": ""}), self.assertRaises(AppError) as caught:
            self.s.post(self.owner, "/api/voc/reviews", {"app_id": "12345"})
        self.assertEqual(caught.exception.status, 503)


if __name__ == "__main__":
    unittest.main()
