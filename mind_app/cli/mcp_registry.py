# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import sys
import json
import typing
import subprocess
from pathlib import Path
from urllib.parse import (
    urlsplit,
    urlunsplit
)
from infrastructure.errors import AppError
from mind_app.runtime.mcp.registry import McpServerRegistry
from infrastructure.config.runtime_paths import mind_config_path
from infrastructure.config.schema import ConfigOverride
from infrastructure.config.session import ConfigSession
from infrastructure.config.store import ConfigStore
from mind_app.cli.commands import (
    McpAddCommand,
    McpGetCommand,
    McpListCommand,
    McpRegistryCommand,
    McpRemoveCommand,
    McpSetEnabledCommand
)

SENSITIVE_PATH_COMPONENT = re.compile(r"^[A-Za-z0-9_-]{24,}$")


def _server_transport(config: dict[str, typing.Any]) -> str:
    """返回适合展示的服务传输类型。"""
    if str(config.get("command") or "").strip():
        return "stdio"

    url = str(config.get("url") or "").strip().lower().rstrip("/")
    return "sse" if url.endswith("/sse") else "streamable-http"


def _server_target(config: dict[str, typing.Any]) -> str:
    """返回适合列表展示的服务目标。"""
    command = str(config.get("command") or "").strip()
    if command:
        raw_args = config.get("args")
        args = (
            [str(item) for item in raw_args]
            if isinstance(raw_args, list)
            else []
        )
        return subprocess.list2cmdline([command, *args])
    return _redact_url(config.get("url")) or "-"


def _redact_url(value: object) -> str:
    """隐藏 URL 中可能包含凭据的查询串和片段。"""
    text = str(value or "").strip()
    if not text:
        return ""

    parsed = urlsplit(text)

    path = "/".join(
        "<redacted>" if SENSITIVE_PATH_COMPONENT.fullmatch(part) else part
        for part in parsed.path.split("/")
    )

    return urlunsplit((
        parsed.scheme,
        parsed.netloc,
        path,
        "<redacted>" if parsed.query else "",
        "<redacted>" if parsed.fragment else "",
    ))


def _public_server_config(
    config: dict[str, typing.Any],
) -> dict[str, typing.Any]:
    """返回适合终端展示的脱敏服务配置。"""
    public = dict(config)

    if "url" in public:
        public["url"] = _redact_url(public.get("url"))
    for field in ("env", "http_headers"):
        value = public.get(field)
        if isinstance(value, dict):
            public[field] = {
                str(name): "<redacted>"
                for name in value
            }

    return public


def _write_json(value: object, stream: typing.TextIO) -> None:
    """向输出流写入一个完整 JSON 文档。"""
    stream.write(json.dumps(value, ensure_ascii=False, indent=2))
    stream.write("\n")


def _write_server_list(
    servers: tuple[tuple[str, dict[str, typing.Any]], ...],
    stream: typing.TextIO,
) -> None:
    """以稳定列宽输出外部 MCP 服务列表。"""
    if not servers:
        stream.write("No MCP servers configured.\n")
        return None

    rows = [
        (
            name,
            _server_transport(config),
            "enabled" if config.get("enabled", True) is not False else "disabled",
            _server_target(config),
        )
        for name, config in servers
    ]

    name_width      = max(len("Name"), *(len(row[0]) for row in rows))
    transport_width = max(len("Transport"), *(len(row[1]) for row in rows))
    status_width    = max(len("Status"), *(len(row[2]) for row in rows))

    stream.write(
        f"{'Name':<{name_width}}  "
        f"{'Transport':<{transport_width}}  "
        f"{'Status':<{status_width}}  Target\n"
    )
    for name, transport, status, target in rows:
        stream.write(
            f"{name:<{name_width}}  "
            f"{transport:<{transport_width}}  "
            f"{status:<{status_width}}  {target}\n"
        )


def _add_config(command: McpAddCommand) -> dict[str, typing.Any]:
    """把强类型添加命令转换为持久化配置。"""
    if command.stdio_command:
        executable, *arguments = command.stdio_command

        config: dict[str, typing.Any] = {
            "command": executable,
            "args": arguments,
            "enabled": command.enabled,
        }

        if command.env:
            config["env"] = dict(command.env)
        if command.cwd is not None:
            config["cwd"] = command.cwd

    else:
        if command.url is None:
            raise AppError("MCP server target is incomplete")

        config = {
            "url"     : command.url,
            "enabled" : command.enabled
        }

        if command.bearer_token_env_var is not None:
            config["bearer_token_env_var"] = command.bearer_token_env_var
        if command.headers:
            config["http_headers"] = dict(command.headers)
        if command.env_http_headers:
            config["env_http_headers"] = dict(command.env_http_headers)

    if command.required:
        config["required"] = True
    if command.allow:
        config["allow"] = list(command.allow)
    if command.deny:
        config["deny"] = list(command.deny)
    if command.startup_timeout_sec is not None:
        config["startup_timeout_sec"] = command.startup_timeout_sec
    if command.tool_timeout_sec is not None:
        config["tool_timeout_sec"] = command.tool_timeout_sec

    return config


def run_mcp_registry_command(
    command: McpRegistryCommand,
    *,
    config_overrides: tuple[ConfigOverride, ...] = (),
    config_profile: str | None = None,
    output_stream: typing.TextIO | None = None
) -> int:
    """执行一个外部 MCP 服务注册表命令。"""
    stream   = sys.stdout if output_stream is None else output_stream

    registry = McpServerRegistry(ConfigSession(
        ConfigStore(mind_config_path()),
        config_overrides,
        profile=config_profile,
        workspace=Path.cwd(),
    ))

    try:
        if isinstance(command, McpListCommand):
            servers = registry.list()
            if command.output_format == "json":
                _write_json(
                    {
                        "servers": [
                            {
                                "name": name,
                                "config": _public_server_config(config),
                            }
                            for name, config in servers
                        ]
                    },
                    stream,
                )
            else:
                _write_server_list(servers, stream)
            return 0

        if isinstance(command, McpGetCommand):
            config = _public_server_config(registry.get(command.name))
            value  = {"name": command.name, "config": config}

            if command.output_format == "json":
                _write_json(value, stream)
            else:
                stream.write(f"{command.name}\n")
                stream.write(json.dumps(config, ensure_ascii=False, indent=2))
                stream.write("\n")

            return 0

        if isinstance(command, McpAddCommand):
            registry.add(command.name, _add_config(command))
            stream.write(f"Added MCP server '{command.name}'.\n")
            return 0

        if isinstance(command, McpRemoveCommand):
            registry.remove(command.name)
            stream.write(f"Removed MCP server '{command.name}'.\n")
            return 0

        if isinstance(command, McpSetEnabledCommand):
            registry.set_enabled(command.name, command.enabled)
            action = "Enabled" if command.enabled else "Disabled"
            stream.write(f"{action} MCP server '{command.name}'.\n")
            return 0

    except (OSError, TypeError, ValueError) as error:
        raise AppError(str(error)) from error

    typing.assert_never(command)


if __name__ == "__main__":
    pass
