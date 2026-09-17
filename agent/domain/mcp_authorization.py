# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from dataclasses import (
    dataclass,
    replace,
)

from agent.domain.mcp_oauth import (
    McpOAuthCredentialState,
    McpOAuthCredentialView,
    McpOAuthErrorCode,
    McpOAuthStorageErrorCode,
)

McpAuthorizationState = typing.Literal[
    "unknown", "unsupported", "header", "bearer", "oauth", "anonymous",
    "not_logged_in", "reauthorization_required", "unavailable",
]


@dataclass(frozen=True, slots=True)
class McpAuthorizationStatus:
    """统一投影配置、本地凭据和最后一次认证观察，不持有凭据或授予工具调用权限。

    存储和连接适配器生成事实，前端只消费；accepted 是已有请求的结果，不代表实时探测。
    generation 只用于拒绝旧凭据版本的观察，配置身份隔离仍由运行时负责。
    """

    state: McpAuthorizationState = "unknown"
    credentials: McpOAuthCredentialState | None = None
    verification: typing.Literal["unverified", "accepted", "rejected"] = "unverified"
    generation: int | None = None
    expires_at: float | None = None
    error: McpOAuthErrorCode | McpOAuthStorageErrorCode | None = None


def authorization_from_credentials(
    view: McpOAuthCredentialView, previous: McpAuthorizationStatus,
) -> McpAuthorizationStatus:
    """合并同一身份的本地事实，版本改变后撤销旧认证结论；不以缺少凭据断言服务需要登录。"""
    if view.generation is not None and previous.generation is not None and view.generation < previous.generation:
        return previous
    state: McpAuthorizationState
    if view.state in ("stored", "expired"):
        state = "oauth"
    elif view.state in ("refresh_uncertain", "reauthorization_required"):
        state = "reauthorization_required"
    elif view.state == "unavailable":
        state = "unavailable"
    elif view.state == "registered" or (view.generation is not None and view.generation > 0):
        state = "not_logged_in"
    elif previous.state in ("anonymous", "not_logged_in", "reauthorization_required"):
        state = previous.state
    else:
        state = "unknown"
    same_version = view.generation is not None and view.generation == previous.generation
    if same_version and previous.state == "reauthorization_required" and view.state != "unavailable":
        state = previous.state
    previous_error = previous.error if previous.state != "unavailable" else None
    return McpAuthorizationStatus(
        state=state, credentials=view.state,
        verification=previous.verification if same_version else "unverified",
        generation=view.generation, expires_at=view.expires_at,
        error=view.error or (previous_error if same_version else None),
    )


def authorization_failed(
    status: McpAuthorizationStatus, error: McpOAuthErrorCode | McpOAuthStorageErrorCode,
) -> McpAuthorizationStatus:
    """投影固定认证失败代码，不把网络故障或存储故障伪装为远端拒绝。"""
    state = status.state
    storage_failure = error in ("storage_unavailable", "storage_corrupt", "storage_busy", "credential_conflict")
    if state in ("header", "bearer", "unsupported"):
        pass
    elif error in ("reauthorization_required", "insufficient_scope", "refresh_uncertain"):
        state = "reauthorization_required"
    elif error == "login_required" and state != "reauthorization_required":
        state = "not_logged_in"
    elif storage_failure:
        state = "unavailable"
    return replace(
        status, state=state, error=error,
        credentials="unavailable" if storage_failure else status.credentials,
        verification="rejected" if error in ("login_required", "insufficient_scope") else "unverified",
    )


def authorization_accepted(status: McpAuthorizationStatus) -> McpAuthorizationStatus:
    """记录已完成请求的认证结果，匿名成功不被标记为 OAuth 登录。"""
    if status.state == "unsupported":
        return status
    return replace(
        status, state="anonymous" if status.state in ("unknown", "not_logged_in") else status.state,
        verification="accepted", error=None,
    )


if __name__ == '__main__':
    pass
