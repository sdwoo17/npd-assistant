"""Owner-only document processing, private drafts and explicit versioned publication."""
import hashlib
import json
from .contracts import text, optional, strings, revision, public_url, date_value
from .ingest import decode_file, chunks
from .store import AppError


def owner(user):
    if user["role"] != "owner":
        raise AppError("자료 소유자만 수행할 수 있습니다.", 403)


def visible_job(job):
    return {k: v for k, v in job.items() if not k.startswith("encrypted_")}


class Research:
    def research_get(self, user, route):
        owner(user)
        p = user["project_id"]
        if route == "/api/research":
            return {"sources": [{k: v for k, v in r.items() if not k.startswith("encrypted_")} for r in self.store.list(p, "source")],
                    "insights": self.store.list(p, "insight"), "jobs": [visible_job(r) for r in self.store.list(p, "job")]}
        if route == "/api/jobs":
            return [visible_job(r) for r in self.store.list(p, "job")]
        if route.startswith("/api/research/raw/"):
            r = self.store.get(p, "source", route.rsplit("/", 1)[-1])
            self.audit(user, "source_read", r["id"])
            return {"filename": r["filename"], "text": self.store.decrypt(r["encrypted_text"]), "version": r.get("content_version", 1)}
        if route.startswith("/api/research/versions/"):
            return [{k: v for k, v in r.items() if not k.startswith("encrypted_")} for r in self.store.history(p, "source", route.rsplit("/", 1)[-1])]
        if route.startswith("/api/insights/versions/"):
            return self.store.history(p, "insight", route.rsplit("/", 1)[-1])
        raise AppError("경로를 찾을 수 없습니다.", 404)

    def new_job(self, user, action, payload, job_id=None):
        p = user["project_id"]
        if job_id:
            previous = self.store.get(p, "job", job_id)
            if previous["status"] == "running":
                raise AppError("처리 중인 작업입니다.", 409)
            return self.store.update(p, "job", job_id, {"status": "running", "error": "", "attempts": previous["attempts"] + 1}, previous["version"])
        return self.store.put(p, "job", {"action": action, "status": "running", "attempts": 1,
            "encrypted_payload": self.store.encrypt(json.dumps(payload)), "owner_id": user["id"], "error": "", "progress": 0})

    def research_upload(self, user, body, job_id=None):
        owner(user)
        p = user["project_id"]
        job = self.new_job(user, "research_upload", body, job_id)
        try:
            filename, content, digest = decode_file(body)
            old = self.store.get(p, "source", body["source_id"]) if body.get("source_id") else None
            policy = body.get("policy", old.get("policy", "domain_proprietary") if old else "domain_proprietary")
            if old and old.get("policy") == "amazon_internal" and policy != "amazon_internal":
                raise AppError("비공개 내부 자료의 보호 정책을 업로드 갱신으로 해제할 수 없습니다.", 403)
            if policy not in ("domain_public", "domain_proprietary", "amazon_internal"):
                raise AppError("리서치 자료 정책을 확인하세요.")
            fields = {"filename": filename, "title": text(body, "title", 200), "encrypted_text": self.store.encrypt(content),
                "encrypted_file": self.store.encrypt(body["content_base64"]), "hash": digest, "owner_id": user["id"], "status": "extracted", "policy": policy}
            if body.get("source_id"):
                old = self.store.get(p, "source", body["source_id"])
                fields["content_version"] = old.get("content_version", 1) + 1
                record = self.store.update(p, "source", old["id"], fields, revision(body))
            else:
                record = self.store.put(p, "source", {**fields, "content_version": 1})
            self.store.update(p, "job", job["id"], {"status": "completed", "progress": 1, "source_id": record["id"]})
            self.audit(user, "source_uploaded", record["id"])
            return {**{k: v for k, v in record.items() if not k.startswith("encrypted_")}, "job_id": job["id"]}
        except Exception as e:
            self.store.update(p, "job", job["id"], {"status": "failed", "error": "문서 처리 실패. 파일 형식·크기·텍스트 추출 여부를 확인한 후 재시도하세요."})
            if isinstance(e, AppError):
                raise AppError(str(e) + " 작업 ID: " + job["id"], e.status)
            raise AppError("문서 처리에 실패했습니다. 작업 ID: " + job["id"], 500)

    def insight_fields(self, project, body):
        feature = body.get("feature") or "unclassified"
        taxonomy = self.features(project)
        if feature not in taxonomy:
            raise AppError("등록된 기능을 선택하세요.")
        observed = optional(body, "observed_at", 40)
        if observed:
            date_value(observed)
        return {"title": text(body, "title", 200), "text": text(body, "text", 10000), "feature": feature,
            "feature_ids": [feature], "service_id": taxonomy[feature]["service_id"],
            "applicability": optional(body, "applicability", 3000), "limitations": optional(body, "limitations", 3000),
            "competitor": optional(body, "competitor", 500), "observed_at": observed,
            "public_url": public_url(optional(body, "public_url", 1500)), "allowed_projects": [project], "published": False}

    def insight_save(self, user, body):
        owner(user)
        p = user["project_id"]
        fields = self.insight_fields(p, body)
        source = self.store.get(p, "source", body.get("source_id"))
        fields.update(source_id=source["id"], source_version=source.get("content_version", 1),
                      evidence_type=body.get("evidence_type", "research"))
        if fields["evidence_type"] not in ("research", "synthetic"):
            raise AppError("리서치 또는 합성 자료를 선택하세요.")
        if body.get("insight_id"):
            record = self.store.update(p, "insight", body["insight_id"], fields, revision(body))
        else:
            record = self.store.put(p, "insight", fields)
        self.audit(user, "insight_saved_as_private_draft", record["id"])
        return record

    def insight_release(self, user, body):
        owner(user)
        p = user["project_id"]
        epoch = self.store.epoch(p)
        if not isinstance(body.get("published"), bool):
            raise AppError("published는 true/false여야 합니다.")
        ids = strings(body.get("insight_ids", [body.get("insight_id")]), 200, 80)
        versions = body.get("versions", {})
        if not isinstance(versions, dict):
            raise AppError("버전 목록을 확인하세요.")
        updates = []
        for rid in ids:
            r = self.store.get(p, "insight", rid)
            if body["published"]:
                s = self.store.get(p, "source", r["source_id"])
                if s.get("policy") == "amazon_internal":
                    raise AppError("아마존 비공개 내부 자료는 공개 근거로 전환할 수 없습니다.", 403)
                if r.get("source_version", 1) != s.get("content_version", 1):
                    raise AppError("원문 버전이 변경됐습니다. 인사이트를 갱신하세요.", 409)
            expected = versions.get(rid, body.get("expected_version", r["version"]))
            if type(expected) is not int:
                raise AppError("버전 번호를 확인하세요.")
            updates.append(("insight", rid, {"published": body["published"]}, expected))
        result = self.store.write(p, updates=updates, expected_epoch=epoch)
        for r in result:
            self.audit(user, "insight_released" if body["published"] else "insight_revoked", r["id"])
        return result if "insight_ids" in body else result[0]

    def research_extract(self, user, body, job_id=None):
        owner(user)
        p = user["project_id"]
        if body.get("evidence_type", "research") not in ("research", "synthetic"):
            raise AppError("리서치 또는 합성 자료를 선택하세요.")
        source = self.store.get(p, "source", body.get("source_id"))
        job = self.new_job(user, "research_extract", body, job_id)
        try:
            epoch = self.store.epoch(p)
            segments = chunks(self.store.decrypt(source["encrypted_text"]))
            drafts, seen = [], set()
            for index, chunk in enumerate(segments):
                self.store.assert_epoch(p, epoch)
                result = self.generate("insights", {"document": chunk, "chunk": index + 1, "chunks": len(segments),
                    "taxonomy": self.features(p), "purpose": "소유자 검토용 비공개 초안"})
                if not 1 <= len(result["insights"]) <= 8:
                    raise AppError("청크별 인사이트 수는 1~8개여야 합니다.", 502)
                for row in result["insights"]:
                    fields = self.insight_fields(p, row)
                    key = hashlib.sha256(fields["text"].encode()).hexdigest()
                    if key not in seen:
                        seen.add(key)
                        drafts.append(("insight", {**fields, "source_id": source["id"], "source_version": source.get("content_version", 1),
                            "evidence_type": body.get("evidence_type", "research"), "chunk": index + 1, "job_id": job["id"]}, None))
                self.store.update(p, "job", job["id"], {"progress": index + 1, "total": len(segments)})
            result = self.store.write(p, inserts=drafts, expected_epoch=epoch)
            self.store.update(p, "job", job["id"], {"status": "completed", "insight_ids": [r["id"] for r in result]})
            return result
        except Exception as e:
            self.store.update(p, "job", job["id"], {"status": "failed", "error": "인사이트 추출 실패. 모델 연결·자료 변경을 확인하고 재시도하세요."})
            if isinstance(e, AppError):
                raise e
            raise AppError("인사이트 추출에 실패했습니다.", 502)

    def retry_job(self, user, body):
        owner(user)
        j = self.store.get(user["project_id"], "job", body.get("job_id"))
        if j["status"] != "failed":
            raise AppError("실패한 작업만 재시도할 수 있습니다.", 409)
        payload = json.loads(self.store.decrypt(j["encrypted_payload"]))
        if j["action"] == "research_upload":
            return self.research_upload(user, payload, j["id"])
        if j["action"] == "research_extract":
            return self.research_extract(user, payload, j["id"])
        if j["action"] == "review_collect":
            return self.collect_reviews(user, payload, j["id"])
        raise AppError("재시도할 수 없는 작업입니다.")
