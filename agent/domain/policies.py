# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import (
    dataclass,
    field,
)

from protocol.schema.permissions import (
    ApprovalPolicy,
    ApprovalReviewer,
    NetworkAccess,
    SandboxMode,
    normalize_approval_policy,
    normalize_approval_reviewer,
    normalize_network_access,
    normalize_sandbox_mode,
)

PermissionPreset = typing.Literal[
    "read-only",
    "auto",
    "full-access",
    "custom",
]


@dataclass(frozen=True, slots=True)
class PermissionSettings(object):
    """描述一次运行实际采用的沙箱和审批策略。"""
    sandbox_mode: SandboxMode
    approval_policy: ApprovalPolicy
    display_label: str | None = field(default=None, compare=False)
    approvals_reviewer: ApprovalReviewer = "user"
    network_access: NetworkAccess = "restricted"

    @property
    def preset(self) -> PermissionPreset:
        """返回与当前设置匹配的用户预设。"""
        pair = (self.sandbox_mode, self.approval_policy)
        if pair == ("read-only", "on-request"):
            return "read-only"
        if pair == ("workspace-write", "on-request"):
            return "auto"
        if pair == ("danger-full-access", "never"):
            return "full-access"
        return "custom"


def preset_permissions(
    preset: PermissionPreset,
    *,
    approvals_reviewer: ApprovalReviewer = "user",
    display_label: str | None = None
) -> PermissionSettings:
    """返回用户预设对应的权限设置。"""
    if preset == "read-only":
        return PermissionSettings(
            sandbox_mode="read-only",
            approval_policy="on-request",
            display_label=display_label,
            approvals_reviewer=approvals_reviewer,
        )
    if preset == "auto":
        return PermissionSettings(
            sandbox_mode="workspace-write",
            approval_policy="on-request",
            display_label=display_label,
            approvals_reviewer=approvals_reviewer,
        )
    if preset == "full-access":
        return PermissionSettings(
            sandbox_mode="danger-full-access",
            approval_policy="never",
            display_label=display_label,
            approvals_reviewer=approvals_reviewer,
        )
    raise ValueError(f"unsupported permission preset: {preset}")


def resolve_permissions(
    config: typing.Any,
    *,
    interactive: bool
) -> PermissionSettings:
    """根据配置和入口类型解析有效权限。"""
    data = config if isinstance(config, dict) else {}

    default_sandbox: SandboxMode = "workspace-write" if interactive else "read-only"
    default_approval: ApprovalPolicy = "on-request" if interactive else "never"

    sandbox_value = data.get("sandbox_mode") or default_sandbox
    approval_value = data.get("approval_policy") or default_approval
    reviewer_value = data.get("approvals_reviewer") or "user"
    network_value = data.get("network_access") or "restricted"

    return PermissionSettings(
        sandbox_mode=normalize_sandbox_mode(sandbox_value),
        approval_policy=normalize_approval_policy(approval_value),
        approvals_reviewer=normalize_approval_reviewer(reviewer_value),
        network_access=normalize_network_access(network_value),
    )


def permission_label(settings: PermissionSettings) -> str:
    """返回 footer 和命令输出使用的权限名称。"""
    if settings.display_label:
        return settings.display_label
    if settings.preset == "auto" and settings.approvals_reviewer == "auto_review":
        return "Approve for me"
    labels: dict[PermissionPreset, str] = {
        "read-only": "Read Only",
        "auto": "Ask for approval",
        "full-access": "Full Access",
        "custom": "Custom",
    }
    return labels[settings.preset]


if __name__ == '__main__':
    pass
