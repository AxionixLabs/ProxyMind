# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import hashlib
import json
import typing
from collections.abc import Mapping
from pathlib import Path

from agent.domain.approvals import ActionFingerprint
from agent.protocol.json_value import (
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


if __name__ == '__main__':
    pass
