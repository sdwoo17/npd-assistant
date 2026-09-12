"""Ephemeral HTTP fixture for DOM tests; never used by manage.py."""
import signal
from app.server import Server
from tests.helpers import Fixture, encoded


def main():
    fixture = Fixture()
    fixture.store.create_user("owner@example.test", "Owner-test-pass!", "owner", "project-a")
    fixture.store.create_user("po@example.test", "Planner-test-pass!", "po", "project-a")
    fixture.persona()
    fixture.persona("대행사운영자")
    fixture.conversation()
    fixture.service.post(fixture.owner, "/api/voc/upload", encoded(
        "synthetic.csv",
        "external_id,text,evidence_type\n1,소재 리포트 분석이 어렵다.,synthetic\n",
        source_name="DOM test synthetic fixture"))
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
