# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from mind_nova.requests.permissions import (
    ApprovalPolicy,
    SandboxMode,
    normalize_approval_policy,
    normalize_sandbox_mode
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


def preset_permissions(preset: PermissionPreset) -> PermissionSettings:
    """返回用户预设对应的权限设置。"""
    if preset == "read-only":
        return PermissionSettings("read-only", "on-request")
    if preset == "auto":
        return PermissionSettings("workspace-write", "on-request")
    if preset == "full-access":
        return PermissionSettings("danger-full-access", "never")
    raise ValueError(f"unsupported permission preset: {preset}")


def resolve_permissions(
    config: typing.Any,
    *,
    interactive: bool
) -> PermissionSettings:
    """根据配置和入口类型解析有效权限。"""
    data = config if isinstance(config, dict) else {}

    default_sandbox: SandboxMode     = "workspace-write" if interactive else "read-only"
    default_approval: ApprovalPolicy = "on-request" if interactive else "never"

    sandbox_value  = data.get("sandbox_mode") or default_sandbox
    approval_value = data.get("approval_policy") or default_approval

    return PermissionSettings(
        sandbox_mode=normalize_sandbox_mode(sandbox_value),
        approval_policy=normalize_approval_policy(approval_value),
    )


def permission_label(settings: PermissionSettings) -> str:
    """返回 footer 和命令输出使用的权限名称。"""
    labels: dict[PermissionPreset, str] = {
        "read-only": "Read Only",
        "auto": "Auto",
        "full-access": "Full Access",
        "custom": "Custom",
    }
    return labels[settings.preset]


if __name__ == '__main__':
    pass
