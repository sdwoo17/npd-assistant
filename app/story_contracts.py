"""Structured planning input. Origin labels are provenance, never accuracy scores."""
from .contracts import optional, strings, text
from .store import AppError

FIELDS = {'title': 200, 'actor': 1000, 'problem': 3000, 'goal': 3000, 'benefit': 3000,
          'scenario': 6000, 'exceptions': 6000, 'assumptions': 5000}
ORIGIN_FIELDS = (*FIELDS, 'acceptance_criteria')
CONTEXT_FIELDS = ('customer', 'problem', 'benefit', 'evidence_summary', 'experience',
                  'existing_product', 'constraints', 'in_scope', 'out_scope', 'hypothesis', 'metric')


def obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}

S = {'type': 'string'}
SA = {'type': 'array', 'items': S}
CRITERION = obj({'given': S, 'when': S, 'then': S})
EXTRACTION_SCHEMA = obj({
    'transcript': S,
    'regions': {'type': 'array', 'items': obj({'id': S, 'text': S,
        'x': {'type': 'number'}, 'y': {'type': 'number'}, 'width': {'type': 'number'}, 'height': {'type': 'number'},
        'uncertain': {'type': 'boolean'}})},
    'candidates': {'type': 'array', 'items': obj({**{k: S for k in FIELDS},
        'acceptance_criteria': {'type': 'array', 'items': CRITERION},
        'questions': {'type': 'array', 'items': obj({'text': S, 'critical': {'type': 'boolean'}})},
        'origins': {'type': 'array', 'items': obj({'field': S, 'origin': {'type': 'string', 'enum': ['from_source', 'ai_proposed']}, 'region_ids': SA})}})},
    'warnings': SA})


def criteria(rows):
    if not isinstance(rows, list) or len(rows) > 30:
        raise AppError('수용 기준은 최대 30개까지 입력하세요.')
    return [{k: optional(r, k, 3000) for k in ('given', 'when', 'then')} for r in rows]


def extraction(result):
    """Additional model-domain checks beyond its JSON response envelope."""
    transcript = optional(result, 'transcript', 50000)
    rows = result['regions']
    if len(rows) > 80 or len(result['candidates']) > 20:
        raise AppError('이미지 해석 결과가 너무 큽니다. 영역을 나눠 다시 시도하세요.', 502)
    regions, seen = [], set()
    for row in rows:
        rid = text(row, 'id', 80)
        if rid in seen:
            raise AppError('원본 영역 ID가 중복됐습니다.', 502)
        seen.add(rid)
        nums = [row[k] for k in ('x', 'y', 'width', 'height')]
        if any(type(n) not in (int, float) or not 0 <= n <= 1 for n in nums) or nums[2] <= 0 or nums[3] <= 0 or nums[0]+nums[2] > 1.00001 or nums[1]+nums[3] > 1.00001:
            raise AppError('원본 영역 좌표가 올바르지 않습니다.', 502)
        regions.append({**{k: row[k] for k in ('x', 'y', 'width', 'height', 'uncertain')}, 'id': rid, 'text': optional(row, 'text', 4000)})
    candidates = []
    for row in result['candidates']:
        candidate = {k: optional(row, k, limit) for k, limit in FIELDS.items()}
        if not candidate['title']:
            raise AppError('스토리 후보 제목을 확인하지 못했습니다.', 502)
        origins = {}
        for origin in row['origins']:
            key = origin['field']
            ids = strings(origin['region_ids'], 80, 80)
            if key not in ORIGIN_FIELDS or key in origins or not set(ids).issubset(seen):
                raise AppError('스토리와 원본 영역 연결이 올바르지 않습니다.', 502)
            if origin['origin'] == 'from_source' and not ids:
                raise AppError('원본 추출 필드에 원본 위치가 없습니다.', 502)
            origins[key] = {'origin': origin['origin'], 'region_ids': ids}
        # Omitted origins can never be promoted to an observed fact.
        for key in ORIGIN_FIELDS:
            origins.setdefault(key, {'origin': 'ai_proposed', 'region_ids': []})
        if len(row['questions']) > 30:
            raise AppError('확인 질문이 너무 많습니다.', 502)
        questions = [{'text': text(q, 'text', 2000), 'critical': q['critical']} for q in row['questions']]
        for region in regions:
            if region['uncertain']:
                questions.append({'text': '원본 영역 ' + region['id'] + '의 판독과 의미를 확인해 주세요.', 'critical': True})
        if len(questions) > 40:
            raise AppError('불확실한 영역이 너무 많습니다. 이미지를 나눠 다시 올려주세요.', 422)
        candidates.append({**candidate, 'origins': origins, 'acceptance_criteria': criteria(row['acceptance_criteria']), 'questions': questions})
    if not candidates:
        raise AppError('스토리를 해석하지 못했습니다. 선명한 사진이나 텍스트를 추가해 주세요.', 422)
    return {'transcript': transcript, 'regions': regions, 'candidates': candidates, 'warnings': strings(result['warnings'], 30, 2000)}
