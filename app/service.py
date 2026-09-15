"""Framework-neutral application service. Authentication scope comes only from the server."""
import json
import threading
import time
from urllib.parse import urlparse, parse_qs
from .contracts import text, strings, filters, dependency_map
from .ingest import FEATURES, retrieve
from .model import validate
from .research import Research, owner
from .voc import Voc
from .planning import Planning
from .chat import Chat
from .assets import Assets
from .citations import Citations
from .studies import Studies, GUIDE_SECTIONS
from .store import AppError, timestamp
from .planning_assets import PlanningAssets
from .stories import Stories
from .definitions import Definitions
from .research_workflow import ResearchWorkflow
from .persona_catalog import PersonaCatalog, PERSONA_POOL_LIMIT
from .research_workspace import ResearchWorkspace
from .research_demo import ResearchDemo
from .research_flow import ResearchFlow
from .flow_contracts import SERVICE_FIELDS

# Public compatibility export for earlier integrations.
from .contracts import validate_answer as cited


class Service(Research, Voc, Planning, Chat, Assets, Citations, Studies, PlanningAssets, Stories, Definitions, ResearchWorkflow, PersonaCatalog, ResearchWorkspace, ResearchFlow, ResearchDemo):
    def __init__(self, store, model):
        self.store, self.model = store, model
        self._locks = {}
        self._lock_guard = threading.Lock()
        self._last_model_success = None
        self._model_probe_lock = threading.Lock()
        self._next_model_probe = 0

    def audit(self, user, action, rid):
        self.store.put(user["project_id"], "audit", {"actor_id": user["id"], "action": action, "record_id": rid})

    def generate(self, task, payload, images=None):
        try:
            result = validate(task, self.model.generate(task, payload, images=images) if images else self.model.generate(task, payload))
        except Exception:
            self._last_model_success = None
            raise
        self._last_model_success = timestamp()
        return result

    def test_model(self, user, body):
        owner(user)
        if getattr(self.model, "provider", None) != "bedrock":
            raise AppError("서버를 Bedrock 제공자로 설정한 후 테스트하세요.", 503)
        if not self._model_probe_lock.acquire(blocking=False):
            raise AppError("Bedrock 연결 테스트가 진행 중입니다.", 409)
        try:
            if time.monotonic() < self._next_model_probe:
                raise AppError("연결 테스트는 10초 뒤 다시 실행할 수 있습니다.", 429)
            self._next_model_probe = time.monotonic() + 10
            self._last_model_success = None
            result = self.model.probe()
            self._last_model_success = timestamp()
            self.audit(user, "bedrock_probe_success", "runtime")
            return result
        finally:
            self._model_probe_lock.release()

    def base_knowledge(self, project):
        from .research_provenance import derived_nature
        sources = {r["id"]: r for r in self.store.list(project, "source")}
        taxonomy = self.features(project)
        insights = []
        for r in self.store.list(project, "insight"):
            source = sources.get(r["source_id"])
            if not r["published"] or not source or source.get("policy") == "amazon_internal" or r.get("source_version", 1) != source.get("content_version", 1):
                continue
            if project not in r.get("allowed_projects", [project]):
                continue
            row = {k: r.get(k, "") for k in ("id", "text", "title", "feature", "applicability", "limitations", "competitor", "observed_at", "public_url")}
            row.update(kind="insight", version=r["version"], source_version=r.get("source_version", 1), segment="",
                feature_ids=r.get("feature_ids", [r["feature"]]), service_id=r.get("service_id", "advertiser_portal"), evidence_type=r.get("evidence_type", "research"))
            row.update(evidence_category=r.get('evidence_category','SYNTHETIC_FGI' if r.get('evidence_type')=='synthetic' else 'ATTACHMENT_STATEMENT'),
                source_locator=r.get('source_locator',{'status':'NEEDS_REVIEW','parts':[]}),
                source_family_id=r.get('source_family_id',''),claim_status=r.get('claim_status','UNVERIFIED'))
            row.update(derived_nature([source,r]))
            insights.append(row)
        voc = [{k: r.get(k, "") for k in ("id", "kind", "version", "text", "feature", "feature_ids", "service_id", "segment", "evidence_type",
            "external_id", "occurred_at", "collected_at", "source_type", "source_name", "source_url", "app_id", "rating", "problem", "need")} for r in self.voc_records(project)]
        for r in voc:r.update(derived_nature([r]))
        for r in insights + voc:
            r["feature_names"] = " ".join(taxonomy[f]["name"] for f in r["feature_ids"] if f in taxonomy)
            r["feature_terms"] = " ".join(t for f in r["feature_ids"] if f in taxonomy for t in taxonomy[f]["terms"])
        return insights + voc

    def knowledge(self, project):
        from .research_provenance import derived_nature, synthetic_record
        base=self.base_knowledge(project)
        allowed={(e['kind'],e['id'],e['version']) for e in base}
        results=[]
        for kind in ('research_result','research_item','debrief'):
            for row in self.store.list(project,kind):
                if not (row.get('state')=='reviewed' or row.get('review_status')=='po_reviewed'):
                    continue
                if not self.accessible(project,row,_allowed=allowed):continue
                results.append({'id':row['id'],'kind':kind,'version':row['version'],
                    'title':row.get('title','검토한 가상 FGI 결과'),'text':row['text'],
                    'evidence_type':'synthetic' if synthetic_record(row) else 'research',
                    'feature':'','feature_ids':[],'feature_names':'','feature_terms':'','segment':'',
                    'service_id':'','source_type':'saved_analysis','category':row.get('category',row.get('output_type','fgi')),
                    'applicability':'검토한 프로젝트 분석 결과','limitations':'원천 관찰과 PO 해석을 구분하세요.',
                    'dependencies':row.get('dependencies',[])+[{'kind':kind,'id':row['id'],'version':row['version']}],**derived_nature([row])})
        return base+results

    def accessible(self, project, record, seen=None, _memo=None, _allowed=None):
        if record.get('withdrawn'):return False
        seen = set(seen or ())
        identity = (record.get("kind"), record.get("id"), record.get("version"))
        if identity in seen:
            return False
        memo={} if _memo is None else _memo
        if identity in memo:return memo[identity]
        seen.add(identity)
        dependencies = record.get("dependencies")
        if dependencies is None:
            # Legacy generated artifacts have incomplete provenance; never grandfather them in.
            if record.get("kind") in ("message", "persona", "proposal", "debrief"):
                return False
            return True
        if record.get("kind") == "debrief":
            group_ids = {eid for key in ("common_needs", "disagreements", "hypotheses", "unsupported_claims", "followup_questions")
                for row in record.get(key, []) for eid in row.get("evidence_ids", [])}
            tracked_ids = {d["id"] for d in dependencies if d["kind"] in ("insight", "voc", "research_result", "research_item", "debrief")}
            if not group_ids.issubset(tracked_ids):
                # Old edited summaries may have citations without version lineage.
                return False
        allowed = {(e["kind"], e["id"], e["version"]) for e in self.base_knowledge(project)} if _allowed is None else _allowed
        if record.get('kind')=='research_result' and record.get('category')=='voc':
            from .research_provenance import synthetic_record
            mids=record.get('message_ids',[])
            real={r['id'] for r in self.voc_records(project) if r['evidence_type']=='real'}
            if not mids:return False
            for mid in mids:
                try:message=self.store.get(project,'message',mid)
                except AppError:return False
                if synthetic_record(message) or any(d['kind']=='persona' for d in message.get('dependencies',[])) or not real.intersection(message.get('evidence_ids',[])):
                    return False
                if not self.accessible(project,message,seen,memo,allowed):return False
        for d in dependencies:
            if d["kind"] == "persona":
                try:
                    candidate = next((r for r in self.store.history(project, "persona", d["id"]) if r.get("version", 1) == d["version"]), None)
                except AppError:
                    return False
                if not candidate or not self.accessible(project, candidate, seen, memo, allowed):
                    return False
            elif d["kind"] in ("planning_asset", "user_story", "definition", "research_result", "debrief", "story_draft", "public_search", "research_item", "research_pack", "service_context", "research_inputs", "persona_batch"):
                try:
                    source = self.store.get(project, d["kind"], d["id"])
                    if source["version"] != d["version"] or source.get("withdrawn") or not self.accessible(project, source, seen, memo, allowed):
                        return False
                except AppError:
                    return False
            elif (d["kind"], d["id"], d["version"]) not in allowed:
                return False
        memo[identity]=True
        return True

    def persona_active(self, project, persona):
        return self.accessible(project, persona)

    def redacted(self, record):
        return {k: record[k] for k in ("id", "kind", "version", "created_at") if k in record} | {
            "speaker": "system", "text": "근거의 공유 범위·버전이 변경되어 이 산출물을 다시 확인해야 합니다.",
            "evidence_ids": [], "assumptions": [], "redacted": True, "status": "requires_review"}

    def search(self, project, query, scope=None):
        scope = filters({} if scope is None else scope)
        knowledge = self.knowledge(project)
        voc_ids = {r["id"] for r in self.voc_records(project, scope)}
        selected = [e for e in knowledge if e["kind"] != "voc" or e["id"] in voc_ids]
        for k in ("feature", "service_id"):
            if scope[k]:
                selected = [e for e in selected if (scope[k] in e["feature_ids"] if k == "feature" else e.get(k) == scope[k])]
        matches = retrieve(selected, query, 24)
        method = "field_weighted_bm25_multilingual_aliases"
        if not matches and any(s in query for s in ("서비스의 방향", "이 서비스", "기획 방향", "project direction", "service direction")):
            matches = selected[:24]
            method = "explicit_project_context"
        # Channel volume must not bury research in hundreds of similar VoC rows.
        # Reserve up to eight relevant insights and eight VoC; fill spare capacity
        # from global rank. Never add an unrelated row just to meet a quota.
        reserved = []
        if method != "explicit_project_context":
            for kind in ("insight", "voc"):
                reserved += retrieve([e for e in selected if e["kind"] == kind], query, 8)
            ids = {e["id"] for e in reserved}
            pool = reserved + [e for e in matches if e["id"] not in ids][:24 - len(reserved)]
            matches = retrieve(pool, query, 24)
        return matches, {"method": method, "retrieved": len(matches), "eligible": len(selected), "filters": scope, "limit": 24}

    def export_package(self, user, cid):
        p = user["project_id"]
        conv = self.conversation(user, cid)
        study = self.store.get(p, "study", conv["study_id"]) if conv.get("study_id") else None
        if study and not self.accessible(p, study):
            study = self.redacted(study)
        available_proposals = self.get(user, "/api/proposals")
        proposals = [r for r in available_proposals if r["conversation_id"] == cid]
        prd = self.store.get(p, "prd", conv["prd_id"]) if conv.get("prd_id") else None
        if prd and not self.accessible(p, prd):
            prd = self.redacted(prd)
        if prd and not prd.get("redacted"):
            seen = {r["id"] for r in proposals}
            proposals += [r for r in available_proposals if r["id"] not in seen and r["state"] == "accepted"
                          and r["target_prd_id"] == prd["id"] and r.get("applied_prd_version", 0) <= prd["version"]]
        debriefs = [r for r in self.get(user, "/api/debriefs") if r["conversation_id"] == cid]
        refs = conv.get("participant_persona_versions", [])
        people = []
        for ref in refs:
            try:
                people.append(self.get_persona(p, ref["id"], ref["version"]))
            except AppError:
                pass
        ids = {eid for r in conv["messages"] + proposals + debriefs + people for eid in r.get("evidence_ids", [])}
        for proposal in proposals:
            ids.update(e["id"] for e in proposal.get("evidence_snapshots", []))
        if prd and not prd.get("redacted"):
            ids.update(d["id"] for d in prd.get("dependencies", []) if d["kind"] in ("insight", "voc"))
        if study and not study.get("redacted"):
            ids.update(d["id"] for d in study.get("dependencies", []) if d["kind"] in ("insight", "voc"))
        return {"schema_version": "npd.research-package.v2", "exported_at": timestamp(), "conversation": conv,
            "proposals": proposals, "debriefs": debriefs, "personas": people, "prd": prd, "study": study,
            "evidence": [e for e in self.knowledge(p) if e["id"] in ids],
            "disclosure": "가상 인터뷰는 실제 고객 검증이 아닙니다. 인용 ID·버전을 유지한 자체 교환 형식이며 AXIOM 수신 규격은 별도 검증해야 합니다."}

    def markdown_package(self, package):
        lines = ["# " + package["conversation"]["title"], "", package["disclosure"]]
        study = package.get("study")
        if study and not study.get("redacted"):
            lines.extend(["", "## FGI 스터디 설계", "스터디 ID: " + study["id"] + " v" + str(study["version"]),
                          "상태: " + study["status"], "연구 목적: " + study["objective"], "리크루팅 기준: " + study["recruitment_criteria"]])
            lines += ["연구 질문: " + q for q in study["research_questions"]]
            lines += ["참여자: " + r["id"] + " v" + str(r["version"]) for r in study["participants"]]
            for section in study["guide"]["sections"]:
                lines.extend(["### " + section["title"], section["text"], " ".join("[" + i + "]" for i in section["evidence_ids"])])
        elif study:
            lines.extend(["", "## FGI 스터디", study["text"]])
        lines.extend(["", "## PO 결정"])
        for d in package["conversation"].get("decisions", []):
            if d.get("active", True):
                lines.append("- " + d["text"] + " [decision:" + d["id"] + "]")
        for m in package["conversation"]["messages"]:
            lines.extend(["", "### " + m["speaker"] + (" · 가상 인터뷰" if m.get("is_synthetic") else ""), m["text"],
                          "메시지 ID: " + m["id"]])
            if m.get("assumptions"):
                lines.append("미검증 가정: " + " / ".join(m["assumptions"]))
        for d in package["debriefs"]:
            lines.extend(["", "## FGI 디브리프", d["text"]])
            for key, label in (("common_needs", "공통 요구"), ("disagreements", "의견 차이"), ("hypotheses", "가설"), ("unsupported_claims", "근거 부족"), ("followup_questions", "실제 고객 확인 질문")):
                lines.append("### " + label)
                for item in d[key]:
                    lines.append("- " + item["text"] + " " + " ".join("["+i+"]" for i in item["evidence_ids"]) + " · 메시지 " + ", ".join(item["message_ids"]))
        for r in package["proposals"]:
            lines.extend(["", "## PRD 변경 제안 · " + r["state"], "기준 PRD: " + r["target_prd_id"] + " v" + str(r["target_prd_version"]), r["text"]])
            lines.append("제안 ID: " + r["id"] + " v" + str(r["version"]))
            lines.append("원본 대화 ID: " + r["conversation_id"])
            if r.get("applied_prd_version"):
                lines.append("반영 PRD: " + r["target_prd_id"] + " v" + str(r["applied_prd_version"]))
            for change in r["changes"]:
                lines.extend(["### " + change["section_title"], "문단 ID: " + change["section_id"], "변경 전: " + change["before"], "변경 후: " + change["after"],
                    "이유: " + change["rationale"], " ".join("["+i+"]" for i in change["evidence_ids"])])
        if package["prd"]:
            r = package["prd"]
            lines.extend(["", "## 현재 PRD · v" + str(r["version"]), "PRD ID: " + r["id"]])
            if r.get("redacted"):
                lines.append(r["text"])
            else:
                for section in r["sections"]:
                    lines.extend(["### " + section["title"], "문단 ID: " + section["id"], section["text"]])
        lines.extend(["", "## 근거 목록"])
        for e in package["evidence"]:
            lines.extend(["", "["+e["id"]+"] v" + str(e["version"]) + " · " + e["evidence_type"]+" · 자료 성격: "+e.get("evidence_nature","UNVERIFIED"), e["text"]])
            if e.get("occurred_at"):
                lines.append("관찰 시각: " + e["occurred_at"])
            if e.get("external_id"):
                lines.append("외부 ID: " + e["external_id"] + " · 출처: " + e.get("source_name", ""))
            if e.get("public_url"):
                lines.append("공개 출처: " + e["public_url"])
        return "\n\n".join(lines)

    def get(self, user, route):
        parsed = urlparse(route)
        path, query = parsed.path, {k: v[-1] for k, v in parse_qs(parsed.query).items()}
        p = user["project_id"]
        if path == '/api/service-contexts':
            return {'fields':SERVICE_FIELDS,
                'contexts':[r for r in self.store.list(p,'service_context') if self.accessible(p,r)]}
        if path == '/api/research-inputs':return self.research_inputs(user)
        if path.startswith('/api/research-results/history/'):return self.result_history(user,path.rsplit('/',1)[-1])
        if path == '/api/persona-candidates':return [r for r in self.store.list(p,'persona_batch') if self.accessible(p,r)]
        if path.startswith('/api/research-workspace/history/'):
            return self.research_item_history(user,path.rsplit('/',1)[-1])
        if path == '/api/research-workspace':
            return self.research_workspace(user)
        if path == '/api/research-workspace/impact':
            return self.research_impact(user)
        if path == '/api/research-workspace/exports':
            return [{k:r[k] for k in ('id','version','research_id','hash','receipt_status','created_at')}
                for r in self.store.list(p,'research_export') if self.research_history_visible(user,r)]
        if path == '/api/research-workspace/receipts':
            return [r for r in self.store.list(p,'research_receipt') if self.accessible(p,r)]
        if path == "/api/assets/persona-templates":
            return self.template_previews(user)
        if path == "/api/research-results":
            return self.research_results(user)
        if path == "/api/persona-catalog":
            return self.persona_catalog(user, parse_qs(urlparse(route).query).get("q", [""])[0])
        if path == "/api/planning-assets":
            return self.planning_assets(user)
        if path.startswith("/api/planning-assets/raw/"):
            return self.planning_asset_raw(user, path.rsplit("/", 1)[-1], query.get("version"))
        if path == "/api/planning-extractions":
            return self.planning_extractions(user)
        if path.startswith("/api/planning-extractions/"):
            return self.extraction(user, path.rsplit("/", 1)[-1])
        if path == "/api/stories":
            return self.story_list(user)
        if path.startswith("/api/stories/versions/"):
            return self.story_history(user, path.rsplit("/", 1)[-1])
        if path.startswith("/api/stories/"):
            return self.story(user, path.rsplit("/", 1)[-1])
        if path == "/api/story-drafts":
            return [r for r in self.store.list(p, "story_draft") if self.accessible(p, r)]
        if path == "/api/definitions":
            return self.definition_list(user)
        if path.startswith("/api/definitions/versions/"):
            return [r if self.accessible(p,r) else self.redacted(r) for r in self.store.history(p,"definition",path.rsplit("/",1)[-1])]
        if path == "/api/studies":
            return {"studies": self.study_list(user), "guide_sections": list(GUIDE_SECTIONS), "participant_limit": 6, "persona_pool_limit": PERSONA_POOL_LIMIT}
        if path.startswith("/api/studies/"):
            return self.study(user, path.rsplit("/", 1)[-1])
        if path == "/api/model/status":
            owner(user)
            if getattr(self.model, "provider", None) != "bedrock":
                raise AppError("Bedrock 서버 설정이 필요합니다.", 503)
            return self.model.status()
        if path == "/api/bootstrap":
            return {"user": user, "features": self.features(p), "model_configured": self.model.configured,
                "model_name": self.model.model if self.model.configured else None, "model_provider": getattr(self.model, "provider", "test"),
                "model_connection_verified": self._last_model_success is not None, "last_model_success": self._last_model_success,
                "knowledge_count": len(self.knowledge(p)), "version": "0.2.0",
                "persona_pool_count": sum(not r.get("archived", False) for r in self.store.list(p, "persona")),
                "unavailable_personas": [self.redacted(r) for r in self.store.list(p, "persona") if not r.get("archived") and not self.accessible(p, r)],
                "archived_personas": [r for r in self.store.list(p, "persona") if r.get("archived") and self.accessible(p, r)],
                "projects": self.store.projects(user["id"]), "personas": [r for r in self.store.list(p, "persona") if not r.get("archived") and self.accessible(p, r)], "conversations": [{k: r[k] for k in ("id", "title", "mode", "version") if k in r} for r in self.store.list(p, "conversation")]}
        if path.startswith(("/api/research", "/api/insights/versions", "/api/jobs")):
            return self.research_get(user, path)
        if path == "/api/evidence":
            return self.knowledge(p)
        if path == "/api/projects":
            return self.store.projects(user["id"])
        if path == "/api/features":
            return self.features(p)
        if path == "/api/voc":
            return self.voc_analysis(p, query)
        if path.startswith("/api/conversations/"):
            return self.conversation(user, path.rsplit("/", 1)[-1])
        if path in ("/api/proposals", "/api/debriefs"):
            kind = "proposal" if path == "/api/proposals" else "debrief"
            rows = [r for r in self.store.list(p, kind) if self.accessible(p, r)]
            return [{**r, "planning_stale": self.proposal_stale(user, r)} for r in rows] if kind == "proposal" else rows
        if path == "/api/prds":
            return [r if self.accessible(p, r) else self.redacted(r) for r in self.store.list(p, "prd")]
        if path.startswith(("/api/personas/versions/", "/api/prds/versions/")):
            kind = "persona" if "personas" in path else "prd"
            return [r if self.accessible(p, r) else self.redacted(r) for r in self.store.history(p, kind, path.rsplit("/", 1)[-1])]
        if path.startswith("/api/export/"):
            package = self.export_package(user, path.rsplit("/", 1)[-1])
            return {"format": "markdown", "text": self.markdown_package(package)} if query.get("format") == "markdown" else package
        raise AppError("경로를 찾을 수 없습니다.", 404)

    def post(self, user, route, body):
        if user.get("role") not in ("owner","po"):raise AppError("조회 권한으로 변경·확정할 수 없습니다.",403)
        p = user["project_id"]
        routes = {"/api/research/upload": self.research_upload, "/api/research/extract": self.research_extract,
            '/api/definitions/link-research':self.link_definition_research,
            '/api/service-contexts/extract':self.extract_service_context,
            '/api/service-contexts/save':self.save_service_context,
            '/api/service-contexts/interview':self.interview_service_context,
            '/api/service-contexts/apply':self.apply_context_updates,
            '/api/service-contexts/complete':self.complete_service_context,
            '/api/service-contexts/edit':self.edit_service_report,
            '/api/research-inputs':self.save_research_inputs,
            '/api/studies/followup':self.followup_study,
            '/api/studies/demo':self.import_targeting_demo,
            '/api/persona-candidates/generate':self.generate_persona_candidates,
            '/api/persona-candidates/adopt':self.adopt_persona_candidates,
            '/api/research-workspace/interview/start':self.start_po_interview,
            '/api/research-workspace/interview/apply':self.apply_po_interview,
            '/api/research-workspace/items':self.save_research_item,
            '/api/research-workspace/generate':self.generate_research_item,
            '/api/research-workspace/items/review':self.review_research_item,
            '/api/research-workspace/items/withdraw':self.withdraw_research_item,
            '/api/research-workspace/packs':self.save_research_pack,
            '/api/research-workspace/select':self.select_research_pack,
            '/api/research-workspace/gate':self.research_gate,
            '/api/research-workspace/compose':self.compose_research_prd,
            '/api/research-workspace/export':self.research_handoff,
            '/api/research-workspace/receipt':self.record_research_receipt,
            '/api/definitions/readiness':self.definition_readiness,
            "/api/persona-catalog/register": self.register_persona_catalog,
            "/api/research-results": self.save_research_result, "/api/research-results/review": self.review_research_result,
            "/api/service-analysis": self.analyze_existing_service, "/api/public-research": self.public_research, "/api/public-research/analyze": self.analyze_public_research,
            "/api/planning-assets": self.upload_planning_asset, "/api/planning-assets/upload": self.upload_planning_asset, "/api/planning-assets/withdraw": self.withdraw_planning_asset,
            "/api/planning-assets/extract": self.extract_planning_asset,
            "/api/planning-assets/regions": self.edit_planning_regions,
            "/api/stories/recovery-preview": self.preview_story_recovery, "/api/stories/recover": self.recover_story_sources,
            "/api/stories": self.save_story, "/api/stories/update": self.save_story,
            "/api/stories/review": self.review_story, "/api/stories/validation": self.story_validation,
            "/api/story-drafts": self.generate_stories, "/api/story-drafts/apply": self.apply_story_draft,
            "/api/stories/restructure": self.restructure_stories, "/api/stories/export": self.story_package,
            "/api/definitions": self.save_definition, "/api/definitions/update": self.save_definition,
            "/api/definitions/generate": self.generate_definition, "/api/definitions/confirm": self.confirm_definition,
            "/api/definitions/export": self.definition_export,
            "/api/citations/verify": self.verify_prd_citations,
            "/api/studies": self.save_study, "/api/studies/update": self.save_study,
            "/api/studies/guide": self.study_guide, "/api/studies/start": self.start_study,
            "/api/studies/complete": self.complete_study, "/api/personas/archive": self.archive_persona,
            "/api/assets/activate-personas": self.activate_templates,
            "/api/model/test": self.test_model,
            "/api/insights": self.insight_save, "/api/insights/update": self.insight_save, "/api/insights/release": self.insight_release,
            "/api/jobs/retry": self.retry_job, "/api/features/import": self.import_features,
            "/api/voc/upload": self.voc_upload, "/api/voc/reviews": self.collect_reviews, "/api/voc/feature": self.voc_edit,
            "/api/voc/classify": self.classify_voc, "/api/personas": self.save_persona, "/api/personas/update": self.save_persona,
            "/api/personas/generate": self.generate_persona, "/api/conversations": self.create_conversation,
            "/api/conversations/state": self.conversation_state, "/api/conversations/decisions": self.decision,
            "/api/prds": self.save_prd, "/api/prds/update": self.save_prd, "/api/prds/import": self.import_prd,
            "/api/debriefs": self.make_debrief, "/api/debriefs/update": self.review_debrief, "/api/debriefs/select": self.select_debrief,
            "/api/proposals": self.make_proposal, "/api/proposals/update": self.edit_proposal, "/api/proposals/decision": self.decide_proposal}
        if route == "/api/chat":
            cid = text(body, "conversation_id", 80)
            with self._lock_guard:
                lock = self._locks.setdefault((p, cid), threading.Lock())
            with lock:
                return self.chat(user, body)
        if route == "/api/projects":
            return self.store.create_project(user["id"], text(body, "title", 200))
        if route == "/api/projects/switch":
            return self.store.switch_project(user, text(body, "project_id", 80))
        if route == "/api/projects/members":
            owner(user)
            return self.store.add_member(p, text(body, "email", 300), body.get("role", "po"))
        if route == "/api/search":
            rows, metadata = self.search(p, text(body, "query", 5000), body.get("filters"))
            return {"evidence": rows, **metadata}
        if route == "/api/voc/analysis":
            return self.voc_analysis(p, body.get("filters"))
        if route == "/api/voc/withdraw":
            owner(user)
            r = self.store.get(p, "voc", body.get("voc_id"))
            return self.store.update(p, "voc", r["id"], {"withdrawn": True}, r["version"])
        if route in routes:
            return routes[route](user, body)
        raise AppError("경로를 찾을 수 없습니다.", 404)
