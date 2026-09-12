# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import math
import os
import typing
from fnmatch import fnmatchcase

from agent.ports.mcp_runtime import McpTransport
from .values import slugify_mcp_name

DEFAULT_MCP_TRANSPORT = "streamable_http"
DEFAULT_MCP_START_TIMEOUT_SEC = 10.0
DEFAULT_MCP_REQ_TIMEOUT_SEC = 60.0
DEFAULT_MCP_SSE_TIMEOUT_SEC = 30 * 60
MCP_APPROVAL_MODES = frozenset({"auto", "prompt", "writes", "approve"})


class NormalizedMcpServer(typing.TypedDict, total=False):
    """保存配置边界已规范化的连接参数，只有传输适配器消费可选传输字段。"""

    name: str
    config_key: str
    enabled: bool
    required: bool
    transport: McpTransport
    startup_timeout_sec: float
    timeout_sec: float
    tool_filter: dict[str, list[str]]
    default_tools_approval_mode: str
    tool_approval_modes: dict[str, str]
    command: str
    args: list[str]
    env: dict[str, str]
    cwd: str
    encoding: str
    encoding_error_handler: str
    url: str
    headers: dict[str, str]
    sse_read_timeout_sec: float
    terminate_on_close: bool


class McpConfigError(ValueError):
    """表示外部 MCP 配置无法解析。"""


def positive_float(value: typing.Any, fallback: float) -> float:
    """把输入转换为正浮点数，失败时返回给定默认值。"""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return fallback

    if not math.isfinite(number) or number <= 0:
        return fallback
    return number


def string_map(value: typing.Any) -> dict[str, str]:
    """把映射型配置规范化为字符串键值字典。"""
    if not isinstance(value, dict):
        return {}
    return {
        str(map_key).strip(): str(map_value)
        for map_key, map_value in value.items()
        if str(map_key).strip()
    }


def string_list(value: typing.Any) -> list[str]:
    """把列表型配置规范化为字符串列表。"""
    if not isinstance(value, list):
        return []
    return [
        str(item)
        for item in value
        if isinstance(item, (str, int, float, bool))
    ]


def _tool_patterns(value: typing.Any) -> list[str]:
    """规范化外接 MCP 工具匹配模式，并保持配置顺序。"""
    if not isinstance(value, list):
        return []

    patterns: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        pattern = item.strip()
        if not pattern or pattern in seen:
            continue
        seen.add(pattern)
        patterns.append(pattern)
    return patterns


def normalize_mcp_approval_mode(
    value: typing.Any,
    *,
    default: str = "auto",
) -> str:
    """把 MCP 工具审批模式规范化为受支持的稳定值。"""
    mode = str(value if value is not None else default).strip().casefold()
    if mode not in MCP_APPROVAL_MODES:
        choices = ", ".join(sorted(MCP_APPROVAL_MODES))
        raise McpConfigError(f"MCP approval mode must be one of: {choices}")
    return mode


def _tool_approval_modes(value: typing.Any) -> dict[str, str]:
    """读取逐工具 MCP 审批覆盖并拒绝不完整条目。"""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise McpConfigError("MCP tools approval config must be a table")
    result: dict[str, str] = {}
    for raw_name, raw_config in value.items():
        name = str(raw_name or "").strip()
        if not name or not isinstance(raw_config, dict):
            raise McpConfigError("MCP tool approval config is invalid")
        if set(raw_config) != {"approval_mode"}:
            raise McpConfigError(
                f"MCP tool {name} must define only approval_mode"
            )
        result[name] = normalize_mcp_approval_mode(
            raw_config.get("approval_mode")
        )
    return result


def is_mcp_tool_allowed(name: str, rules: typing.Any) -> bool:
    """按原始工具名判断外接 MCP 工具是否允许暴露。"""
    policy = rules if isinstance(rules, dict) else {}
    tool_name = str(name)

    allow = policy.get("allow")
    if "allow" in policy and not any(
        fnmatchcase(tool_name, pattern) for pattern in list(allow or [])
    ):
        return False

    deny = policy.get("deny")
    return not any(
        fnmatchcase(tool_name, pattern) for pattern in list(deny or [])
    )


def normalize_mcp_servers(raw: typing.Any) -> list[NormalizedMcpServer]:
    """把有效配置中的 MCP 服务表规范化为内部服务列表。"""
    if not isinstance(raw, dict):
        return []

    normalized: list[NormalizedMcpServer] = []
    seen_names: set[str] = set()

    for index, (key, item) in enumerate(raw.items(), start=1):
        if not isinstance(item, dict):
            continue

        name = str(key or "")
        if not name.strip():
            raise McpConfigError("MCP configuration key must not be empty")
        slug = slugify_mcp_name(name, fallback=f"server-{index}")
        url = str(item.get("url", "") or "").strip()
        command = str(item.get("command", "") or "").strip()

        if command and url:
            continue
        transport: McpTransport = (
            "stdio"
            if command
            else "sse" if url.lower().rstrip("/").endswith("/sse")
            else DEFAULT_MCP_TRANSPORT
        )
        if transport == "stdio":
            if not command:
                continue
        elif not url:
            continue

        timeout_sec = positive_float(
            item.get("tool_timeout_sec"),
            DEFAULT_MCP_REQ_TIMEOUT_SEC,
        )
        tool_rules: dict[str, list[str]] = {}
        if "allow" in item:
            tool_rules["allow"] = _tool_patterns(item.get("allow"))
        if "deny" in item:
            tool_rules["deny"] = _tool_patterns(item.get("deny"))

        unique_slug = slug
        suffix = 2
        while unique_slug in seen_names:
            unique_slug = f"{slug}-{suffix}"
            suffix += 1
        seen_names.add(unique_slug)

        base: NormalizedMcpServer = {
            "name": unique_slug,
            "config_key": name,
            "enabled": item.get("enabled", True) is not False,
            "required": item.get("required", False) is True,
            "transport": transport,
            "startup_timeout_sec": positive_float(
                item.get("startup_timeout_sec"),
                DEFAULT_MCP_START_TIMEOUT_SEC,
            ),
            "timeout_sec": timeout_sec,
            "tool_filter": tool_rules,
            "default_tools_approval_mode": normalize_mcp_approval_mode(
                item.get("default_tools_approval_mode")
            ),
            "tool_approval_modes": _tool_approval_modes(item.get("tools")),
        }

        if transport == "stdio":
            cwd = str(item.get("cwd", "") or "").strip()
            normalized.append({
                **base,
                "command": command,
                "args": string_list(item.get("args")),
                "env": string_map(item.get("env")),
                "cwd": cwd,
                "encoding": "utf-8",
                "encoding_error_handler": "strict",
            })
            continue

        headers = string_map(item.get("http_headers"))
        for header, environment_name in string_map(
            item.get("env_http_headers")
        ).items():
            environment_value = os.environ.get(environment_name)
            if environment_value is not None:
                headers[header] = environment_value

        bearer_name = str(item.get("bearer_token_env_var") or "").strip()
        bearer_token = os.environ.get(bearer_name) if bearer_name else None
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"

        normalized.append({
            **base,
            "url": url,
            "headers": headers,
            "sse_read_timeout_sec": timeout_sec,
            "terminate_on_close": True,
        })

    return normalized


def request_timeout_sec(server: dict[str, typing.Any]) -> float:
    """读取外部 MCP 服务的请求超时时间。"""
    return positive_float(
        server.get("timeout_sec"),
        DEFAULT_MCP_REQ_TIMEOUT_SEC,
    )


def startup_timeout_sec(server: dict[str, typing.Any]) -> float:
    """读取外部 MCP 服务的启动超时时间。"""
    return positive_float(
        server.get("startup_timeout_sec"),
        DEFAULT_MCP_START_TIMEOUT_SEC,
    )


if __name__ == '__main__':
    pass
