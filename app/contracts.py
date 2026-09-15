"""Validated public contracts; model output never controls storage or authorization."""
import json
import re
from datetime import date
from urllib.parse import urlparse
from .store import AppError


def text(body, key, maximum=10000, default=None):
    if not isinstance(body, dict):
        raise AppError("객체 형식의 입력이 필요합니다.")
    value = body.get(key, default)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise AppError(f"{key}: 1~{maximum}자의 텍스트가 필요합니다.")
    return value.strip()


def optional(body, key, maximum=1000):
    if not isinstance(body, dict):
        raise AppError("객체 형식의 입력이 필요합니다.")
    value = body.get(key, "")
    if value is None:
        value = ""
    if not isinstance(value, str) or len(value) > maximum:
        raise AppError(f"{key}: 텍스트 형식을 확인하세요.")
    return value.strip()


def strings(value, maximum=50, width=3000):
    if not isinstance(value, list) or len(value) > maximum or any(not isinstance(x, str) or not x.strip() or len(x) > width for x in value):
        raise AppError("텍스트 목록 형식을 확인하세요.")
    return list(dict.fromkeys(x.strip() for x in value))


def revision(body):
    n = body.get("expected_version")
    if type(n) is not int or n < 1:
        raise AppError("최신 expected_version이 필요합니다.")
    return n


def date_value(value):
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))?", value):
        raise AppError("날짜는 YYYY-MM-DD 또는 시간대가 있는 ISO 형식이어야 합니다.")
    try:
        from datetime import datetime
        date.fromisoformat(value[:10])
        if len(value) > 10:
            if int(value[11:13]) > 23 or int(value[14:16]) > 59 or int(value[17:19]) > 59:
                raise ValueError()
            datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise AppError("유효하지 않은 날짜입니다.")
    return value


def filters(body):
    if not isinstance(body, dict):
        raise AppError("필터 객체가 필요합니다.")
    result = {k: optional(body, k, 200) for k in ("feature", "service_id", "segment", "source_name", "evidence_type", "date_from", "date_to")}
    for k in ("date_from", "date_to"):
        if result[k]:
            date_value(result[k])
            result[k] = result[k][:10]
    if result["date_from"] and result["date_to"] and result["date_from"] > result["date_to"]:
        raise AppError("시작일은 종료일보다 늦을 수 없습니다.")
    if result["evidence_type"] not in ("", "real", "synthetic", "research"):
        raise AppError("자료 유형을 확인하세요.")
    return result


def public_url(value):
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or len(value) > 1500:
        raise AppError("공개 출처는 인증 정보가 없는 HTTPS URL이어야 합니다.")
    return value


def dependency_map(records):
    result = {}
    for r in records:
        for dep in r.get("dependencies", []):
            result[(dep["kind"], dep["id"], dep["version"])] = dep
        if r.get("kind") in ("insight", "voc", "persona", "feature"):
            result[(r["kind"], r["id"], r.get("version", 1))] = {"id": r["id"], "kind": r["kind"], "version": r.get("version", 1)}
    return sorted(result.values(), key=lambda d: (d["kind"], d["id"]))


def numbers(value):
    # Discard identifiers and numbered-list prefixes before checking numerical claims.
    s = re.sub(r"\[[^\]\n]+\]", "", value)
    s = re.sub(r"(?m)^\s*\d+[.)]\s+", "", s)
    s = re.sub(r"[a-zA-Z_-]+[a-zA-Z0-9_-]*", "", s)
    return {n.replace(",", "") for n in re.findall(r"(?<!\w)\d[\d,]*(?:\.\d+)?", s)}


def quantities(value):
    return {(n.replace(",", ""), unit.lower()) for n, unit in re.findall(
        r"(?<!\d)(\d[\d,]*(?:\.\d+)?)\s*(%|퍼센트|percent\b|건|명|원|달러|usd\b|krw\b)", value, re.IGNORECASE)}


def citation_ids(content):
    """Recognize internal UUIDs and reserved external evidence/entity IDs.

    Ordinary bracketed prose is not a citation. Recognized IDs must be resolved
    by the caller; matching the grammar never grants access to a source.
    """
    prefixes = r"(?:INS|VOC|PROB|OPP|PDEF|REQ|NFR|SCOPE|HYP|MET|BEN|AST|TASK|UTASK|IMP|PER|FGI|DEB)"
    return [value for value in re.findall(r"\[([^\]\n]+)\]", content)
            if re.fullmatch(r"[0-9a-f-]{36}", value, re.IGNORECASE)
            or re.fullmatch(prefixes + r"-[\w.-]+", value, re.IGNORECASE)]


def validate_citations(content, allowed_ids):
    if any(value not in allowed_ids for value in citation_ids(content)):
        raise AppError("본문에 확인되지 않은 인용 ID가 있습니다.", 502)


def validate_claims(content, evidence, statistics=None):
    """Apply the same claim guards to summaries and individual generated fields.

    Empty evidence is valid for qualitative questions/hypotheses, but it cannot
    authorize source citations, numerical claims or real-customer assertions.
    """
    by_id = {e["id"]: e for e in evidence}
    source_text = "\n".join(e["text"] for e in evidence)
    allowed_numbers, allowed_quantities = numbers(source_text), quantities(source_text)
    if statistics:
        counts = [statistics.get("total", 0), statistics.get("denominator", 0)]
        for key in ("counts", "evidence_type_counts", "segment_counts"):
            counts.extend(statistics.get(key, {}).values())
        counts = [n for n in counts if type(n) is int and n >= 0]
        allowed_numbers |= {str(n) for n in counts}
        allowed_quantities |= {(str(n), "건") for n in counts}
        denominator = statistics.get("denominator", 0)
        if denominator:
            for n in counts:
                for digits in (0, 1, 2):
                    value = f"{n / denominator * 100:.{digits}f}"
                    allowed_numbers.add(value)
                    allowed_quantities |= {(value, unit) for unit in ("%", "퍼센트", "percent")}
    if not numbers(content).issubset(allowed_numbers):
        raise AppError("근거·서버 집계에 없는 수치가 포함됐습니다. 수치를 확인해 다시 요청하세요.", 502)
    if not quantities(content).issubset(allowed_quantities):
        raise AppError("수치의 단위가 근거·서버 집계와 일치하지 않습니다.", 502)
    # This is not semantic truth verification; human review remains mandatory.
    validate_citations(content, by_id)
    if any(p in content for p in ("실제 광고주 전원", "모든 고객이 동의", "실제 고객 100%", "실제 인터뷰 결과")) and not any(content in e["text"] and e["evidence_type"] == "real" for e in evidence):
        raise AppError("가상 추론을 실제 고객 검증으로 표현할 수 없습니다.", 502)


def validate_answer(answer, evidence, statistics=None, interview=False):
    if not isinstance(answer, dict):
        raise AppError("AI 응답은 객체여야 합니다.", 502)
    by_id = {e["id"]: e for e in evidence}
    ids = answer.get("evidence_ids")
    if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or i not in by_id for i in ids):
        raise AppError("AI 응답의 근거를 검증하지 못했습니다.", 502)
    assumptions = answer.get("assumptions", [])
    try:
        assumptions = strings(assumptions, 30)
        content = text(answer, "text", 20000)
    except AppError:
        raise AppError("AI 응답의 텍스트·가정 형식이 올바르지 않습니다.", 502)
    if interview and not assumptions:
        raise AppError("가상 인터뷰는 추론·가정을 명시해야 합니다.", 502)
    validate_claims(content, evidence, statistics)
    observations = answer.get("observations", [])
    if not isinstance(observations, list) or len(observations) > 30:
        raise AppError("관찰 근거 형식이 올바르지 않습니다.", 502)
    clean = []
    for item in observations:
        if not isinstance(item, dict) or item.get("evidence_id") not in by_id:
            raise AppError("관찰 출처를 확인하지 못했습니다.", 502)
        quote = item.get("quote")
        if not isinstance(quote, str) or not quote.strip() or len(quote) > 1500 or quote not in by_id[item["evidence_id"]]["text"]:
            raise AppError("직접 인용이 제공된 근거와 일치하지 않습니다.", 502)
        source = by_id[item["evidence_id"]]
        clean.append({"evidence_id": item["evidence_id"], "quote": quote, "evidence_type": source["evidence_type"]})
    return {"text": content, "evidence_ids": list(dict.fromkeys(ids)), "assumptions": assumptions,
            "observations": clean, "interpretation_status": "hypothesis_requires_po_review"}


def inline_text(answer):
    lines = [answer["text"] + " " + " ".join("[" + i + "]" for i in answer.get("evidence_ids", []))]
    for observation in answer.get("observations", []):
        lines.append('근거 발췌: ' + observation["quote"] + ' [' + observation["evidence_id"] + ']')
    return "\n\n".join(lines)
