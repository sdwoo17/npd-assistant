"""Ephemeral HTTP fixture for DOM tests; never used by manage.py."""
import signal
from app.server import Server
from app.assets import register_templates
from tests.helpers import Fixture, encoded


def main():
    fixture = Fixture()
    from tests.test_research_flow import FlowModel
    fixture.service.model = FlowModel()
    fixture.store.create_user("owner@example.test", "Owner-test-pass!", "owner", "project-a")
    fixture.store.create_user("po@example.test", "Planner-test-pass!", "po", "project-a")
    fixture.persona()
    fixture.persona("대행사운영자")
    fixture.conversation()
    fixture.service.post(fixture.owner, "/api/voc/upload", encoded(
        "synthetic.csv",
        "external_id,text,evidence_type\n1,소재 리포트 분석이 어렵다.,synthetic\n",
        source_name="DOM test synthetic fixture"))
    draft = fixture.service.insight_save(fixture.owner, {'source_id': fixture.source['id'], 'title': '템플릿 검토 근거',
        'text': '합성 소재 리포트에는 비교 조건을 표시한다.', 'feature': 'reporting', 'evidence_type': 'synthetic'})
    voc = fixture.service.voc_records('project-a')[0]
    register_templates(fixture.service, fixture.owner, {'UI': {'name': '검토프로필', 'segment': '합성 광고주',
        'goals': '소재 비교', 'constraints': '시간 부족', 'assumptions': ['테스트 합성 가정'], 'insights': ['I'], 'voc_refs': ['V']}},
        {'pack_id': 'ui-synthetic', 'content_digest': 'ui-synthetic-digest', 'insight_ids': {'I': draft['id']},
         'voc_ids': {'V': voc['id']}, 'persona_ids': {}, 'interview_conversation_id': 'unused-test-conversation'})
    server = Server(("127.0.0.1", 0), fixture.service)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    print(server.origin, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        fixture.close()


if __name__ == "__main__":
    main()
