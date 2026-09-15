"""Explicit app-review sources; general uploads remain in the VoC intake."""
import base64
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlencode
from .contracts import text, filters, date_value
from .ingest import redact, classify_all
from .research import owner
from .store import AppError, timestamp


class ReviewSources:
    def collect_android_reviews(self,user,body):
        owner(user)
        package=text(body,'package_name',180)
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+',package):raise AppError('Android 패키지 이름을 확인하세요.')
        token=os.getenv('ANDROID_PUBLISHER_TOKEN','')
        if not token:raise AppError('서버에 관리 앱용 Google Play API 인증을 설정하세요.',503)
        pages=body.get('max_pages',3)
        if type(pages)is not int or not 1<=pages<=20:raise AppError('수집 페이지는 1~20으로 지정하세요.')
        scope=filters(body.get('filters',{}));p=user['project_id'];page_token='';seen=set();imported=duplicates=0
        # Only Google's documented endpoint is reachable. Tokens are never returned or logged.
        from .voc import source_key, safe_external
        for page in range(1,pages+1):
            query={'maxResults':100}
            if page_token:query['token']=page_token
            try:
                result=self.fetch_review_page('https://androidpublisher.googleapis.com/androidpublisher/v3/applications/'+package+'/reviews?'+urlencode(query),token)
                rows=result.get('reviews',[])
                if not isinstance(rows,list):raise ValueError()
                normalized=[]
                for review in rows:
                    comment=next((c['userComment'] for c in review.get('comments',[]) if 'userComment'in c),None)
                    if not comment:continue
                    content=redact(text(comment,'text',10000));external=text(review,'reviewId',200)
                    occurred=date_value(datetime.fromtimestamp(int(comment['lastModified']['seconds']),timezone.utc).isoformat())
                    if scope['date_from'] and occurred[:10]<scope['date_from'] or scope['date_to'] and occurred[:10]>scope['date_to']:continue
                    rating=comment.get('starRating')
                    if type(rating)is not int or not 1<=rating<=5:raise ValueError()
                    feature_ids=classify_all(content,self.features(p))
                    normalized.append((external,{'external_id':safe_external(external),'text':content,'feature':feature_ids[0],
                        'feature_ids':feature_ids,'service_id':self.features(p)[feature_ids[0]]['service_id'],
                        'segment':'앱 사용자 · 광고주 여부 미확인','occurred_at':occurred,'collected_at':timestamp(),
                        'source_type':'android_publisher','source_name':'Google Play:'+package,'source_url':'https://play.google.com/store/apps/details?id='+package,
                        'evidence_type':'real','rating':rating,'classification_source':'rules','problem':'','need':''}))
                self.planning_actor(user)
                for external,row in normalized:
                    saved=self.store.voc(p,source_key(row['source_name'],external),row)
                    imported+=bool(saved);duplicates+=not bool(saved)
                page_token=result.get('tokenPagination',{}).get('nextPageToken','')
                if not page_token:break
                if not isinstance(page_token,str) or len(page_token)>4000 or page_token in seen:raise ValueError()
                seen.add(page_token)
            except Exception as exc:
                if isinstance(exc,AppError):raise
                raise AppError('Google Play 리뷰 수집 실패. 관리 앱 권한·토큰 만료·응답 형식을 확인하세요. 이미 수집한 리뷰는 중복 없이 보존됩니다.',502)
        return {'imported':imported,'duplicates':duplicates,'pages':page,'has_more':bool(page_token),
            'scope':'관리 권한이 있는 Google Play 앱의 제공 리뷰 범위. 전체 고객 표본을 보장하지 않습니다.'}

    def import_s3_reviews(self,user,body):
        owner(user)
        bucket=os.getenv('NPD_REVIEW_S3_BUCKET','');base=os.getenv('NPD_REVIEW_S3_PREFIX','reviews').strip('/')
        if not bucket:raise AppError('서버에 앱 리뷰 S3 저장소를 설정하세요.',503)
        key=text(body,'object_key',500)
        prefix=base+'/'+user['project_id']+'/'
        if not key.startswith(prefix) or not key.endswith('.csv') or any(part in ('.','..','') for part in key.split('/')):
            raise AppError('현재 프로젝트의 앱 리뷰 CSV 경로를 선택하세요.',403)
        stream=None
        try:
            import boto3
            from botocore.config import Config
            result=boto3.client('s3',config=Config(connect_timeout=10,read_timeout=20,retries={'max_attempts':1})).get_object(Bucket=bucket,Key=key)
            stream=result['Body']
            if result['ContentLength']>3*1024*1024:raise AppError('리뷰 CSV는 3MB 이하이어야 합니다.')
            data=stream.read(3*1024*1024+1)
            if len(data)>3*1024*1024:raise AppError('리뷰 CSV는 3MB 이하이어야 합니다.')
            self.planning_actor(user)
            return self.voc_upload(user,{'filename':'app-reviews.csv','content_base64':base64.b64encode(data).decode(),
                'source_name':'S3 app reviews','source_type':'s3_import'})
        except AppError:raise
        except Exception:raise AppError('앱 리뷰 S3 가져오기에 실패했습니다. 저장소 권한과 CSV 형식을 확인하세요.',502)
        finally:
            if stream:stream.close()
