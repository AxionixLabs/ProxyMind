# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import typing
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    field_validator,
)

from agent.domain.mcp_authorization import McpAuthorizationStatus
from agent.domain.mcp_oauth import (
    McpDynamicClient,
    McpMetadataClient,
    McpOAuthClientRegistration,
    McpOAuthBinding,
    McpOAuthError,
    McpOAuthLoginRequest,
    McpOAuthTarget,
    McpRegisteredClient,
    normalize_oauth_scopes,
    normalize_oauth_url,
)


class McpOAuthOptions(BaseModel):
    """校验单层 OAuth 选项；合并后的客户端组合由请求边界校验。"""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, hide_input_in_errors=True)
    scopes: list[str] | None = None
    client_id: str | None = Field(default=None, min_length=1)
    client_metadata_url: str | None = None
    callback_port: int | None = Field(default=None, ge=1, le=65535)
    login_timeout_sec: float = Field(default=300.0, gt=0)

    @field_validator("callback_port")
    @classmethod
    def validate_callback_port(cls, value: int | None) -> int:
        """只允许省略端口或提供合法整数，不接受显式空值。"""
        if value is None:
            raise ValueError("OAuth callback port cannot be null")
        return value

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, value: list[str] | None) -> list[str] | None:
        """将显式范围校验为去重、有序的 scope-token。"""
        if value is None:
            raise ValueError("OAuth scopes cannot be null")
        return list(normalize_oauth_scopes(tuple(value)))

    @field_validator("client_id", "client_metadata_url")
    @classmethod
    def validate_identity(cls, value: str | None) -> str:
        """拒绝显式空身份，省略字段保持未配置。"""
        if value is None or not value.strip():
            raise ValueError("OAuth client identity must be non-empty")
        return value

    @field_validator("client_metadata_url")
    @classmethod
    def validate_metadata_url(cls, value: str | None) -> str | None:
        """校验 HTTPS 客户端元数据文档地址。"""
        if value is not None:
            McpMetadataClient(value)
        return value

    def registration(self) -> McpOAuthClientRegistration:
        """从已合并的选项选择唯一公共客户端策略。"""
        if self.client_id is not None:
            if self.client_metadata_url is not None or self.callback_port is None:
                raise ValueError("OAuth client_id requires callback_port and excludes client_metadata_url")
            return McpRegisteredClient(self.client_id)
        if self.client_metadata_url is not None:
            return McpMetadataClient(self.client_metadata_url)
        return McpDynamicClient()


def validate_mcp_oauth_options(value: JsonValue, *, effective: bool = False) -> None:
    """在配置边界校验原始选项，并使用固定错误避免泄露错误输入。"""
    try:
        options = McpOAuthOptions.model_validate(value)
        if effective:
            options.registration()
    except (ValidationError, ValueError):
        raise ValueError("Invalid MCP OAuth options or public client configuration") from None


class McpOAuthServerSettings(BaseModel):
    """只读取登录所需的已验证配置，其他 MCP 设置由其原责任模块处理。"""

    model_config = ConfigDict(extra="ignore", strict=True, hide_input_in_errors=True)
    command: str | None = None
    url: str | None = None
    bearer_token_env_var: str | None = None
    http_headers: dict[str, str] = Field(default_factory=dict, repr=False)
    env_http_headers: dict[str, str] = Field(default_factory=dict, repr=False)
    oauth: McpOAuthOptions = Field(default_factory=McpOAuthOptions)

    @property
    def authorization(self) -> McpAuthorizationStatus:
        """仅依据配置选择认证类型，不解析环境秘密或宣称远端已接受。"""
        if self.command:
            return McpAuthorizationStatus("unsupported")
        if "bearer_token_env_var" in self.model_fields_set:
            return McpAuthorizationStatus("bearer")
        if self.explicit_auth:
            return McpAuthorizationStatus("header")
        if not self.applicable:
            return McpAuthorizationStatus("unsupported")
        return McpAuthorizationStatus("not_logged_in" if "oauth" in self.model_fields_set else "unknown")

    @property
    def explicit_auth(self) -> bool:
        """按配置存在性识别显式身份，不因环境变量尚未解析而回退。"""
        return "bearer_token_env_var" in self.model_fields_set or any(
            name.casefold() == "authorization"
            for name in (*self.http_headers, *self.env_http_headers)
        )

    @property
    def applicable(self) -> bool:
        """判断本地 OAuth 凭据是否适用于该服务，不触发网络发现。"""
        return bool(
            not self.command and self.url and not self.explicit_auth
            and not urlsplit(self.url).path.lower().rstrip("/").endswith("/sse")
        )

    def target(self, name: str) -> McpOAuthTarget:
        """按原始配置键冻结目标，退出登录也可使用此无显式身份限制的入口。"""
        if self.command or not self.url or urlsplit(self.url).path.lower().rstrip("/").endswith("/sse"):
            raise McpOAuthError("unsupported_transport")
        return McpOAuthTarget(name, normalize_oauth_url(self.url))

    def runtime_binding(self, name: str) -> McpOAuthBinding | None:
        """为非交互连接冻结认证选择，显式认证和非 HTTP 服务不查 OAuth 凭据。"""
        if not self.applicable:
            return None
        return McpOAuthBinding(self.target(name), self.oauth.registration())

    def login_request(
        self, name: str, *, scopes: tuple[str, ...] | None, timeout_sec: float | None,
        environment: typing.Mapping[str, str] | None = None,
    ) -> McpOAuthLoginRequest:
        """冻结配置及 CLI 选择，定制 Header 只供 MCP 来源的能力探测使用。"""
        if self.explicit_auth:
            raise McpOAuthError("configuration_conflict")
        source = os.environ if environment is None else environment
        headers = dict(self.http_headers)
        for name_key, variable in self.env_http_headers.items():
            value = source.get(variable)
            if value is None:
                raise McpOAuthError("configuration_conflict")
            headers[name_key] = value
        configured_scopes = None if self.oauth.scopes is None else tuple(self.oauth.scopes)
        return McpOAuthLoginRequest(
            target=self.target(name), registration=self.oauth.registration(),
            scopes=configured_scopes if scopes is None else scopes,
            callback_port=self.oauth.callback_port,
            timeout_sec=self.oauth.login_timeout_sec if timeout_sec is None else timeout_sec,
            headers=tuple(headers.items()),
        )


if __name__ == '__main__':
    pass
