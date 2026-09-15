"""Project-scoped inspection of citations pasted from an external PRD.

No external tool is contacted and no PRD text is persisted. Unknown/private/
foreign IDs cannot disclose whether a protected record exists.
"""
import re
from collections import Counter
from .contracts import optional, citation_ids
from .store import AppError


class Citations:
    def verify_prd_citations(self, user, body):
        content = optional(body, "text", 200000)
        versions = body.get("expected_versions", {})
        if not isinstance(versions, dict) or len(versions) > 2000 or any(
                not isinstance(k, str) or type(v) is not int or v < 1 for k, v in versions.items()):
            raise AppError("인용 버전은 근거 ID와 양의 정수로 입력하세요.")
        occurrences = []
        for match in re.finditer(r"\[([^\]\n]+)\](?:[ \t]+v([1-9]\d*))?", content):
            rid = match[1]
            if citation_ids("[" + rid + "]"):
                occurrences.append((rid, int(match[2]) if match[2] else versions.get(rid)))
        if len(occurrences) > 5000:
            raise AppError("한 번에 인용 5,000개 이하를 확인하세요.")
        p = user["project_id"]
        knowledge = {e["id"]: e for e in self.knowledge(p)}
        own_insights = {r["id"]: r for r in self.store.list(p, "insight")}
        withdrawn_voc = {r["id"] for r in self.store.list(p, "voc") if r.get("withdrawn")}
        rows, counts, valid_ids = [], Counter(), set()
        unchecked = 0
        for (rid, expected), count in Counter(occurrences).items():
            evidence = knowledge.get(rid)
            row = {"id": rid, "occurrences": count, "requested_version": expected}
            status = "unknown_or_unavailable"
            if evidence:
                status = "version_mismatch" if expected is not None and expected != evidence["version"] else "verified"
                row.update(current_version=evidence["version"], evidence_type=evidence["evidence_type"],
                           title=evidence.get("title", ""), version_checked=expected is not None)
                if status == "verified":
                    valid_ids.add(rid)
                    unchecked += count if expected is None else 0
            elif rid in withdrawn_voc:
                status = "retracted"
            elif rid in own_insights:
                # Only previously released records can reveal a retraction. An
                # unpublished private draft stays indistinguishable from unknown.
                if any(r.get("published") for r in self.store.history(p, "insight", rid)):
                    status = "retracted" if not own_insights[rid].get("published") else "requires_review"
            row["status"] = status
            counts[status] += count
            rows.append(row)
        return {"total_citations": len(occurrences), "unique_citations": len({rid for rid, _ in occurrences}),
                "verified_unique": len(valid_ids), "counts": dict(counts), "version_unchecked": unchecked,
                "references": rows, "stored": False, "model_called": False,
                "scope": "pasted_text_current_project",
                "disclosure": "붙여넣은 PRD의 근거 ID·공개 상태를 확인한 결과입니다. 인용의 의미적 정확성이나 AXIOM 실제 사용·전송 성공을 확인한 결과는 아닙니다."}
