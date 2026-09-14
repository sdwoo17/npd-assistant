"""Contracts for planning images and versioned, unvalidated design proposals."""
import math
from .contracts import text, optional, strings
from .store import AppError

STORY_FIELDS = ('title', 'actor', 'situation', 'action', 'value', 'relationship', 'problem',
                'validation_task', 'priority', 'epic', 'journey', 'release')
DEFINITION_STAGES = {
    'product': '2.1 프로덕트정의', 'stories': '2.2 사용자스토리정의',
    'requirements': '2.3 요구사항 정의', 'constraints': '2.4 비기능요구사항 정의',
    'goals': '2.5 목표설명', 'prd': '2.6 PRD생성',
}
IMAGE_LIMIT = 3 * 1024 * 1024
IMAGE_PIXELS = 16_000_000
IMAGE_EDGE = 8000


def objects(value, limit, label):
    if not isinstance(value, list) or len(value) > limit or any(not isinstance(r, dict) for r in value):
        raise AppError(label + ' 형식을 확인하세요.')
    return value


def rect(value):
    if not isinstance(value, list) or len(value) != 4 or any(type(n) not in (int, float) or not math.isfinite(n) for n in value):
        raise AppError('원본 영역은 [x,y,너비,높이]로 지정하세요.')
    x, y, w, h = value
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > 1.000001 or y + h > 1.000001:
        raise AppError('원본 영역은 이미지 내부의 0~1 좌표여야 합니다.')
    return list(value)


def story_fields(data):
    result = {key: optional(data, key, 3000 if key != 'title' else 200) for key in STORY_FIELDS}
    # Unfinished ideas may be saved without artificial evidence or a fabricated benefit.
    result['actor'] = text(data, 'actor', 1000)
    result['action'] = text(data, 'action', 3000)
    result['title'] = result['title'] or result['action'][:100]
    for key in ('mvp', 'new_problem'):
        if type(data.get(key, False)) is not bool:
            raise AppError(key + '는 true/false여야 합니다.')
        result[key] = data.get(key, False)
    result['assumptions'] = strings(data.get('assumptions', []), 30, 3000)
    result['scenarios'] = []
    for row in objects(data.get('scenarios', []), 40, '시나리오'):
        kind = row.get('type', 'normal')
        if kind not in ('normal', 'exception'):
            raise AppError('정상/예외 흐름을 선택하세요.')
        result['scenarios'].append({'type': kind, 'title': text(row, 'title', 200),
            'steps': strings(row.get('steps', []), 30, 2000), 'branch': optional(row, 'branch', 2000)})
    result['acceptance_criteria'] = [{key: text(row, key, 2000) for key in ('given', 'when', 'then')}
        for row in objects(data.get('acceptance_criteria', []), 40, '수용 기준')]
    result['questions'] = []
    for index, row in enumerate(objects(data.get('questions', []), 40, '확인 질문')):
        status = row.get('status', 'unanswered')
        if status not in ('unanswered', 'answered', 'deferred', 'excluded') or type(row.get('critical', True)) is not bool:
            raise AppError('확인 질문의 상태를 확인하세요.')
        answer = optional(row, 'answer', 3000)
        if status in ('answered', 'excluded') and not answer:
            raise AppError('질문의 답변 또는 범위 제외 사유를 기록하세요.')
        result['questions'].append({'id': row.get('id') or 'question-' + str(index + 1),
            'text': text(row, 'text', 2000), 'critical': row.get('critical', True), 'status': status, 'answer': answer})
    return result


def schemas(obj, string, strings_schema):
    number = {'type': 'number'}
    boolean = {'type': 'boolean'}
    array = lambda item: {'type': 'array', 'items': item}
    fields = {key: string for key in STORY_FIELDS}
    fields.update(mvp=boolean, new_problem=boolean, assumptions=strings_schema,
        scenarios=array(obj({'type': {'type':'string','enum':['normal','exception']}, 'title':string,'steps':strings_schema,'branch':string})),
        acceptance_criteria=array(obj({'given':string,'when':string,'then':string})),
        questions=array(obj({'text':string,'critical':boolean})),
        evidence_ids=strings_schema,
        provenance=array(obj({'field':string,'origin':{'type':'string','enum':['extracted','ai_proposed']},
            'asset_id':string,'region_id':string,'quote':string})))
    return {
        'story_drafts': obj({'stories':array(obj(fields)), 'assumptions':strings_schema}),
        'planning_image': obj({'transcript':string, 'quality_issues':strings_schema,
            'regions':array(obj({'id':string,'bbox':array(number),'text':string,
                'kind':{'type':'string','enum':['text','box','arrow','branch','crossout','question','unreadable']}})),
            'relations':array(obj({'from_region':string,'to_region':string,'meaning':string,'uncertain':boolean})),
            'questions':array(obj({'text':string,'critical':boolean}))}),
        'definition_draft': obj({'title':string, 'sections':array(obj({'id':string,'title':string,'text':string,
            'evidence_ids':strings_schema})), 'assumptions':strings_schema, 'questions':strings_schema}),
        'service_baseline': obj({'title':string,'sections':array(obj({'title':string,'text':string,'asset_ids':strings_schema})),
            'unknowns':strings_schema}),
    }
