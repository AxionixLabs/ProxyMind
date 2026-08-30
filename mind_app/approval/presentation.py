# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import copy
from dataclasses import dataclass
from mind_app.presentation.models import PatchView
from mind_app.presentation.patch_views import build_patch_start_view
from mind_app.presentation.stream.approval_trace import (
    approval_shell_commands,
    approval_summary
)
from .models import (
    ApprovalDecisionValue,
    ApprovalRequestKey,
    ApprovalRequestKind,
    ExecPolicyAmendmentProposal
)
from .policy import (
    approval_decisions,
    approval_execpolicy_amendment,
    approval_prompt
)

ApprovalCommand: typing.TypeAlias = str | tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ApprovalPresentationContext(object):
    """保存所有审批展示类型共享的身份和文案。"""
    request_id: str
    approval_id: str
    call_id: str
    kind: ApprovalRequestKind
    tool: str
    prompt: str
    decisions: tuple[ApprovalDecisionValue, ...]
    environment: str = ""
    justification: str = ""
    agent_id: str = ""
    agent_type: str = ""
    agent_depth: int | None = None


@dataclass(frozen=True, slots=True)
class ExecApprovalPresentation(object):
    """保存命令类审批的展示数据。"""
    context: ApprovalPresentationContext
    commands: tuple[ApprovalCommand, ...]
    summary: str
    amendment: ExecPolicyAmendmentProposal | None = None
    additional_permissions: dict[str, typing.Any] | None = None

    def __post_init__(self) -> None:
        """复制附加权限资料，避免展示状态被外部修改。"""
        if self.additional_permissions is not None:
            object.__setattr__(
                self,
                "additional_permissions",
                copy.deepcopy(self.additional_permissions),
            )


@dataclass(frozen=True, slots=True)
class ApplyPatchApprovalPresentation(object):
    """保存补丁审批的展示数据。"""
    context: ApprovalPresentationContext
    patch: str
    summary: str
    patch_view: PatchView | None = None


@dataclass(frozen=True, slots=True)
class ToolApprovalPresentation(object):
    """保存尚未细分动作的工具审批展示数据。"""
    context: ApprovalPresentationContext
    operations: tuple[ApprovalCommand, ...]
    summary: str


@dataclass(frozen=True, slots=True)
class RequestPermissionsApprovalPresentation(object):
    """保存权限申请卡片的展示数据。"""
    context: ApprovalPresentationContext
    permissions: dict[str, typing.Any]
    summary: str

    def __post_init__(self) -> None:
        """复制权限资料，避免展示状态被外部修改。"""
        object.__setattr__(self, "permissions", copy.deepcopy(self.permissions))


ApprovalPresentation: typing.TypeAlias = (
    ExecApprovalPresentation
    | ApplyPatchApprovalPresentation
    | RequestPermissionsApprovalPresentation
    | ToolApprovalPresentation
)


def ensure_approval_presentation(
    value: ApprovalPresentation | dict[str, typing.Any]
) -> ApprovalPresentation:
    """在展示边界把原始载荷转换为按类型区分的请求。"""
    if isinstance(
        value,
        (
            ExecApprovalPresentation,
            ApplyPatchApprovalPresentation,
            RequestPermissionsApprovalPresentation,
            ToolApprovalPresentation,
        ),
    ):
        return value
    if isinstance(value, dict):
        return build_approval_presentation(value)
    raise TypeError("approval presentation must be a typed request or object")


def build_approval_presentation(
    payload: dict[str, typing.Any],
    *,
    key: ApprovalRequestKey | None = None,
    kind: ApprovalRequestKind | None = None,
    decisions: tuple[ApprovalDecisionValue, ...] | None = None
) -> ApprovalPresentation:
    """把原始审批载荷一次转换为按动作区分的不可变展示对象。"""
    normalized    = dict(payload)
    resolved_kind = kind or approval_request_kind(normalized)
    resolved_key  = key or _request_key(normalized, resolved_kind)

    if decisions is None:
        resolved_decisions: tuple[ApprovalDecisionValue, ...] = tuple(
            approval_decisions(normalized)
        )
    else:
        resolved_decisions = decisions

    context = _presentation_context(
        normalized,
        key=resolved_key,
        kind=resolved_kind,
        decisions=resolved_decisions,
    )
    summary = _approval_summary(normalized, resolved_kind)

    if resolved_kind in {"command", "write_stdin", "network_access"}:
        additional_permissions = normalized.get("additional_permissions")
        if not isinstance(additional_permissions, dict):
            additional_permissions = None
        return ExecApprovalPresentation(
            context=context,
            commands=_command_values(normalized),
            summary=summary,
            amendment=approval_execpolicy_amendment(normalized),
            additional_permissions=additional_permissions,
        )

    if resolved_kind == "apply_patch":
        patch, patch_view = _patch_values(normalized, context=context)
        return ApplyPatchApprovalPresentation(
            context=context,
            patch=patch,
            summary=summary,
            patch_view=patch_view,
        )

    if resolved_kind == "request_permissions":
        permissions = normalized.get("permissions")
        if not isinstance(permissions, dict):
            permissions = {}
        return RequestPermissionsApprovalPresentation(
            context=context,
            permissions=permissions,
            summary=summary,
        )

    return ToolApprovalPresentation(
        context=context,
        operations=_tool_operation_values(normalized),
        summary=summary,
    )


def _approval_summary(
    payload: dict[str, typing.Any],
    kind: ApprovalRequestKind,
) -> str:
    """按动作类别生成卡片操作摘要。"""
    if kind == "request_permissions":
        permissions = payload.get("permissions")
        if isinstance(permissions, dict):
            parts: list[str] = []
            if isinstance(permissions.get("network"), dict):
                if permissions["network"].get("enabled") is True:
                    parts.append("network")
            if isinstance(permissions.get("file_system"), dict):
                entries = permissions["file_system"].get("entries")
                if isinstance(entries, list):
                    grouped: dict[str, list[str]] = {
                        "read": [],
                        "write": [],
                        "deny": [],
                    }
                    for item in entries:
                        if not isinstance(item, dict):
                            continue
                        path = _permission_path(item.get("path"))
                        access = str(item.get("access") or "").strip().casefold()
                        if path and access in grouped:
                            grouped[access].append(path)
                    non_empty = [
                        (access, paths)
                        for access, paths in grouped.items()
                        if paths
                    ]
                    for access, paths in non_empty:
                        label = {
                            "read": "read",
                            "write": "write",
                            "deny": "deny read",
                        }[access]
                        parts.append(f"{label} " + ", ".join(paths))
            if parts:
                return "; ".join(parts)
        return "additional permissions"
    if kind == "mcp_tool_call":
        server = _text(payload.get("server"))
        title = _text(payload.get("tool_title")) or _text(payload.get("tool_name"))
        return ": ".join(value for value in (server, title) if value) or "MCP tool call"
    return approval_summary(payload)


def approval_request_kind(payload: dict[str, typing.Any]) -> ApprovalRequestKind:
    """按显式 kind 和工具名归一化审批类别。"""
    raw_kind = _text(payload.get("kind")).lower()
    if raw_kind in {"exec", "execve", "command"}:
        return "command"
    if raw_kind == "write_stdin":
        return "write_stdin"
    if raw_kind == "network_access":
        return "network_access"
    if raw_kind in {"apply_patch", "patch", "file_change"}:
        return "apply_patch"
    if raw_kind in {"permissions", "permission", "request_permissions"}:
        return "request_permissions"
    if raw_kind in {"mcp", "mcp_elicitation", "mcp_tool_call"}:
        return "mcp_tool_call"

    tool = _text(payload.get("tool")).lower()
    if tool in {"shell_command", "exec_command"}:
        return "command"
    if tool == "write_stdin":
        return "write_stdin"
    if tool in {"apply_patch", "patch"}:
        return "apply_patch"
    if "permission" in tool:
        return "request_permissions"
    if tool.startswith("mcp"):
        return "mcp_tool_call"
    return "command"


def _presentation_context(
    payload: dict[str, typing.Any],
    *,
    key: ApprovalRequestKey,
    kind: ApprovalRequestKind,
    decisions: tuple[ApprovalDecisionValue, ...],
) -> ApprovalPresentationContext:
    """构造展示类型共享的字段。"""
    environment = _text(
        payload.get("environment")
        or payload.get("environment_id")
    ).replace("_", " ")

    justification = _text(
        payload.get("justification")
        or payload.get("reason")
    )
    agent_depth   = payload.get("agent_depth")

    if isinstance(agent_depth, bool) or not isinstance(agent_depth, int):
        agent_depth = None

    return ApprovalPresentationContext(
        request_id=key.request_id,
        approval_id=key.approval_id,
        call_id=key.call_id,
        kind=kind,
        tool=key.tool,
        prompt=_presentation_prompt(payload, kind),
        decisions=decisions,
        environment=environment,
        justification=justification,
        agent_id=_text(payload.get("agent_id")),
        agent_type=_text(payload.get("agent_type")) or "agent",
        agent_depth=agent_depth,
    )


def _presentation_prompt(
    payload: dict[str, typing.Any],
    kind: ApprovalRequestKind,
) -> str:
    """按规范化动作类别生成审批标题。"""
    if kind in {"command", "write_stdin", "network_access"}:
        if kind == "network_access":
            host = _text(payload.get("host")) or "the requested host"
            return f'Do you want to approve network access to "{host}"?'
        if kind == "write_stdin":
            return "Would you like to send the following input?"
        return "Would you like to run the following command?"
    if kind == "apply_patch":
        return "Would you like to make the following edits?"
    if kind == "request_permissions":
        return "Would you like to grant these permissions?"
    return approval_prompt(payload)


def _request_key(
    payload: dict[str, typing.Any],
    kind: ApprovalRequestKind,
) -> ApprovalRequestKey:
    """从独立载荷构造展示测试所需的稳定身份。"""
    approval_id = _text(payload.get("approval_id") or payload.get("id"))
    call_id     = _text(payload.get("call_id"))

    request_id = _text(
        payload.get("request_id")
        or payload.get("requestId")
        or approval_id
        or call_id
    )

    tool = _text(payload.get("tool"))
    if not tool:
        tool = {
            "network_access": "exec_command",
            "request_permissions": "request_permissions",
            "mcp_tool_call": "mcp_tool_call",
        }.get(str(payload.get("kind") or ""), "shell_command")

    return ApprovalRequestKey(
        request_id=request_id,
        approval_id=approval_id,
        call_id=call_id,
        tool=tool,
        kind=kind,
    )


def _command_values(
    payload: dict[str, typing.Any],
) -> tuple[ApprovalCommand, ...]:
    """读取命令审批中的 shell 操作并转换为不可变值。"""
    return tuple(_command_value(value) for value in approval_shell_commands(payload))


def _tool_operation_values(
    payload: dict[str, typing.Any],
) -> tuple[ApprovalCommand, ...]:
    """读取未细分工具审批中的操作并转换为不可变值。"""
    tool = _text(payload.get("tool")).lower()
    if tool in {"shell_command", "exec_command", "write_stdin"}:
        return _command_values(payload)

    fallback = payload.get("patch")
    if fallback in (None, ""):
        fallback = payload.get("command", payload.get("resolved_command"))
    if fallback in (None, ""):
        return ()
    return (_command_value(fallback),)


def _patch_values(
    payload: dict[str, typing.Any],
    *,
    context: ApprovalPresentationContext
) -> tuple[str, PatchView | None]:
    """读取补丁正文并在有结构化预览时构造补丁视图。"""
    arguments      = payload.get("arguments")
    arguments_dict = dict(arguments) if isinstance(arguments, dict) else {}
    raw_patch      = payload.get("patch", arguments_dict.get("patch", ""))
    patch          = _command_text(raw_patch)

    preview = payload.get("preview")
    if not isinstance(preview, dict):
        return patch, None

    try:
        view = build_patch_start_view(
            arguments_dict or {"patch": patch},
            preview_data=preview,
            call_id=context.call_id or context.approval_id or "preview",
        )
    except (KeyError, TypeError, ValueError):
        view = None
    return patch, view


def _command_value(value: typing.Any) -> ApprovalCommand:
    """把单项操作转换为字符串或不可变参数元组。"""
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return str(value or "").strip()


def _command_text(value: typing.Any) -> str:
    """把操作值转换为补丁正文文本。"""
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value)
    return str(value or "").strip()


def _permission_path(value: typing.Any) -> str:
    """将结构化文件路径转换为卡片摘要文本。"""
    if isinstance(value, str):
        text = value.strip()
        return f"`{text}`" if text else ""
    if not isinstance(value, dict):
        return ""
    path_type = str(value.get("type") or "").strip()
    if path_type == "path":
        text = _text(value.get("path"))
        return f"`{text}`" if text else ""
    if path_type == "glob_pattern":
        text = _text(value.get("pattern"))
        return f"glob `{text}`" if text else ""
    if path_type == "special":
        nested = value.get("value")
        if isinstance(nested, dict):
            text = _text(nested.get("path") or nested.get("subpath"))
            return f"`{text}`" if text else ""
    text = _text(value.get("path") or value.get("pattern"))
    return f"`{text}`" if text else ""


def _text(value: typing.Any) -> str:
    """把可选字段转换为去除首尾空白的文本。"""
    return str(value or "").strip()


if __name__ == '__main__':
    pass
