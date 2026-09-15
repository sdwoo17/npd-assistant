"""Evidence nature labels never authorize access or certify real observations."""
import re
from .store import AppError

NATURES=('UNVERIFIED','SYNTHETIC','OBSERVED','MIXED')

def upload_nature(body,content='',old=None):
    value=body.get('source_nature',(old or {}).get('source_nature','UNVERIFIED'))
    if value not in NATURES:raise AppError('자료 성격을 확인하세요.')
    declared_synthetic=bool(re.search(r'모두[^\n]{0,80}합성|all[^\n]{0,60}(?:synthetic|fictional)',content,re.I))
    if declared_synthetic:
        if value=='OBSERVED':raise AppError('본문이 전체 합성 자료로 선언되어 있습니다. 실제 관측으로 등록할 수 없습니다.')
        value='SYNTHETIC'
    if old and old.get('source_nature') in ('SYNTHETIC','MIXED') and value=='UNVERIFIED':
        value=old['source_nature']
    return value

def synthetic_record(row):
    f=row.get('fields',{})
    return bool(row.get('contains_synthetic') or row.get('source_nature') in ('SYNTHETIC','MIXED')
        or row.get('evidence_nature') in ('SYNTHETIC','MIXED') or row.get('evidence_type') in ('synthetic','SYNTHETIC_FGI')
        or row.get('evidence_category')=='SYNTHETIC_FGI' or f.get('provenance')=='SYNTHETIC'
        or f.get('nature')=='SYNTHETIC' or f.get('evidence_type')=='SYNTHETIC_FGI'
        or f.get('measurement_state')=='SYNTHETIC' or f.get('baseline_state')=='SYNTHETIC'
        or any(v.get('nature')=='SYNTHETIC' for v in f.get('records',[])))

def derived_nature(records,fields=None):
    values=list(records)+([{'fields':fields}] if fields else [])
    synthetic=any(synthetic_record(r) for r in values)
    observed=any(r.get('source_nature') in ('OBSERVED','MIXED') or r.get('evidence_nature') in ('OBSERVED','MIXED') or r.get('evidence_type')=='real' for r in values)
    return {'evidence_nature':'MIXED' if synthetic and observed else 'SYNTHETIC' if synthetic else 'OBSERVED' if observed else 'UNVERIFIED',
        'contains_synthetic':synthetic}
