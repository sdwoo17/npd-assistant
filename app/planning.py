"""Versioned persona, debrief and PRD domain operations."""
import re
import unicodedata
import uuid
from .contracts import text, optional, strings, revision, dependency_map, validate_answer, inline_text
from .ingest import decode_file
from .store import AppError


class Planning:
    def prd_sections(self, rows):
        if not isinstance(rows, list) or not 1 <= len(rows) <= 80:
            raise AppError("PRD 문단은 1~80개여야 합니다.")
        clean, seen = [], set()
        for row in rows:
            rid = text(row, "id", 80)
            if rid in seen or not re.fullmatch(r"[\w-]+", rid):
                raise AppError("PRD 문단 ID가 중복되거나 올바르지 않습니다.")
            seen.add(rid)
            clean.append({"id": rid, "title": text(row, "title", 200), "text": optional(row, "text", 20000)})
        if sum(len(r["text"]) for r in clean) > 100000:
            raise AppError("PRD는 전체 100,000자 이하이어야 합니다.")
        return clean

    def save_prd(self, user, body):
        p = user["project_id"]
        fields = {"title": text(body, "title", 200), "sections": self.prd_sections(body.get("sections")), "created_by": user["id"]}
        if body.get("prd_id"):
            old = self.store.get(p, "prd", body["prd_id"])
            if not self.accessible(p, old):
                raise AppError("이 PRD의 근거가 철회됐습니다. 내용을 재검토하세요.", 409)
            return self.store.update(p, "prd", old["id"], fields, revision(body))
        return self.store.put(p, "prd", {**fields, "dependencies": []})

    def import_prd(self, user, body):
        _, content, _ = decode_file(body)
        sections, title, buffer = [], "개요", []
        for line in content.splitlines():
            if re.match(r"^#{1,6}\s+", line):
                if buffer:
                    sections.append({"id": "section-" + str(len(sections) + 1), "title": title, "text": "\n".join(buffer)})
                title, buffer = re.sub(r"^#{1,6}\s+", "", line)[:200], []
            else:
                buffer.append(line)
        if buffer or not sections:
            sections.append({"id": "section-" + str(len(sections) + 1), "title": title, "text": "\n".join(buffer)})
        return self.save_prd(user, {"title": text(body, "title", 200), "sections": sections})

    def get_persona(self, project, rid, version=None):
        records = self.store.history(project, "persona", rid)
        person = next((r for r in records if r.get("version", 1) == version), None) if version is not None else records[-1]
        if person is None or not self.accessible(project, person):
            raise AppError("페르소나 버전의 근거가 변경됐습니다. 새 근거로 갱신하세요.", 409)
        return person

    def save_persona(self, user, body, input_evidence=None, expected_epoch=None, persist=True):
        p = user["project_id"]
        evidence = input_evidence if input_evidence is not None else self.knowledge(p)
        by_id = {e["id"]: e for e in evidence}
        ids = strings(body.get("evidence_ids"), 100, 80)
        if not ids or any(i not in by_id for i in ids):
            raise AppError("현재 사용할 수 있는 근거를 선택하세요.", 409)
        selected = [by_id[i] for i in ids]
        name = unicodedata.normalize("NFKC", text(body, "name", 40)).lstrip("@")
        alias = re.sub(r"[^\w-]", "", name, flags=re.UNICODE).casefold()
        if not alias or len(alias) > 40:
            raise AppError("@태그로 사용할 이름을 입력하세요.")
        assumptions = body.get("assumptions", [])
        if isinstance(assumptions, str):
            assumptions = [assumptions] if assumptions.strip() else []
        assumptions = strings(assumptions, 30)
        if not assumptions:
            assumptions = ["목표·선호는 가상 페르소나 설계를 위한 가정이며 실제 인터뷰로 확인해야 합니다."]
        observed = validate_answer({"text": "근거 관찰", "evidence_ids": ids, "assumptions": assumptions,
            "observations": body.get("observations", [])}, evidence)["observations"]
        research = sum(e["kind"] == "insight" and e["evidence_type"] == "research" for e in selected)
        real_voc = sum(e["kind"] == "voc" and e["evidence_type"] == "real" for e in selected)
        status = "research_and_real_voc" if research and real_voc else "synthetic_or_partial_evidence"
        fields = {"name": name, "alias": alias, "segment": text(body, "segment", 500), "goals": text(body, "goals", 3000),
            "constraints": text(body, "constraints", 3000), "assumptions": assumptions, "observations": observed,
            "evidence_ids": ids, "evidence_snapshots": selected, "dependencies": dependency_map(evidence if input_evidence is not None else selected),
            "grounding_status": status, "grounding_counts": {"research": research,
                "synthetic_research": sum(e["kind"] == "insight" and e["evidence_type"] == "synthetic" for e in selected),
                "real_voc": real_voc, "synthetic_voc": sum(e["kind"] == "voc" and e["evidence_type"] == "synthetic" for e in selected)},
            "is_synthetic": True, "review_status": "needs_po_review"}
        if not persist:
            return {**fields, "id": str(uuid.uuid4()), "version": 1, "kind": "persona"}
        if body.get("persona_id"):
            return self.store.write(p, updates=[("persona", body["persona_id"], fields, revision(body))], expected_epoch=expected_epoch)[0]
        return self.store.write(p, inserts=[("persona", fields, None)], expected_epoch=expected_epoch)[0]

    def generate_persona(self, user, body, persist=True):
        p = user["project_id"]
        epoch = self.store.epoch(p)
        segment = text(body, "segment", 500)
        evidence = self.search(p, segment, body.get("filters"))[0]
        if not evidence:
            raise AppError("페르소나를 만들 근거가 없습니다. 관련 리서치·VoC를 추가하세요.", 409)
        result = self.generate("persona", {"target_segment": segment, "requested_name": optional(body, "name", 40), "evidence": evidence,
            "grounding_requirements": "리서치와 VoC가 모두 제공되면 두 종류를 각각 인용. 합성 자료·자료 부족을 가정에 명시."})
        by_id = {e["id"]: e for e in evidence}
        cited_kinds = {by_id[i]["kind"] for i in result["evidence_ids"] if i in by_id}
        if {"insight", "voc"}.issubset({e["kind"] for e in evidence}) and not {"insight", "voc"}.issubset(cited_kinds):
            raise AppError("페르소나가 리서치와 VoC를 모두 연결하지 못했습니다. 다시 생성하세요.", 502)
        if body.get("name"):
            result["name"] = body["name"]
        return self.save_persona(user, result, evidence, epoch, persist)

    def make_debrief(self, user, body):
        p = user["project_id"]
        epoch = self.store.epoch(p)
        conv, messages, evidence, statistics, deps = self.planning_context(user, body)
        result = self.generate("debrief", {"conversation": messages, "evidence": evidence, "statistics": statistics, "decisions": conv.get("decisions", [])})
        answer = validate_answer(result, evidence, statistics)
        message_ids, evidence_ids = {m["id"] for m in messages}, {e["id"] for e in evidence}
        groups = self.debrief_groups(result, message_ids, evidence_ids)
        return self.store.write(p, inserts=[("debrief", {**answer, **groups, "text": inline_text(answer), "conversation_id": conv["id"],
            "source_message_ids": [m["id"] for m in messages], "dependencies": deps, "model": self.model.model,
            "review_status": "needs_po_review", "is_synthetic": True}, None)], expected_epoch=epoch)[0]

    def debrief_groups(self, body, message_ids, evidence_ids):
        groups = {}
        for key in ("common_needs", "disagreements", "hypotheses", "unsupported_claims", "followup_questions"):
            rows = body.get(key)
            if not isinstance(rows, list) or len(rows) > 30:
                raise AppError("디브리프 항목 형식을 확인하세요.", 502)
            groups[key] = []
            for row in rows:
                mids, eids = strings(row.get("message_ids"), 200, 80), strings(row.get("evidence_ids"), 100, 80)
                if not mids or not set(mids).issubset(message_ids) or not set(eids).issubset(evidence_ids):
                    raise AppError("디브리프의 메시지·근거 연결이 올바르지 않습니다.", 502)
                groups[key].append({"text": text(row, "text", 5000), "message_ids": mids, "evidence_ids": eids})
        return groups

    def review_debrief(self, user, body):
        p = user["project_id"]
        old = self.store.get(p, "debrief", body.get("debrief_id"))
        if not self.accessible(p, old):
            raise AppError("디브리프의 근거가 변경됐습니다.", 409)
        groups = self.debrief_groups(body, set(old["source_message_ids"]), {e["id"] for e in self.knowledge(p)})
        return self.store.update(p, "debrief", old["id"], {**groups, "review_status": "po_reviewed", "reviewed_by": user["id"]}, revision(body))

    def planning_context(self, user, body):
        p = user["project_id"]
        conv = self.conversation(user, text(body, "conversation_id", 80))
        messages = [m for m in conv["messages"] if not m.get("redacted") and m.get("status") != "no_evidence"]
        generated = [m for m in messages if m.get("evidence_ids")]
        if not generated:
            raise AppError("근거가 있는 대화를 먼저 진행하세요.", 409)
        if sum(len(m["text"]) for m in messages) > 160000:
            raise AppError("대화 처리 한도입니다. 범위를 나눠 별도 기획안을 만드세요.", 409)
        ids = {i for m in generated for i in m["evidence_ids"]}
        evidence = [e for e in self.knowledge(p) if e["id"] in ids]
        statistics = self.voc_analysis(p, conv.get("filters"))
        deps = dependency_map(messages + evidence + statistics["records"] + conv.get("decisions", []))
        return conv, messages, evidence, {k: v for k, v in statistics.items() if k != "records"}, deps

    def make_proposal(self, user, body):
        p = user["project_id"]
        epoch = self.store.epoch(p)
        conv, messages, evidence, statistics, deps = self.planning_context(user, body)
        prd = self.store.get(p, "prd", conv["prd_id"])
        if not self.accessible(p, prd):
            raise AppError("기준 PRD의 근거가 변경됐습니다.", 409)
        decisions = [d for d in conv.get("decisions", []) if d.get("active", True)]
        result = self.generate("proposal", {"conversation": messages, "evidence": evidence, "statistics": statistics,
            "target_prd": prd, "decisions": decisions})
        answer = validate_answer(result, evidence, statistics)
        if set(result["decision_ids"]) != {d["id"] for d in decisions}:
            raise AppError("PRD 제안에 모든 PO 결정이 연결되지 않았습니다.", 502)
        changes = result["changes"]
        by_section = {s["id"]: s for s in prd["sections"]}
        if not 1 <= len(changes) <= len(by_section) or len({c["section_id"] for c in changes}) != len(changes):
            raise AppError("PRD 변경 문단 목록을 확인하지 못했습니다.", 502)
        cleaned = []
        for change in changes:
            section = change["section_id"]
            if section not in by_section:
                raise AppError("기준 PRD에 없는 문단입니다.", 502)
            ids = strings(change["evidence_ids"], 100, 80)
            if not ids or not set(ids).issubset({e["id"] for e in evidence}):
                raise AppError("PRD 변경의 근거가 올바르지 않습니다.", 502)
            proposed = text(change, "proposed_text", 20000)
            rationale = text(change, "rationale", 5000)
            validate_answer({"text": proposed + "\n" + rationale, "evidence_ids": ids, "assumptions": []}, evidence, statistics)
            cleaned.append({"section_id": section, "section_title": by_section[section]["title"], "before": by_section[section]["text"],
                "after": proposed, "rationale": rationale, "evidence_ids": ids})
        fields = {**answer, "text": inline_text(answer), "conversation_id": conv["id"], "state": "draft", "changes": cleaned,
            "target_prd_id": prd["id"], "target_prd_version": prd["version"], "decision_ids": result["decision_ids"],
            "decisions": decisions, "source_message_ids": [m["id"] for m in messages],
            "persona_versions": conv.get("participant_persona_versions", []), "dependencies": dependency_map([{"dependencies": deps}, prd]),
            "evidence_snapshots": evidence, "model": self.model.model}
        return self.store.write(p, inserts=[("proposal", fields, None)], expected_epoch=epoch)[0]

    def decide_proposal(self, user, body):
        p = user["project_id"]
        epoch = self.store.epoch(p)
        old = self.store.get(p, "proposal", text(body, "proposal_id", 80))
        if not self.accessible(p, old):
            raise AppError("제안의 근거가 변경됐습니다. 다시 생성하세요.", 409)
        state = body.get("state")
        if state not in ("accepted", "held"):
            raise AppError("accepted 또는 held를 선택하세요.")
        if old["state"] == "accepted":
            if state == "accepted":
                return old
            raise AppError("반영된 제안은 보류로 되돌릴 수 없습니다. 새 PRD 변경안을 작성하세요.", 409)
        if state == "held":
            return self.store.update(p, "proposal", old["id"], {"state": state, "decided_by": user["id"]}, old["version"])
        prd = self.store.get(p, "prd", old["target_prd_id"])
        if prd["version"] != old["target_prd_version"]:
            raise AppError("기준 PRD가 변경됐습니다. 최신 버전에서 제안을 다시 생성하세요.", 409)
        edits = {c["section_id"]: c for c in old["changes"]}
        sections = [{**s, "text": edits[s["id"]]["after"] + "\n\n" + " ".join("["+i+"]" for i in edits[s["id"]]["evidence_ids"])}
                    if s["id"] in edits else s for s in prd["sections"]]
        deps = dependency_map([prd, old])
        result = self.store.write(p, updates=[
            ("prd", prd["id"], {"sections": sections, "dependencies": deps, "last_proposal_id": old["id"], "decisions": old.get("decisions", [])}, prd["version"]),
            ("proposal", old["id"], {"state": "accepted", "decided_by": user["id"], "applied_prd_version": prd["version"] + 1}, old["version"])
        ], expected_epoch=epoch)
        return result[-1]

    def edit_proposal(self, user, body):
        p = user["project_id"]
        epoch = self.store.epoch(p)
        old = self.store.get(p, "proposal", body.get("proposal_id"))
        if old["state"] == "accepted" or not self.accessible(p, old):
            raise AppError("반영됐거나 근거가 변경된 제안은 수정할 수 없습니다.", 409)
        rows = body.get("changes")
        if not isinstance(rows, list) or len(rows) != len(old["changes"]):
            raise AppError("변경 문단 목록을 확인하세요.")
        originals = {r["section_id"]: r for r in old["changes"]}
        if any(not isinstance(r, dict) for r in rows) or {r.get("section_id") for r in rows} != set(originals):
            raise AppError("제안에 없는 문단은 수정할 수 없습니다.")
        updates = [{**originals[r["section_id"]], "after": text(r, "after", 20000), "rationale": text(r, "rationale", 5000)} for r in rows]
        return self.store.write(p, updates=[("proposal", old["id"], {"changes": updates, "edited_by": user["id"], "review_status": "po_edited_hypothesis"}, revision(body))], expected_epoch=epoch)[0]
