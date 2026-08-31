# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import math
import argparse
from .commands import (
    McpAddCommand,
    McpGetCommand,
    McpListCommand,
    McpRegistryCommand,
    McpRemoveCommand,
    McpSetEnabledCommand,
    OutputFormat
)
from .help import CliArgumentParser


def split_mcp_stdio_command(
    arguments: tuple[str, ...]
) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    """从 MCP add 参数中分离双横线后的 stdio 命令。"""
    if arguments[:2] != ("mcp", "add"):
        return arguments, (), False
    try:
        separator = arguments.index("--", 2)
    except ValueError:
        return arguments, (), False

    return arguments[:separator], arguments[separator + 1:], True


def parse_mcp_command(
    parser: CliArgumentParser,
    values: dict[str, object]
) -> McpRegistryCommand:
    """解析 MCP 子命令并返回强类型命令。"""
    command = _optional_string(parser, values, "mcp_command")
    if command is None:
        parser.print_command_help(("mcp",))

    if command == "list":
        output_format: OutputFormat = (
            "json" if bool(values["json"]) else "text"
        )
        return McpListCommand(output_format=output_format)

    if command == "get":
        output_format: OutputFormat = (
            "json" if bool(values["json"]) else "text"
        )
        return McpGetCommand(
            name=_required_string(parser, values, "name"),
            output_format=output_format,
        )

    if command == "add":
        return _parse_add_command(parser, values)

    if command == "remove":
        return McpRemoveCommand(
            name=_required_string(parser, values, "name"),
        )

    if command in {"enable", "disable"}:
        return McpSetEnabledCommand(
            name=_required_string(parser, values, "name"),
            enabled=command == "enable",
        )

    if command == "help":
        topic = _optional_string(parser, values, "mcp_help_topic")
        path = ("mcp",) if topic is None else ("mcp", topic)
        parser.print_command_help(path)

    parser.error(f"unsupported mcp command: {command}")


def _parse_add_command(
    parser: argparse.ArgumentParser,
    values: dict[str, object]
) -> McpAddCommand:
    """解析 MCP 服务注册参数。"""
    name          = _required_string(parser, values, "name")
    raw_url       = _optional_string(parser, values, "url")
    url           = str(raw_url or "").strip() or None
    stdio_command = _stdio_command(parser, values)
    env           = _key_value_pairs(parser, values, "env")
    headers       = _key_value_pairs(parser, values, "header")

    env_http_headers = _key_value_pairs(
        parser,
        values,
        "env_http_headers",
    )

    allow = _patterns(parser, values, "allow")
    deny  = _patterns(parser, values, "deny")

    startup_timeout_sec = _positive_number(
        parser,
        values,
        "startup_timeout_sec",
    )

    tool_timeout_sec = _positive_number(
        parser,
        values,
        "tool_timeout_sec",
    )

    cwd_value = _optional_string(parser, values, "cwd")
    cwd       = str(cwd_value or "").strip() or None

    bearer_value = _optional_string(
        parser,
        values,
        "bearer_token_env_var",
    )
    bearer_token_env_var = str(bearer_value or "").strip() or None

    if stdio_command and not bool(values["stdio_separated"]):
        parser.error("stdio commands must follow the '--' separator")
    if url is not None and stdio_command:
        parser.error("--url and a stdio command cannot be used together")
    if url is None and not stdio_command:
        parser.error("mcp add requires --url or a stdio command after '--'")

    if url is not None:
        if env or cwd is not None:
            parser.error("--env and --cwd are only valid for stdio servers")
        return McpAddCommand(
            name=name,
            url=url,
            bearer_token_env_var=bearer_token_env_var,
            headers=headers,
            env_http_headers=env_http_headers,
            enabled=not bool(values["disabled"]),
            required=bool(values["required"]),
            allow=allow,
            deny=deny,
            startup_timeout_sec=startup_timeout_sec,
            tool_timeout_sec=tool_timeout_sec,
        )

    if headers or env_http_headers or bearer_token_env_var is not None:
        parser.error(
            "--header, --env-http-header and --bearer-token-env-var "
            "are only valid for remote servers"
        )

    return McpAddCommand(
        name=name,
        stdio_command=stdio_command,
        env=env,
        cwd=cwd,
        enabled=not bool(values["disabled"]),
        required=bool(values["required"]),
        allow=allow,
        deny=deny,
        startup_timeout_sec=startup_timeout_sec,
        tool_timeout_sec=tool_timeout_sec,
    )


def _optional_string(
    parser: argparse.ArgumentParser,
    values: dict[str, object],
    key: str
) -> str | None:
    """读取一个可选字符串参数。"""
    value = values.get(key)
    if value is None or isinstance(value, str):
        return value
    parser.error(f"invalid {key}: expected string")


def _required_string(
    parser: argparse.ArgumentParser,
    values: dict[str, object],
    key: str
) -> str:
    """读取并验证一个非空字符串参数。"""
    value = _optional_string(parser, values, key)
    normalized = str(value or "").strip()
    if normalized:
        return normalized
    parser.error(f"invalid {key}: expected non-empty string")


def _string_sequence(
    parser: argparse.ArgumentParser,
    values: dict[str, object],
    key: str
) -> tuple[str, ...]:
    """读取并验证一个字符串序列参数。"""
    value = values.get(key)
    if not isinstance(value, list):
        parser.error(f"invalid {key}: expected strings")
    if not all(isinstance(item, str) for item in value):
        parser.error(f"invalid {key}: expected strings")
    return tuple(value)


def _key_value_pairs(
    parser: argparse.ArgumentParser,
    values: dict[str, object],
    key: str
) -> tuple[tuple[str, str], ...]:
    """解析可重复的 KEY=VALUE 参数并拒绝重复键。"""
    items = _string_sequence(parser, values, key)

    pairs: list[tuple[str, str]] = []
    seen: set[str]               = set()

    option = f"--{key.replace('_', '-').removesuffix('s')}"

    for item in items:
        name, separator, value = item.partition("=")
        name = name.strip()
        if not separator or not name:
            parser.error(f"{option} requires KEY=VALUE")
        if name in seen:
            parser.error(f"duplicate {option} key: {name}")
        seen.add(name)
        pairs.append((name, value))

    return tuple(pairs)


def _patterns(
    parser: argparse.ArgumentParser,
    values: dict[str, object],
    key: str
) -> tuple[str, ...]:
    """读取并去重一个非空匹配模式序列。"""
    patterns: list[str] = []
    seen: set[str]      = set()

    for raw in _string_sequence(parser, values, key):
        pattern = raw.strip()
        if not pattern:
            parser.error(f"--{key} pattern must not be empty")
        if pattern in seen:
            continue
        seen.add(pattern)
        patterns.append(pattern)

    return tuple(patterns)


def _positive_number(
    parser: argparse.ArgumentParser,
    values: dict[str, object],
    key: str
) -> float | None:
    """读取一个可选的正有限浮点数。"""
    value = values.get(key)
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        parser.error(f"--{key.replace('_', '-')} must be a positive number")

    return float(value)


def _stdio_command(
    parser: argparse.ArgumentParser,
    values: dict[str, object]
) -> tuple[str, ...]:
    """读取 stdio 服务命令并移除可选分隔符。"""
    command = list(_string_sequence(parser, values, "stdio_command"))
    if command and command[0] == "--":
        command.pop(0)
    if command and command[0].strip():
        return tuple(command)
    if command:
        parser.error("stdio command executable must not be empty")

    return ()


if __name__ == "__main__":
    pass
