# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

SandboxMode = typing.Literal[
    "read-only",
    "workspace-write",
    "danger-full-access",
]

ApprovalPolicy = typing.Literal[
    "untrusted",
    "on-request",
    "never",
]

ApprovalReviewer = typing.Literal[
    "user",
    "auto_review",
]

NetworkAccess = typing.Literal[
    "restricted",
    "enabled",
]


class GranularApprovalConfig(typing.TypedDict):
    """描述请求协议中按动作类别配置的审批策略。"""

    sandbox_approval: bool
    rules: bool
    skill_approval: bool
    request_permissions: bool
    mcp_elicitations: bool


class GranularApprovalPolicy(typing.TypedDict):
    """封装请求协议的细粒度审批策略。"""

    granular: GranularApprovalConfig


RequestApprovalPolicy = ApprovalPolicy | GranularApprovalPolicy


class PermissionPayload(typing.TypedDict):
    """描述 AgentRequest 顶层的结构化权限字段。"""

    sandbox_mode: SandboxMode
    approval_policy: RequestApprovalPolicy
    approvals_reviewer: ApprovalReviewer
    network_access: NetworkAccess


SANDBOX_MODES: tuple[SandboxMode, ...] = (
    "read-only",
    "workspace-write",
    "danger-full-access",
)

APPROVAL_POLICIES: tuple[ApprovalPolicy, ...] = (
    "untrusted",
    "on-request",
    "never",
)

APPROVAL_REVIEWERS: tuple[ApprovalReviewer, ...] = (
    "user",
    "auto_review",
)

NETWORK_ACCESS_VALUES: tuple[NetworkAccess, ...] = (
    "restricted",
    "enabled",
)


def normalize_sandbox_mode(value: typing.Any) -> SandboxMode:
    """规范化请求协议使用的沙箱模式。"""
    normalized = str(value or "").strip().lower()
    if normalized == "read-only":
        return "read-only"
    if normalized == "workspace-write":
        return "workspace-write"
    if normalized == "danger-full-access":
        return "danger-full-access"
    raise ValueError(f"invalid sandbox mode: {value}")


def normalize_approval_policy(value: typing.Any) -> ApprovalPolicy:
    """规范化本地执行策略使用的字符串审批模式。"""
    normalized = str(value or "").strip().lower()
    if normalized == "untrusted":
        return "untrusted"
    if normalized == "on-request":
        return "on-request"
    if normalized == "never":
        return "never"
    raise ValueError(f"invalid approval policy: {value}")


def normalize_request_approval_policy(value: typing.Any) -> RequestApprovalPolicy:
    """校验 AgentRequest 的字符串或细粒度审批策略。"""
    if not isinstance(value, dict):
        return normalize_approval_policy(value)
    if set(value) != {"granular"}:
        raise ValueError("granular approval policy contains unknown fields")
    granular = value.get("granular")
    if not isinstance(granular, dict):
        raise TypeError("granular approval policy must be an object")
    allowed = {
        "sandbox_approval",
        "rules",
        "skill_approval",
        "request_permissions",
        "mcp_elicitations",
    }
    unknown = sorted(set(granular).difference(allowed))
    if unknown:
        raise ValueError(
            "granular approval policy contains unknown fields: "
            + ", ".join(unknown)
        )
    required = {"sandbox_approval", "rules", "mcp_elicitations"}
    missing = sorted(required.difference(granular))
    if missing:
        raise ValueError(
            "granular approval policy is missing fields: "
            + ", ".join(missing)
        )
    normalized: GranularApprovalConfig = {
        "sandbox_approval": _approval_flag(
            granular.get("sandbox_approval"),
            name="sandbox_approval",
        ),
        "rules": _approval_flag(granular.get("rules"), name="rules"),
        "skill_approval": _approval_flag(
            granular.get("skill_approval", False),
            name="skill_approval",
        ),
        "request_permissions": _approval_flag(
            granular.get("request_permissions", False),
            name="request_permissions",
        ),
        "mcp_elicitations": _approval_flag(
            granular.get("mcp_elicitations"),
            name="mcp_elicitations",
        ),
    }
    return GranularApprovalPolicy(granular=normalized)


def normalize_approval_reviewer(value: typing.Any) -> ApprovalReviewer:
    """规范化审批裁决方。"""
    normalized = str(value or "user").strip().lower()
    if normalized == "user":
        return "user"
    if normalized == "auto_review":
        return "auto_review"
    raise ValueError(f"invalid approval reviewer: {value}")


def normalize_network_access(value: typing.Any) -> NetworkAccess:
    """规范化客户端本地网络访问策略。"""
    normalized = str(value or "restricted").strip().lower()
    if normalized == "restricted":
        return "restricted"
    if normalized == "enabled":
        return "enabled"
    raise ValueError(f"invalid network access: {value}")


def permission_payload(value: typing.Any) -> PermissionPayload:
    """把权限设置转换为 AgentRequest 顶层结构化字段。"""
    if isinstance(value, dict):
        sandbox_mode = value.get("sandbox_mode", "workspace-write")
        approval_policy = value.get("approval_policy", "on-request")
        approvals_reviewer = value.get("approvals_reviewer", "user")
        network_access = value.get("network_access", "restricted")
    else:
        sandbox_mode = getattr(value, "sandbox_mode", "workspace-write")
        approval_policy = getattr(value, "approval_policy", "on-request")
        approvals_reviewer = getattr(value, "approvals_reviewer", "user")
        network_access = getattr(value, "network_access", "restricted")

    return PermissionPayload(
        sandbox_mode=normalize_sandbox_mode(sandbox_mode),
        approval_policy=normalize_request_approval_policy(approval_policy),
        approvals_reviewer=normalize_approval_reviewer(approvals_reviewer),
        network_access=normalize_network_access(network_access),
    )


def _approval_flag(value: typing.Any, *, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"granular approval policy {name} must be a boolean")
    return value
