import base64
import csv
import hashlib
import io
import re
import zipfile
from pathlib import Path
from .store import AppError

FEATURES = {
    "reporting": {"name": "성과 리포트", "terms": ["리포트", "보고서", "보고", "분석", "성과", "roas", "report"]},
    "creative_test": {"name": "소재 실험", "terms": ["소재", "a/b", "실험", "테스트", "이미지", "creative"]},
    "budget": {"name": "예산·효율", "terms": ["광고비", "예산", "비용", "수익", "budget"]},
    "workflow": {"name": "협업·승인", "terms": ["승인", "권한", "대행사", "공유", "엑셀", "csv", "다운로드"]},
    "unclassified": {"name": "미분류", "terms": []},
}


def redact(text):
    text = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[이메일]", text)
    text = re.sub(r"(?<!\d)(?:\+82[- .]?)?0?1[016789][- .]?\d{3,4}[- .]?\d{4}(?!\d)", "[전화번호]", text)
    text = re.sub(r"(?<!\d)\d{6}[- ]?[1-8]\d{6}(?!\d)", "[식별번호]", text)
    return text


def classify(text):
    scores = {k: sum(text.lower().count(t) for t in v["terms"]) for k, v in FEATURES.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] else "unclassified"


def decode_file(body):
    filename = body.get("filename", "")
    if not isinstance(filename, str) or not filename or len(filename) > 180:
        raise AppError("파일 이름을 확인하세요.")
    try:
        data = base64.b64decode(body.get("content_base64", ""), validate=True)
    except (ValueError, TypeError):
        raise AppError("파일 인코딩을 확인하세요.")
    if not 0 < len(data) <= 3 * 1024 * 1024:
        raise AppError("파일은 3MB 이하이어야 합니다.")
    suffix = Path(filename).suffix.lower()
    try:
        if suffix in (".md", ".txt", ".csv"):
            text = data.decode("utf-8-sig")
        elif suffix == ".pdf":
            from pypdf import PdfReader
            pdf = PdfReader(io.BytesIO(data))
            if len(pdf.pages) > 100:
                raise AppError("PDF는 100쪽 이하이어야 합니다.")
            text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        elif suffix == ".docx":
            from docx import Document
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if len(z.infolist()) > 1000 or sum(f.file_size for f in z.infolist()) > 20 * 1024 * 1024:
                    raise AppError("문서 압축 해제 크기가 너무 큽니다.")
            doc = Document(io.BytesIO(data))
            text = "\n".join([p.text for p in doc.paragraphs] + [" | ".join(c.text for c in row.cells) for t in doc.tables for row in t.rows])
        else:
            raise AppError("PDF, DOCX, Markdown, TXT, CSV를 지원합니다.")
    except AppError:
        raise
    except Exception:
        raise AppError("문서를 추출하지 못했습니다. 텍스트 문서 또는 UTF-8 CSV로 다시 업로드하세요.")
    if not text.strip():
        raise AppError("추출된 텍스트가 없습니다. 스캔 PDF는 먼저 OCR이 필요합니다.")
    if len(text) > 150000:
        raise AppError("추출 텍스트는 150,000자 이하이어야 합니다.")
    return Path(filename).name, text, hashlib.sha256(data).hexdigest()


def parse_csv(text):
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or not {"external_id", "text"}.issubset(reader.fieldnames):
        raise AppError("CSV에는 external_id,text 열이 필요합니다. 선택 열: feature,segment,occurred_at,evidence_type")
    rows, errors = [], []
    for index, row in enumerate(reader, 2):
        if index > 1001:
            raise AppError("한 번에 1,000건까지 업로드할 수 있습니다.")
        if not row.get("external_id", "").strip() or not row.get("text", "").strip():
            errors.append({"row": index, "error": "external_id 또는 text가 비어 있습니다."})
            continue
        if len(row["text"]) > 10000:
            errors.append({"row": index, "error": "발언이 10,000자를 초과합니다."})
            continue
        if row.get("evidence_type", "real") not in ("real", "synthetic", ""):
            errors.append({"row": index, "error": "evidence_type은 real 또는 synthetic이어야 합니다."})
            continue
        cleaned = redact(row["text"].strip())
        rows.append({"external_id": row["external_id"].strip()[:200], "text": cleaned,
                     "feature": row.get("feature") if row.get("feature") in FEATURES else classify(cleaned),
                     "segment": redact(row.get("segment", "미지정"))[:200], "occurred_at": row.get("occurred_at", "")[:40],
                     "evidence_type": row.get("evidence_type") or "real"})
    return rows, errors


def terms(text):
    words = re.findall(r"[a-z0-9가-힣]+", text.lower())
    result = set(words)
    for word in words:
        if re.search(r"[가-힣]", word):
            result.update(word[i:i+2] for i in range(len(word) - 1))
    return result


def retrieve(records, query, limit=12):
    query_terms = terms(query)
    scored = [(len(query_terms & terms(r["text"] + " " + r.get("feature", "") + " " + r.get("segment", ""))), r) for r in records]
    return [r for score, r in sorted(scored, key=lambda x: x[0], reverse=True) if score > 0][:limit]
