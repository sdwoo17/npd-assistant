"""Project planning uploads, separate from owner-only proprietary research."""
import base64
import hashlib
import io
import json
import os
import uuid
from pathlib import Path
from .contracts import text, optional, revision, strings
from .ingest import decode_file
from .story_contracts import IMAGE_LIMIT, IMAGE_PIXELS, IMAGE_EDGE, objects
from .store import AppError
from .planning_regions import PlanningRegions, image_view, validated_regions


def decode_image(body):
    try:
        from PIL import Image, ImageOps
        raw = base64.b64decode(body.get('content_base64', ''), validate=True)
        if not 0 < len(raw) <= IMAGE_LIMIT:
            raise AppError('기획 이미지는 3MB 이하이어야 합니다.')
        with Image.open(io.BytesIO(raw)) as probe:
            if probe.format not in ('PNG', 'JPEG') or getattr(probe, 'n_frames', 1) != 1:
                raise AppError('정지 PNG/JPEG 이미지를 올리세요.')
            width, height = probe.size
            if width * height > IMAGE_PIXELS or max(width, height) > IMAGE_EDGE:
                raise AppError('이미지는 1,600만 화소·한 변 8,000픽셀 이하이어야 합니다.')
        with Image.open(io.BytesIO(raw)) as check:
            check.verify()
        with Image.open(io.BytesIO(raw)) as picture:
            orientation = picture.getexif().get(274, 1)
            preview = ImageOps.exif_transpose(picture).convert('RGB')
            output = io.BytesIO()
            preview.save(output, format='JPEG', quality=90)
            if output.tell() > IMAGE_LIMIT:
                raise AppError('회전 보정 이미지가 3MB를 초과합니다. 해상도를 줄여 주세요.')
            return raw, output.getvalue(), {'original_width': width, 'original_height': height,
                'width': preview.width, 'height': preview.height, 'exif_orientation': orientation,
                'format': 'jpeg', 'coordinate_space': 'oriented_preview_normalized',
                'transform': 'EXIF orientation applied; RGB JPEG preview; original preserved'}
    except AppError:
        raise
    except ImportError:
        raise AppError('서버에 Pillow 이미지 처리 패키지를 설치하세요.', 503)
    except Exception:
        raise AppError('손상되었거나 지원하지 않는 이미지입니다. PNG/JPEG로 다시 저장하세요.')


class PlanningAssets(PlanningRegions):
    def planning_actor(self, user):
        # Long model operations must not persist after session/project access changes.
        if not user.get('csrf'):
            return  # Domain tests use explicit synthetic principals without HTTP sessions.
        with self.store.db() as db:
            row = db.execute('SELECT 1 FROM sessions s JOIN memberships m ON m.user_id=s.user_id AND m.project_id=s.project_id '
                'WHERE s.user_id=? AND s.project_id=? AND s.csrf=? AND m.role=? AND s.expires>strftime(\'%s\',\'now\') '
                'AND NOT EXISTS (SELECT 1 FROM account_state a WHERE a.user_id=s.user_id AND a.disabled=1)',
                (user['id'], user['project_id'], user['csrf'], user['role'])).fetchone()
        if not row:
            raise AppError('세션 또는 프로젝트 권한이 변경됐습니다. 다시 로그인하세요.', 409)

    def asset(self, user, rid, version=None):
        row = self.store.get(user['project_id'], 'planning_asset', rid)
        if row.get('withdrawn') or (version is not None and row['version'] != version):
            raise AppError('기획 원본의 버전 또는 공개 범위가 변경됐습니다. 다시 검토하세요.', 409)
        return row

    def asset_metadata(self, row):
        return {k: v for k, v in row.items() if not k.startswith('encrypted_') and k != 'storage'}

    def planning_assets(self, user):
        return [self.asset_metadata(r) for r in self.store.list(user['project_id'], 'planning_asset') if not r.get('withdrawn')]

    def archive_planning_blob(self, user, rid, encrypted):
        bucket = os.getenv('NPD_PLANNING_S3_BUCKET', '')
        if not bucket:
            return {'provider': 'encrypted_sqlite'}
        import boto3
        key = 'npd-planning/' + hashlib.sha256(user['project_id'].encode()).hexdigest() + '/' + rid + '/' + str(uuid.uuid4())
        try:
            boto3.client('s3').put_object(Bucket=bucket, Key=key, Body=encrypted.encode(),
                ContentType='application/octet-stream', ServerSideEncryption='AES256')
        except Exception:
            raise AppError('설정된 S3에 암호화 원본을 저장하지 못했습니다. 버킷 권한과 리전을 확인하세요.', 503)
        return {'provider': 's3', 'bucket': bucket, 'key': key}

    def upload_planning_asset(self, user, body):
        p = user['project_id']
        epoch = self.store.epoch(p)
        filename = text(body, 'filename', 180)
        suffix = Path(filename).suffix.lower()
        if suffix in ('.png', '.jpg', '.jpeg'):
            raw, preview, image_info = decode_image(body)
            content, media = '', 'image'
        else:
            filename, content, _ = decode_file(body)
            raw = base64.b64decode(body['content_base64'], validate=True)
            preview, image_info, media = b'', {}, 'document'
        digest = hashlib.sha256(raw).hexdigest()
        purpose = body.get('purpose', 'story_sketch')
        if purpose not in ('story_sketch', 'existing_service', 'actual_fgi', 'internal_voc'):
            raise AppError('기획 자료의 용도를 확인하세요.')
        old = self.asset(user, body['asset_id']) if body.get('asset_id') else None
        if old and purpose != old['purpose']:
            raise AppError('원본의 용도를 변경하려면 별도 자료로 업로드하세요.')
        duplicate = next((r for r in self.store.list(p, 'planning_asset') if r['hash'] == digest and r['purpose'] == purpose and not r.get('withdrawn')), None)
        if duplicate and not old:
            return {**self.asset_metadata(duplicate), 'duplicate': True, 'analysis_started': False}
        rid = old['id'] if old else str(uuid.uuid4())
        encrypted = self.store.encrypt(base64.b64encode(raw).decode())
        self.planning_actor(user)
        expected = revision(body) if old else None
        if old and old['version'] != expected:
            raise AppError('기획 원본 버전이 변경됐습니다.', 409)
        title = text(body, 'title', 200)
        storage = self.archive_planning_blob(user, rid, encrypted)
        fields = {'title': title, 'filename': Path(filename).name, 'hash': digest, 'media_type': media,
            'purpose': purpose, 'uploaded_by': user['id'], 'withdrawn': False, 'storage_backend': storage['provider'], 'storage': storage,
            'encrypted_file': encrypted, 'encrypted_text': self.store.encrypt(content),
            'encrypted_preview': self.store.encrypt(base64.b64encode(preview).decode()), 'image': image_info,
            'sharing': 'project_private', 'customer_validation': 'unverified', 'dependencies': []}
        try:
            self.planning_actor(user)
            if old:
                saved = self.store.write(p, updates=[('planning_asset', rid, fields, expected)], expected_epoch=epoch)[0]
            else:
                saved = self.store.write(p, inserts=[('planning_asset', fields, rid)], expected_epoch=epoch)[0]
        except Exception:
            if storage['provider']=='s3':
                try:
                    import boto3
                    boto3.client('s3').delete_object(Bucket=storage['bucket'],Key=storage['key'])
                except Exception:
                    # Only this upload's randomly generated key is eligible for cleanup.
                    self.audit(user,'planning_archive_cleanup_required',rid)
            raise
        return {**self.asset_metadata(saved), 'duplicate': False, 'analysis_started': False}

    def planning_asset_raw(self, user, rid, version=None):
        row = self.asset(user, rid)
        if version is not None:
            try:
                number = int(version)
            except (ValueError, TypeError):
                raise AppError('원본 버전을 확인하세요.')
            row = next((r for r in self.store.history(user['project_id'], 'planning_asset', rid) if r['version'] == number), None)
            if not row:
                raise AppError('기획 원본 버전을 찾을 수 없습니다.', 404)
        self.audit(user, 'planning_asset_viewed', rid)
        result = {'id': rid, 'version': row['version'], 'media_type': row['media_type'], 'image': row['image']}
        if row['media_type'] == 'image':
            result.update(content_base64=self.store.decrypt(row['encrypted_preview']), mime='image/jpeg')
        else:
            result['text'] = self.store.decrypt(row['encrypted_text'])
        return result

    def withdraw_planning_asset(self, user, body):
        row = self.asset(user, text(body, 'asset_id', 80))
        self.planning_actor(user)
        return self.asset_metadata(self.store.update(user['project_id'], 'planning_asset', row['id'], {'withdrawn': True}, revision(body)))

    def extract_planning_asset(self, user, body):
        p = user['project_id']
        epoch = self.store.epoch(p)
        asset = self.asset(user, text(body, 'asset_id', 80), revision(body))
        prompt = text(body, 'prompt', 5000)
        view = None
        if asset['media_type'] == 'image':
            image_bytes, view = image_view(base64.b64decode(self.store.decrypt(asset['encrypted_preview'])), body)
        elif 'crop' in body or body.get('rotation', 0):
            raise AppError('영역 선택과 회전은 이미지에서 사용할 수 있습니다.')
        run = self.store.put(p, 'extraction_run', {'asset_id': asset['id'], 'asset_version': asset['version'],
            'status': 'running', 'prompt': prompt, 'prompt_version': 'planning-image-v2', 'model': self.model.model if asset['media_type']=='image' else None,
            'created_by': user['id'], 'dependencies': [], 'error': '', 'view': view})
        try:
            if asset['media_type'] == 'image':
                result = self.generate('planning_image', {'prompt': prompt, 'asset_id': asset['id'],
                    'image': view, 'original_image': asset['image'], 'source_is': 'untrusted planning intention; not customer evidence'},
                    images=[{'format': 'jpeg', 'bytes': image_bytes}])
            else:
                content = self.store.decrypt(asset['encrypted_text'])
                result = {'transcript': content, 'quality_issues': [], 'regions': [{'id':'document', 'bbox':[0,0,1,1],
                    'text':content, 'kind':'text'}], 'relations':[], 'questions':[]}
            transcript = optional(result, 'transcript', 150000)
            regions, relations = validated_regions(result['regions'], result['relations'], view)
            if not transcript.strip() and not regions:
                raise AppError('읽을 수 있는 내용이 없습니다. 사진 또는 텍스트를 보충하세요.', 422)
            self.planning_actor(user)
            saved = self.store.write(p, updates=[('extraction_run', run['id'], {'status':'completed',
                'transcript':transcript,'regions':regions,'relations':relations,'quality_issues':strings(result['quality_issues'],30),
                'questions':objects(result['questions'],40,'확인 질문'), 'review_status':'source_check_required'}, run['version'])],
                expected_epoch=epoch, checks=[('planning_asset',asset['id'],asset['version'])])[0]
            return saved
        except Exception as error:
            self.store.update(p, 'extraction_run', run['id'], {'status':'failed','error':'원본 분석 실패. 모델·이미지 상태·자료 버전을 확인하고 다시 분석하세요.'})
            if isinstance(error, AppError):
                raise
            raise AppError('원본 분석을 완료하지 못했습니다. 재시도할 수 있습니다.', 502)

    def extraction(self, user, rid):
        row = self.store.get(user['project_id'], 'extraction_run', rid)
        self.asset(user, row['asset_id'], row['asset_version'])
        return row


    def planning_extractions(self, user):
        assets={a['id']:a for a in self.planning_assets(user)}
        return [r for r in self.store.list(user['project_id'],'extraction_run')
            if r['asset_id'] in assets and assets[r['asset_id']]['version']==r['asset_version']]
