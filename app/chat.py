"""Moderated interviews with pinned personas, complete bounded context and atomic turns."""
from .research_provenance import derived_nature
import hashlib
import json
import re
import uuid
from .contracts import text, optional, filters, strings, dependency_map, validate_answer, inline_text
from .ingest import redact, retrieve
from .voc import inferred_filters
from .store import AppError
from .research_workflow import internal_amazon_request

MAX_PARTICIPANTS = 8


class Chat:
    def create_conversation(self, user, body, persist=True):
        p = user["project_id"]
        ids = strings(body.get("persona_ids", []), MAX_PARTICIPANTS, 80)
        people = [self.get_persona(p, rid) for rid in ids]
        mode = body.get("mode", "research")
        if mode not in ("research", "interview"):
            raise AppError("대화 모드를 확인하세요.")
        inserts = []
        prd_id = body.get("prd_id")
        if prd_id:
            prd = self.store.get(p, "prd", prd_id)
            if not self.accessible(p, prd):
                raise AppError("사용할 수 없는 PRD입니다.", 409)
        else:
            prd_id = str(uuid.uuid4())
            inserts.append(("prd", {"title": text(body, "title", 200) + " · 새 PRD", "sections": [
                {"id": "problem", "title": "문제 정의", "text": ""},
                {"id": "requirements", "title": "요구사항", "text": ""},
                {"id": "validation", "title": "검증 계획", "text": ""}], "created_by": user["id"], "dependencies": []}, prd_id))
        conv = {"title": text(body, "title", 200), "mode": mode, "persona_ids": ids, "created_by": user["id"],
            "participant_persona_versions": [{"id": r["id"], "version": r["version"]} for r in people],
            "prd_id": prd_id, "decisions": [], "objective": optional(body, "objective", 3000), "filters": filters(body.get("filters", {})), "round_type": "explore"}
        inserts.append(("conversation", conv, None))
        if not persist:
            return inserts
        return self.store.write(p, inserts=inserts)[-1]

    def conversation(self, user, cid):
        p = user["project_id"]
        conv = self.store.get(p, "conversation", cid)
        if conv.get("followup_ref") and not self.accessible(p,conv):
            raise AppError("후속 분석의 FGI 검토본이 변경됐습니다. 현재 결과를 다시 연결하세요.",409)
        messages = []
        for m in self.store.list(p, "message"):
            if m["conversation_id"] != cid:
                continue
            if not self.accessible(p, m):
                m = self.redacted(m)
            messages.append(m)
        decisions = []
        for d in conv.get("decisions", []):
            if self.accessible(p, d):
                decisions.append(d)
        return {**conv, "messages": messages, "decisions": decisions}

    def conversation_state(self, user, body):
        p = user["project_id"]
        conv = self.store.get(p, "conversation", body.get("conversation_id"))
        if conv.get("study_id"):
            study = self.study(user, conv["study_id"])
            if study["status"] == "completed" or any(k in body and body[k] != conv.get(k) for k in ("objective", "mode", "prd_id")):
                raise AppError("스터디 시작 후 목적·모드·기준 PRD는 고정됩니다. 후속 스터디를 만드세요.", 409)
        updates = {}
        if "objective" in body:
            updates["objective"] = optional(body, "objective", 3000)
        if "mode" in body:
            if body["mode"] not in ("research", "interview"):
                raise AppError("대화 모드를 확인하세요.")
            updates["mode"] = body["mode"]
        if "round_type" in body:
            if body["round_type"] not in ("explore", "challenge"):
                raise AppError("인터뷰 라운드를 확인하세요.")
            updates["round_type"] = body["round_type"]
        if "filters" in body:
            updates["filters"] = filters(body["filters"])
        if "prd_id" in body:
            prd = self.store.get(p, "prd", body["prd_id"])
            if not self.accessible(p, prd):
                raise AppError("사용할 수 없는 PRD입니다.", 409)
            updates["prd_id"] = prd["id"]
        return self.store.update(p, "conversation", conv["id"], updates, body.get("expected_version", conv["version"]))

    def decision(self, user, body):
        p = user["project_id"]
        conv = self.conversation(user, body.get("conversation_id"))
        decisions = conv.get("decisions", [])
        if body.get("decision_id"):
            found = next((d for d in decisions if d["id"] == body["decision_id"]), None)
            if not found:
                raise AppError("PO 결정을 찾을 수 없습니다.", 404)
            found["active"] = False
        else:
            if len(decisions) >= 100:
                raise AppError("한 대화에 최대 100개 결정을 저장할 수 있습니다.")
            deps = dependency_map([m for m in conv["messages"] if not m.get("redacted")])
            decisions.append({"id": str(uuid.uuid4()), "text": redact(text(body, "text", 3000)), "active": True,
                              "actor_id": user["id"], "dependencies": deps})
        self.store.update(p, "conversation", conv["id"], {"decisions": decisions}, body.get("expected_version", conv["version"]))
        return self.conversation(user, conv["id"])

    def chat(self, user, body):
        p = user["project_id"]
        epoch = self.store.epoch(p)
        conv = self.conversation(user, text(body, "conversation_id", 80))
        study = self.study_context(user, conv)
        if study and study["status"] == "completed":
            raise AppError("완료한 스터디에는 발언을 추가할 수 없습니다. 후속 스터디를 만드세요.", 409)
        question = redact(text(body, "message", 5000))
        request_id = optional(body, "request_id", 100) or str(uuid.uuid4())
        fingerprint = hashlib.sha256(json.dumps({"message": question, "action": body.get("action"), "filters": body.get("filters")}, sort_keys=True).encode()).hexdigest()
        previous = next((r for r in self.store.list(p, "turn") if r["conversation_id"] == conv["id"] and r["request_id"] == request_id), None)
        if previous:
            if previous["fingerprint"] != fingerprint:
                raise AppError("같은 요청 ID에 다른 내용이 입력됐습니다.", 409)
            return self.conversation(user, conv["id"])
        if internal_amazon_request(question):
            response = {"speaker":"system", "text":"아마존의 비공개 내부 정보에 대해서는 답변하기 어렵습니다. 공개된 자료를 기준으로 질문해 주세요.",
                "evidence_ids":[], "assumptions":[], "dependencies":[], "status":"restricted_information", "model":None}
            return self.persist_turn(user, conv, question, [response], [], request_id, fingerprint, epoch, {})
        history = [m for m in conv["messages"] if not m.get("redacted")]
        if len(history) >= 1000 or sum(len(m["text"]) for m in history) + len(question) > 160000:
            raise AppError("대화 처리 한도입니다. 기존 내용을 내보내고 새 기획 대화를 시작하세요.", 409)
        # Explicit chat action and natural-language persona creation share one domain operation.
        action = body.get("action")
        if action not in (None, "ask", "create_persona"):
            raise AppError("지원하지 않는 채팅 동작입니다.")
        natural_create = bool(re.search(r"페르소나.*(?:만들|생성)", question)) and "@" not in question
        if action == "create_persona" or natural_create:
            if study:
                raise AppError("스터디 세션에서는 참여자 풀이 고정됩니다. 페르소나 화면에서 별도로 생성하세요.", 409)
            person = self.generate_persona(user, {"segment": question, "name": body.get("persona_name", ""), "filters": conv.get("filters")}, persist=False)
            # A chat request produces a draft batch; activation requires explicit PO selection.
            deps = person["dependencies"];bid=str(uuid.uuid4())
            batch={'profile':question,'candidates':[person],'rationale':'채팅에서 요청한 검토용 프로필 초안',
                'dependencies':deps,'state':'draft','selected_indices':[],'is_synthetic':True,'created_by':user['id']}
            response = {"speaker":"system","text":"페르소나 초안을 준비했습니다. 페르소나 화면에서 근거·가정을 비교하고 등록할 초안을 선택하세요.",
                "evidence_ids":person['evidence_ids'],"assumptions":person['assumptions'],"is_synthetic":True,
                "persona_batch_id":bid,"dependencies":deps,"status":"persona_draft_created"}
            return self.persist_turn(user,conv,question,[response],deps,request_id,fingerprint,epoch,{},[('persona_batch',batch,bid)])
        current = [r for r in self.store.list(p, "persona") if not r.get("archived") and self.accessible(p, r)]
        by_alias = {r["alias"]: r for r in current}
        pinned = []
        for ref in conv.get("participant_persona_versions", []):
            try:
                r = self.get_persona(p, ref["id"], ref["version"])
                pinned.append(r)
                if study:
                    by_alias[r["alias"]] = r
                else:
                    by_alias.setdefault(r["alias"], r)
            except AppError:
                pass
        aliases = re.findall(r"(?<![\w@])@([\w-]+)", question, re.UNICODE)
        targets = []
        if aliases:
            for alias in dict.fromkeys(a.casefold() for a in aliases):
                if alias not in by_alias:
                    raise AppError("@" + alias + " 페르소나를 찾을 수 없거나 근거가 철회됐습니다.", 404)
                targets.append(by_alias[alias])
        elif conv["mode"] == "interview":
            targets = pinned
            if not targets or len(targets) != len(conv.get("participant_persona_versions", [])):
                raise AppError("인터뷰 대상의 근거를 확인하고 @태그로 다시 지정하세요.", 409)
        if study and (not targets or any({"id": r["id"], "version": r["version"]} not in study["participants"] for r in targets)):
            raise AppError("FGI 스터디는 리크루팅한 참여자와 버전으로 진행합니다. 참여자 변경은 새 스터디에 기록하세요.", 409)
        if len(targets) > MAX_PARTICIPANTS:
            raise AppError(f"한 번에 최대 {MAX_PARTICIPANTS}명을 지정하세요.")
        chosen = body.get("filters", conv.get("filters", {}))
        scope = inferred_filters(question, chosen)
        retrieval_question = question
        if history:
            retrieval_question += " " + " ".join(m["text"] for m in history[-2:])
        evidence, search_info = self.search(p, retrieval_question, scope)
        knowledge = self.knowledge(p)
        if conv.get('followup_ref'):
            linked=self.research_ref(user,conv['followup_ref'],True)
            if linked['id'] not in {e['id'] for e in evidence}:
                evidence.append(next(e for e in knowledge if e['id']==linked['id']))
        if targets:
            # Persona evidence is explicit, not silently counted in filtered VoC statistics.
            ids = {i for r in targets for i in r["evidence_ids"]} | {e["id"] for e in evidence}
            evidence = [e for e in knowledge if e["id"] in ids]
        if not evidence and search_info["eligible"] and self.model.configured and len(question.split()) > 2:
            expansion = self.generate("search", {"question": question})
            keywords = strings(expansion["keywords"], 10, 200)
            evidence, search_info = self.search(p, question + " " + " ".join(keywords), scope)
            search_info["method"] = "multilingual_query_expansion"
        if not evidence:
            response = {"speaker": "system", "text": "현재 질문과 연결할 수 있는 공개 근거가 없습니다. 서비스·기능을 지정하거나 리서치·VoC를 추가해 주세요. 근거가 없는 상태에서는 고객 사실이나 PRD 결론을 생성하지 않습니다.",
                "evidence_ids": [], "assumptions": [], "dependencies": [], "status": "no_evidence", "model": None}
            return self.persist_turn(user, conv, question, [response], [], request_id, fingerprint, epoch, {"filters": scope})
        statistics = self.voc_analysis(p, scope)
        if study:
            provided = {e["id"] for e in evidence}
            guide_ids = set(study["guide"]["evidence_ids"])
            evidence += [e for e in self.knowledge(p) if e["id"] in guide_ids and e["id"] not in provided]
        deps = dependency_map(evidence + targets + history + statistics["records"] + conv.get("decisions", []) + ([study] if study else []))
        stat_input = {k: v for k, v in statistics.items() if k != "records"}
        structured_history = [{k: m.get(k) for k in ("id", "speaker", "text", "evidence_ids", "assumptions", "observations", "is_synthetic", "persona_id", "persona_version")} for m in history]
        moderator = [{"id": m["id"], "text": m["text"]} for m in history if m["speaker"] == "PO"]
        responses = []
        for person in targets or [None]:
            self.store.assert_epoch(p, epoch)
            payload = {"question": question, "history": structured_history, "moderator_messages": moderator,
                "decisions": [d for d in conv.get("decisions", []) if d.get("active", True)], "objective": conv.get("objective", ""),
                "round_type": conv.get("round_type", "explore"), "evidence": evidence, "statistics": stat_input,
                "study": study, "followup_context": conv.get("followup_context"),
                "search": search_info, "persona_evidence_scope": "Profile evidence may predate the requested analytics filter; use statistics only for filtered counts."}
            if person:
                payload["persona"] = person
            answer = validate_answer(self.generate("interview" if person else "chat", payload), evidence, stat_input, bool(person))
            response = {**answer, "text": inline_text(answer), "speaker": person["alias"] if person else "리서치 어시스턴트",
                "persona_id": person["id"] if person else None, "persona_version": person["version"] if person else None,
                "is_synthetic": bool(person), **derived_nature(evidence+history+targets), "model": self.model.model, "provider": getattr(self.model, "provider", "test"),
                "dependencies": deps, "status": "completed", "statistics": stat_input, "search": search_info}
            responses.append(response)
            structured_history.append({**response, "id": "current-round-" + str(len(responses))})
        updates = {"filters": scope}
        if targets and not study:
            updates.update(mode="interview", persona_ids=[r["id"] for r in targets],
                participant_persona_versions=[{"id": r["id"], "version": r["version"]} for r in targets])
        return self.persist_turn(user, conv, question, responses, deps, request_id, fingerprint, epoch, updates)

    def persist_turn(self, user, conv, question, responses, deps, request_id, fingerprint, epoch, updates, extra_inserts=()):
        p = user["project_id"]
        inserts = list(extra_inserts) + [("message", {"conversation_id": conv["id"], "speaker": "PO", "text": question, "evidence_ids": [],
            "assumptions": [], "dependencies": deps, "is_synthetic": False, "status": "completed"}, None)]
        inserts.extend(("message", {**response, "conversation_id": conv["id"]}, None) for response in responses)
        inserts.append(("turn", {"conversation_id": conv["id"], "request_id": request_id, "fingerprint": fingerprint}, None))
        self.store.write(p, inserts=inserts, updates=[("conversation", conv["id"], updates, conv["version"])], expected_epoch=epoch)
        return self.conversation(user, conv["id"])
