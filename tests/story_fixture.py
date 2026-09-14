"""Synthetic planning interpretation for tests ONLY; not handwriting accuracy evidence."""
from tests.helpers import RecordingModel


def story_result():
    fields = {'title': '성과 비교 판단', 'actor': '광고 운영자', 'problem': '표본이 적으면 비교하기 어렵다',
        'goal': '비교 가능 여부를 확인한다', 'benefit': '잘못된 광고 중단을 줄인다', 'scenario': '리포트에서 소재를 선택한다',
        'exceptions': '표본 부족이면 판단 보류', 'assumptions': '합성 기획 가설'}
    return {'transcript': '표본 부족이면 자동 중단하지 않는다', 'regions': [
        {'id': 'r1', 'text': '자동 중단하지 않는다', 'x': 0.1, 'y': 0.1, 'width': 0.8, 'height': 0.4, 'uncertain': True}],
        'candidates': [{**fields, 'acceptance_criteria': [{'given': '표본이 부족하다', 'when': '소재를 비교한다', 'then': '판단 보류를 표시한다'}],
            'questions': [{'text': '표본 부족 기준은 무엇인가?', 'critical': True}],
            'origins': [{'field': k, 'origin': 'from_source' if k == 'exceptions' else 'ai_proposed',
                        'region_ids': ['r1'] if k == 'exceptions' else []} for k in (*fields, 'acceptance_criteria')]}], 'warnings': ['합성 테스트 응답']}


class StoryModel(RecordingModel):
    provider = 'test-double'

    def generate(self, task, payload):
        if task != 'story_extract':
            return super().generate(task, payload)
        self.calls.append((task, payload))
        if self.hook:
            self.hook()
        return story_result()

    def generate_visual(self, task, payload, image):
        self.last_image = image
        return self.generate(task, payload)
