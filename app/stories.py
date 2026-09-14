"""Versioned user stories and private PO-authored planning assets.

Planning intent is not customer evidence. Model extraction never approves,
updates an existing story, or obtains owner-only research originals.
"""
import base64
import hashlib
import io
import json
import uuid
from .contracts import dependency_map, optional, revision, strings, text
from .ingest import redact
from .model import validate
from .story_contracts import FIELDS, ORIGIN_FIELDS, CONTEXT_FIELDS, criteria, extraction
from .store import AppError, timestamp


def ref(record):
    return {'kind': record['kind'], 'id': record['id'], 'version': record['version']}


def image_bytes(body):
    from PIL import Image, ImageOps
    filename = text(body, 'filename', 180)
    try:
        data = base64.b64decode(text(body, 'content_base64', 4300000), validate=True)
        if not 0 < len(data) <= 3 * 1024 * 1024:
            raise AppError('이미지는 장당 3MiB 이하이어야 합니다.', 413)
        im = Image.open(io.BytesIO(data))
        if im.format not in ('PNG', 'JPEG') or im.width > 8000 or im.height > 8000 or im.width * im.height > 20000000 or getattr(im, 'n_frames', 1) != 1:
            raise AppError('정지 PNG/JPEG, 가로·세로 8,000px 및 2,000만 화소 이하를 지원합니다.')
        im.load()
        # Keep the encrypted original. A separately encoded, orientation-normalized
        # copy strips EXIF metadata before model/UI delivery.
        im = ImageOps.exif_transpose(im).convert('RGBA')
        im = Image.alpha_composite(Image.new('RGBA', im.size, 'white'), im).convert('RGB')
        out = io.BytesIO()
        im.save(out, format='JPEG', quality=90)
        normalized = out.getvalue()
        if len(normalized) > 3 * 1024 * 1024:
            raise AppError('처리용 이미지가 너무 큽니다. 사진 영역을 줄여 다시 올려주세요.', 413)
        return filename, data, normalized, im.width, im.height
    except AppError:
        raise
    except Exception:
        raise AppError('이미지를 읽지 못했습니다. 올바른 PNG/JPEG 파일을 선택하세요.') from None


class Stories:
    def stage_context(self, project):
        rows = self.store.list(project, 'product_context')
        return rows[0] if rows else None

    def save_stage_context(self, user, body):
        p, epoch = user['project_id'], self.store.epoch(user['project_id'])
        old = self.stage_context(p)
        if old and not self.accessible(p, old) and any(k not in body for k in CONTEXT_FIELDS):
            raise AppError('근거가 변경된 상품 기준은 모든 항목을 새로 작성하세요.', 409)
        fields = {k: redact(optional(body, k, 10000)) if k in body else (old or {}).get(k, '') for k in CONTEXT_FIELDS}
        ids = strings(body.get('evidence_ids', (old or {}).get('evidence_ids', [])), 100, 80)
        evidence = self.story_evidence(p, ids)
        prd_id = optional(body, 'prd_id', 80) if 'prd_id' in body else (old or {}).get('prd_id', '')
        if prd_id:
            prd = self.store.get(p, 'prd', prd_id)
            if not self.accessible(p, prd):
                raise AppError('기준 PRD 근거를 먼저 확인하세요.', 409)
        fields.update(prd_id=prd_id, evidence_ids=ids, dependencies=dependency_map(evidence), updated_by=user['id'])
        if old:
            return self.store.write(p, updates=[('product_context', old['id'], fields, revision(body))], expected_epoch=epoch)[0]
        return self.store.write(p, inserts=[('product_context', fields, 'stage2-' + hashlib.sha256(p.encode()).hexdigest()[:24])], expected_epoch=epoch)[0]

    def story_evidence(self, project, ids):
        allowed = {e['id']: e for e in self.knowledge(project)}
        if any(i not in allowed for i in ids):
            raise AppError('현재 공개된 근거만 연결할 수 있습니다.', 409)
        return [allowed[i] for i in ids]

    def planning_asset_view(self, record):
        return {k: v for k, v in record.items() if not k.startswith('encrypted_')}

    def upload_planning_asset(self, user, body):
        p = user['project_id']
        title = text(body, 'title', 200)
        kind = body.get('input_type', 'image')
        if kind == 'image':
            filename, original, normalized, width, height = image_bytes(body)
            fields = {'input_type': 'image', 'filename': filename, 'width': width, 'height': height,
                      'image_format': 'jpeg', 'encrypted_image': self.store.encrypt(base64.b64encode(normalized).decode()),
                      'encrypted_original': self.store.encrypt(base64.b64encode(original).decode())}
        elif kind == 'text':
            value = text(body, 'text', 50000)
            original = value.encode()
            fields = {'input_type': 'text', 'filename': '', 'encrypted_text': self.store.encrypt(value)}
        else:
            raise AppError('이미지 또는 텍스트를 선택하세요.')
        digest = hashlib.sha256(original).hexdigest()
        # Project-local deterministic identity makes simultaneous retries idempotent.
        rid = 'pa-' + hashlib.sha256((p + ':' + digest).encode()).hexdigest()
        existing = next((r for r in self.store.list(p, 'planning_asset') if r['id'] == rid), None)
        if existing:
            if existing.get('withdrawn'):
                raise AppError('철회된 원본입니다. 복사본을 자동 재활성화하지 않습니다.', 409)
            return {**self.planning_asset_view(existing), 'duplicate': True}
        fields.update(title=title, sha256=digest, uploaded_by=user['id'], withdrawn=False,
                      purpose='planning_intent_not_customer_evidence', dependencies=[])
        try:
            record = self.store.put(p, 'planning_asset', fields, rid)
        except AppError as exc:
            if exc.status != 409:
                raise
            record = self.store.get(p, 'planning_asset', rid)
            if record.get('withdrawn'):
                raise AppError('원본이 철회됐습니다.', 409)
        return self.planning_asset_view(record)

    def planning_asset_content(self, user, rid):
        epoch = self.store.epoch(user['project_id'])
        r = self.store.get(user['project_id'], 'planning_asset', rid)
        if r.get('withdrawn'):
            raise AppError('철회된 기획 원본입니다.', 409)
        if r['input_type'] == 'image':
            result = {'image_base64': self.store.decrypt(r['encrypted_image']), 'mime': 'image/jpeg', 'width': r['width'], 'height': r['height']}
        else:
            result = {'text': self.store.decrypt(r['encrypted_text'])}
        self.store.assert_epoch(user['project_id'], epoch)
        return result

    def withdraw_planning_asset(self, user, body):
        r = self.store.get(user['project_id'], 'planning_asset', text(body, 'asset_id', 80))
        return self.planning_asset_view(self.store.update(user['project_id'], 'planning_asset', r['id'], {'withdrawn': True}, revision(body)))

    def extract_stories(self, user, body):
        p, epoch = user['project_id'], self.store.epoch(user['project_id'])
        asset = self.store.get(p, 'planning_asset', text(body, 'asset_id', 80))
        if asset.get('withdrawn'):
            raise AppError('철회된 원본입니다.', 409)
        context = self.stage_context(p)
        if context and not self.accessible(p, context):
            raise AppError('현재 상품 기준의 근거를 다시 확인하세요.', 409)
        eid = text(body, 'request_id', 100)
        notes = redact(optional(body, 'notes', 4000))
        existing = next((r for r in self.store.list(p, 'story_extraction') if r['request_id'] == eid), None)
        if existing:
            if existing['asset_id'] != asset['id'] or existing.get('notes', '') != notes:
                raise AppError('같은 요청 ID에 다른 자료를 사용할 수 없습니다.', 409)
            if existing['status'] == 'completed' and self.accessible(p, existing):
                return existing
            raise AppError('이 요청은 진행 중이거나 실패했습니다. 새 요청으로 재분석하세요.', 409)
        baseline = self.store.get(p, 'prd', context['prd_id']) if context and context.get('prd_id') else None
        if baseline and not self.accessible(p, baseline):
            raise AppError('참고 PRD의 근거를 다시 확인하세요.', 409)
        deps = dependency_map([asset] + ([context] if context else []) + ([baseline] if baseline else []))
        run = self.store.put(p, 'story_extraction', {'asset_id': asset['id'], 'asset_version': asset['version'],
            'request_id': eid, 'notes': notes, 'status': 'running', 'dependencies': deps, 'created_by': user['id'],
            'model': self.model.model, 'provider': getattr(self.model, 'provider', 'test'), 'prompt_version': 'story-extract-v1', 'baseline_refs': [ref(baseline)] if baseline else []})
        payload = {'planning_context': context, 'baseline_prd': baseline, 'input_kind': asset['input_type'], 'notes': notes}
        try:
            if asset['input_type'] == 'image':
                if not hasattr(self.model, 'generate_visual'):
                    raise AppError('이미지 해석에는 이미지 입력을 지원하는 Bedrock 설정이 필요합니다.', 503)
                image = {'format': 'jpeg', 'source': {'bytes': base64.b64decode(self.store.decrypt(asset['encrypted_image']))}}
                answer = validate('story_extract', self.model.generate_visual('story_extract', payload, image))
            else:
                payload['planning_text'] = self.store.decrypt(asset['encrypted_text'])
                answer = self.generate('story_extract', payload)
            result = extraction(answer)
            # No existing user_story records are touched by extraction or re-extraction.
            return self.store.write(p, updates=[('story_extraction', run['id'], {**result, 'status': 'completed'}, run['version'])],
                expected_epoch=epoch, checks=[('planning_asset', asset['id'], asset['version'])] +
                ([('product_context', context['id'], context['version'])] if context else []) +
                ([('prd', baseline['id'], baseline['version'])] if baseline else []))[0]
        except Exception as exc:
            self.store.update(p, 'story_extraction', run['id'], {'status': 'failed', 'error': '해석에 실패했습니다. 모델 설정·원본 상태를 확인하고 새 요청으로 재시도하세요.'}, run['version'])
            if isinstance(exc, AppError):
                raise
            raise AppError('스토리 해석을 완료하지 못했습니다.', 502) from None

    def story_fields(self, project, body, old=None):
        old = old or {}
        fields = {k: redact(optional(body, k, limit)) if k in body else old.get(k, '') for k, limit in FIELDS.items()}
        if not fields['title']:
            raise AppError('스토리 제목을 입력하세요.')
        for key, limit in (('epic', 200), ('journey', 200), ('feature', 80), ('validation_plan', 5000)):
            fields[key] = redact(optional(body, key, limit)) if key in body else old.get(key, '')
        if fields['feature'] and fields['feature'] not in self.features(project):
            raise AppError('등록된 기능을 선택하세요.')
        release = body.get('release', old.get('release', 'mvp'))
        if release not in ('mvp', 'later', 'excluded'):
            raise AppError('MVP·후속·제외 중 범위를 선택하세요.')
        fields['release'] = release
        rows = criteria(body.get('acceptance_criteria', old.get('acceptance_criteria', [])))
        fields['acceptance_criteria'] = [{k: redact(v) for k, v in row.items()} for row in rows]
        original_questions = {q['id']: q for q in old.get('questions', [])}
        raw = body.get('questions', old.get('questions', []))
        if not isinstance(raw, list) or len(raw) > 40:
            raise AppError('확인 질문은 최대 40개까지 입력하세요.')
        questions, seen = [], set()
        for q in raw:
            if not isinstance(q, dict):
                raise AppError('질문은 객체 형식이어야 합니다.')
            if q.get('id') is not None and (not isinstance(q['id'], str) or len(q['id']) > 80):
                raise AppError('질문 ID 형식을 확인하세요.')
            qid = q.get('id') or str(uuid.uuid4())
            if qid in seen or (q.get('id') and qid not in original_questions):
                raise AppError('확인 질문 ID를 확인하세요.')
            seen.add(qid)
            state = q.get('state', 'open')
            if state not in ('open', 'answered', 'excluded'):
                raise AppError('질문 상태를 확인하세요.')
            critical = original_questions.get(qid, {}).get('critical', q.get('critical', True))
            if type(critical) is not bool:
                raise AppError('질문의 중요도 형식을 확인하세요.')
            response = redact(optional(q, 'response', 3000))
            if state != 'open' and not response:
                raise AppError('확인 답변 또는 범위 제외 사유를 입력하세요.')
            questions.append({'id': qid, 'text': redact(text(q, 'text', 2000)), 'critical': critical, 'state': state, 'response': response})
        if not set(original_questions).issubset(seen):
            raise AppError('기존 질문은 삭제 대신 답변 또는 범위 제외 사유를 남기세요.')
        fields['questions'] = questions
        ids = strings(body.get('evidence_ids', old.get('evidence_ids', [])), 100, 80)
        selected = self.story_evidence(project, ids)
        fields['evidence_ids'] = ids
        persona_id = body.get('persona_id', old.get('persona_id', ''))
        if not isinstance(persona_id, str):
            raise AppError('페르소나를 확인하세요.')
        fields['persona_id'] = persona_id
        if persona_id:
            selected.append(self.get_persona(project, persona_id))
        context = self.stage_context(project)
        if context:
            if not self.accessible(project, context):
                raise AppError('현재 상품 기준의 근거를 확인하세요.', 409)
            selected.append(context)
        baselines = {}
        for source in old.get('source_refs', []):
            asset = self.store.get(project, 'planning_asset', source['asset_id'])
            if asset.get('withdrawn') or asset['version'] != source['asset_version']:
                raise AppError('기획 원본이 변경되거나 철회됐습니다.', 409)
            selected.append(asset)
            for baseline_ref in source.get('baseline_refs', []):
                baseline = self.store.get(project, 'prd', baseline_ref['id'])
                if baseline['version'] != baseline_ref['version'] or not self.accessible(project, baseline):
                    raise AppError('참고 PRD가 변경됐습니다. 최신 PRD로 다시 해석하세요.', 409)
                selected.append(baseline)
                baselines[baseline['id']] = ref(baseline)
        fields['baseline_refs'] = list(baselines.values())
        fields['dependencies'] = dependency_map(selected)
        fields['context_ref'] = ref(context) if context else None
        fields['validation_status'] = 'planned' if fields['validation_plan'] else 'unverified'
        return fields

    def assert_story_editable(self, project, story):
        # A changed product definition may be explicitly re-reviewed. Removing a
        # withdrawn evidence ID must never launder its derived text into a draft.
        guard = {'dependencies': [d for d in story.get('dependencies', []) if d['kind'] != 'product_context']}
        if not self.accessible(project, guard):
            raise AppError('원본 또는 근거가 변경됐습니다. 유효한 자료로 새 스토리를 작성하세요.', 409)

    def save_story(self, user, body):
        p, epoch = user['project_id'], self.store.epoch(user['project_id'])
        old = self.store.get(p, 'user_story', body['story_id']) if body.get('story_id') else None
        if old:
            self.assert_story_editable(p, old)
        if old and old.get('superseded_by'):
            raise AppError('분할·병합된 스토리는 이력으로 보존됩니다.', 409)
        fields = self.story_fields(p, body, old)
        origins = dict((old or {}).get('origins', {}))
        for key in FIELDS:
            if not old or fields[key] != old.get(key):
                previous = origins.get(key, {})
                origins[key] = {**previous, 'origin': 'po_edited', 'edited_by': user['id']}
        if not old or fields['acceptance_criteria'] != old.get('acceptance_criteria'):
            origins['acceptance_criteria'] = {'origin': 'po_edited', 'region_ids': [], 'edited_by': user['id']}
        fields.update(origins=origins, definition_status='draft', approval=None, updated_by=user['id'])
        if old:
            return self.store.write(p, updates=[('user_story', old['id'], fields, revision(body))], expected_epoch=epoch)[0]
        return self.store.write(p, inserts=[('user_story', {**fields, 'source_refs': [], 'created_by': user['id']}, None)], expected_epoch=epoch)[0]

    def apply_story_candidate(self, user, body):
        p, epoch = user['project_id'], self.store.epoch(user['project_id'])
        run = self.store.get(p, 'story_extraction', text(body, 'extraction_id', 80))
        index = body.get('candidate_index')
        if run['status'] != 'completed' or not self.accessible(p, run):
            raise AppError('현재 사용할 수 없는 해석 결과입니다.', 409)
        if type(index) is not int or not 0 <= index < len(run['candidates']):
            raise AppError('스토리 후보를 선택하세요.')
        candidate = run['candidates'][index]
        source_ref = {'asset_id': run['asset_id'], 'asset_version': run['asset_version'], 'extraction_id': run['id'], 'candidate_index': index, 'baseline_refs': run.get('baseline_refs', [])}
        old = self.store.get(p, 'user_story', body['story_id']) if body.get('story_id') else None
        if old:
            self.assert_story_editable(p, old)
        if old and old.get('superseded_by'):
            raise AppError('분할·병합된 스토리는 변경할 수 없습니다.', 409)
        if old:
            selected = strings(body.get('fields'), len(ORIGIN_FIELDS), 80)
            if not selected or any(k not in ORIGIN_FIELDS for k in selected):
                raise AppError('반영할 필드를 선택하세요.')
            updates = {k: candidate[k] for k in selected}
            source_refs = old.get('source_refs', []) + ([source_ref] if source_ref not in old.get('source_refs', []) else [])
            fields = self.story_fields(p, updates, {**old, 'source_refs': source_refs})
            origins = dict(old.get('origins', {}))
            for key in selected:
                origins[key] = {**candidate['origins'][key], 'extraction_id': run['id']}
            # Reanalysis adds questions; it cannot discard previously recorded questions.
            fields['questions'] += [{'id': str(uuid.uuid4()), **q, 'text': redact(q['text']), 'state': 'open', 'response': ''} for q in candidate['questions'] if q['text'] not in {x['text'] for x in fields['questions']}]
            if len(fields['questions']) > 40:
                raise AppError('기존 확인 질문을 정리한 뒤 다시 반영하세요.')
            fields.update(source_refs=source_refs, origins=origins, approval=None, definition_status='draft', updated_by=user['id'])
            return self.store.write(p, updates=[('user_story', old['id'], fields, revision(body))], expected_epoch=epoch)[0]
        fields = self.story_fields(p, {**candidate, 'questions': candidate['questions']}, {'source_refs': [source_ref]})
        fields.update(source_refs=[source_ref], origins={k: {**v, 'extraction_id': run['id']} for k, v in candidate['origins'].items()},
            definition_status='draft', approval=None, created_by=user['id'])
        rid = 'story-' + hashlib.sha256((p + ':' + run['id'] + ':' + str(index)).encode()).hexdigest()[:32]
        previous = next((r for r in self.store.list(p, 'user_story') if r['id'] == rid), None)
        if previous:
            return self.story_view(p, previous)
        return self.store.write(p, inserts=[('user_story', fields, rid)], expected_epoch=epoch)[0]

    def story_gate(self, project, story):
        missing = [k for k in ('actor', 'problem', 'goal', 'benefit', 'scenario') if not story.get(k, '').strip()]
        rows = story.get('acceptance_criteria', [])
        if not rows or any(not all(r.get(k, '').strip() for k in ('given', 'when', 'then')) for r in rows):
            missing.append('acceptance_criteria')
        if any(q['critical'] and q['state'] == 'open' for q in story.get('questions', [])):
            missing.append('open_questions')
        if not story.get('evidence_ids') and not (story.get('assumptions') and story.get('validation_plan')):
            missing.append('evidence_or_validation_plan')
        if not self.accessible(project, story):
            missing.append('changed_dependencies')
        if story.get('superseded_by'):
            missing.append('superseded')
        return missing

    def confirm_story(self, user, body):
        p, epoch = user['project_id'], self.store.epoch(user['project_id'])
        story = self.store.get(p, 'user_story', text(body, 'story_id', 80))
        expected = revision(body)
        if story['version'] != expected:
            raise AppError('스토리가 변경됐습니다. 최신 버전을 다시 검토하세요.', 409)
        if body.get('reviewed_source') is not True or body.get('reviewed_criteria') is not True:
            raise AppError('원본 의미와 수용 기준을 검토한 뒤 확정하세요.')
        missing = self.story_gate(p, story)
        if missing:
            raise AppError('확정 전 보완할 항목: ' + ', '.join(missing), 409)
        if story.get('definition_status') == 'confirmed':
            return story
        approval = {'by': user['id'], 'at': timestamp(), 'reviewed_version': expected, 'confirmed_version': expected+1,
                    'note': redact(optional(body, 'note', 3000))}
        return self.store.write(p, updates=[('user_story', story['id'], {'definition_status': 'confirmed', 'approval': approval}, expected)], expected_epoch=epoch)[0]

    def reorganize_stories(self, user, body):
        """Split/merge creates fresh drafts with retained ancestry; originals survive."""
        p, epoch = user['project_id'], self.store.epoch(user['project_id'])
        ids = strings(body.get('story_ids'), 10, 80)
        outputs = body.get('stories')
        if not ids or not isinstance(outputs, list) or not 1 <= len(outputs) <= 10 or (len(ids) == 1 and len(outputs) < 2) or (len(ids) > 1 and len(outputs) != 1):
            raise AppError('분할은 한 스토리에서 여러 개, 병합은 여러 스토리에서 한 개로 지정하세요.')
        originals = [self.store.get(p, 'user_story', i) for i in ids]
        versions = body.get('versions', {})
        if not isinstance(versions, dict) or any(not isinstance(row, dict) for row in outputs):
            raise AppError('스토리와 버전 객체를 확인하세요.')
        if any(r.get('superseded_by') or not self.accessible(p, r) or versions.get(r['id']) != r['version'] for r in originals):
            raise AppError('최신의 유효한 스토리를 선택하세요.', 409)
        source_refs = list({json.dumps(x, sort_keys=True): x for r in originals for x in r.get('source_refs', [])}.values())
        evidence_ids = sorted({e for r in originals for e in r.get('evidence_ids', [])})
        questions = list({q['id']: q for r in originals for q in r.get('questions', [])}.values())
        newids = [str(uuid.uuid4()) for _ in outputs]
        inserts = []
        for rid, row in zip(newids, outputs):
            fields = self.story_fields(p, {**row, 'evidence_ids': evidence_ids}, {'source_refs': source_refs, 'questions': questions})
            fields.update(source_refs=source_refs, origins={k: {'origin': 'po_edited', 'region_ids': []} for k in FIELDS},
                ancestry=[ref(r) for r in originals], definition_status='draft', approval=None, created_by=user['id'])
            inserts.append(('user_story', fields, rid))
        saved = self.store.write(p, inserts=inserts, updates=[('user_story', r['id'], {'superseded_by': newids, 'definition_status': 'superseded', 'approval': None}, r['version']) for r in originals], expected_epoch=epoch)
        return saved[:len(outputs)]

    def save_story_requirement(self, user, body):
        p, epoch = user['project_id'], self.store.epoch(user['project_id'])
        story = self.store.get(p, 'user_story', text(body, 'story_id', 80))
        if story.get('definition_status') != 'confirmed' or not self.accessible(p, story) or self.story_gate(p, story):
            raise AppError('현재 확정된 스토리에 요구사항을 연결하세요.', 409)
        if body.get('story_version') != story['version']:
            raise AppError('스토리 버전을 다시 확인하세요.', 409)
        priority = body.get('priority', 'should')
        if priority not in ('must', 'should', 'could', 'wont'):
            raise AppError('우선순위를 확인하세요.')
        fields = {k: redact(text(body, k, 5000)) for k in ('title', 'condition', 'behavior')}
        fields.update(story_id=story['id'], story_version=story['version'], priority=priority, dependencies=dependency_map([story]),
            definition_status='po_defined', created_by=user['id'])
        if body.get('requirement_id'):
            return self.store.write(p, updates=[('story_requirement', body['requirement_id'], fields, revision(body))], expected_epoch=epoch)[0]
        return self.store.write(p, inserts=[('story_requirement', fields, None)], expected_epoch=epoch)[0]

    def story_view(self, project, record):
        if not self.accessible(project, record):
            return self.redacted(record)
        return {**record, 'confirmation_missing': self.story_gate(project, record)}

    def stage_export(self, user, query):
        p, epoch = user['project_id'], self.store.epoch(user['project_id'])
        context = self.stage_context(p)
        if context and not self.accessible(p, context):
            raise AppError('현재 상품 기준의 근거를 다시 확인하세요.', 409)
        stories = [r for r in self.store.list(p, 'user_story') if r.get('definition_status') == 'confirmed' and not self.story_gate(p, r)]
        if not stories:
            raise AppError('현재 유효한 확정 스토리가 없습니다.', 409)
        ids = {s['id'] for s in stories}
        reqs = [r for r in self.store.list(p, 'story_requirement') if r['story_id'] in ids and self.accessible(p, r)]
        evidence_ids = {d['id'] for s in stories for d in s.get('dependencies', []) if d['kind'] in ('insight', 'voc')} | set((context or {}).get('evidence_ids', []))
        package = {'schema_version': 'npd.story-package.v1', 'exported_at': timestamp(), 'product_context': context,
            'stories': stories, 'requirements': reqs, 'evidence': self.story_evidence(p, sorted(evidence_ids)),
            'disclosure': 'PO가 확정한 설계안입니다. 실제 고객 검증·구현 완료와 다릅니다. AXIOM 수신 규격은 별도 확인해야 합니다.'}
        if query.get('format') != 'markdown':
            self.store.assert_epoch(p, epoch)
            return package
        lines = ['# 사용자 스토리·요구사항 패키지', package['disclosure']]
        if context:
            lines += ['## 제품 기준 · v' + str(context['version'])]
            lines += ['### '+k+'\n'+context.get(k, '') for k in CONTEXT_FIELDS if context.get(k)]
        for story in stories:
            lines += ['## '+story['title'], 'STORY-ID: '+story['id']+' · v'+str(story['version']),
                      '고객 검증 상태: '+story['validation_status']]
            lines += ['### '+k+'\n'+story[k] for k in FIELDS if k != 'title' and story.get(k)]
            lines += ['검증 계획: '+story.get('validation_plan', ''), '범위: '+story['release']+' · 여정: '+story.get('journey', '')]
            for row in story['acceptance_criteria']:
                lines.append('- Given '+row['given']+' / When '+row['when']+' / Then '+row['then'])
            for q in story['questions']:
                lines.append('- 확인 질문: '+q['text']+' · '+q['state']+' · '+q['response'])
            lines += ['근거: '+' '.join('['+i+']' for i in story['evidence_ids']), 'PO 확정: '+json.dumps(story['approval'], ensure_ascii=False)]
            for r in story['source_refs']:
                lines.append('기획 원본 ID: '+r['asset_id']+' · v'+str(r['asset_version'])+' · 해석 ID: '+r['extraction_id'])
        for r in reqs:
            lines += ['## REQ '+r['id']+' · v'+str(r['version']), 'STORY '+r['story_id']+' · v'+str(r['story_version']),
                      r['title']+' · '+r['priority'], 'WHEN '+r['condition']+' THE SYSTEM SHALL '+r['behavior']]
        for e in package['evidence']:
            lines += ['## ['+e['id']+'] · v'+str(e['version'])+' · '+e['evidence_type'], e['text']]
            lines += [k+': '+str(e[k]) for k in ('competitor', 'observed_at', 'occurred_at', 'applicability', 'limitations', 'public_url', 'source_name', 'source_url') if e.get(k)]
        self.store.assert_epoch(p, epoch)
        return {'format': 'markdown', 'text': '\n\n'.join(lines)}

    def create_story_prd(self, user, body):
        p, epoch = user['project_id'], self.store.epoch(user['project_id'])
        package = self.stage_export(user, {})
        versions = {r['id']: r['version'] for r in package['stories']}
        requirement_versions = {r['id']: r['version'] for r in package['requirements']}
        if body.get('story_versions') != versions or body.get('requirement_versions') != requirement_versions or body.get('context_version') != (package['product_context'] or {}).get('version'):
            raise AppError('검토 대상이 변경됐습니다. 패키지를 다시 확인하세요.', 409)
        markdown = self.stage_export(user, {'format': 'markdown'})['text']
        # A new PRD snapshot preserves existing PRDs; source revisions remain guarded.
        sections = self.prd_sections([{'id': 'story-package-' + str(i//18000+1), 'title': 'PO 확정 스토리·요구사항 ' + str(i//18000+1), 'text': markdown[i:i+18000]} for i in range(0, len(markdown), 18000)])
        deps = dependency_map(package['stories'] + package['requirements'] + ([package['product_context']] if package['product_context'] else []))
        return self.store.write(p, inserts=[('prd', {'title': text(body, 'title', 200), 'sections': sections,
            'created_by': user['id'], 'dependencies': deps, 'story_versions': versions,
            'requirement_versions': requirement_versions, 'source_schema': package['schema_version']}, None)], expected_epoch=epoch,
            checks=[(r['kind'], r['id'], r['version']) for r in package['requirements']])[0]

    def stage_get(self, user, path, query):
        p = user['project_id']
        if path == '/api/stage2':
            context = self.stage_context(p)
            return {'context': context if not context or self.accessible(p, context) else self.redacted(context),
                'stories': [self.story_view(p, r) for r in self.store.list(p, 'user_story')],
                'assets': [self.planning_asset_view(r) for r in self.store.list(p, 'planning_asset')],
                'extractions': [r if self.accessible(p, r) else self.redacted(r) for r in self.store.list(p, 'story_extraction')],
                'requirements': [r if self.accessible(p, r) else self.redacted(r) for r in self.store.list(p, 'story_requirement')]}
        if path.startswith('/api/planning-assets/content/'):
            return self.planning_asset_content(user, path.rsplit('/', 1)[-1])
        if path.startswith('/api/stories/versions/'):
            return [self.story_view(p, r) for r in self.store.history(p, 'user_story', path.rsplit('/', 1)[-1])]
        if path == '/api/stage2/export':
            return self.stage_export(user, query)
        raise AppError('경로를 찾을 수 없습니다.', 404)
