# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import json
import typing
import subprocess
from pathlib import Path
from engine.errors import ApplicationError
from mind_app.mcp.registry import McpServerRegistry
from mind_app.paths import mind_config_path
from mind_core.config import ConfigOverride
from mind_core.config_session import ConfigSession
from mind_core.config_store import ConfigStore
from mind_app.cli.commands import (
    McpAddCommand,
    McpGetCommand,
    McpListCommand,
    McpRegistryCommand,
    McpRemoveCommand,
    McpSetEnabledCommand
)


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
    return str(config.get("url") or "").strip() or "-"


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

        return config

    if command.url is None:
        raise ApplicationError("MCP server target is incomplete")

    config = {
        "url"     : command.url,
        "enabled" : command.enabled
    }

    if command.bearer_token_env_var is not None:
        config["bearer_token_env_var"] = command.bearer_token_env_var
    if command.headers:
        config["http_headers"] = dict(command.headers)

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
                            {"name": name, "config": config}
                            for name, config in servers
                        ]
                    },
                    stream,
                )
            else:
                _write_server_list(servers, stream)
            return 0

        if isinstance(command, McpGetCommand):
            config = registry.get(command.name)
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
        raise ApplicationError(str(error)) from error

    typing.assert_never(command)


if __name__ == "__main__":
    pass
