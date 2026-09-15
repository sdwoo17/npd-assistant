"""R01/R04 regressions: synthetic fixtures and explicit model doubles only."""
import copy
import unittest
from unittest.mock import patch
from app.store import AppError
from tests.helpers import Fixture, encoded


GROUPS = ("common_needs", "disagreements", "hypotheses", "unsupported_claims", "followup_questions")


class PriorityGuardTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.s, self.store = self.f.service, self.f.store
        self.po, self.owner = self.f.po, self.f.owner
        self.p = self.po["project_id"]

    def tearDown(self):
        self.f.close()

    def discussion(self):
        conv = self.f.conversation()
        self.s.post(self.po, "/api/chat", {"conversation_id": conv["id"], "message": "소재 리포트 분석"})
        return conv

    def proposal(self, conv):
        return self.s.post(self.po, "/api/proposals", {"conversation_id": conv["id"]})

    def decide(self, proposal, state="accepted", user=None):
        return self.s.post(user or self.po, "/api/proposals/decision", {
            "proposal_id": proposal["id"], "expected_version": proposal["version"], "state": state})

    def edit(self, proposal):
        changes = copy.deepcopy(proposal["changes"])
        changes[0]["after"] = "다른 PO가 수정한 자동 실행 요구사항"
        return self.s.post(self.po, "/api/proposals/update", {
            "proposal_id": proposal["id"], "expected_version": proposal["version"], "changes": changes})

    def test_stale_decisions_never_apply_unseen_edits(self):
        conv = self.discussion()
        shown = self.proposal(conv)
        edited = self.edit(shown)
        before = self.store.get(self.p, "prd", conv["prd_id"])
        for state in ("accepted", "held"):
            with self.subTest(state=state), self.assertRaises(AppError) as caught:
                self.decide(shown, state)
            self.assertEqual(caught.exception.status, 409)
            self.assertEqual(self.store.get(self.p, "proposal", shown["id"]), edited)
            self.assertEqual(self.store.get(self.p, "prd", conv["prd_id"]), before)
        applied = self.decide(edited)
        self.assertEqual(applied["applied_prd_version"], before["version"] + 1)

    def test_decisions_require_a_positive_integer_displayed_version(self):
        conv = self.discussion()
        proposal = self.proposal(conv)
        before = self.store.get(self.p, "prd", conv["prd_id"])
        for state in ("accepted", "held"):
            for extra in ({}, {"expected_version": None}, {"expected_version": True},
                          {"expected_version": 0}, {"expected_version": -1},
                          {"expected_version": "1"}, {"expected_version": 1.0}):
                with self.subTest(state=state, extra=extra), self.assertRaises(AppError) as caught:
                    self.s.post(self.po, "/api/proposals/decision", {
                        "proposal_id": proposal["id"], "state": state, **extra})
                self.assertEqual(caught.exception.status, 400)
        self.assertEqual(self.store.get(self.p, "proposal", proposal["id"]), proposal)
        self.assertEqual(self.store.get(self.p, "prd", conv["prd_id"]), before)

    def test_acceptance_retry_returns_same_result_without_new_prd_version(self):
        conv = self.discussion()
        proposal = self.proposal(conv)
        applied = self.decide(proposal)
        self.assertEqual(self.decide(proposal), applied)
        self.assertEqual(self.decide(applied), applied)
        self.assertEqual(len(self.store.history(self.p, "prd", conv["prd_id"])), 2)
        with self.assertRaises(AppError) as caught:
            self.decide(proposal, user={**self.po, "id": "other-po"})
        self.assertEqual(caught.exception.status, 409)

    def test_hold_retry_is_invalid_after_a_later_edit(self):
        conv = self.discussion()
        proposal = self.proposal(conv)
        held = self.decide(proposal, "held")
        self.assertEqual(self.decide(proposal, "held"), held)
        edited = self.edit(held)
        for state in ("accepted", "held"):
            with self.subTest(state=state), self.assertRaises(AppError) as caught:
                self.decide(proposal, state)
            self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.store.get(self.p, "proposal", proposal["id"]), edited)
        self.assertEqual(self.store.get(self.p, "prd", conv["prd_id"])["version"], 1)
        self.assertEqual(self.decide(edited)["state"], "accepted")

    def test_hold_to_accept_requires_reviewing_the_held_version(self):
        conv = self.discussion()
        proposal = self.proposal(conv)
        held = self.decide(proposal, "held")
        with self.assertRaises(AppError) as caught:
            self.decide(proposal)
        self.assertEqual(caught.exception.status, 409)
        accepted = self.decide(held)
        self.assertEqual(self.decide(held), accepted)
        with self.assertRaises(AppError):
            self.decide(accepted, "held")

    def test_edit_during_acceptance_rolls_back_prd_write(self):
        conv = self.discussion()
        shown = self.proposal(conv)
        before = self.store.get(self.p, "prd", conv["prd_id"])
        original = self.store.write
        def concurrent_edit(project, *args, **kwargs):
            if any(update[0] == "prd" for update in kwargs.get("updates", [])):
                self.edit(shown)
            return original(project, *args, **kwargs)
        with patch.object(self.store, "write", side_effect=concurrent_edit):
            with self.assertRaises(AppError) as caught:
                self.decide(shown)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.store.get(self.p, "prd", conv["prd_id"]), before)
        self.assertEqual(len(self.store.history(self.p, "prd", conv["prd_id"])), 1)
        self.assertEqual(self.store.get(self.p, "proposal", shown["id"])["state"], "draft")

    def test_old_accepted_records_do_not_authorize_unknown_stale_retries(self):
        conv = self.discussion()
        proposal = self.proposal(conv)
        legacy = self.store.update(self.p, "proposal", proposal["id"], {
            "state": "accepted", "decided_by": self.po["id"]}, proposal["version"])
        with self.assertRaises(AppError) as caught:
            self.decide(proposal)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.decide(legacy), legacy)

    def generate_debrief(self, conv, mutate):
        original = self.f.model.generate
        def generated(task, payload):
            result = original(task, payload)
            if task == "debrief":
                mutate(result)
            return result
        with patch.object(self.f.model, "generate", side_effect=generated):
            return self.s.post(self.po, "/api/debriefs", {"conversation_id": conv["id"], "regenerate": True, "reason": "회귀 검사: 생성 결과 검증"})

    def test_all_generated_groups_reject_unsupported_claims_atomically(self):
        insight = self.store.get(self.p, "insight", self.f.insight["id"])
        self.store.update(self.p, "insight", insight["id"], {
            "text": "소재 리포트에 관한 합성 의견 25건.", "evidence_type": "synthetic"}, insight["version"])
        conv = self.discussion()
        existing = self.generate_debrief(conv, lambda result: None)
        invalid = ("실제 인터뷰 결과 광고주 987654321명이 동의했다.", "의견은 987654321건이다.",
                   "의견은 25명이다.", "실제 인터뷰 결과 모두 동의했다.",
                   "확인한 근거 [00000000-0000-0000-0000-000000000001]")
        for group in GROUPS:
            for content in invalid:
                with self.subTest(group=group, content=content):
                    def mutate(result):
                        result[group][0]["text"] = content
                    with self.assertRaises(AppError) as caught:
                        self.generate_debrief(conv, mutate)
                    self.assertEqual(caught.exception.status, 502)
                    self.assertEqual(self.store.list(self.p, "debrief"), [existing])
                    self.assertNotIn(content, self.s.get(self.po, "/api/export/" + conv["id"] + "?format=markdown")["text"])

    def test_group_cannot_borrow_citations_or_numbers_from_unlinked_evidence(self):
        insight = self.store.get(self.p, "insight", self.f.insight["id"])
        self.store.update(self.p, "insight", insight["id"], {"text": "소재 리포트 합성 의견 25건."}, insight["version"])
        conv = self.discussion()
        for content in ("의견은 25건이다.", "근거 [" + insight["id"] + "]"):
            with self.subTest(content=content):
                def mutate(result):
                    result["hypotheses"][0].update(text=content, evidence_ids=[])
                with self.assertRaises(AppError) as caught:
                    self.generate_debrief(conv, mutate)
                self.assertEqual(caught.exception.status, 502)
        self.assertEqual(self.store.list(self.p, "debrief"), [])

    def test_qualitative_questions_without_evidence_are_preserved(self):
        conv = self.discussion()
        def mutate(result):
            for group in GROUPS:
                result[group][0].update(text="실제 고객에게 비교 조건이 필요한지 확인해야 한다.", evidence_ids=[])
        debrief = self.generate_debrief(conv, mutate)
        self.assertTrue(all(debrief[group][0]["evidence_ids"] == [] for group in GROUPS))

    def test_linked_quantities_and_filtered_server_counts_are_preserved(self):
        insight = self.store.get(self.p, "insight", self.f.insight["id"])
        self.store.update(self.p, "insight", insight["id"], {"text": "소재 리포트 합성 의견 25건."}, insight["version"])
        self.s.post(self.owner, "/api/voc/upload", encoded("synthetic.csv",
            "external_id,text,evidence_type\na,소재 리포트,synthetic\n", source_name="synthetic"))
        conv = self.discussion()
        def mutate(result):
            for group in GROUPS:
                result[group][0]["text"] = "자료의 의견 25건을 검토한다."
            result["followup_questions"][0].update(text="필터 범위의 VoC 1건을 추가 확인한다.", evidence_ids=[])
        debrief = self.generate_debrief(conv, mutate)
        self.assertIn("25건", debrief["common_needs"][0]["text"])
        self.assertIn("1건", debrief["followup_questions"][0]["text"])

    def test_exact_real_evidence_statement_remains_valid(self):
        statement = "실제 인터뷰 결과 비교 조건 확인이 필요했다."
        self.s.post(self.owner, "/api/voc/upload", encoded("synthetic-test-fixture.csv",
            "external_id,text,evidence_type\na," + statement + " 소재 리포트,real\n", source_name="authored-test-only"))
        conv = self.discussion()
        def mutate(result):
            result["common_needs"][0]["text"] = statement
        self.assertEqual(self.generate_debrief(conv, mutate)["common_needs"][0]["text"], statement)


if __name__ == "__main__":
    unittest.main()
