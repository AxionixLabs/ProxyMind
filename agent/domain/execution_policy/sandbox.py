# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import Mapping

__all__ = (
    "SandboxPermission",
    "effective_sandbox_mode",
    "normalize_sandbox_permission",
    "validate_sandbox_permission_arguments",
)


SandboxPermission: typing.TypeAlias = typing.Literal[
    "use_default",
    "require_escalated",
    "with_additional_permissions",
]


def normalize_sandbox_permission(value: object) -> SandboxPermission:
    """规范化单次本地执行的沙箱权限覆盖。"""
    normalized = str(value or "use_default").strip().casefold()
    if normalized == "use_default":
        return "use_default"
    if normalized == "require_escalated":
        return "require_escalated"
    if normalized == "with_additional_permissions":
        return "with_additional_permissions"
    raise ValueError(
        "sandbox_permissions must be 'use_default', 'require_escalated', "
        "or 'with_additional_permissions'"
    )


def validate_sandbox_permission_arguments(
    arguments: Mapping[str, object],
) -> SandboxPermission:
    """校验权限覆盖与附加权限的配对，审批说明不参与权限判定。"""
    permission = normalize_sandbox_permission(arguments.get("sandbox_permissions"))
    additional = arguments.get("additional_permissions")
    if permission == "with_additional_permissions":
        if not isinstance(additional, dict):
            raise ValueError(
                "additional permissions are required for with_additional_permissions"
            )
    elif additional is not None:
        raise ValueError(
            "additional permissions require with_additional_permissions"
        )
    return permission


def effective_sandbox_mode(
    sandbox_mode: str,
    sandbox_permissions: object = "use_default",
) -> str:
    """根据单次权限覆盖返回本地执行器实际使用的沙箱模式。"""
    permission = normalize_sandbox_permission(sandbox_permissions)
    if permission == "require_escalated":
        return "danger-full-access"
    return str(sandbox_mode or "workspace-write")


if __name__ == '__main__':
    pass
