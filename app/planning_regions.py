"""Immutable PO region revisions and crop transforms for planning images."""
import io
import math
from .contracts import text, optional, revision
from .story_contracts import IMAGE_LIMIT, objects, rect
from .store import AppError

REGION_KINDS = ('text', 'box', 'arrow', 'branch', 'crossout', 'question', 'unreadable')


def image_view(preview, body):
    from PIL import Image
    crop = rect(body.get('crop', [0, 0, 1, 1]))
    rotation = body.get('rotation', 0)
    if type(rotation) is not int or rotation not in (0, 90, 180, 270):
        raise AppError('분석 회전은 0·90·180·270도 중에서 선택하세요.')
    with Image.open(io.BytesIO(preview)) as picture:
        w, h = picture.size
        x, y, cw, ch = crop
        bounds = (math.floor(x*w), math.floor(y*h), min(w, math.ceil((x+cw)*w)), min(h, math.ceil((y+ch)*h)))
        left, top, right, bottom = bounds
        if right <= left or bottom <= top:
            raise AppError('분석 영역에 이미지 픽셀이 없습니다.')
        cropped = picture.crop(bounds)
        if rotation:
            cropped = cropped.transpose({90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_90}[rotation])
        output = io.BytesIO()
        cropped.save(output, format='JPEG', quality=90)
        if output.tell() > IMAGE_LIMIT:
            raise AppError('선택한 분석 이미지가 3MB를 초과합니다.')
        return output.getvalue(), {'crop': [left/w, top/h, (right-left)/w, (bottom-top)/h],
            'rotation': rotation, 'width': cropped.width, 'height': cropped.height,
            'coordinate_space': 'analysis_view_normalized'}


def original_rect(value, view):
    x, y, w, h = rect(value)
    rotation = view['rotation']
    if rotation == 90:
        x, y, w, h = y, 1-x-w, h, w
    elif rotation == 180:
        x, y = 1-x-w, 1-y-h
    elif rotation == 270:
        x, y, w, h = 1-y-h, x, h, w
    cx, cy, cw, ch = view['crop']
    return rect([max(0, cx+x*cw), max(0, cy+y*ch), w*cw, h*ch])


def validated_regions(rows, relations, view=None):
    regions, ids = [], set()
    for item in objects(rows, 100, '원본 영역'):
        rid = text(item, 'id', 80)
        if rid in ids or item.get('kind') not in REGION_KINDS:
            raise AppError('영역 ID 중복 또는 영역 종류를 확인하세요.')
        ids.add(rid)
        regions.append({'id': rid, 'bbox': original_rect(item['bbox'], view) if view else rect(item.get('bbox')),
            'text': optional(item, 'text', 150000), 'kind': item['kind']})
    edges = []
    for item in objects(relations, 200, '영역 관계'):
        if item.get('from_region') not in ids or item.get('to_region') not in ids or type(item.get('uncertain')) is not bool:
            raise AppError('영역 관계의 시작·끝 또는 불확실성 표시를 확인하세요.')
        edges.append({'from_region': item['from_region'], 'to_region': item['to_region'],
            'meaning': text(item, 'meaning', 3000), 'uncertain': item['uncertain']})
    if sum(len(r['text']) for r in regions) > 150000:
        raise AppError('영역 전사 합계는 150,000자 이하이어야 합니다.')
    return regions, edges


class PlanningRegions:
    def edit_planning_regions(self, user, body):
        p = user['project_id']; epoch = self.store.epoch(p)
        run = self.extraction(user, text(body, 'extraction_id', 80))
        if run['status'] != 'completed' or run['version'] != revision(body):
            raise AppError('편집 기준 분석이 변경됐거나 완료되지 않았습니다.', 409)
        regions, relations = validated_regions(body.get('regions'), body.get('relations', []))
        if not regions:
            raise AppError('검토할 영역을 하나 이상 남겨 주세요.')
        note = text(body, 'note', 3000)
        previous = {r['id']: r for r in run['regions']}
        for region in regions:
            prior = previous.get(region['id'], {})
            region.update(origin='po_edited', original_bbox=prior.get('original_bbox', prior.get('bbox')),
                original_text=prior.get('original_text', prior.get('text')), edited_by=user['id'])
        fields = {k: run[k] for k in ('asset_id', 'asset_version', 'prompt', 'prompt_version', 'model', 'quality_issues', 'questions')}
        fields.update(status='completed', transcript=optional(body, 'transcript', 150000), regions=regions, relations=relations,
            review_status='source_check_required', dependencies=[], derived_from={'id': run['id'], 'version': run['version']},
            origin='po_edited', review_note=note, created_by=user['id'], view=run.get('view'), error='')
        self.planning_actor(user)
        return self.store.write(p, inserts=[('extraction_run', fields, None)], expected_epoch=epoch,
            checks=[('extraction_run', run['id'], run['version']), ('planning_asset', run['asset_id'], run['asset_version'])])[0]
