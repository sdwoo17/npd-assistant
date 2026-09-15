"""Versioned FGI design, recruitment, grounded guide and session lifecycle."""
import uuid
from .contracts import text, optional, strings, revision, dependency_map, validate_claims, validate_citations
from .store import AppError

STAGES = ("design", "recruitment", "guide", "session", "debrief")
GUIDE_SECTIONS = ("목적 요약", "참여자 구성", "세션 구조·시간·워밍업", "연구 질문별 핵심 질문", "프로브", "모더레이터 규칙", "클로징", "디브리프 체크리스트")
STUDY_PARTICIPANTS = 6


class Studies:
    def study(self, user, rid):
        row = self.store.get(user["project_id"], "study", rid)
        if not self.accessible(user["project_id"], row):
            raise AppError("스터디의 근거가 변경됐습니다. 현재 공개 근거로 새 스터디를 설계하세요.", 409)
        return row

    def study_list(self, user):
        return [r if self.accessible(user["project_id"], r) else self.redacted(r)
                for r in self.store.list(user["project_id"], "study")]

    def save_study(self, user, body):
        p = user["project_id"]
        epoch = self.store.epoch(p)
        old = self.study(user, body["study_id"]) if body.get("study_id") else None
        if old and old.get("conversation_id"):
            raise AppError("세션 시작 후 연구 설계는 고정됩니다. 별도 스터디로 새 설계를 기록하세요.", 409)
        stage = body.get("stage", old["stage"] if old else "design")
        if stage not in STAGES[:3] or (not old and stage != "design"):
            raise AppError("설계·리크루팅·가이드 순서로 준비하세요.")
        if old and STAGES.index(stage) > STAGES.index(old["stage"]) + 1:
            raise AppError("앞 단계를 먼저 완료하세요.", 409)
        data = {**(old or {}), **body}
        questions = strings(data.get("research_questions", []), 10, 1000)
        ids = strings(data.get("persona_ids", []), STUDY_PARTICIPANTS, 80)
        if len(ids) != len(data.get("persona_ids", [])):
            raise AppError("참여자를 중복 없이 선택하세요.")
        people = [self.get_persona(p, rid) for rid in ids]
        fields = {"title": text(data, "title", 200), "objective": optional(data, "objective", 3000), "moderation_brief": optional(data, "moderation_brief", 5000),
                  "research_questions": questions, "recruitment_criteria": optional(data, "recruitment_criteria", 3000),
                  "persona_ids": ids, "participants": [{"id": r["id"], "version": r["version"]} for r in people],
                  "target_prd_id": optional(data, "target_prd_id", 80), "stage": stage,
                  "status": "preparing", "is_synthetic": True, "created_by": old.get("created_by", user["id"]) if old else user["id"]}
        if fields["target_prd_id"]:
            prd = self.store.get(p, "prd", fields["target_prd_id"])
            if not self.accessible(p, prd):
                raise AppError("유효한 기준 PRD를 선택하세요.", 409)
        if stage != "design" and (not fields["objective"] or not questions):
            raise AppError("연구 목적과 연구 질문을 먼저 입력하세요.")
        if stage == "guide" and (not people or not fields["recruitment_criteria"]):
            raise AppError("리크루팅 기준과 참여자 1~6명을 지정하세요.")
        changed = old and any(fields[k] != old.get(k) for k in (
            "title", "objective", "moderation_brief", "research_questions", "recruitment_criteria", "participants", "target_prd_id"))
        guide = old.get("guide") if old and not changed else None
        fields.update(guide=guide, dependencies=dependency_map(people + ([guide] if guide else [])))
        if old:
            return self.store.write(p, updates=[("study", old["id"], fields, revision(body))], expected_epoch=epoch)[0]
        return self.store.write(p, inserts=[("study", fields, None)], expected_epoch=epoch)[0]

    def checked_guide(self, rows, evidence, generated=False):
        if not isinstance(rows, list) or len(rows) != len(GUIDE_SECTIONS):
            raise AppError("가이드의 여덟 항목을 모두 작성하세요.")
        allowed = {e["id"]: e for e in evidence}
        cleaned = []
        for row, title in zip(rows, GUIDE_SECTIONS):
            if not isinstance(row, dict) or row.get("title") != title:
                raise AppError("가이드 항목 순서와 제목을 확인하세요.")
            content = text(row, "text", 5000)
            ids = strings(row.get("evidence_ids", []), 50, 80)
            if any(rid not in allowed for rid in ids):
                raise AppError("현재 공개된 가이드 근거를 선택하세요.", 409)
            validate_citations(content, ids)
            if generated:
                validate_claims(content, [allowed[rid] for rid in ids])
            cleaned.append({"title": title, "text": content, "evidence_ids": ids})
        return cleaned

    def study_guide(self, user, body):
        p = user["project_id"]
        epoch = self.store.epoch(p)
        row = self.study(user, text(body, "study_id", 80))
        if row["stage"] != "guide" or row.get("conversation_id"):
            raise AppError("리크루팅 완료 후 가이드 단계에서 작성하세요.", 409)
        expected = revision(body)
        if row["version"] != expected:
            raise AppError("스터디가 변경됐습니다. 새로고침하세요.", 409)
        people = [self.get_persona(p, r["id"], r["version"]) for r in row["participants"]]
        generated = "sections" not in body
        if generated:
            evidence = self.search(p, row["objective"] + " " + " ".join(row["research_questions"]))[0]
            if not evidence:
                raise AppError("가이드를 생성할 공개 근거가 없습니다. 근거를 추가하거나 직접 작성하세요.", 409)
            result = self.generate("fgi_guide", {"objective": row["objective"], "moderation_brief": row.get("moderation_brief", ""), "research_questions": row["research_questions"],
                "recruitment_criteria": row["recruitment_criteria"], "participants": people,
                "section_titles": list(GUIDE_SECTIONS), "evidence": evidence})
            sections = self.checked_guide(result["sections"], evidence, True)
            assumptions = strings(result["assumptions"], 30)
        else:
            evidence = self.knowledge(p)
            sections = self.checked_guide(body["sections"], evidence)
            assumptions = strings(body.get("assumptions", []), 30)
        ids = {rid for section in sections for rid in section["evidence_ids"]}
        if generated and not ids:
            raise AppError("가이드가 제공된 근거를 연결하지 못했습니다. 다시 생성하세요.", 502)
        deps = dependency_map(evidence if generated else [e for e in evidence if e["id"] in ids])
        guide = {"sections": sections, "evidence_ids": sorted(ids), "dependencies": deps, "assumptions": assumptions,
                 "authorship": "model_draft" if generated else "po_reviewed", "is_synthetic": True}
        return self.store.write(p, updates=[("study", row["id"], {"guide": guide,
            "dependencies": dependency_map(people + [guide])}, expected)], expected_epoch=epoch)[0]

    def start_study(self, user, body):
        p = user["project_id"]
        epoch = self.store.epoch(p)
        row = self.study(user, text(body, "study_id", 80))
        expected = revision(body)
        if row["version"] != expected:
            raise AppError("스터디가 변경됐습니다. 새로고침하세요.", 409)
        if row.get("conversation_id"):
            return row
        if row["stage"] != "guide" or not row.get("guide") or row["guide"]["authorship"] != "po_reviewed":
            raise AppError("가이드를 검토한 후 세션을 시작하세요.", 409)
        # A pinned profile can still be used by old conversations, but retiring it
        # must prevent recruitment into a new session.
        for person in row["participants"]:
            self.get_persona(p, person["id"])
        inserts = self.create_conversation(user, {"title": row["title"], "mode": "interview",
            "persona_ids": row["persona_ids"], "prd_id": row.get("target_prd_id"), "objective": row["objective"]}, persist=False)
        cid = str(uuid.uuid4())
        kind, conversation, _ = inserts[-1]
        # Preserve the actual recruited versions rather than silently upgrading
        # profiles edited after guide preparation.
        conversation.update(study_id=row["id"], participant_persona_versions=row["participants"])
        inserts[-1] = (kind, conversation, cid)
        return self.store.write(p, inserts=inserts, updates=[("study", row["id"], {
            "stage": "session", "status": "running", "conversation_id": cid}, expected)], expected_epoch=epoch)[-1]

    def study_context(self, user, conv):
        if not conv.get("study_id"):
            return None
        row = self.study(user, conv["study_id"])
        if row.get("conversation_id") != conv["id"]:
            raise AppError("스터디와 대화 연결을 확인하세요.", 409)
        return {k: row.get(k, "") for k in ("id", "title", "objective", "moderation_brief", "research_questions", "recruitment_criteria",
                                    "participants", "guide", "dependencies", "status")}

    def complete_study(self, user, body):
        p = user["project_id"]
        epoch = self.store.epoch(p)
        row = self.study(user, text(body, "study_id", 80))
        if not row.get("conversation_id"):
            raise AppError("세션을 먼저 진행하세요.", 409)
        conv = self.conversation(user, row["conversation_id"])
        reviewed = self.planning_debriefs(user, conv)
        if not reviewed or reviewed[0]["id"] != body.get("debrief_id"):
            raise AppError("현재 기획 기준으로 선택한 디브리프를 검토 완료하세요.", 409)
        debrief = reviewed[0]
        if type(body.get("debrief_version")) is not int or body["debrief_version"] != debrief["version"]:
            raise AppError("디브리프 검토본이 변경됐습니다. 최신 버전을 확인하세요.", 409)
        messages = [m["id"] for m in conv["messages"] if not m.get("redacted") and m.get("status") != "no_evidence"]
        if messages != debrief["source_message_ids"]:
            raise AppError("디브리프 이후 발언이 추가됐습니다. 다시 요약·검토하세요.", 409)
        return self.store.write(p, updates=[("study", row["id"], {"stage": "debrief", "status": "completed",
            "completed_debrief": {"id": debrief["id"], "version": debrief["version"]},
            "dependencies": dependency_map([row, debrief])}, revision(body))], expected_epoch=epoch,
            checks=[("conversation", conv["id"], conv["version"]), ("debrief", debrief["id"], debrief["version"])])[0]

    def archive_persona(self, user, body):
        p = user["project_id"]
        row = self.store.get(p, "persona", text(body, "persona_id", 80))
        if type(body.get("archived")) is not bool:
            raise AppError("보관 여부를 true/false로 지정하세요.")
        if not body["archived"] and not self.accessible(p, row):
            raise AppError("근거를 다시 검토한 뒤 프로필을 갱신하세요.", 409)
        saved = self.store.update(p, "persona", row["id"], {"archived": body["archived"]}, revision(body))
        return saved if self.accessible(p, saved) else self.redacted(saved)
