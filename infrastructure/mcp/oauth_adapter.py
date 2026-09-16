# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import secrets
import time
import typing

import httpx
from dataclasses import (
    dataclass,
    field,
)
from urllib.parse import (
    parse_qsl,
    urlsplit,
)

from mcp.client.auth.oauth2 import PKCEParameters
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    extract_resource_metadata_from_www_auth,
    extract_scope_from_www_auth,
)
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthMetadata,
    OAuthToken,
    ProtectedResourceMetadata,
)
from mcp.shared.auth_utils import check_resource_allowed
from mcp.types import LATEST_PROTOCOL_VERSION
from pydantic import (
    AnyUrl,
    JsonValue,
    TypeAdapter,
    ValidationError,
)

from agent.domain.mcp_oauth import (
    McpMetadataClient,
    McpOAuthClientInfo,
    McpOAuthCredentialSnapshot,
    McpOAuthError,
    McpOAuthLoginRequest,
    McpOAuthToken,
    McpRegisteredClient,
    normalize_oauth_scopes,
    normalize_oauth_url,
)
from agent.ports.mcp_oauth import McpOAuthPresenter
from infrastructure.mcp.oauth_callback import oauth_callback
from metadata import const


_JSON_DOCUMENT = TypeAdapter(dict[str, JsonValue])
_MAX_DOCUMENT_BYTES = 1024 * 1024


@dataclass(frozen=True)
class _Discovery:
    """保存当前授权已验证的远端绑定，SDK 模型限定在适配器内部。"""

    issuer: str
    resource: str
    metadata: OAuthMetadata = field(repr=False)
    scopes: tuple[str, ...] | None
    require_issuer: bool


def _endpoint(url: str, server_url: str) -> str:
    """拒绝不安全端点；仅允许本机 HTTP MCP 使用本机 HTTP 授权测试服务。"""
    normalize_oauth_url(url)
    parsed = urlsplit(url)
    server = urlsplit(server_url)
    loopback = {"127.0.0.1", "::1", "localhost"}
    if parsed.scheme != "https" and not (
        parsed.scheme == server.scheme == "http"
        and parsed.hostname in loopback and server.hostname in loopback
    ):
        raise McpOAuthError("invalid_response")
    if parsed.hostname in loopback and server.hostname not in loopback:
        raise McpOAuthError("invalid_response")
    return url


async def _json_document(
    client: httpx.AsyncClient, method: str, url: str,
    *, data: dict[str, str] | None = None, document: dict[str, JsonValue] | None = None,
) -> tuple[int, dict[str, JsonValue] | None]:
    """有界读取协议 JSON，拒绝重定向，不把错误正文附加到异常。"""
    async with client.stream(
        method, url, data=data, json=document, follow_redirects=False,
        headers={"MCP-Protocol-Version": LATEST_PROTOCOL_VERSION},
    ) as response:
        if 300 <= response.status_code < 400:
            raise McpOAuthError("invalid_response")
        if response.status_code not in (200, 201):
            return response.status_code, None
        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > _MAX_DOCUMENT_BYTES:
                raise McpOAuthError("invalid_response")
        return response.status_code, _JSON_DOCUMENT.validate_json(bytes(content))


def _scopes(value: str | None) -> tuple[str, ...] | None:
    """将协议中的空格分隔范围转换为有序具名契约。"""
    return None if value is None else normalize_oauth_scopes(tuple(value.split(" ")) if value else ())


class McpOAuthAdapter:
    """组合公开 SDK 原语与 HTTPX 完成显式登录；不接入默认 provider 的交互重试。"""

    def __init__(
        self, *, open_browser: typing.Callable[[str], typing.Awaitable[bool]],
        client_factory: typing.Callable[[], httpx.AsyncClient] | None = None,
        clock: typing.Callable[[], float] = time.time,
    ) -> None:
        """注入浏览器平台能力与可替换 HTTP 客户端工厂，每次授权独立关闭客户端。"""
        self._open_browser = open_browser
        self._client_factory = client_factory
        self._clock = clock

    async def _discover(self, client: httpx.AsyncClient, request: McpOAuthLoginRequest) -> _Discovery:
        """按资源发现、issuer 元数据和 scope 优先级确定当前授权绑定。"""
        server = _endpoint(request.target.server_url, request.target.server_url)
        headers = dict(request.headers)
        headers["MCP-Protocol-Version"] = LATEST_PROTOCOL_VERSION
        headers["Accept"] = "application/json, text/event-stream"
        async with client.stream("GET", server, headers=headers, follow_redirects=False) as response:
            if 300 <= response.status_code < 400:
                raise McpOAuthError("invalid_response")
            if response.status_code >= 500:
                raise McpOAuthError("network_error")
            challenge = response.headers.get("WWW-Authenticate", "")
            bearer = challenge.lower().startswith("bearer ")
            metadata_url = extract_resource_metadata_from_www_auth(response) if bearer else None
            challenged_scopes = _scopes(extract_scope_from_www_auth(response)) if bearer else None
        resource_document: dict[str, JsonValue] | None = None
        for url in build_protected_resource_metadata_discovery_urls(metadata_url, server):
            status, document = await _json_document(client, "GET", _endpoint(url, server))
            if status == 200:
                resource_document = document
                break
            if status not in (404, 405):
                raise McpOAuthError("network_error" if status >= 500 else "invalid_response")
        if resource_document is None:
            raise McpOAuthError("oauth_unavailable")
        resource_metadata = ProtectedResourceMetadata.model_validate(resource_document)
        resource = resource_document.get("resource")
        issuers = resource_document.get("authorization_servers")
        if not isinstance(resource, str) or not isinstance(issuers, list) or not issuers or not isinstance(issuers[0], str):
            raise McpOAuthError("invalid_response")
        _endpoint(resource, server)
        if not check_resource_allowed(server, resource) or (
            urlsplit(resource).query and urlsplit(resource).query != urlsplit(server).query
        ):
            raise McpOAuthError("invalid_response")
        issuer = _endpoint(issuers[0], server)
        if urlsplit(issuer).query:
            raise McpOAuthError("invalid_response")
        metadata_document: dict[str, JsonValue] | None = None
        for url in build_oauth_authorization_server_metadata_discovery_urls(issuer, server):
            status, document = await _json_document(client, "GET", _endpoint(url, server))
            if status == 200:
                metadata_document = document
                break
            if status not in (404, 405):
                raise McpOAuthError("network_error" if status >= 500 else "invalid_response")
        if metadata_document is None:
            raise McpOAuthError("oauth_unavailable")
        metadata = OAuthMetadata.model_validate(metadata_document)
        if metadata_document.get("issuer") != issuer:
            raise McpOAuthError("invalid_response")
        for endpoint in (metadata.authorization_endpoint, metadata.token_endpoint, metadata.registration_endpoint):
            if endpoint is not None:
                _endpoint(str(endpoint), server)
        if (
            "S256" not in (metadata.code_challenge_methods_supported or [])
            or "code" not in metadata.response_types_supported
            or (metadata.grant_types_supported is not None and "authorization_code" not in metadata.grant_types_supported)
            or (metadata.token_endpoint_auth_methods_supported is not None and "none" not in metadata.token_endpoint_auth_methods_supported)
        ):
            raise McpOAuthError("oauth_unavailable")
        require_issuer = metadata_document.get("authorization_response_iss_parameter_supported", False)
        if not isinstance(require_issuer, bool):
            raise McpOAuthError("invalid_response")
        scopes = request.scopes
        if scopes is not None:
            if challenged_scopes is not None and not set(challenged_scopes).issubset(scopes):
                raise McpOAuthError("configuration_conflict")
        elif challenged_scopes is not None:
            scopes = challenged_scopes
        elif resource_metadata.scopes_supported is not None:
            scopes = normalize_oauth_scopes(tuple(resource_metadata.scopes_supported))
        elif metadata.scopes_supported is not None:
            scopes = normalize_oauth_scopes(tuple(metadata.scopes_supported))
        return _Discovery(issuer, resource, metadata, scopes, require_issuer)

    async def _register(
        self, client: httpx.AsyncClient, request: McpOAuthLoginRequest,
        discovery: _Discovery, redirect_uri: str,
    ) -> McpOAuthClientInfo:
        """选择预注册、CIMD 或 DCR；仅接受绑定当前回调的公共客户端。"""
        registration = request.registration
        if isinstance(registration, McpRegisteredClient):
            return McpOAuthClientInfo(registration.client_id, (redirect_uri,), "registered")
        if isinstance(registration, McpMetadataClient):
            if discovery.metadata.client_id_metadata_document_supported is not True:
                raise McpOAuthError("registration_failed")
            return McpOAuthClientInfo(registration.metadata_url, (redirect_uri,), "metadata")
        endpoint = discovery.metadata.registration_endpoint
        if endpoint is None:
            raise McpOAuthError("registration_failed")
        metadata = OAuthClientMetadata(
            redirect_uris=[AnyUrl(redirect_uri)], client_name=const.APP_DESC,
            token_endpoint_auth_method="none",
            scope=" ".join(discovery.scopes) if discovery.scopes else None,
        )
        document = _JSON_DOCUMENT.validate_json(metadata.model_dump_json(exclude_none=True))
        status, registered = await _json_document(client, "POST", str(endpoint), document=document)
        if status not in (200, 201) or registered is None:
            raise McpOAuthError("registration_failed")
        info = OAuthClientInformationFull.model_validate(registered)
        if (
            not info.client_id or info.client_secret is not None
            or info.token_endpoint_auth_method != "none"
            or info.redirect_uris is None or AnyUrl(redirect_uri) not in info.redirect_uris
        ):
            raise McpOAuthError("registration_failed")
        return McpOAuthClientInfo(info.client_id, (redirect_uri,), "dynamic")

    async def authorize(
        self, request: McpOAuthLoginRequest, generation: int, presenter: McpOAuthPresenter,
    ) -> McpOAuthCredentialSnapshot:
        """拥有授权所需短期资源，成功后返回待提交快照，取消沿调用链传播。"""
        client = self._client_factory() if self._client_factory is not None else httpx.AsyncClient(timeout=20.0, trust_env=False)
        try:
            async with client:
                discovery = await self._discover(client, request)
                state = secrets.token_urlsafe(32)
                pkce = PKCEParameters.generate()
                async with oauth_callback(
                    port=request.callback_port, state=state, issuer=discovery.issuer,
                    require_issuer=discovery.require_issuer,
                ) as callback:
                    info = await self._register(client, request, discovery, callback.redirect_uri)
                    parameters = {
                        "response_type": "code", "client_id": info.client_id,
                        "redirect_uri": callback.redirect_uri, "state": state,
                        "code_challenge": pkce.code_challenge, "code_challenge_method": "S256",
                        "resource": discovery.resource,
                    }
                    if discovery.scopes:
                        parameters["scope"] = " ".join(discovery.scopes)
                    endpoint = str(discovery.metadata.authorization_endpoint)
                    if any(name in {*parameters, "scope"} for name, _ in parse_qsl(urlsplit(endpoint).query, keep_blank_values=True)):
                        raise McpOAuthError("invalid_response")
                    url = str(httpx.URL(endpoint).copy_merge_params(parameters))
                    presenter.authorization_url(url)
                    if not await self._open_browser(url):
                        presenter.browser_unavailable()
                    code = await callback.wait()
                    received_at = self._clock()
                    status, document = await _json_document(
                        client, "POST", str(discovery.metadata.token_endpoint),
                        data={
                            "grant_type": "authorization_code", "client_id": info.client_id,
                            "code": code, "code_verifier": pkce.code_verifier,
                            "redirect_uri": callback.redirect_uri, "resource": discovery.resource,
                        },
                    )
                    if status != 200 or document is None:
                        raise McpOAuthError("authorization_denied" if status in (400, 401, 403) else "network_error")
                    if "token_type" not in document:
                        raise McpOAuthError("invalid_response")
                    token = OAuthToken.model_validate(document, strict=True)
                    if token.expires_in is not None and token.expires_in < 0:
                        raise McpOAuthError("invalid_response")
                    granted = _scopes(token.scope) if token.scope is not None else discovery.scopes
                    if discovery.scopes is not None and set(granted or ()) != set(discovery.scopes):
                        raise McpOAuthError("configuration_conflict")
                    return McpOAuthCredentialSnapshot(
                        request.target, discovery.issuer, discovery.resource,
                        str(discovery.metadata.token_endpoint), info, generation,
                        McpOAuthToken(
                            token.access_token, token.refresh_token,
                            received_at + token.expires_in if token.expires_in is not None else None,
                            granted,
                        ),
                    )
        except httpx.TimeoutException:
            raise McpOAuthError("timeout") from None
        except httpx.RequestError:
            raise McpOAuthError("network_error") from None
        except (ValidationError, ValueError):
            raise McpOAuthError("invalid_response") from None


if __name__ == '__main__':
    pass
