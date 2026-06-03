# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import time
import typing


def normalize_cloud_sandbox_handoff(
    *,
    tool_name: str,
    fields: typing.Union[str, dict[str, typing.Any]],
    ok: bool
) -> dict[str, typing.Any] | None:
    """把本地工具的云端 sandbox 路由请求改写成云端主循环可接管的 handoff 结果。"""
    if ok:
        return None

    fields_map = _fields_map(fields)

    requests = collect_cloud_sandbox_requests(tool_name=tool_name, fields=fields_map)
    if not requests:
        return None

    return {
        "text"        : f"cloud sandbox handoff requested count={len(requests)}",
        "attachments" : fields_map.get("attachments") or [],
        "data": {
            "ok"                    : True,
            "pending_cloud_sandbox" : True,
            "original_tool"         : tool_name,
            "sandbox_requests"      : requests,
            "original_result"       : fields_map.get("data"),
            "handoff_at"            : time.time()
        }
    }


def collect_cloud_sandbox_requests(
    *,
    tool_name: str,
    fields: dict[str, typing.Any]
) -> list[dict[str, typing.Any]]:
    """从工具结果中提取需要云端沙箱接管的命令请求。"""
    data    = fields.get("data") if isinstance(fields.get("data"), dict) else {}
    results = data.get("results") if isinstance(data.get("results"), list) else []

    requests: list[dict[str, typing.Any]] = []
    if results:
        for result in results:
            if not isinstance(result, dict):
                continue
            item_data = result.get("data") if isinstance(result.get("data"), dict) else {}

            context = {
                "agent_id"   : result.get("agent_id"),
                "session_id" : item_data.get("session_id"),
                "run_id"     : item_data.get("run_id")
            }
            requests.extend(_collect_from_value(tool_name, item_data, context=context))

    else:
        requests.extend(_collect_from_value(tool_name, data, context={}))

    return _dedupe_requests(requests)


def _fields_map(fields: typing.Union[str, dict[str, typing.Any]]) -> dict[str, typing.Any]:
    if isinstance(fields, dict):
        return fields
    if isinstance(fields, str):
        try:
            parsed = json.loads(fields)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed

        return {
            "text"        : fields,
            "attachments" : [],
            "data"        : {}
        }

    return {
        "text"        : str(fields),
        "attachments" : [],
        "data"        : {}
    }


def _collect_from_value(
    tool_name: str,
    value: typing.Any,
    *,
    context: dict[str, typing.Any]
) -> list[dict[str, typing.Any]]:
    found: list[dict[str, typing.Any]] = []
    if isinstance(value, dict):
        next_context = {
            **context,
            "session_id": value.get("session_id") or context.get("session_id"),
            "run_id": value.get("run_id") or context.get("run_id")
        }
        if _is_cloud_sandbox_node(value):
            item = _build_handoff_request(tool_name, value, context=next_context)
            if item:
                found.append(item)
        for child in value.values():
            found.extend(_collect_from_value(tool_name, child, context=next_context))
    elif isinstance(value, list):
        for child in value:
            found.extend(_collect_from_value(tool_name, child, context=context))

    return found


def _is_cloud_sandbox_node(value: dict[str, typing.Any]) -> bool:
    execution = value.get("execution") if isinstance(value.get("execution"), dict) else {}
    if execution.get("target") == "cloud_sandbox":
        return True
    if value.get("execution_target") == "cloud_sandbox":
        return True
    return value.get("requires_cloud_sandbox") is True


def _build_handoff_request(
    tool_name: str,
    node: dict[str, typing.Any],
    *,
    context: dict[str, typing.Any]
) -> dict[str, typing.Any] | None:
    execution = node.get("execution") if isinstance(node.get("execution"), dict) else {}
    canonical = execution.get("canonicalArguments") or execution.get("canonical_arguments")

    if not isinstance(canonical, dict):
        canonical = {}

    command = canonical.get("command") or node.get("command")
    if not isinstance(command, list) or not command:
        return None

    session_id = context.get("session_id")
    run_id     = context.get("run_id")
    cwd        = canonical.get("cwd") or node.get("cwd") or "."

    request = {
        "protocol_version": 1,
        "tool": tool_name,
        "agent_id": context.get("agent_id"),
        "session_id": session_id,
        "run_id": run_id,
        "command": command,
        "cwd": cwd,
        "timeout_sec": canonical.get("timeout_sec") or node.get("timeout_sec"),
        "reason": node.get("reason"),
        "risk": node.get("risk"),
        "category": node.get("category"),
        "execution": execution or None,
        "grant_id": execution.get("grantId") or execution.get("grant_id"),
        "workspace": None,
        "cloud_schema": {
            "preferred": "command",
            "requires_workspace_materialization": True
        },
        "record_tool": None,
        "record_strategy": None,
        "record_call": None,
        "record_args": None
    }
    return request


def _dedupe_requests(items: list[dict[str, typing.Any]]) -> list[dict[str, typing.Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, typing.Any]] = []
    for item in items:
        key = json.dumps(
            {
                "session_id" : item.get("session_id"),
                "run_id"     : item.get("run_id"),
                "command"    : item.get("command"),
                "cwd"        : item.get("cwd")
            },
            ensure_ascii=False,
            sort_keys=True
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    return deduped


if __name__ == '__main__':
    pass
