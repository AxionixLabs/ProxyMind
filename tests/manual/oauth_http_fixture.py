# -*- coding: utf-8 -*-

"""提供真实本机 HTTP OAuth 故障服务，只签发合成凭据。"""

import base64
import hashlib
import json
import threading
from http.server import (
    BaseHTTPRequestHandler,
    ThreadingHTTPServer,
)
from urllib.parse import (
    parse_qsl,
    urlencode,
    urlsplit,
)

from mcp.shared.auth import OAuthClientMetadata
from pydantic import JsonValue

from tests.infrastructure.mcp.oauth_fixture import (
    AuthorizationRequest,
    TokenRequest,
)


class OAuthHttpFixture(ThreadingHTTPServer):
    """拥有独立 TCP 监听、请求线程和交换屏障，关闭前释放全部等待。"""

    daemon_threads = False

    def __init__(self, *, block_token: bool = False) -> None:
        """绑定随机 loopback 端口并创建每场景独立的授权绑定。"""
        super().__init__(("127.0.0.1", 0), _OAuthHandler)
        self.base = f"http://127.0.0.1:{self.server_port}"
        self.authorization: AuthorizationRequest | None = None
        self.registration: OAuthClientMetadata | None = None
        self.code = "synthetic-private-callback-code"
        self.token_calls = 0
        self.token_entered = threading.Event()
        self.token_release = threading.Event()
        if not block_token:
            self.token_release.set()
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        """停止接入并等待监听线程退出，交换屏障不会遗留阻塞请求。"""
        self.token_release.set()
        self.shutdown()
        self.server_close()
        self.thread.join(5)
        if self.thread.is_alive():
            raise RuntimeError("OAuth fixture did not close")


class _OAuthHandler(BaseHTTPRequestHandler):
    """处理验收需要的协议端点，不记录 URL、表单或合成令牌。"""

    @property
    def fixture(self) -> OAuthHttpFixture:
        """验证请求所属服务类型。"""
        server = self.server
        if not isinstance(server, OAuthHttpFixture):
            raise TypeError("Unexpected fixture server")
        return server

    def log_message(self, format: str, *args: str) -> None:
        """关闭包含回调参数的默认 HTTP 日志。"""

    def reply(self, status: int, document: dict[str, JsonValue], headers: dict[str, str] | None = None) -> None:
        """只发送固定协议文档，取消请求关闭连接属于预期终态。"""
        content = json.dumps(document).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return

    def do_GET(self) -> None:
        """提供能力发现及授权跳转，授权码绑定真实 PKCE 请求。"""
        fixture = self.fixture
        base = fixture.base
        target = urlsplit(self.path)
        if target.path == "/mcp":
            self.reply(401, {}, {"WWW-Authenticate": f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource", scope="read"'})
        elif target.path == "/.well-known/oauth-protected-resource":
            self.reply(200, {"resource": base + "/mcp", "authorization_servers": [base], "scopes_supported": ["read"]})
        elif target.path == "/.well-known/oauth-authorization-server":
            self.reply(200, {
                "issuer": base, "authorization_endpoint": base + "/authorize", "token_endpoint": base + "/token",
                "registration_endpoint": base + "/register", "response_types_supported": ["code"],
                "code_challenge_methods_supported": ["S256"], "token_endpoint_auth_methods_supported": ["none"],
                "authorization_response_iss_parameter_supported": True,
            })
        elif target.path == "/authorize":
            request = AuthorizationRequest.model_validate(dict(parse_qsl(target.query)))
            registration = fixture.registration
            assert registration is not None and request.resource == base + "/mcp"
            assert request.redirect_uri in [str(uri) for uri in registration.redirect_uris or ()]
            fixture.authorization = request
            location = request.redirect_uri + "?" + urlencode({"code": fixture.code, "state": request.state, "iss": base})
            self.reply(302, {}, {"Location": location})
        else:
            self.reply(404, {})

    def do_POST(self) -> None:
        """接受公共客户端注册或一次授权码交换，不提供真实账户能力。"""
        fixture = self.fixture
        length = int(self.headers.get("Content-Length", "0"))
        assert 0 < length <= 65536
        body = self.rfile.read(length)
        if self.path == "/register":
            registration = OAuthClientMetadata.model_validate_json(body)
            fixture.registration = registration
            self.reply(201, {**registration.model_dump(mode="json"), "client_id": "synthetic-client"})
        elif self.path == "/token":
            form = TokenRequest.model_validate(dict(parse_qsl(body.decode("ascii"))))
            authorization = fixture.authorization
            assert authorization is not None and form.code_verifier is not None
            assert form.grant_type == "authorization_code" and form.code == fixture.code
            assert form.client_id == authorization.client_id and form.redirect_uri == authorization.redirect_uri
            assert form.resource == authorization.resource
            digest = hashlib.sha256(form.code_verifier.encode("ascii")).digest()
            assert base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=") == authorization.code_challenge
            fixture.token_calls += 1
            fixture.token_entered.set()
            if not fixture.token_release.wait(20):
                self.reply(503, {})
                return
            self.reply(200, {"access_token": "synthetic-private-access", "refresh_token": "synthetic-private-refresh", "token_type": "Bearer", "expires_in": 3600, "scope": "read"})
        else:
            self.reply(404, {})
