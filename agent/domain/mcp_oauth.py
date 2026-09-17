# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import math
import typing
from dataclasses import (
    dataclass,
    field,
)
from urllib.parse import urlsplit


McpOAuthStorageErrorCode = typing.Literal[
    "storage_unavailable", "storage_corrupt", "credential_conflict", "storage_busy",
]


class McpOAuthStorageError(RuntimeError):
    """提供可展示的存储错误；不接受底层异常文本或凭据载荷。"""

    def __init__(self, code: McpOAuthStorageErrorCode) -> None:
        """以固定文本描述失败原因，调用方可按代码决定恢复方式。"""
        messages = {
            "storage_unavailable": "MCP OAuth credential storage is unavailable.",
            "storage_corrupt": "MCP OAuth credential storage is corrupt or incomplete.",
            "credential_conflict": "MCP OAuth credential identity or generation changed.",
            "storage_busy": "MCP OAuth credential storage is busy; retry later.",
        }
        self.code = code
        super().__init__(messages[code])


def normalize_oauth_url(value: str) -> str:
    """规范化 HTTP 来源，保留显式端口、路径和查询，拒绝隐式丢弃的内容。"""
    try:
        if not value or any(char.isspace() or ord(char) < 32 for char in value):
            raise ValueError
        parts = urlsplit(value)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or "#" in value
            or "\\" in value
        ):
            raise ValueError
        hostname = parts.hostname.encode("idna").decode("ascii").lower()
        host = f"[{hostname}]" if ":" in hostname else hostname
        port = parts.port
        if parts.netloc.endswith(":") or port == 0:
            raise ValueError
        authority = host if port is None else f"{host}:{port}"
        query = f"?{parts.query}" if "?" in value else ""
        return f"{parts.scheme}://{authority}{parts.path or '/'}{query}"
    except (ValueError, UnicodeError):
        raise ValueError("Invalid MCP OAuth URL") from None


@dataclass(frozen=True, slots=True)
class McpOAuthTarget:
    """冻结原始配置键与完整服务地址，不以显示名或来源代替服务身份。"""

    config_key: str
    server_url: str = field(repr=False)

    def __post_init__(self) -> None:
        """拒绝空键与尚未规范化的 URL，保留配置键的原始语义。"""
        if not self.config_key.strip():
            raise ValueError("MCP OAuth requires a configuration key")
        if normalize_oauth_url(self.server_url) != self.server_url:
            raise ValueError("MCP OAuth requires a normalized server URL")


@dataclass(frozen=True, slots=True)
class McpOAuthClientInfo:
    """保存公共客户端注册结果；不接收 client secret 或注册管理凭据。"""

    client_id: str = field(repr=False)
    redirect_uris: tuple[str, ...] = field(repr=False)
    registration: typing.Literal["dynamic", "metadata", "registered"]
    token_endpoint_auth_method: typing.Literal["none"] = "none"

    def __post_init__(self) -> None:
        """保证注册记录只描述可恢复的公共客户端。"""
        if (
            not self.client_id.strip()
            or not self.redirect_uris
            or self.registration not in ("dynamic", "metadata", "registered")
            or self.token_endpoint_auth_method != "none"
        ):
            raise ValueError("Invalid MCP OAuth public client")
        for uri in self.redirect_uris:
            normalize_oauth_url(uri)


@dataclass(frozen=True, slots=True)
class McpOAuthToken:
    """保存令牌与绝对有效期；表示形式不输出令牌或授权范围。"""

    access_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)
    expires_at: float | None
    granted_scopes: tuple[str, ...] | None = field(repr=False)

    def __post_init__(self) -> None:
        """拒绝不能放入 Bearer 头的令牌与非有限时间，不为未知有效期制造截止时间。"""
        if not self.access_token or self.refresh_token == "" or any(
            ord(char) <= 32 or ord(char) >= 127 for char in self.access_token
        ):
            raise ValueError("Invalid MCP OAuth token")
        if self.expires_at is not None and not math.isfinite(self.expires_at):
            raise ValueError("Invalid MCP OAuth expiration")
        if self.granted_scopes is not None and any(
            not scope or any(char.isspace() for char in scope)
            for scope in self.granted_scopes
        ):
            raise ValueError("Invalid MCP OAuth scopes")

    def remaining_lifetime(self, now: float) -> float | None:
        """从调用方注入的 UTC 时间计算剩余秒数，过期返回零。"""
        return None if self.expires_at is None else max(0.0, self.expires_at - now)


@dataclass(frozen=True, slots=True)
class McpOAuthCredentialSnapshot:
    """冻结认证绑定与版本；只能交给凭据用例和认证适配器，不进入前端事件。"""

    target: McpOAuthTarget
    issuer: str = field(repr=False)
    resource: str = field(repr=False)
    token_endpoint: str = field(repr=False)
    client: McpOAuthClientInfo = field(repr=False)
    generation: int
    token: McpOAuthToken | None = field(repr=False)
    recovery: typing.Literal["refresh_uncertain", "reauthorization_required"] | None = None

    def __post_init__(self) -> None:
        """校验恢复记录的基本结构；远端元数据绑定由认证适配器负责。"""
        if isinstance(self.generation, bool) or self.generation < 0:
            raise ValueError("Invalid MCP OAuth generation")
        if self.recovery not in (None, "refresh_uncertain", "reauthorization_required") or (
            self.recovery is not None and self.token is not None
        ):
            raise ValueError("Invalid MCP OAuth recovery state")
        for url in (self.issuer, self.resource, self.token_endpoint):
            normalize_oauth_url(url)

    def require_binding(self, *, issuer: str, resource: str, client_id: str) -> None:
        """在消费令牌前检查已验证的授权服务器、资源与客户端身份。"""
        if (self.issuer, self.resource, self.client.client_id) != (
            issuer, resource, client_id,
        ):
            raise McpOAuthStorageError("credential_conflict")


@dataclass(frozen=True, slots=True)
class McpOAuthCredentialRecord:
    """包含删除后的版本墓碑，使迟到的登录或刷新仍能被拒绝。"""

    generation: int
    snapshot: McpOAuthCredentialSnapshot | None = field(repr=False)


McpOAuthCredentialState = typing.Literal[
    "not_applicable", "missing", "registered", "stored", "expired", "unavailable",
    "refresh_uncertain", "reauthorization_required",
]


@dataclass(frozen=True, slots=True)
class McpOAuthCredentialView:
    """投影本地凭据状态；不包含机密，也不宣称远端认证仍有效。"""

    target: McpOAuthTarget
    state: McpOAuthCredentialState
    expires_at: float | None = None
    error: McpOAuthStorageErrorCode | None = None
    generation: int | None = None


def credential_view_from_record(
    target: McpOAuthTarget, record: McpOAuthCredentialRecord, *, now: float,
) -> McpOAuthCredentialView:
    """从已读取记录投影本地凭据事实，供存储查询和请求边界共同使用。"""
    snapshot = record.snapshot
    if snapshot is None:
        return McpOAuthCredentialView(target, "missing", generation=record.generation)
    if snapshot.recovery is not None:
        return McpOAuthCredentialView(target, snapshot.recovery, generation=record.generation)
    token = snapshot.token
    if token is None:
        return McpOAuthCredentialView(target, "registered", generation=record.generation)
    expired = token.expires_at is not None and token.expires_at <= now
    return McpOAuthCredentialView(
        target, "expired" if expired else "stored", token.expires_at, generation=record.generation,
    )


@dataclass(frozen=True, slots=True)
class McpOAuthLogoutResult:
    """报告本地删除及最新版本；不表示已撤销远端授权。"""

    target: McpOAuthTarget
    removed: bool
    generation: int


MCP_OAUTH_CALLBACK_MAX_BYTES = 64 * 1024

McpOAuthErrorCode = typing.Literal[
    "unsupported_transport", "configuration_conflict", "invalid_response",
    "registration_failed", "authorization_denied", "timeout", "network_error",
    "reauthorization_required", "callback_unavailable", "oauth_unavailable",
    "login_required", "insufficient_scope", "refresh_uncertain",
    "callback_input_unavailable", "callback_input_closed", "callback_input_too_long",
]


class McpOAuthError(RuntimeError):
    """提供固定的授权失败提示，不携带响应、授权码或回调地址。"""

    def __init__(self, code: McpOAuthErrorCode) -> None:
        """保存供 CLI 和认证用例裁决的安全错误码。"""
        messages = {
            "unsupported_transport": "OAuth login requires a Streamable HTTP MCP server.",
            "configuration_conflict": "MCP OAuth settings conflict with the requested authorization.",
            "invalid_response": "The MCP OAuth server returned an invalid authorization response.",
            "registration_failed": "MCP OAuth public client registration failed.",
            "authorization_denied": "MCP OAuth authorization was declined.",
            "timeout": "MCP OAuth login timed out.",
            "network_error": "MCP OAuth could not reach the authorization service.",
            "reauthorization_required": "MCP OAuth authorization must be repeated.",
            "callback_unavailable": "The local OAuth callback port could not be opened.",
            "oauth_unavailable": "The MCP server does not provide the required OAuth capabilities.",
            "login_required": "MCP OAuth login is required.",
            "insufficient_scope": "MCP OAuth permissions are insufficient; log in with the required scopes.",
            "refresh_uncertain": "MCP OAuth refresh could not be confirmed; log in again before retrying.",
            "callback_input_unavailable": "Manual OAuth callback input requires an interactive terminal with hidden input support.",
            "callback_input_closed": "No OAuth callback URL was received before input closed.",
            "callback_input_too_long": "OAuth callback URL exceeds 64 KiB.",
        }
        self.code = code
        super().__init__(messages[code])


def normalize_oauth_scopes(values: tuple[str, ...]) -> tuple[str, ...]:
    """校验 scope-token 字符并去重保序，空元组表示显式不请求范围。"""
    if any(
        not value or any(not (ord(char) == 0x21 or 0x23 <= ord(char) <= 0x5B or 0x5D <= ord(char) <= 0x7E) for char in value)
        for value in values
    ):
        raise ValueError("OAuth scopes must contain non-empty scope tokens")
    return tuple(dict.fromkeys(values))


@dataclass(frozen=True, slots=True)
class McpDynamicClient:
    """选择授权服务器声明的动态公共客户端注册端点。"""

    kind: typing.Literal["dynamic"] = field(default="dynamic", init=False)


@dataclass(frozen=True, slots=True)
class McpMetadataClient:
    """冻结由用户明确配置的客户端元数据文档身份。"""

    metadata_url: str = field(repr=False)
    kind: typing.Literal["metadata"] = field(default="metadata", init=False)

    def __post_init__(self) -> None:
        """拒绝非 HTTPS、根路径及带身份或片段的文档地址。"""
        normalize_oauth_url(self.metadata_url)
        parsed = urlsplit(self.metadata_url)
        if parsed.scheme != "https" or parsed.path in ("", "/"):
            raise ValueError("OAuth client metadata requires an HTTPS document URL")


@dataclass(frozen=True, slots=True)
class McpRegisteredClient:
    """冻结预注册的公共客户端身份，回调端口由登录请求明确提供。"""

    client_id: str = field(repr=False)
    kind: typing.Literal["registered"] = field(default="registered", init=False)

    def __post_init__(self) -> None:
        """拒绝空客户端身份。"""
        if not self.client_id.strip():
            raise ValueError("OAuth client ID must be non-empty")


McpOAuthClientRegistration = McpDynamicClient | McpMetadataClient | McpRegisteredClient


@dataclass(frozen=True, slots=True)
class McpOAuthBinding:
    """冻结运行时允许消费的服务与客户端身份，不承载交互登录资源。"""

    target: McpOAuthTarget
    registration: McpOAuthClientRegistration


@dataclass(frozen=True, slots=True)
class McpOAuthLoginRequest:
    """冻结一次显式登录的目标、客户端、范围和期限，不承载现有令牌。"""

    target: McpOAuthTarget
    registration: McpOAuthClientRegistration = field(default_factory=McpDynamicClient)
    scopes: tuple[str, ...] | None = None
    callback_port: int | None = None
    timeout_sec: float = 300.0
    headers: tuple[tuple[str, str], ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        """拒绝无法形成完整公共客户端登录的参数。"""
        if isinstance(self.timeout_sec, bool) or not math.isfinite(self.timeout_sec) or self.timeout_sec <= 0:
            raise ValueError("OAuth login timeout must be positive and finite")
        if self.callback_port is not None and (
            isinstance(self.callback_port, bool) or not 1 <= self.callback_port <= 65535
        ):
            raise ValueError("OAuth callback port must be between 1 and 65535")
        if isinstance(self.registration, McpRegisteredClient) and self.callback_port is None:
            raise ValueError("Pre-registered OAuth clients require a callback port")
        if self.scopes is not None:
            normalize_oauth_scopes(self.scopes)
        if any(name.casefold() == "authorization" for name, _ in self.headers):
            raise McpOAuthError("configuration_conflict")


@dataclass(frozen=True, slots=True)
class McpOAuthLoginResult:
    """只在凭据持久提交后返回可展示结果。"""

    target: McpOAuthTarget
    expires_at: float | None


if __name__ == '__main__':
    pass
