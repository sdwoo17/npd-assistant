import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from app.model import Model
from app.store import AppError


class ModelTransportTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fixture.requests.append({"path": self.path, "authorization": self.headers.get("Authorization"), "body": body})
                if self.path == "/redirect":
                    self.send_response(302); self.send_header("Location", "http://127.0.0.1:1/never"); self.end_headers(); return
                data = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps({"text": "[mock HTTP response]", "evidence_ids": ["e1"], "assumptions": [], "observations": []})}]}]}
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(json.dumps(data).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def test_responses_http_contract_and_stateless_request(self):
        model = Model(key="test-placeholder", model="test-model", endpoint="http://127.0.0.1:"+str(self.server.server_port)+"/v1/responses")
        result = model.generate("chat", {"question": "리포트", "evidence": [{"id": "e1", "text": "근거"}]})
        self.assertEqual(result["evidence_ids"], ["e1"])
        request = self.requests[0]
        self.assertFalse(request["body"]["store"])
        self.assertEqual(request["body"]["text"]["format"]["type"], "json_schema")
        self.assertEqual(request["authorization"], "Bearer test-placeholder")
        self.assertEqual(request["path"], "/v1/responses")

    def test_redirect_does_not_forward_credentials(self):
        model = Model(key="test-placeholder", model="test-model", endpoint="http://127.0.0.1:"+str(self.server.server_port)+"/redirect")
        with self.assertRaises(AppError):
            model.generate("chat", {"question": "x"})
        self.assertEqual(len(self.requests), 1)
