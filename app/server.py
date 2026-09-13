import hmac
import json
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from .store import AppError

ROOT = Path(__file__).resolve().parents[1]


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, service, origin=None):
        self.service = service
        super().__init__(address, Handler)
        self.origin = origin or f"http://127.0.0.1:{self.server_port}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # Do not log uploaded text, credentials or conversation contents.

    def send(self, code, data, mime="application/json; charset=utf-8", cookie=None):
        raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(raw)

    def context(self):
        cookies = SimpleCookie()
        cookies.load(self.headers.get("Cookie", ""))
        token = cookies["npd_session"].value if "npd_session" in cookies else ""
        return token, self.server.service.store.authenticate(token)

    def check_host(self):
        expected = urlparse(self.server.origin).netloc
        if self.headers.get("Host") != expected:
            raise AppError("설정된 접속 주소를 사용하세요.", 403)

    def do_GET(self):
        try:
            route = urlparse(self.path).path
            if route == "/healthz":
                # ALB probes use the target IP as Host. This route contains no
                # project/model information and never authenticates or writes.
                return self.send(200, {"status": "ok"})
            self.check_host()
            static = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/styles.css": ("styles.css", "text/css")}
            if route in static:
                path, mime = static[route]
                return self.send(200, (ROOT / "static" / path).read_bytes(), mime + "; charset=utf-8")
            if route == "/api/health":
                return self.send(200, {"status": "ok", "version": "0.2.0"})
            _, user = self.context()
            return self.send(200, self.server.service.get(user, self.path))
        except AppError as e:
            self.send(e.status, {"error": str(e), **({"code": e.code} if hasattr(e, "code") else {})})
        except Exception:
            self.send(500, {"error": "요청 처리 중 오류가 발생했습니다."})

    def do_POST(self):
        try:
            self.check_host()
            if self.headers.get("Origin") not in (None, self.server.origin):
                raise AppError("다른 출처의 요청은 허용되지 않습니다.", 403)
            if self.headers.get_content_type() != "application/json":
                raise AppError("application/json 요청이 필요합니다.", 415)
            size = int(self.headers.get("Content-Length", 0))
            if not 0 < size <= 4500000:
                raise AppError("요청 크기를 확인하세요.", 413)
            body = json.loads(self.rfile.read(size), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(body, dict):
                raise AppError("JSON 객체가 필요합니다.")
            route = urlparse(self.path).path
            if route == "/api/login":
                email, password = body.get("email", ""), body.get("password", "")
                if not isinstance(email, str) or not isinstance(password, str) or len(email) > 300 or len(password) > 1000:
                    raise AppError("로그인 정보를 확인하세요.")
                token, csrf = self.server.service.store.login(email, password, self.client_address[0])
                secure = "; Secure" if self.server.origin.startswith("https://") else ""
                return self.send(200, {"csrf": csrf}, cookie=f"npd_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=28800{secure}")
            token, user = self.context()
            if not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), user["csrf"]):
                raise AppError("세션 확인에 실패했습니다. 새로고침 후 다시 시도하세요.", 403)
            if route == "/api/logout":
                self.server.service.store.logout(token)
                return self.send(200, {"ok": True}, cookie="npd_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")
            return self.send(201, self.server.service.post(user, route, body))
        except AppError as e:
            self.send(e.status, {"error": str(e), **({"code": e.code} if hasattr(e, "code") else {})})
        except (ValueError, TypeError, KeyError):
            self.send(400, {"error": "입력 형식을 확인하세요."})
        except Exception:
            self.send(500, {"error": "요청 처리 중 오류가 발생했습니다. 입력 원문은 오류 응답에 포함되지 않습니다."})
