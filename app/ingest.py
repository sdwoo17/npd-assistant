import base64
import csv
import hashlib
import io
import re
import zipfile
import unicodedata
import math
from .contracts import date_value
from pathlib import Path
from .store import AppError

FEATURES = {
    "reporting": {"name": "성과 리포트", "terms": ["리포트", "보고서", "성과", "roas", "report", "rapport", "bericht", "レポート", "보고"], "service_id": "advertiser_portal", "parent_feature_id": ""},
    "creative_test": {"name": "소재 실험", "terms": ["소재", "a/b", "실험", "테스트 설계", "대조군", "유의성", "가설", "표본", "creative", "experiment", "significance", "création", "créatif", "expérience", "werbemittel", "signifikanz", "クリエイティブ", "有意", "素材", "実験"], "service_id": "advertiser_portal", "parent_feature_id": ""},
    "budget": {"name": "예산·효율", "terms": ["광고비", "예산", "비용", "수익", "budget", "spend", "ausgaben", "予算", "費用"], "service_id": "advertiser_portal", "parent_feature_id": ""},
    "workflow": {"name": "협업·승인", "terms": ["승인", "권한", "대행사", "공유", "인수인계", "approval", "collaboration", "freigabe", "genehmigung", "approbation", "承認"], "service_id": "advertiser_portal", "parent_feature_id": ""},
    "unclassified": {"name": "미분류", "terms": [], "service_id": "advertiser_portal", "parent_feature_id": ""},
}



def redact(text):
    text = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[이메일]", text)
    text = re.sub(r"(?<!\d)(?:\+82[- .]?)?0?1[016789][- .]?\d{3,4}[- .]?\d{4}(?!\d)", "[전화번호]", text)
    text = re.sub(r"(?<!\d)\d{6}[- ]?[1-8]\d{6}(?!\d)", "[식별번호]", text)
    return text


def normalized(value):
    return unicodedata.normalize("NFC", "".join(c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c)))


def classify_all(value, features=None):
    features = features or FEATURES
    content = normalized(value)
    scored = []
    for key, f in features.items():
        hits = [t for t in f["terms"] if normalized(t) in content]
        # Broad report language must not swallow specific creative/experiment needs.
        score = sum(1 + min(len(t), 8) / 8 for t in hits)
        if key == "creative_test" and hits:
            score += 2
        if score:
            scored.append((score, key))
    ordered = [k for _, k in sorted(scored, key=lambda x: (-x[0], x[1]))]
    if not ordered:
        return ["unclassified"]
    # One VoC belongs to one service, including initial rule classification.
    service = features[ordered[0]].get("service_id", "advertiser_portal")
    return [key for key in ordered if features[key].get("service_id", "advertiser_portal") == service]


def classify(value, features=None):
    return classify_all(value, features)[0]


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
            from docx.text.paragraph import Paragraph
            from docx.table import Table
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if len(z.infolist()) > 1000 or sum(f.file_size for f in z.infolist()) > 20 * 1024 * 1024:
                    raise AppError("문서 압축 해제 크기가 너무 큽니다.")
            doc = Document(io.BytesIO(data))
            blocks = []
            for item in doc.element.body:
                if item.tag.endswith('}p'):
                    blocks.append(Paragraph(item, doc).text)
                elif item.tag.endswith('}tbl'):
                    blocks.extend(' | '.join(c.text for c in row.cells) for row in Table(item, doc).rows)
            text = '\n'.join(blocks)
        elif suffix == '.pptx':
            text = pptx_text(data)
        else:
            raise AppError("PDF, DOCX, PPTX, Markdown, TXT, CSV를 지원합니다.")
    except AppError:
        raise
    except Exception:
        raise AppError("문서를 추출하지 못했습니다. 텍스트 문서 또는 UTF-8 CSV로 다시 업로드하세요.")
    if not text.strip():
        raise AppError("추출된 텍스트가 없습니다. 스캔 PDF는 먼저 OCR이 필요합니다.")
    if len(text) > 150000:
        raise AppError("추출 텍스트는 150,000자 이하이어야 합니다.")
    return Path(filename).name, text, hashlib.sha256(data).hexdigest()


def pptx_text(data):
    """Visible slide text in presentation order; no images, notes or OCR claims."""
    import posixpath
    import xml.etree.ElementTree as ET
    ns = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
          'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
          'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        entries = z.infolist()
        if (len(entries) > 1000 or len({i.filename for i in entries}) != len(entries)
                or sum(i.file_size for i in entries) > 20 * 1024 * 1024):
            raise AppError('PPTX 압축 해제 한도를 초과했습니다.')
        def xml(name):
            raw = z.read(name)
            declarations = raw.replace(b'\x00', b'').upper()
            if b'<!DOCTYPE' in declarations or b'<!ENTITY' in declarations:
                raise AppError('문서에 허용되지 않는 XML 선언이 있습니다.')
            return ET.fromstring(raw)
        rels = {r.attrib['Id']: r.attrib for r in xml('ppt/_rels/presentation.xml.rels')}
        slides = xml('ppt/presentation.xml').findall('p:sldIdLst/p:sldId', ns)
        if not 1 <= len(slides) <= 100:
            raise AppError('PPTX는 1~100장이어야 합니다.')
        content = []
        for index, slide in enumerate(slides, 1):
            rel = rels[slide.attrib['{' + ns['r'] + '}id']]
            path = posixpath.normpath(posixpath.join('ppt', rel['Target']))
            if rel.get('TargetMode') == 'External' or not re.fullmatch(r'ppt/slides/[^/]+\.xml', path):
                raise AppError('슬라이드 참조가 올바르지 않습니다.')
            paragraphs = []
            for paragraph in xml(path).findall('.//a:p', ns):
                value = ''.join(node.text or '' for node in paragraph.findall('.//a:t', ns))
                if value.strip():
                    paragraphs.append(value)
            if paragraphs:
                content.append('# 슬라이드 ' + str(index) + '\n' + '\n'.join(paragraphs))
        return '\n\n'.join(content)


def parse_csv(value, features=None):
    features = features or FEATURES
    try:
        reader = csv.DictReader(io.StringIO(value), strict=True)
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)) or not {"external_id", "text"}.issubset(reader.fieldnames):
            raise AppError("CSV에는 중복 없는 external_id,text 열이 필요합니다.")
        rows, errors = [], []
        for index, row in enumerate(reader, 2):
            if index > 1001:
                raise AppError("한 번에 1,000건까지 업로드할 수 있습니다.")
            try:
                if None in row:
                    raise AppError("헤더보다 열이 많습니다.")
                external = (row.get("external_id") or "").strip()
                content = (row.get("text") or "").strip()
                if not external or not content:
                    raise AppError("external_id 또는 text가 비어 있습니다.")
                if len(external) > 200 or len(content) > 10000:
                    raise AppError("external_id는 200자, 발언은 10,000자 이하이어야 합니다.")
                kind = row.get("evidence_type") or "real"
                if kind not in ("real", "synthetic"):
                    raise AppError("evidence_type은 real 또는 synthetic이어야 합니다.")
                cleaned = redact(content)
                requested = row.get("feature") or ""
                if requested and requested not in features:
                    raise AppError("등록되지 않은 기능입니다. 기능 트리를 먼저 가져오세요.")
                linked = [requested] if requested else classify_all(cleaned, features)
                segment = redact(row.get("segment") or "미지정")
                if len(segment) > 200:
                    raise AppError("광고주 유형은 200자 이하이어야 합니다.")
                rows.append({"row": index, "external_id": external, "text": cleaned, "feature": linked[0], "feature_ids": linked,
                             "service_id": features[linked[0]].get("service_id", "advertiser_portal"),
                             "segment": segment, "occurred_at": date_value(row.get("occurred_at") or ""),
                             "evidence_type": kind, "classification_source": "imported" if requested else "rules",
                             "problem": "", "need": "", "classification_confidence": None})
            except AppError as e:
                errors.append({"row": index, "error": str(e)})
        return rows, errors
    except csv.Error:
        raise AppError("CSV 따옴표 또는 행 형식이 올바르지 않습니다.")


def terms(value):
    value = normalized(value)
    words = re.findall(r"[^\W_]+", value, re.UNICODE)
    result = {w for w in words if len(w) > 1}
    for word in words:
        if re.search(r"[가-힣ぁ-んァ-ン一-龯]", word):
            result.update(word[i:i+2] for i in range(len(word) - 1))
    # Domain aliases give deterministic Korean/English/Japanese/German/French recall.
    for feature_id, feature in FEATURES.items():
        if any(normalized(t) in value for t in feature["terms"]):
            result.add("domain:" + feature_id)
    return result


def retrieve(records, query, limit=24):
    """Field-weighted, length-normalized lexical retrieval over authorized rows.

    Taxonomy terms are weak recall hints, never substitutes for a claim in the
    record itself. No raw source or cross-project index participates here.
    """
    query_terms = terms(query)
    if not query_terms or not records or limit <= 0:
        return []
    weights = {"title": 3.0, "text": 2.0, "problem": 1.5, "need": 1.5,
               "applicability": 1.0, "limitations": 0.7, "segment": 0.7,
               "competitor": 0.8, "feature_names": 0.25, "feature_terms": 0.15}
    fields = [{k: terms(str(r.get(k, ""))) for k in weights} for r in records]
    bags = [set().union(*row.values()) for row in fields]
    averages = {k: max(1, sum(len(row[k]) for row in fields) / len(records)) for k in weights}
    document_frequency = {term: sum(term in bag for bag in bags) for term in query_terms}
    scored = []
    for record, row, bag in zip(records, fields, bags):
        common = query_terms & bag
        score = 0.0
        for term in common:
            tf = sum(weight / (0.4 + 0.6 * len(row[k]) / averages[k])
                     for k, weight in weights.items() if term in row[k])
            idf = math.log(1 + (len(records) - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
            score += idf * tf * 2.2 / (tf + 1.2) * (0.2 if term.startswith("domain:") else 1)
        if score:
            scored.append((score, record))
    return [r for _, r in sorted(scored, key=lambda x: (-x[0], x[1]["id"]))[:limit]]


def chunks(value, size=12000):
    """Bounded chunks, no silent truncation, prefer a paragraph boundary."""
    result = []
    while value:
        end = min(size, len(value))
        if end < len(value):
            boundary = value.rfind("\n", size // 2, end)
            if boundary != -1:
                end = boundary + 1
        result.append(value[:end])
        value = value[end:]
    return result
