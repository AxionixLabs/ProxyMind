# -*- coding: utf-8 -*-

"""为 OAuth SDK 契约测试提供进程内 HTTP 服务，不访问真实账户或网络。"""

import base64
import hashlib
import typing

import httpx
from urllib.parse import parse_qsl

from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)
from mcp.types import LATEST_PROTOCOL_VERSION
from pydantic import (
    AnyUrl,
    BaseModel,
    JsonValue,
)

from metadata import const

RESOURCE_URL = "https://mcp.example.test/mcp/project"
RESOURCE_METADATA_URL = "https://mcp.example.test/.well-known/oauth-protected-resource/mcp/project"
ISSUER_URL = "https://auth.example.test/tenant"
AUTH_METADATA_URL = "https://auth.example.test/.well-known/oauth-authorization-server/tenant"
TOKEN_URL = "https://auth.example.test/tenant/token"
CALLBACK_URL = "http://127.0.0.1:12608/callback"
CLIENT_METADATA_URL = "https://client.example.test/oauth/client.json"


class AuthorizationRequest(BaseModel):
    """校验 SDK 实际生成的授权请求，保留待交换授权码所绑定的参数。"""

    response_type: typing.Literal["code"]
    client_id: str
    redirect_uri: str
    state: str
    code_challenge: str
    code_challenge_method: typing.Literal["S256"]
    resource: str
    scope: str | None = None


class TokenRequest(BaseModel):
    """校验真实 HTTP 表单后区分授权码交换和刷新请求。"""

    grant_type: typing.Literal["authorization_code", "refresh_token"]
    client_id: str
    resource: str
    code: str | None = None
    code_verifier: str | None = None
    redirect_uri: str | None = None
    refresh_token: str | None = None


class ProbeRpcRequest(BaseModel):
    """读取测试服务支持的最小 MCP 请求身份。"""

    jsonrpc: typing.Literal["2.0"]
    id: int | str | None = None
    method: str
    params: dict[str, JsonValue] | None = None


class MemoryTokenStorage:
    """实现 SDK 存储契约，只保存每次测试独有的令牌与注册快照。"""

    def __init__(self) -> None:
        """创建空记录和写入证据，不读取进程环境。"""
        self.tokens: OAuthToken | None = None
        self.client_info: OAuthClientInformationFull | None = None
        self.saved_tokens: list[OAuthToken] = []

    async def get_tokens(self) -> OAuthToken | None:
        """返回与原记录分离的令牌快照。"""
        return self.tokens.model_copy(deep=True) if self.tokens is not None else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        """保存令牌快照，并保留刷新前后的写入证据。"""
        self.tokens = tokens.model_copy(deep=True)
        self.saved_tokens.append(tokens.model_copy(deep=True))

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        """返回可模拟冷启动的注册快照。"""
        return self.client_info.model_copy(deep=True) if self.client_info is not None else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        """保存注册快照，不把注册成功当作令牌签发。"""
        self.client_info = client_info.model_copy(deep=True)


class OAuthService:
    """提供分离来源的 MCP、发现、注册、授权和令牌端点，供三个契约测试模块复用。"""

    def __init__(self) -> None:
        """初始化可控响应与服务端事实，各实例完全隔离。"""
        self.requests: list[httpx.Request] = []
        self.authorizations: list[AuthorizationRequest] = []
        self.token_requests: list[TokenRequest] = []
        self.registrations: list[OAuthClientMetadata] = []
        self.registered_clients: dict[str, OAuthClientMetadata] = {}
        self.codes: dict[str, AuthorizationRequest] = {}
        self.callback_result: tuple[str, str | None] | None = None
        self.cimd_supported = False
        self.metadata_issuer = ISSUER_URL
        self.challenge_scope: str | None = None
        self.resource_scopes: list[str] | None = ["project:read"]
        self.server_scopes: list[str] | None = ["org:read"]
        self.expires_in = 3600
        self.registration_status = 201
        self.token_status = 200
        self.refresh_status = 200
        self.denied_status: int | None = None
        self.denied_challenge: str | None = None
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.revoked_access_tokens: set[str] = set()
        self.issued_tokens = 0

    def provider(
        self, storage: MemoryTokenStorage, *, explicit_scope: str | None = None,
        client_metadata_url: str | None = None,
    ) -> OAuthClientProvider:
        """仅经公开构造参数创建 SDK provider，不注入或修改其内部上下文。"""
        return OAuthClientProvider(
            server_url=RESOURCE_URL,
            client_metadata=OAuthClientMetadata(
                redirect_uris=[AnyUrl(CALLBACK_URL)],
                client_name=const.APP_DESC,
                token_endpoint_auth_method="none",
                scope=explicit_scope,
            ),
            storage=storage,
            redirect_handler=self.authorize,
            callback_handler=self.callback,
            client_metadata_url=client_metadata_url,
        )

    def client(self, provider: OAuthClientProvider | None = None) -> httpx.AsyncClient:
        """构造由调用方关闭的进程内 HTTP 客户端。"""
        return httpx.AsyncClient(
            transport=httpx.MockTransport(self.handle), auth=provider, follow_redirects=False,
        )

    async def authorize(self, url: str) -> None:
        """模拟浏览器访问真实授权 URL 并读取重定向，不绕过授权端点校验。"""
        async with self.client() as browser:
            response = await browser.get(url)
        assert response.status_code == 302
        callback = httpx.URL(response.headers["Location"])
        assert str(callback.copy_with(query=None)) == CALLBACK_URL
        self.callback_result = (callback.params["code"], callback.params.get("state"))

    async def callback(self) -> tuple[str, str | None]:
        """转交本次模拟浏览器得到的授权码与 state。"""
        assert self.callback_result is not None
        return self.callback_result

    async def handle(self, request: httpx.Request) -> httpx.Response:
        """按完整 URL 路由实际 HTTP 请求，未知端点直接报告测试失败。"""
        # HTTPX 会在认证重试时修改原请求；审计记录必须保留发送当时的快照。
        self.requests.append(httpx.Request(
            request.method, request.url, headers=request.headers, content=request.content,
        ))
        url = str(request.url.copy_with(query=None))
        if url == RESOURCE_METADATA_URL:
            return httpx.Response(200, json={
                "resource": RESOURCE_URL,
                "authorization_servers": [ISSUER_URL],
                "scopes_supported": self.resource_scopes,
            })
        if url == AUTH_METADATA_URL:
            return httpx.Response(200, json={
                "issuer": self.metadata_issuer,
                "authorization_endpoint": f"{ISSUER_URL}/authorize",
                "token_endpoint": TOKEN_URL,
                "registration_endpoint": f"{ISSUER_URL}/register",
                "scopes_supported": self.server_scopes,
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"],
                "client_id_metadata_document_supported": self.cimd_supported,
            })
        if url == f"{ISSUER_URL}/register":
            metadata = OAuthClientMetadata.model_validate_json(request.content)
            self.registrations.append(metadata)
            if self.registration_status != 201:
                return httpx.Response(self.registration_status, json={"error": "invalid_client_metadata"})
            self.registered_clients["dynamic-client"] = metadata
            return httpx.Response(201, json={**metadata.model_dump(mode="json"), "client_id": "dynamic-client"})
        if url == f"{ISSUER_URL}/authorize":
            authorization = AuthorizationRequest.model_validate(dict(request.url.params))
            assert authorization.resource == RESOURCE_URL
            assert authorization.state
            if authorization.client_id == CLIENT_METADATA_URL:
                assert self.cimd_supported
            else:
                metadata = self.registered_clients[authorization.client_id]
                assert AnyUrl(authorization.redirect_uri) in (metadata.redirect_uris or [])
            code = f"code-{len(self.authorizations) + 1}"
            self.authorizations.append(authorization)
            self.codes[code] = authorization
            location = httpx.URL(authorization.redirect_uri).copy_add_param("code", code)
            location = location.copy_add_param("state", authorization.state)
            return httpx.Response(302, headers={"Location": str(location)})
        if url == TOKEN_URL:
            return self.exchange(request)
        if url == RESOURCE_URL:
            return self.resource(request)
        raise AssertionError(f"unexpected OAuth fixture endpoint: {request.method} {url}")

    def exchange(self, request: httpx.Request) -> httpx.Response:
        """校验授权码绑定及 PKCE 或 refresh token 轮换后签发测试令牌。"""
        form = TokenRequest.model_validate(dict(parse_qsl(request.content.decode("ascii"))))
        self.token_requests.append(form)
        assert request.method == "POST"
        assert form.resource == RESOURCE_URL
        if form.grant_type == "authorization_code":
            assert form.code is not None and form.code_verifier is not None
            authorization = self.codes[form.code]
            assert form.client_id == authorization.client_id
            assert form.redirect_uri == authorization.redirect_uri
            assert 43 <= len(form.code_verifier) <= 128
            digest = hashlib.sha256(form.code_verifier.encode("ascii")).digest()
            assert base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=") == authorization.code_challenge
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
        else:
            if form.refresh_token != self.refresh_token:
                return httpx.Response(400, json={"error": "invalid_grant"})
            if self.refresh_status != 200:
                return httpx.Response(self.refresh_status, json={"error": "invalid_grant"})
        self.issued_tokens += 1
        self.access_token = f"access-{self.issued_tokens}"
        self.refresh_token = f"refresh-{self.issued_tokens}"
        return httpx.Response(200, json={
            "access_token": self.access_token, "token_type": "Bearer",
            "refresh_token": self.refresh_token, "expires_in": self.expires_in,
            "scope": self.authorizations[-1].scope,
        })

    def resource(self, request: httpx.Request) -> httpx.Response:
        """根据服务端令牌状态保护最小 MCP initialize 与 tools/list 端点。"""
        if self.denied_status is not None:
            headers = {"WWW-Authenticate": self.denied_challenge} if self.denied_challenge is not None else {}
            return httpx.Response(self.denied_status, headers=headers)
        if (
            self.access_token is None or self.access_token in self.revoked_access_tokens
            or request.headers.get("Authorization") != f"Bearer {self.access_token}"
        ):
            challenge = f'Bearer resource_metadata="{RESOURCE_METADATA_URL}"'
            if self.challenge_scope is not None:
                challenge += f', scope="{self.challenge_scope}"'
            return httpx.Response(401, headers={"WWW-Authenticate": challenge})
        if request.method == "GET":
            return httpx.Response(200, json={"authenticated": True})
        message = ProbeRpcRequest.model_validate_json(request.content)
        if message.id is None:
            return httpx.Response(202)
        result: dict[str, JsonValue]
        if message.method == "initialize":
            result = {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "oauth-fixture", "version": "1"},
            }
        else:
            assert message.method == "tools/list"
            result = {"tools": [{
                "name": "read_fixture", "description": "Read fixture data",
                "inputSchema": {"type": "object", "properties": {}},
            }]}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": message.id, "result": result})
