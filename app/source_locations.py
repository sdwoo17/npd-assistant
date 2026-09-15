"""Content-free locations into the validated extracted text, never raw exports."""
import base64
import io
import re
from pathlib import Path


def document_locations(body, content):
    suffix=Path(body['filename']).suffix.lower();raw=base64.b64decode(body['content_base64'],validate=True)
    parts=[];cursor=0;unread=[]
    def add(locator, value):
        nonlocal cursor
        if not value:return
        start=content.find(value,cursor)
        if start<0:
            unread.append(locator+': extracted text location unavailable');return
        parts.append({'locator':locator,'start':start,'end':start+len(value)})
        cursor=start+len(value)
    if suffix=='.docx':
        from docx import Document
        from docx.text.paragraph import Paragraph
        from docx.table import Table
        doc=Document(io.BytesIO(raw));paragraph=table=0
        for element in doc.element.body:
            if element.tag.endswith('}p'):
                paragraph+=1;add(f'paragraph/{paragraph}',Paragraph(element,doc).text)
            elif element.tag.endswith('}tbl'):
                table+=1
                for ri,row in enumerate(Table(element,doc).rows,1):
                    for ci,cell in enumerate(row.cells,1):add(f'table/{table}/row/{ri}/cell/{ci}',cell.text)
        if doc.inline_shapes:unread.append('embedded images: not OCR-read')
        unread.append('headers, footers, comments and tracked-edit semantics: not evaluated')
    elif suffix=='.pdf':
        from pypdf import PdfReader
        pdf=PdfReader(io.BytesIO(raw))
        for index,page in enumerate(pdf.pages,1):
            value=page.extract_text() or ''
            if value.strip():add(f'page/{index}',value)
            else:unread.append(f'page/{index}: no extractable text; OCR required')
        unread.append('page images and visual layout: not evaluated')
    elif suffix=='.pptx':
        matches=list(re.finditer(r'(?m)^# 슬라이드 (\d+)\n',content))
        for index,match in enumerate(matches):
            end=matches[index+1].start() if index+1<len(matches) else len(content)
            parts.append({'locator':'slide/'+match[1]+'/body','start':match.start(),'end':end})
        unread.append('speaker notes and slide images: not extracted; review separately')
    else:
        for index,line in enumerate(content.splitlines(keepends=True),1):add(f'line/{index}',line)
    return {'schema_version':'npd.source-locations.v1','parts':parts,
        'read_scope':'extracted text only','unread_scope':unread,'claim_support':'REQUIRES_PASSAGE_REVIEW'}


def clean_locator(value):
    if value is None:return {'status':'NEEDS_REVIEW','parts':[]}
    # Earlier private packs used a human-authored string, not verified offsets.
    # Retain that label for review without treating it as parsed coverage.
    if isinstance(value,str):value={'parts':[],'legacy_label':value}
    if not isinstance(value,dict) or not isinstance(value.get('parts'),list) or len(value['parts'])>2000:
        from .store import AppError
        raise AppError('원문 위치 형식을 확인하세요.')
    from .store import AppError
    legacy=value.get('legacy_label','')
    if not isinstance(legacy,str) or len(legacy)>500:raise AppError('기존 원문 위치는 500자 이내로 작성하세요.')
    result=[]
    for part in value['parts']:
        if not isinstance(part,dict) or not isinstance(part.get('locator'),str) or not re.fullmatch(r'[a-z0-9/._-]{1,160}',part['locator']):
            raise AppError('원문 위치 식별자를 확인하세요.')
        start,end=part.get('start'),part.get('end')
        if type(start) is not int or type(end) is not int or not 0<=start<end<=150000:
            raise AppError('원문 문자 위치 범위를 확인하세요.')
        result.append({'locator':part['locator'],'start':start,'end':end})
    # Positions identify extraction coverage, never certify semantic support.
    return {'status':'COVERAGE_REQUIRES_PASSAGE_REVIEW' if result else 'NEEDS_REVIEW','parts':result,
        **({'legacy_label':legacy} if legacy else {})}
