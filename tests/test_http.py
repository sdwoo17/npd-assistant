import json
import threading
import time
import unittest
from http.client import HTTPConnection
from app.server import Server
from tests.helpers import Fixture, CANARY


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.f.store.create_user("owner@example.test", "Owner-test-pass!", "owner", "project-a")
        self.f.store.create_user("po@example.test", "Planner-test-pass!", "po", "project-a")
        self.server = Server(("127.0.0.1", 0), self.f.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookie, self.csrf = "", ""

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.f.close()

    def request(self, method, path, body=None, headers=None):
        conn = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        hdr = {"Content-Type": "application/json", "Cookie": self.cookie, "X-CSRF-Token": self.csrf, **(headers or {})}
        conn.request(method, path, None if body is None else json.dumps(body), hdr)
        response = conn.getresponse()
        raw = response.read()
        result = json.loads(raw) if response.getheader("Content-Type").startswith("application/json") else raw.decode()
        status, rh = response.status, dict(response.getheaders())
        conn.close()
        return status, result, rh

    def login(self, role="po"):
        status, body, headers = self.request("POST", "/api/login", {"email": role+"@example.test", "password": "Planner-test-pass!" if role=="po" else "Owner-test-pass!"})
        self.assertEqual(status, 200)
        self.cookie = headers["Set-Cookie"].split(";")[0]
        self.csrf = body["csrf"]
        return headers

    def test_unauthenticated_api_and_static_headers(self):
        self.assertEqual(self.request("GET", "/api/bootstrap")[0], 401)
        status, html, headers = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn('id="chat-form"', html)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(self.request("GET", "/runtime/private.key")[0], 401)

    def test_cookie_csrf_and_cross_origin_controls(self):
        headers = self.login()
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        self.assertEqual(self.request("POST", "/api/conversations", {"title": "분석"}, {"X-CSRF-Token": ""})[0], 403)
        self.assertEqual(self.request("POST", "/api/conversations", {"title": "분석"}, {"Origin": "https://other.example"})[0], 403)
        self.assertEqual(self.request("POST", "/api/conversations", {"title": "분석"})[0], 201)

    def test_role_in_body_does_not_override_authenticated_role(self):
        self.login()
        status, _, _ = self.request("POST", "/api/research/upload", {"role": "owner", "project_id": "project-b"})
        self.assertEqual(status, 403)
        self.assertEqual(self.request("GET", "/api/research/raw/"+self.f.source["id"])[0], 403)

    def test_http_chat_proposal_export_flow(self):
        self.login()
        _, conv, _ = self.request("POST", "/api/conversations", {"title": "소재 분석"})
        status, chat, _ = self.request("POST", "/api/chat", {"conversation_id": conv["id"], "message": "소재 리포트 개선"})
        self.assertEqual(status, 201)
        self.assertEqual(len(chat["messages"]), 2)
        self.assertIn("자동 테스트", chat["messages"][-1]["text"])
        _, proposal, _ = self.request("POST", "/api/proposals", {"conversation_id": conv["id"]})
        self.assertEqual(self.request("POST", "/api/proposals/decision", {"proposal_id": proposal["id"], "state": "accepted"})[0], 201)
        status, export, _ = self.request("GET", "/api/export/"+conv["id"])
        self.assertEqual(status, 200)
        self.assertNotIn(CANARY, json.dumps(export))
        self.assertEqual(export["proposals"][0]["state"], "accepted")

    def test_logout_and_expired_session(self):
        self.login()
        self.assertEqual(self.request("POST", "/api/logout", {})[0], 200)
        self.assertEqual(self.request("GET", "/api/bootstrap")[0], 401)
        self.login()
        with self.f.store.db() as db:
            db.execute("UPDATE sessions SET expires=?", (time.time()-1,))
        self.assertEqual(self.request("GET", "/api/bootstrap")[0], 401)

    def test_host_and_path_traversal_rejected(self):
        self.assertEqual(self.request("GET", "/api/health", headers={"Host": "attacker.example"})[0], 403)
        self.login()
        self.assertEqual(self.request("GET", "/../app/store.py")[0], 404)

    def test_login_rate_limit(self):
        for _ in range(10):
            self.assertEqual(self.request("POST", "/api/login", {"email": "po@example.test", "password": "wrong"})[0], 401)
        self.assertEqual(self.request("POST", "/api/login", {"email": "po@example.test", "password": "wrong"})[0], 429)

    def test_projects_switch_scope_rotates_csrf_and_preserves_roles(self):
        self.login()
        old_csrf = self.csrf
        status, new, _ = self.request('POST', '/api/projects', {'title': 'PO 개인 기획'})
        self.assertEqual(status, 201)
        status, switched, _ = self.request('POST', '/api/projects/switch', {'project_id': new['id']})
        self.assertEqual(status, 201)
        self.assertNotEqual(switched['csrf'], old_csrf)
        self.assertEqual(self.request('POST', '/api/conversations', {'title': 'stale csrf'})[0], 403)
        self.csrf = switched['csrf']
        boot = self.request('GET', '/api/bootstrap')[1]
        self.assertEqual(boot['user']['role'], 'owner')
        self.assertEqual(self.request('GET', '/api/evidence')[1], [])
        self.assertEqual(self.request('GET', '/api/research/raw/' + self.f.source['id'])[0], 404)
        self.assertEqual(self.request('POST', '/api/projects/switch', {'project_id': 'project-b'})[0], 403)
        _, back, _ = self.request('POST', '/api/projects/switch', {'project_id': 'project-a'})
        self.csrf = back['csrf']
        self.assertEqual(self.request('GET', '/api/bootstrap')[1]['user']['role'], 'po')
        self.assertEqual(self.request('GET', '/api/research')[0], 403)

    def test_malformed_input_matrix_always_returns_safe_client_error(self):
        self.login()
        cases = [('/api/chat', {'conversation_id': []}), ('/api/voc/analysis', {'filters': []}),
                 ('/api/conversations', {'title': {}}), ('/api/projects', {'title': '<x>' * 100}),
                 ('/api/personas', {'name': 'x', 'segment': []}), ('/api/search', {'query': '소재', 'filters': {'date_from': '2025-02-29'}})]
        for path, body in cases:
            with self.subTest(path=path):
                status, result, _ = self.request('POST', path, body)
                self.assertTrue(400 <= status < 500, (status, result))
                self.assertNotIn(CANARY, json.dumps(result))

    def test_multi_project_foreign_identifiers_across_read_and_write_routes(self):
        self.login()
        other_conv = self.f.service.create_conversation(self.f.other, {'title': 'foreign secret'})
        for path in ('/api/conversations/', '/api/export/'):
            self.assertEqual(self.request('GET', path + other_conv['id'])[0], 404)
        self.assertEqual(self.request('GET', '/api/prds/versions/' + other_conv['prd_id'])[0], 404)
        for path, body in [('/api/chat', {'conversation_id': other_conv['id'], 'message': '소재'}),
                           ('/api/proposals', {'conversation_id': other_conv['id']}),
                           ('/api/conversations/state', {'conversation_id': other_conv['id'], 'expected_version': 1, 'mode': 'research'})]:
            self.assertEqual(self.request('POST', path, body)[0], 404)
