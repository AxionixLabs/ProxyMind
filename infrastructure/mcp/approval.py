# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import hashlib
import json
import typing

import jsonschema
from mcp import types as mcp_types

from agent.domain.approvals import (
    ActionFingerprint,
    McpApprovalMode,
    McpApprovalPolicy,
    McpToolAnnotations,
    McpToolDescriptor,
)

MCP_APPROVAL_MODE_META_KEY = "approval_mode"
MCP_ALLOW_SESSION_META_KEY = "approval_allow_session"
MCP_ALLOW_PERSISTENT_META_KEY = "approval_allow_persistent"


def prepare_mcp_approval_descriptor(
    exposed_name: str,
    tool: mcp_types.Tool,
    arguments: dict[str, typing.Any],
) -> McpToolDescriptor:
    """校验真实 MCP 工具参数并生成不携带 SDK 对象的审批描述符。"""
    if not isinstance(tool, mcp_types.Tool):
        raise TypeError("external MCP tool descriptor is invalid")
    if not isinstance(arguments, dict):
        raise TypeError("external MCP arguments must be an object")
    try:
        jsonschema.validate(instance=arguments, schema=tool.inputSchema)
    except jsonschema.SchemaError as error:
        raise ValueError("external MCP tool schema is invalid") from error
    except jsonschema.ValidationError as error:
        raise ValueError(f"external MCP arguments are invalid: {error.message}") from error

    meta = dict(tool.meta or {})
    annotations = tool.annotations
    schema_json = json.dumps(
        tool.inputSchema,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    approval_mode = str(meta.get(MCP_APPROVAL_MODE_META_KEY) or "auto")
    return McpToolDescriptor(
        server=_required_meta_text(meta, "server"),
        exposed_name=_required_text(exposed_name, "exposed_name"),
        tool_name=_required_text(tool.name, "tool_name"),
        schema_fingerprint=ActionFingerprint(
            hashlib.sha256(schema_json.encode("utf-8")).hexdigest()
        ),
        annotations=McpToolAnnotations(
            read_only_hint=(annotations.readOnlyHint if annotations else None),
            destructive_hint=(annotations.destructiveHint if annotations else None),
            open_world_hint=(annotations.openWorldHint if annotations else None),
        ),
        policy=McpApprovalPolicy(
            mode=McpApprovalMode(approval_mode),
            allow_session_remember=meta.get(MCP_ALLOW_SESSION_META_KEY) is not False,
            allow_persistent_approval=(
                meta.get(MCP_ALLOW_PERSISTENT_META_KEY) is not False
            ),
        ),
        title=_optional_text(tool.title),
        description=_optional_text(tool.description),
        connector_id=_optional_meta_text(meta, "connector_id"),
        connector_name=_optional_meta_text(meta, "connector_name"),
        connector_description=_optional_meta_text(meta, "connector_description"),
        connected_account=_optional_meta_text(meta, "connected_account_email"),
        transport=_optional_meta_text(meta, "transport") or "external",
        config_server_key=_optional_meta_text(meta, "config_server_key"),
    )


def _required_text(value: object, name: str) -> str:
    """读取外部边界要求存在的文本。"""
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        raise ValueError(f"external MCP {name} is required")
    return text


def _required_meta_text(meta: dict[str, typing.Any], name: str) -> str:
    """读取 MCP meta 中要求存在的文本字段。"""
    return _required_text(meta.get(name), name)


def _optional_text(value: object) -> str | None:
    """把 MCP SDK 可选文本转换为真正可空字段。"""
    text = value.strip() if isinstance(value, str) else ""
    return text or None


def _optional_meta_text(
    meta: dict[str, typing.Any],
    name: str,
) -> str | None:
    """读取 MCP meta 中的可选文本字段。"""
    return _optional_text(meta.get(name))


if __name__ == '__main__':
    pass
