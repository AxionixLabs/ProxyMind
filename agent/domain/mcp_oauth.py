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
        """拒绝空令牌与非有限时间，不为未知有效期制造截止时间。"""
        if not self.access_token or self.refresh_token == "":
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

    def __post_init__(self) -> None:
        """校验恢复记录的基本结构；远端元数据绑定由认证适配器负责。"""
        if isinstance(self.generation, bool) or self.generation < 0:
            raise ValueError("Invalid MCP OAuth generation")
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


@dataclass(frozen=True, slots=True)
class McpOAuthCredentialView:
    """投影本地凭据状态；不包含机密，也不宣称远端认证仍有效。"""

    target: McpOAuthTarget
    state: typing.Literal[
        "not_applicable", "missing", "registered", "stored", "expired", "unavailable",
    ]
    expires_at: float | None = None
    error: McpOAuthStorageErrorCode | None = None


@dataclass(frozen=True, slots=True)
class McpOAuthLogoutResult:
    """报告本地删除及最新版本；不表示已撤销远端授权。"""

    target: McpOAuthTarget
    removed: bool
    generation: int


if __name__ == '__main__':
    pass
