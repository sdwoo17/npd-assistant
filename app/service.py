import hashlib
import json
import os
import re
import threading
import urllib.error
import urllib.request
from .ingest import FEATURES, classify, decode_file, parse_csv, redact, retrieve
from .model import NoRedirect
from .store import AppError, timestamp


def text(body, key, maximum=10000):
    value = body.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise AppError(f"{key}: 1~{maximum}자의 텍스트가 필요합니다.")
    return value.strip()


def owner(user):
    if user["role"] != "owner":
        raise AppError("자료 소유자만 수행할 수 있습니다.", 403)


def cited(answer, evidence):
    allowed = {e["id"] for e in evidence}
    ids = answer.get("evidence_ids")
    assumptions = answer.get("assumptions", [])
    if not isinstance(ids, list) or not ids or not all(isinstance(x, str) and x in allowed for x in ids):
        raise AppError("AI 응답의 근거를 검증하지 못했습니다. 다시 질문하세요.", 502)
    if not isinstance(assumptions, list) or not all(isinstance(x, str) and len(x) <= 3000 for x in assumptions):
        raise AppError("AI 응답의 가정 형식이 올바르지 않습니다.", 502)
    return {"text": text(answer, "text", 20000), "evidence_ids": list(dict.fromkeys(ids)), "assumptions": assumptions[:20]}


class Service:
    def __init__(self, store, model):
        self.store, self.model = store, model
        self._locks = {}
        self._lock_guard = threading.Lock()

    def audit(self, user, action, record_id):
        self.store.put(user["project_id"], "audit", {"actor_id": user["id"], "action": action, "record_id": record_id})

    def knowledge(self, project):
        insights = [{"id": r["id"], "kind": "insight", "text": r["text"], "title": r["title"], "feature": r["feature"], "segment": "", "evidence_type": r.get("evidence_type", "research")} for r in self.store.list(project, "insight") if r["published"]]
        voc = [{"id": r["id"], "kind": "voc", "text": r["text"], "feature": r["feature"], "segment": r["segment"], "evidence_type": r["evidence_type"]} for r in self.store.list(project, "voc")]
        return insights + voc

    def persona_active(self, project, persona):
        allowed = {r["id"] for r in self.knowledge(project)}
        return bool(persona["evidence_ids"]) and set(persona["evidence_ids"]).issubset(allowed)

    def conversation(self, user, cid):
        p = user["project_id"]
        conv = self.store.get(p, "conversation", cid)
        allowed = {e["id"] for e in self.knowledge(p)}
        messages = []
        for m in self.store.list(p, "message"):
            if m["conversation_id"] != cid:
                continue
            if m.get("evidence_ids") and not set(m["evidence_ids"]).issubset(allowed):
                m = {"id": m["id"], "speaker": "system", "text": "근거 공유 범위가 변경되어 이 답변을 다시 확인해야 합니다.", "evidence_ids": [], "assumptions": [], "created_at": m["created_at"], "redacted": True}
            messages.append(m)
        return {**conv, "messages": messages}

    def get(self, user, route):
        p = user["project_id"]
        if route == "/api/bootstrap":
            personas = [r for r in self.store.list(p, "persona") if self.persona_active(p, r)]
            return {"user": user, "model_configured": self.model.configured, "model_name": self.model.model if self.model.configured else None,
                    "features": FEATURES, "knowledge_count": len(self.knowledge(p)), "personas": personas,
                    "conversations": self.store.list(p, "conversation"), "version": "0.1.0"}
        if route == "/api/research":
            owner(user)
            return {"sources": [{k: v for k, v in s.items() if k != "encrypted_text"} for s in self.store.list(p, "source")], "insights": self.store.list(p, "insight")}
        if route.startswith("/api/research/raw/"):
            owner(user)
            record = self.store.get(p, "source", route.rsplit("/", 1)[-1])
            self.audit(user, "source_read", record["id"])
            return {"filename": record["filename"], "text": self.store.decrypt(record["encrypted_text"])}
        if route == "/api/evidence":
            return self.knowledge(p)
        if route == "/api/voc":
            records = self.store.list(p, "voc")
            return {"records": records, "counts": {f: sum(r["feature"] == f for r in records) for f in FEATURES}, "total": len(records), "synthetic_count": sum(r["evidence_type"] == "synthetic" for r in records), "scope": "현재 프로젝트에 저장된 중복 제외 VoC만 집계. 전체 광고주 비율이 아님."}
        if route.startswith("/api/conversations/"):
            return self.conversation(user, route.rsplit("/", 1)[-1])
        if route == "/api/proposals":
            allowed = {r["id"] for r in self.knowledge(p)}
            return [r for r in self.store.list(p, "proposal") if set(r["evidence_ids"]).issubset(allowed)]
        if route.startswith("/api/export/"):
            conv = self.conversation(user, route.rsplit("/", 1)[-1])
            evidence = self.knowledge(p)
            ids = {eid for m in conv["messages"] for eid in m.get("evidence_ids", [])}
            proposals = [r for r in self.get(user, "/api/proposals") if r["conversation_id"] == conv["id"]]
            for proposal in proposals:
                ids.update(proposal["evidence_ids"])
            return {"schema_version": "npd.research-package.v1", "exported_at": timestamp(), "conversation": conv, "proposals": proposals, "evidence": [e for e in evidence if e["id"] in ids], "disclosure": "페르소나 발언은 가상 인터뷰. AXIOM 연동 규격은 별도 확인 필요."}
        raise AppError("경로를 찾을 수 없습니다.", 404)

    def post(self, user, route, body):
        p = user["project_id"]
        if route == "/api/research/upload":
            owner(user)
            filename, source, digest = decode_file(body)
            record = self.store.put(p, "source", {"filename": filename, "title": text(body, "title", 200), "encrypted_text": self.store.encrypt(source), "hash": digest, "owner_id": user["id"], "status": "extracted"})
            self.audit(user, "source_uploaded", record["id"])
            return {k: v for k, v in record.items() if k != "encrypted_text"}
        if route == "/api/research/extract":
            owner(user)
            source = self.store.get(p, "source", body.get("source_id"))
            raw = self.store.decrypt(source["encrypted_text"])
            if len(raw) > 45000:
                raise AppError("자동 인사이트 추출은 현재 45,000자 이하 자료를 지원합니다. 문서를 나누거나 인사이트를 직접 입력하세요.")
            result = self.model.generate("insights", {"document": raw, "purpose": "소유자 검토용 인사이트 초안"})
            drafts = result.get("insights")
            if not isinstance(drafts, list) or not 1 <= len(drafts) <= 8:
                raise AppError("인사이트 응답 형식을 확인하지 못했습니다.", 502)
            clean = [{"title": text(r, "title", 200), "text": text(r, "text", 10000), "feature": r.get("feature") if r.get("feature") in FEATURES else "unclassified"} for r in drafts if isinstance(r, dict)]
            if len(clean) != len(drafts):
                raise AppError("인사이트 응답 형식이 올바르지 않습니다.", 502)
            return [self.store.put(p, "insight", {**r, "source_id": source["id"], "published": False, "evidence_type": "research"}) for r in clean]
        if route == "/api/insights":
            owner(user)
            source = self.store.get(p, "source", body.get("source_id"))
            return self.store.put(p, "insight", {"title": text(body, "title", 200), "text": text(body, "text", 10000), "feature": body.get("feature") if body.get("feature") in FEATURES else "unclassified", "source_id": source["id"], "published": False, "evidence_type": "research"})
        if route == "/api/insights/release":
            owner(user)
            if not isinstance(body.get("published"), bool):
                raise AppError("published는 true/false여야 합니다.")
            rid = text(body, "insight_id", 80)
            record = self.store.update(p, "insight", rid, {"published": body["published"]})
            self.audit(user, "insight_released" if body["published"] else "insight_revoked", rid)
            return record
        if route == "/api/voc/upload":
            owner(user)
            filename, source, digest = decode_file(body)
            if not filename.lower().endswith(".csv"):
                raise AppError("VoC는 UTF-8 CSV 파일로 업로드하세요.")
            source_name = text(body, "source_name", 100)
            rows, errors = parse_csv(source)
            saved = self.store.put(p, "voc_source", {"filename": filename, "encrypted_text": self.store.encrypt(source), "hash": digest})
            count = 0
            for row in rows:
                external_id = row.pop("external_id")
                key = hashlib.sha256((source_name + ":" + external_id).encode()).hexdigest()
                if self.store.voc(p, key, {**row, "source_type": "csv", "source_id": saved["id"]}):
                    count += 1
            self.audit(user, "voc_imported", saved["id"])
            return {"imported": count, "duplicates": len(rows) - count, "errors": errors, "redaction_note": "이메일·휴대전화·식별번호 패턴을 가명화했습니다. 이름·주소 등은 업로드 전 추가 확인이 필요합니다."}
        if route == "/api/voc/reviews":
            owner(user)
            return self.collect_reviews(user, body)
        if route == "/api/voc/feature":
            if body.get("feature") not in FEATURES:
                raise AppError("기능 분류를 확인하세요.")
            return self.store.update(p, "voc", text(body, "voc_id", 80), {"feature": body["feature"], "classification_source": "po_reviewed"})
        if route == "/api/personas/generate":
            segment = text(body, "segment", 500)
            evidence = retrieve(self.knowledge(p), segment)
            if not evidence:
                raise AppError("이 광고주 유형에 연결할 근거가 없습니다. 리서치 인사이트와 VoC를 먼저 추가하세요.", 409)
            result = self.model.generate("persona", {"target_segment": segment, "evidence": evidence})
            result["evidence_ids"] = cited({**result, "text": "persona", "assumptions": []}, evidence)["evidence_ids"]
            return self.save_persona(user, result)
        if route == "/api/personas":
            return self.save_persona(user, body)
        if route == "/api/conversations":
            mode = body.get("mode", "research")
            if mode not in ("research", "interview"):
                raise AppError("대화 모드를 확인하세요.")
            ids = body.get("persona_ids", [])
            if not isinstance(ids, list) or len(ids) > 4:
                raise AppError("한 인터뷰에 최대 4개 페르소나를 지정하세요.")
            for rid in ids:
                person = self.store.get(p, "persona", rid)
                if not self.persona_active(p, person):
                    raise AppError("페르소나 근거가 변경됐습니다.", 409)
            return self.store.put(p, "conversation", {"title": text(body, "title", 200), "mode": mode, "persona_ids": ids, "created_by": user["id"]})
        if route == "/api/chat":
            cid = text(body, "conversation_id", 80)
            with self._lock_guard:
                lock = self._locks.setdefault((p, cid), threading.Lock())
            with lock:
                return self.chat(user, body)
        if route == "/api/proposals":
            conv = self.conversation(user, text(body, "conversation_id", 80))
            usable = [m for m in conv["messages"] if m.get("evidence_ids") and not m.get("redacted")]
            if not usable:
                raise AppError("근거가 있는 대화를 먼저 진행하세요.", 409)
            ids = {i for m in usable for i in m["evidence_ids"]}
            evidence = [e for e in self.knowledge(p) if e["id"] in ids]
            result = cited(self.model.generate("proposal", {"conversation": usable[-20:], "evidence": evidence}), evidence)
            if not set(result["evidence_ids"]).issubset({e["id"] for e in self.knowledge(p)}):
                raise AppError("응답 생성 중 근거 공유가 변경됐습니다. 다시 시도하세요.", 409)
            return self.store.put(p, "proposal", {**result, "conversation_id": conv["id"], "state": "draft", "source_message_ids": [m["id"] for m in usable], "model": self.model.model})
        if route == "/api/proposals/decision":
            state = body.get("state")
            if state not in ("accepted", "held"):
                raise AppError("accepted 또는 held를 지정하세요.")
            rid = text(body, "proposal_id", 80)
            if rid not in {r["id"] for r in self.get(user, "/api/proposals")}:
                raise AppError("제안의 근거를 다시 확인하세요.", 409)
            return self.store.update(p, "proposal", rid, {"state": state, "decided_by": user["id"]})
        raise AppError("경로를 찾을 수 없습니다.", 404)

    def save_persona(self, user, body):
        p = user["project_id"]
        evidence = self.knowledge(p)
        ids = cited({**body, "text": "persona", "assumptions": []}, evidence)["evidence_ids"]
        name = text(body, "name", 40)
        alias = re.sub(r"[^a-zA-Z0-9가-힣_-]", "", name)
        if not alias:
            raise AppError("이름에 한글·영문·숫자를 포함하세요.")
        if any(r["alias"] == alias for r in self.store.list(p, "persona")):
            raise AppError("동일한 @태그가 있습니다. 다른 이름을 사용하세요.", 409)
        assumptions = body.get("assumptions", "근거 밖의 선호는 인터뷰 추론으로 표시")
        if isinstance(assumptions, list):
            assumptions = "\n".join(str(x) for x in assumptions)
        return self.store.put(p, "persona", {"name": name, "alias": alias, "segment": text(body, "segment", 500), "goals": text(body, "goals", 3000), "constraints": text(body, "constraints", 3000), "assumptions": str(assumptions)[:5000], "evidence_ids": ids, "version": 1, "is_synthetic": True})

    def chat(self, user, body):
        p = user["project_id"]
        conv = self.conversation(user, text(body, "conversation_id", 80))
        question = text(body, "message", 5000)
        personas = [r for r in self.store.list(p, "persona") if self.persona_active(p, r)]
        aliases = re.findall(r"@([a-zA-Z0-9가-힣_-]+)", question)
        targets = []
        if aliases:
            for alias in dict.fromkeys(aliases):
                found = next((r for r in personas if r["alias"] == alias), None)
                if not found:
                    raise AppError(f"@{alias} 페르소나를 찾을 수 없거나 근거 공유가 철회됐습니다.", 404)
                targets.append(found)
        elif conv["mode"] == "interview":
            by_id = {r["id"]: r for r in personas}
            targets = [by_id[rid] for rid in conv["persona_ids"] if rid in by_id]
            if not targets:
                raise AppError("인터뷰할 페르소나를 @태그로 지정하세요.")
        if len(targets) > 4:
            raise AppError("한 번에 최대 4개 페르소나에게 질문하세요.")
        knowledge = self.knowledge(p)
        history = [{"speaker": m["speaker"], "text": m["text"]} for m in conv["messages"][-12:] if not m.get("redacted")]
        retrieval_query = question + " " + " ".join(m["text"] for m in history[-2:])
        evidence = retrieve(knowledge, retrieval_query)
        if targets:
            ids = {i for r in targets for i in r["evidence_ids"]} | {r["id"] for r in evidence}
            evidence = [e for e in knowledge if e["id"] in ids]
        if not evidence:
            raise AppError("관련 근거를 찾지 못했습니다. 기능명·광고주 유형을 포함해 질문하거나 자료를 추가하세요.", 409)
        responses = []
        for person in targets or [None]:
            payload = {"question": question, "history": history, "evidence": evidence}
            if person:
                payload["persona"] = person
            result = cited(self.model.generate("interview" if person else "chat", payload), evidence)
            responses.append({**result, "speaker": person["alias"] if person else "리서치 어시스턴트", "persona_id": person["id"] if person else None, "is_synthetic": bool(person), "model": self.model.model})
            history.append({"speaker": responses[-1]["speaker"], "text": result["text"]})
        # Persist only after all requested participants produced validated responses.
        currently_allowed = {e["id"] for e in self.knowledge(p)}
        if any(not set(r["evidence_ids"]).issubset(currently_allowed) for r in responses):
            raise AppError("응답 생성 중 근거 공유가 변경됐습니다. 다시 시도하세요.", 409)
        self.store.put(p, "message", {"conversation_id": conv["id"], "speaker": "PO", "text": question, "evidence_ids": [], "assumptions": []})
        for response in responses:
            self.store.put(p, "message", {**response, "conversation_id": conv["id"]})
        if targets:
            self.store.update(p, "conversation", conv["id"], {"mode": "interview", "persona_ids": [r["id"] for r in targets]})
        return self.conversation(user, conv["id"])

    def collect_reviews(self, user, body):
        app_id = text(body, "app_id", 30)
        if not app_id.isdigit():
            raise AppError("숫자로 된 App Store 앱 ID가 필요합니다.")
        token = os.environ.get("APP_STORE_CONNECT_TOKEN", "")
        if not token:
            raise AppError("App Store Connect 토큰이 설정되지 않았습니다. 서버 설정 후 관리 권한이 있는 앱의 리뷰를 수집할 수 있습니다.", 503)
        url = f"https://api.appstoreconnect.apple.com/v1/apps/{app_id}/customerReviews?limit=100&sort=-createdDate"
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
        try:
            with urllib.request.build_opener(NoRedirect()).open(req, timeout=25) as resp:
                raw = resp.read(2000001)
                if len(raw) > 2000000:
                    raise AppError("리뷰 응답이 너무 큽니다.", 502)
                data = json.loads(raw)
            rows = data["data"]
            if not isinstance(rows, list):
                raise ValueError()
            count = 0
            for r in rows:
                a = r["attributes"]
                cleaned = redact(str(a.get("title", "")) + "\n" + str(a.get("body", "")))
                key = hashlib.sha256(("apple:" + app_id + ":" + str(r["id"])).encode()).hexdigest()
                if self.store.voc(user["project_id"], key, {"text": cleaned[:10000], "feature": classify(cleaned), "segment": "앱 리뷰 · 미분류", "occurred_at": a.get("createdDate", ""), "source_type": "app_store_connect", "evidence_type": "real", "rating": a.get("rating")}):
                    count += 1
            return {"imported": count, "duplicates": len(rows) - count, "has_more": bool(data.get("links", {}).get("next")), "scope": "최신 최대 100건. 전체 리뷰 수집 또는 경쟁사 앱 크롤링이 아님."}
        except AppError:
            raise
        except Exception:
            raise AppError("앱 리뷰를 수집하지 못했습니다. 앱 접근 권한·토큰·응답 형식을 확인하세요.", 502)
