# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import hashlib
import json
import typing
from collections.abc import Mapping
from pathlib import Path

from agent.domain.approvals import ActionFingerprint
from agent.protocol.json_value import (
    JsonValue,
    freeze_json,
    thaw_json,
)
from protocol.transport import config

_ACTION_FIELDS = {
    "command": (
        "environment_id",
        "command",
        "cwd",
        "cwd_raw",
        "reason",
        "tty",
        "sandbox_permissions",
        "additional_permissions",
        "proposed_execpolicy_amendment",
        "parsed_cmd",
    ),
    "write_stdin": (
        "session_id",
        "input",
        "control",
        "reason",
    ),
    "apply_patch": (
        "environment_id",
        "cwd",
        "cwd_raw",
        "patch",
        "files",
        "reason",
        "permissions_preapproved",
    ),
    "network_access": (
        "environment_id",
        "target",
        "host",
        "protocol",
        "port",
        "command",
        "cwd",
        "cwd_raw",
        "reason",
        "proposed_network_policy_amendment",
    ),
    "request_permissions": (
        "environment_id",
        "cwd",
        "reason",
        "permissions",
    ),
    "mcp_tool_call": (
        "server",
        "tool_name",
        "arguments",
        "mcp_request_id",
        "connector_id",
        "connector_name",
        "connector_description",
        "connected_account_email",
        "tool_title",
        "tool_description",
        "annotations",
        "reason",
    ),
}


def approval_action_fingerprint(
    payload: Mapping[str, typing.Any],
    kind: str,
) -> ActionFingerprint:
    """为已校验的审批动作生成跨边界稳定 SHA-256 指纹。"""
    normalized_kind = str(kind or "").strip()
    field_names = _ACTION_FIELDS.get(normalized_kind)
    if field_names is None:
        raise ValueError("approval action kind is invalid")
    fields = {
        "kind": normalized_kind,
        **{
            field_name: payload.get(field_name)
            for field_name in field_names
        },
    }
    if normalized_kind == "command":
        fields["tty"] = bool(fields["tty"])
        fields["parsed_cmd"] = fields["parsed_cmd"] or []
    cwd = fields.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        try:
            fields["cwd"] = str(Path(cwd).expanduser().resolve())
        except (OSError, RuntimeError, ValueError):
            fields["cwd"] = cwd.strip()
    frozen = freeze_json(fields, field_name="approval action")
    normalized = thaw_json(frozen)
    encoded = json.dumps(
        normalized,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return ActionFingerprint(hashlib.sha256(encoded.encode(config.CHARSET)).hexdigest())


def approval_execution_fingerprint(
    *,
    tool: str,
    arguments: Mapping[str, JsonValue],
    cwd_default: str,
) -> ActionFingerprint:
    """为审批后实际执行的安全相关参数生成稳定指纹。"""
    normalized_tool = str(tool or "").strip()
    if not normalized_tool:
        raise ValueError("approval execution tool is invalid")

    if normalized_tool in {"exec_command", "shell_command"}:
        cwd_value = arguments.get("cwd")
        cwd = cwd_value if isinstance(cwd_value, str) else cwd_default
        fields: dict[str, JsonValue] = {
            "kind": "command",
            "command": arguments.get("command"),
            "cwd": _normalize_execution_cwd(cwd),
            "shell": arguments.get("shell"),
            "tty": bool(arguments.get("tty", False)),
            "sandbox_permissions": str(
                arguments.get("sandbox_permissions") or "use_default"
            ).strip().casefold(),
            "additional_permissions": arguments.get("additional_permissions"),
        }
    elif normalized_tool == "write_stdin":
        fields = {
            "tool": normalized_tool,
            "session_id": arguments.get("session_id"),
            "input": arguments.get("input", arguments.get("stdin")),
            "control": str(arguments.get("control") or "none").strip().casefold(),
        }
    elif normalized_tool == "apply_patch":
        cwd_value = arguments.get("cwd")
        cwd = cwd_value if isinstance(cwd_value, str) else cwd_default
        fields = {
            "tool": normalized_tool,
            "patch": arguments.get("patch"),
            "cwd": _normalize_execution_cwd(cwd),
        }
    else:
        fields = {
            "tool": normalized_tool,
            "arguments": dict(arguments),
        }

    frozen = freeze_json(fields, field_name="approval execution")
    normalized = thaw_json(frozen)
    encoded = json.dumps(
        normalized,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return ActionFingerprint(hashlib.sha256(encoded.encode(config.CHARSET)).hexdigest())


def _normalize_execution_cwd(value: str) -> str:
    """规范化审批执行指纹中的工作目录。"""
    cwd = str(value or "").strip()
    if not cwd:
        return ""
    try:
        return str(Path(cwd).expanduser().resolve())
    except (OSError, RuntimeError, ValueError):
        return cwd


if __name__ == '__main__':
    pass
