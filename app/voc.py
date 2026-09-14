"""Source-preserving VoC ingestion, editable feature trees and deterministic aggregates."""
import calendar
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse
from .contracts import text, optional, strings, revision, date_value, filters
from .ingest import FEATURES, decode_file, parse_csv, redact, classify_all
from .model import NoRedirect
from .research import owner
from .store import AppError, timestamp


def source_key(source, external):
    return hashlib.sha256(json.dumps([source, external], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def safe_external(external):
    return external if redact(external) == external else "redacted-" + hashlib.sha256(external.encode()).hexdigest()[:20]


def inferred_filters(question, chosen=None):
    result = filters({} if chosen is None else chosen)
    if result["date_from"] or result["date_to"]:
        return result
    exact_dates = re.findall(r"(?<!\d)20\d{2}-\d{2}-\d{2}(?!\d)", question)
    if exact_dates:
        return filters({**result, "date_from": exact_dates[0], "date_to": exact_dates[-1]})
    match = re.search(r"(20\d{2})\s*(?:년|年|-)\s*(\d{1,2})\s*(?:월|月)?", question)
    if match:
        year, month = map(int, match.groups())
        if not 1 <= month <= 12:
            raise AppError("질문의 월을 확인하세요.")
        result.update(date_from=f"{year}-{month:02d}-01", date_to=f"{year}-{month:02d}-{calendar.monthrange(year, month)[1]}")
    elif "이번 달" in question or "이번달" in question:
        now = datetime.now(timezone.utc)
        result.update(date_from=f"{now.year}-{now.month:02d}-01", date_to=f"{now.year}-{now.month:02d}-{calendar.monthrange(now.year, now.month)[1]}")
    return result


class Voc:
    def features(self, project):
        result = {k: dict(v) for k, v in FEATURES.items()}
        for r in self.store.list(project, "feature"):
            result[r["key"]] = {k: r[k] for k in ("name", "terms", "service_id", "parent_feature_id")}
            result[r["key"]].update(version=r["version"], record_id=r["id"])
        return result

    def import_features(self, user, body):
        owner(user)
        p = user["project_id"]
        epoch = self.store.epoch(p)
        rows = body.get("features")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
            raise AppError("기능 목록은 1~200개여야 합니다.")
        tree, seen = self.features(p), set()
        prepared = []
        for row in rows:
            key = text(row, "key", 80)
            if not re.fullmatch(r"[a-z][a-z0-9_-]*", key) or key == "unclassified" or key in seen:
                raise AppError("기능 ID는 고유한 영문 ID이어야 하며 미분류 ID는 예약되어 있습니다.")
            seen.add(key)
            item = {"key": key, "name": text(row, "name", 120), "service_id": text(row, "service_id", 80),
                    "parent_feature_id": optional(row, "parent_feature_id", 80), "terms": strings(row.get("terms", []), 50, 100)}
            prepared.append(item)
            tree[key] = item
        # A parent edit can invalidate children omitted from this import.
        for key in tree:
            path, cursor = set(), key
            while cursor:
                if cursor not in tree or cursor in path:
                    raise AppError("기능 트리의 부모 ID 또는 순환 관계를 확인하세요.")
                path.add(cursor)
                parent = tree[cursor].get("parent_feature_id", "")
                if parent and parent in tree and tree[parent]["service_id"] != tree[cursor]["service_id"]:
                    raise AppError("부모와 자식 기능의 서비스가 다릅니다.")
                cursor = parent
        existing = {r["key"]: r for r in self.store.list(p, "feature")}
        inserts, updates = [], []
        for item in prepared:
            if item["key"] in existing:
                old = existing[item["key"]]
                updates.append(("feature", old["id"], item, old["version"]))
            else:
                inserts.append(("feature", item, None))
        self.store.write(p, inserts=inserts, updates=updates, expected_epoch=epoch)
        self.audit(user, "feature_tree_imported", "taxonomy")
        return self.features(p)

    def voc_upload(self, user, body):
        owner(user)
        p = user["project_id"]
        filename, original, digest = decode_file(body)
        if not filename.lower().endswith(".csv"):
            raise AppError("VoC는 UTF-8 CSV 파일이어야 합니다.")
        source_name = text(body, "source_name", 100)
        rows, errors = parse_csv(original, self.features(p))
        saved = self.store.put(p, "voc_source", {"filename": filename, "encrypted_text": self.store.encrypt(original),
            "encrypted_source_name": self.store.encrypt(source_name), "source_name": redact(source_name), "hash": digest})
        results, count = [], 0
        for row in rows:
            external, index = row["external_id"], row.pop("row")
            row["external_id"] = safe_external(external)
            row.update(source_type="csv", source_id=saved["id"], source_name=redact(source_name), collected_at=timestamp())
            r = self.store.voc(p, source_key(source_name, external), row)
            count += bool(r)
            results.append({"row": index, "status": "imported" if r else "duplicate", "voc_id": r["id"] if r else None, "external_id": row["external_id"]})
        self.audit(user, "voc_imported", saved["id"])
        return {"imported": count, "duplicates": len(rows) - count, "errors": errors, "results": results,
            "redaction_note": "이메일·휴대전화·식별번호 패턴 가명화. 이름·주소 등 추가 정제는 원천 관리자 검토가 필요합니다."}

    def voc_records(self, project, scope=None):
        scope = filters({} if scope is None else scope)
        result = []
        for r in self.store.list(project, "voc"):
            if r.get("withdrawn"):
                continue
            r.setdefault("feature_ids", [r["feature"]])
            r.setdefault("service_id", "advertiser_portal")
            if scope["feature"] and scope["feature"] not in r["feature_ids"]:
                continue
            if any(scope[k] and scope[k] != r.get(k) for k in ("service_id", "segment", "source_name", "evidence_type")):
                continue
            observed = r.get("occurred_at", "")[:10]
            if (scope["date_from"] or scope["date_to"]) and not observed:
                continue
            if scope["date_from"] and observed < scope["date_from"] or scope["date_to"] and observed > scope["date_to"]:
                continue
            result.append(r)
        return result

    def voc_analysis(self, project, scope=None):
        scope = filters({} if scope is None else scope)
        rows = self.voc_records(project, scope)
        counts = {f: sum(f in r["feature_ids"] for r in rows) for f in self.features(project)}
        kinds = {t: sum(r["evidence_type"] == t for r in rows) for t in ("real", "synthetic")}
        segments = {s: sum(r["segment"] == s for r in rows) for s in sorted({r["segment"] for r in rows})}
        return {"records": rows, "counts": counts, "total": len(rows), "denominator": len(rows),
                "synthetic_count": kinds["synthetic"], "evidence_type_counts": kinds, "segment_counts": segments,
                "filters": scope, "evidence_ids": [r["id"] for r in rows], "computed_at": timestamp(),
                "scope": "필터에 해당하는 현재 프로젝트의 중복 제외 VoC. 복수 기능 건수 합은 전체보다 클 수 있음. 합성 표본과 실제 수집을 구분하며 광고주 전체 비율이 아님."}

    def voc_edit(self, user, body):
        p = user["project_id"]
        r = self.store.get(p, "voc", text(body, "voc_id", 80))
        taxonomy = self.features(p)
        ids = strings(body.get("feature_ids", [body.get("feature")]), 15, 80)
        if not ids or any(i not in taxonomy for i in ids):
            raise AppError("등록된 기능을 선택하세요.")
        if len({taxonomy[i]["service_id"] for i in ids}) > 1:
            raise AppError("한 VoC의 연결 기능은 같은 서비스에 속해야 합니다.")
        return self.store.update(p, "voc", r["id"], {"feature": ids[0], "feature_ids": ids,
            "service_id": taxonomy[ids[0]]["service_id"], "classification_source": "po_reviewed",
            "problem": redact(optional(body, "problem", 1500)) if "problem" in body else redact(r.get("problem", "")),
            "need": redact(optional(body, "need", 1500)) if "need" in body else redact(r.get("need", ""))}, body.get("expected_version", r["version"]))

    def classify_voc(self, user, body):
        p = user["project_id"]
        epoch = self.store.epoch(p)
        ids = strings(body.get("voc_ids", []), 1000, 80)
        rows = [self.store.get(p, "voc", rid) for rid in ids] if ids else self.voc_records(p, body.get("filters"))
        rows = [r for r in rows if r.get("classification_source") != "po_reviewed" and not r.get("withdrawn")]
        taxonomy = self.features(p)
        updates = []
        for start in range(0, len(rows), 25):
            batch = rows[start:start + 25]
            result = self.generate("classify", {"taxonomy": taxonomy, "voc": [
                {k: r.get(k) for k in ("id", "text", "segment", "occurred_at", "evidence_type")} for r in batch]})
            returned = result["classifications"]
            if len(returned) != len(batch) or {r["voc_id"] for r in returned} != {r["id"] for r in batch}:
                raise AppError("분류 결과의 VoC 목록이 일치하지 않습니다.", 502)
            by_id = {r["id"]: r for r in batch}
            for item in returned:
                linked = strings(item["feature_ids"], 15, 80)
                confidence = item["confidence"]
                if isinstance(confidence, bool) or not 0 <= confidence <= 1 or any(k not in taxonomy for k in linked):
                    raise AppError("분류 결과의 기능·신뢰도를 확인하지 못했습니다.", 502)
                if len({taxonomy[k]["service_id"] for k in linked}) > 1:
                    raise AppError("분류 결과의 서비스가 혼합됐습니다.", 502)
                if not linked or confidence < 0.65:
                    linked = ["unclassified"]
                r = by_id[item["voc_id"]]
                updates.append(("voc", r["id"], {"feature": linked[0], "feature_ids": linked, "service_id": taxonomy[linked[0]]["service_id"],
                    "problem": redact(text(item, "problem", 1500)), "need": redact(text(item, "need", 1500)),
                    "classification_source": "ai_needs_po_review", "classification_confidence": confidence}, r["version"]))
        saved = self.store.write(p, updates=updates, expected_epoch=epoch)
        return {"updated": len(saved), "skipped_po_reviewed": len(ids) - len(rows) if ids else 0}

    def fetch_review_page(self, url, token=""):
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=25) as response:
            data = response.read(2000001)
            if len(data) > 2000000:
                raise AppError("리뷰 응답 크기가 제한을 초과했습니다.", 502)
            return json.loads(data)

    def collect_reviews(self, user, body, job_id=None):
        owner(user)
        p = user["project_id"]
        app_id = text(body, "app_id", 30)
        if not app_id.isascii() or not app_id.isdigit():
            raise AppError("숫자로 된 앱 ID가 필요합니다.")
        provider = body.get("provider", "app_store_connect")
        if provider not in ("app_store_connect", "apple_public_rss"):
            raise AppError("지원하는 리뷰 출처를 선택하세요.")
        country = body.get("country", "kr")
        if not isinstance(country, str) or not re.fullmatch(r"[a-z]{2}", country):
            raise AppError("국가 코드는 소문자 두 글자여야 합니다.")
        max_pages = body.get("max_pages", 10)
        if type(max_pages) is not int or not 1 <= max_pages <= 20 or provider == "apple_public_rss" and max_pages > 10:
            raise AppError("페이지 제한을 확인하세요. 공개 RSS는 최대 10페이지입니다.")
        token = os.getenv("APP_STORE_CONNECT_TOKEN", "") if provider == "app_store_connect" else ""
        if provider == "app_store_connect" and not token:
            raise AppError("관리 앱 수집용 App Store Connect 토큰이 설정되지 않았습니다.", 503)
        scope = filters(body.get("filters", {}))
        job = self.new_job(user, "review_collect", body, job_id)
        count = duplicates = 0
        has_more = False
        url = f"https://api.appstoreconnect.apple.com/v1/apps/{app_id}/customerReviews?limit=100&sort=-createdDate"
        seen_urls = set()
        try:
            for page in range(1, max_pages + 1):
                if provider == "apple_public_rss":
                    url = f"https://itunes.apple.com/{country}/rss/customerreviews/page={page}/id={app_id}/sortBy=mostRecent/json"
                parsed = urlparse(url)
                if parsed.scheme != "https" or parsed.hostname != ("api.appstoreconnect.apple.com" if token else "itunes.apple.com") or parsed.username or parsed.password or parsed.port not in (None, 443):
                    raise AppError("리뷰 페이지 주소가 허용된 출처가 아닙니다.", 502)
                if url in seen_urls:
                    raise AppError("리뷰 페이지가 반복됩니다.", 502)
                seen_urls.add(url)
                data = self.fetch_review_page(url, token)
                if not isinstance(data, dict) or not token and not isinstance(data.get("feed"), dict):
                    raise AppError("리뷰 응답 형식이 올바르지 않습니다.", 502)
                raw_rows = data.get("data") if token else data.get("feed", {}).get("entry", [])
                if not isinstance(raw_rows, list):
                    raise AppError("리뷰 응답 형식이 올바르지 않습니다.", 502)
                normalized = []
                for item in raw_rows:
                    if token:
                        a = item["attributes"]
                        external = str(item["id"])
                        content = str(a.get("title", "")) + "\n" + str(a.get("body", ""))
                        observed, rating = a.get("createdDate", ""), a.get("rating")
                    else:
                        if "im:rating" not in item:
                            continue
                        external = item["id"]["label"]
                        content = item["title"]["label"] + "\n" + item["content"]["label"]
                        observed, rating = item.get("updated", {}).get("label", ""), int(item["im:rating"]["label"])
                    if not external or len(external) > 200 or not content.strip() or len(content) > 10000:
                        raise AppError("리뷰 식별자·본문 크기를 확인하지 못했습니다.", 502)
                    observed = date_value(observed)
                    if (scope["date_from"] or scope["date_to"]) and not observed:
                        continue
                    if scope["date_from"] and observed[:10] < scope["date_from"] or scope["date_to"] and observed[:10] > scope["date_to"]:
                        continue
                    if type(rating) is not int or not 1 <= rating <= 5:
                        raise AppError("리뷰 평점 형식을 확인하지 못했습니다.", 502)
                    clean = redact(content)
                    linked = classify_all(clean, self.features(p))
                    normalized.append((external, {"external_id": safe_external(external), "app_id": app_id, "country": country,
                        "text": clean, "feature": linked[0], "feature_ids": linked, "service_id": self.features(p)[linked[0]]["service_id"],
                        "segment": "앱 사용자 · 광고주 여부 미확인", "occurred_at": observed, "collected_at": timestamp(),
                        "source_type": provider, "source_name": provider + ":" + app_id + ":" + country,
                        "source_url": f"https://apps.apple.com/{country}/app/id{app_id}", "evidence_type": "real", "rating": rating,
                        "classification_source": "rules", "job_id": job["id"], "problem": "", "need": ""}))
                for external, row in normalized:
                    saved = self.store.voc(p, source_key(row["source_name"], external), row)
                    count += bool(saved)
                    duplicates += not bool(saved)
                self.store.update(p, "job", job["id"], {"progress": page, "imported": count, "duplicates": duplicates})
                next_url = data.get("links", {}).get("next") if token else None
                # Period filters must not stop pagination before older matching pages.
                has_more = bool(next_url) if token else any("im:rating" in r for r in raw_rows)
                if not has_more:
                    break
                if token:
                    url = next_url
            self.store.update(p, "job", job["id"], {"status": "completed", "has_more": has_more})
            return {"job_id": job["id"], "imported": count, "duplicates": duplicates, "pages": page, "has_more": has_more,
                "scope": "요청한 출처·국가·페이지 한도·기간의 리뷰. 전체 리뷰 또는 광고주 대표 표본을 보장하지 않음.", "filters": scope}
        except Exception as e:
            self.store.update(p, "job", job["id"], {"status": "failed", "error": "리뷰 수집 실패. 출처·권한·형식을 확인 후 재시도하세요.", "imported": count, "duplicates": duplicates})
            if isinstance(e, AppError):
                raise e
            raise AppError("리뷰 수집 실패. 작업 상태를 확인하세요.", 502)
