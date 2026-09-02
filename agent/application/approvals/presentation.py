# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import json
import math
import typing
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlparse

from agent.application.approvals.amendments import (
    ExecPolicyAmendmentProposal,
    approval_execpolicy_amendment,
)
from agent.application.views import PatchView
from agent.application.views.builders.patch import build_patch_start_view
from agent.domain.approvals import (
    McpApprovalRisk,
    McpToolAnnotations,
    mcp_approval_risk,
)
from .models import (
    ApprovalDecisionValue,
    ApprovalRequestKey,
    ApprovalRequestKind,
)
from .policy import (
    approval_decisions,
    approval_prompt,
)
from .summary import (
    approval_shell_commands,
    approval_summary,
)

ApprovalCommand: typing.TypeAlias = str | tuple[str, ...]

MCP_ARGUMENT_FIELD_LIMIT = 12
MCP_ARGUMENT_VALUE_WIDTH = 120
MCP_ARGUMENT_BYTE_LIMIT = 2048
MCP_ARGUMENT_NESTING_LIMIT = 2
_MCP_SENSITIVE_KEYS = (
    "token",
    "secret",
    "password",
    "authorization",
    "apikey",
    "accesstoken",
    "privatekey",
    "credential",
)


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
    network_target: str | None = None

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
class McpArgumentPresentation(object):
    """保存一个已经脱敏、折叠和限长的 MCP 参数字段。"""
    name: str
    value: str


@dataclass(frozen=True, slots=True)
class McpApprovalPresentation(object):
    """保存 MCP 工具审批卡使用的完整结构化展示数据。"""
    context: ApprovalPresentationContext
    server: str
    tool_name: str
    title: str
    description: str
    arguments: tuple[McpArgumentPresentation, ...]
    argument_count: int
    omitted_arguments: int
    arguments_truncated: bool
    risk: McpApprovalRisk
    connector: str
    account: str
    source_verified: bool
    degraded: bool
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
    | McpApprovalPresentation
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
                McpApprovalPresentation,
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
    normalized = dict(payload)
    resolved_kind = kind or approval_request_kind(normalized)
    resolved_key = key or _request_key(normalized, resolved_kind)

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
            network_target=(
                _network_target_label(normalized)
                if resolved_kind == "network_access"
                else None
            ),
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

    if resolved_kind == "mcp_tool_call":
        return _mcp_approval_presentation(
            normalized,
            context=context,
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


def _network_target_label(payload: dict[str, typing.Any]) -> str | None:
    """生成网络审批卡使用的规范化目标标签。"""
    host = _text(payload.get("host"))
    protocol = _text(payload.get("protocol")).casefold()
    if not host:
        target = _text(payload.get("target"))
        parsed = urlparse(target)
        host = _text(parsed.hostname)
        protocol = protocol or parsed.scheme.casefold()
        port = parsed.port
    else:
        raw_port = payload.get("port")
        port = raw_port if isinstance(raw_port, int) and not isinstance(raw_port, bool) else None
    if not host:
        return None
    protocol = protocol or "https"
    if port is None:
        port = {
            "http": 80,
            "https": 443,
            "socks5": 1080,
            "socks5_tcp": 1080,
            "socks5_udp": 1080,
        }.get(protocol)
    return f"{protocol}://{host}:{port}" if port is not None else f"{protocol}://{host}"


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
    agent_depth = payload.get("agent_depth")

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
    if kind == "mcp_tool_call":
        return "Would you like to approve the following MCP tool call?"
    return approval_prompt(payload)


def _mcp_approval_presentation(
    payload: dict[str, typing.Any],
    *,
    context: ApprovalPresentationContext,
    summary: str,
) -> McpApprovalPresentation:
    """把经过边界校验的 MCP 字段投影为不可变安全展示。"""
    server = _mcp_display_text(payload.get("server"))
    tool_name = _mcp_display_text(payload.get("tool_name"))
    title = _mcp_display_text(payload.get("tool_title"))
    description = _mcp_display_text(payload.get("tool_description"))
    arguments, argument_count, omitted, truncated, arguments_degraded = (
        _mcp_argument_presentations(payload.get("arguments"))
    )
    risk, risk_degraded = _mcp_risk(payload.get("annotations"))
    identity_degraded = not server or not tool_name
    if identity_degraded or arguments_degraded:
        risk = McpApprovalRisk.UNKNOWN

    connector = _mcp_display_text(
        payload.get("connector_name") or payload.get("connector_id")
    )
    account = _mcp_display_text(payload.get("connected_account_email"))
    return McpApprovalPresentation(
        context=context,
        server=server or "unknown",
        tool_name=tool_name or "unknown",
        title=title,
        description=description,
        arguments=arguments,
        argument_count=argument_count,
        omitted_arguments=omitted,
        arguments_truncated=truncated,
        risk=risk,
        connector=connector or "unverified",
        account=account or "unverified",
        source_verified=bool(connector),
        degraded=identity_degraded or arguments_degraded or risk_degraded,
        summary=summary,
    )


def _mcp_argument_presentations(
    raw_arguments: typing.Any,
) -> tuple[tuple[McpArgumentPresentation, ...], int, int, bool, bool]:
    """生成受字段数、显示宽度、嵌套深度和字节预算约束的参数摘要。"""
    if isinstance(raw_arguments, dict):
        invalid_keys = any(not isinstance(key, str) for key in raw_arguments)
        items = sorted(
            ((str(key), value) for key, value in raw_arguments.items()),
            key=lambda item: item[0],
        )
    elif _mcp_json_scalar(raw_arguments) or isinstance(raw_arguments, list):
        invalid_keys = False
        items = [] if raw_arguments is None else [("value", raw_arguments)]
    else:
        return (), 0, 0, False, True

    total = len(items)
    fields: list[McpArgumentPresentation] = []
    byte_budget = MCP_ARGUMENT_BYTE_LIMIT
    truncated = False
    degraded = invalid_keys

    for name, value in items[:MCP_ARGUMENT_FIELD_LIMIT]:
        safe_name = _mcp_json_name(name)
        separator_bytes = 1 if fields else 0
        safe_value, value_degraded = _mcp_argument_json(
            "[redacted]" if _mcp_sensitive_key(name) else value,
            depth=0,
        )
        degraded = degraded or value_degraded
        display_value = _clip_mcp_display_value(
            safe_value,
            display_width=MCP_ARGUMENT_VALUE_WIDTH,
            byte_limit=max(
                0,
                byte_budget
                - separator_bytes
                - len(safe_name.encode("utf-8"))
                - 2,
            ),
        )
        if display_value != safe_value:
            truncated = True
        entry_bytes = separator_bytes + len(
            f"{safe_name}: {display_value}".encode("utf-8")
        )
        if entry_bytes > byte_budget:
            truncated = True
            break
        fields.append(McpArgumentPresentation(safe_name, display_value))
        byte_budget -= entry_bytes

    omitted = total - len(fields)
    if omitted:
        truncated = True
    return tuple(fields), total, omitted, truncated, degraded


def _mcp_argument_json(value: typing.Any, *, depth: int) -> tuple[str, bool]:
    """递归脱敏结构化参数，并返回稳定 JSON 展示。"""
    sanitized, degraded = _mcp_sanitized_json_value(value, depth=depth)
    try:
        return json.dumps(
            sanitized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(", ", ": "),
            allow_nan=False,
        ), degraded
    except (TypeError, ValueError):
        return '"unknown"', True


def _mcp_sanitized_json_value(
    value: typing.Any,
    *,
    depth: int,
) -> tuple[typing.Any, bool]:
    """递归脱敏 JSON 值，并在深度上限处折叠集合。"""
    if isinstance(value, dict):
        if depth >= MCP_ARGUMENT_NESTING_LIMIT:
            return f"[{len(value)} fields]", False
        result: dict[str, typing.Any] = {}
        degraded = False
        for raw_key, nested in sorted(value.items(), key=lambda item: str(item[0])):
            key = str(raw_key)
            if not isinstance(raw_key, str):
                degraded = True
            if _mcp_sensitive_key(key):
                result[key] = "[redacted]"
                continue
            safe_nested, nested_degraded = _mcp_sanitized_json_value(
                nested,
                depth=depth + 1,
            )
            result[key] = safe_nested
            degraded = degraded or nested_degraded
        return result, degraded
    if isinstance(value, list):
        if depth >= MCP_ARGUMENT_NESTING_LIMIT:
            return f"[{len(value)} items]", False
        result_list: list[typing.Any] = []
        degraded = False
        for nested in value:
            safe_nested, nested_degraded = _mcp_sanitized_json_value(
                nested,
                depth=depth + 1,
            )
            result_list.append(safe_nested)
            degraded = degraded or nested_degraded
        return result_list, degraded
    if _mcp_json_scalar(value):
        if isinstance(value, float) and not math.isfinite(value):
            return "unknown", True
        return value, False
    return "unknown", True


def _mcp_json_scalar(value: typing.Any) -> bool:
    """判断值是否属于 JSON 标量。"""
    return value is None or isinstance(value, (str, int, float, bool))


def _mcp_sensitive_key(value: str) -> bool:
    """按大小写不敏感的规范键名识别凭据字段。"""
    normalized = "".join(
        char for char in str(value).casefold() if char.isalnum()
    )
    return any(token in normalized for token in _MCP_SENSITIVE_KEYS)


def _mcp_json_name(value: str) -> str:
    """把参数键名转换为不会携带控制字符的单行文本。"""
    encoded = json.dumps(str(value), ensure_ascii=False)
    return encoded[1:-1]


def _mcp_display_text(value: typing.Any) -> str:
    """把 MCP 元数据转换为无终端控制符的单行展示文本。"""
    parts: list[str] = []
    for char in str(value or "").strip():
        codepoint = ord(char)
        if char.isspace():
            parts.append(" ")
        elif codepoint < 32 or 127 <= codepoint <= 159:
            parts.append(f"\\u{codepoint:04x}")
        else:
            parts.append(char)
    return " ".join("".join(parts).split())


def _mcp_risk(raw_annotations: typing.Any) -> tuple[McpApprovalRisk, bool]:
    """按领域优先级从严格布尔注解生成风险展示。"""
    if raw_annotations is None:
        return McpApprovalRisk.UNKNOWN, False
    if not isinstance(raw_annotations, dict):
        return McpApprovalRisk.UNKNOWN, True
    allowed = {"read_only_hint", "destructive_hint", "open_world_hint"}
    if set(raw_annotations) - allowed:
        return McpApprovalRisk.UNKNOWN, True
    values: dict[str, bool | None] = {}
    for name in allowed:
        value = raw_annotations.get(name)
        if value is not None and not isinstance(value, bool):
            return McpApprovalRisk.UNKNOWN, True
        values[name] = value
    annotations = McpToolAnnotations(
        read_only_hint=values["read_only_hint"],
        destructive_hint=values["destructive_hint"],
        open_world_hint=values["open_world_hint"],
    )
    return mcp_approval_risk(annotations), False


def _clip_mcp_display_value(
    value: str,
    *,
    display_width: int,
    byte_limit: int,
) -> str:
    """同时按显示列与 UTF-8 字节预算裁剪单个参数值。"""
    text = str(value)
    if _mcp_text_width(text) <= display_width and len(text.encode("utf-8")) <= byte_limit:
        return text
    ellipsis = "…"
    ellipsis_bytes = len(ellipsis.encode("utf-8"))
    if byte_limit < ellipsis_bytes or display_width < 1:
        return ""
    remaining_bytes = byte_limit - ellipsis_bytes
    remaining_width = display_width - 1
    units: list[str] = []
    used_bytes = 0
    used_width = 0
    for char in text:
        char_bytes = len(char.encode("utf-8"))
        char_width = _mcp_character_width(char)
        if used_bytes + char_bytes > remaining_bytes or used_width + char_width > remaining_width:
            break
        units.append(char)
        used_bytes += char_bytes
        used_width += char_width
    return f"{''.join(units).rstrip()}{ellipsis}"


def _mcp_text_width(value: str) -> int:
    """计算参数摘要使用的保守终端显示宽度。"""
    return sum(_mcp_character_width(char) for char in value)


def _mcp_character_width(char: str) -> int:
    """返回单个字符的保守终端显示宽度。"""
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1


def _request_key(
    payload: dict[str, typing.Any],
    kind: ApprovalRequestKind,
) -> ApprovalRequestKey:
    """从独立载荷构造展示测试所需的稳定身份。"""
    approval_id = _text(payload.get("approval_id") or payload.get("id"))
    call_id = _text(payload.get("call_id"))

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
    arguments = payload.get("arguments")
    arguments_dict = dict(arguments) if isinstance(arguments, dict) else {}
    raw_patch = payload.get("patch", arguments_dict.get("patch", ""))
    patch = _command_text(raw_patch)

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
