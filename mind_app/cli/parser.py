# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import typing
import argparse
from mind_nova import const
from mind_nova.modes import (
    DEFAULT_RUN_MODE,
    MODES,
    RunMode
)
from mind_nova.requests.access import (
    ACCESS_MODE_SET,
    AccessMode,
    DEFAULT_ACCESS_MODE
)
from .commands import (
    AgentListenCommand,
    BatchCommand,
    DoctorCommand,
    ExecCommand,
    HelixUpgradeCommand,
    InteractiveCommand,
    McpServerCommand,
    OutputFormat,
    ParsedCommand
)


def create_cli_parser() -> argparse.ArgumentParser:
    """创建 Mind 命令行解析器。"""
    parser = argparse.ArgumentParser(
        prog=const.APP_NAME,
        description=f"{const.APP_DESC} · {const.APP_CN}",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    exec_parser = subparsers.add_parser(
        "exec",
        help="执行单次非交互任务",
        description="执行单次非交互任务。PROMPT 使用 '-' 时从标准输入读取。",
    )
    exec_parser.add_argument(
        "prompt",
        nargs="?",
        metavar="PROMPT",
        help="任务内容；使用 '-' 或管道时从标准输入读取",
    )
    exec_parser.add_argument(
        "--mode",
        choices=MODES,
        default=DEFAULT_RUN_MODE,
        help=f"运行模式，默认 {DEFAULT_RUN_MODE}",
    )
    exec_parser.add_argument(
        "--access",
        choices=sorted(ACCESS_MODE_SET),
        default=DEFAULT_ACCESS_MODE,
        help=f"工具访问模式，默认 {DEFAULT_ACCESS_MODE}",
    )
    exec_parser.add_argument(
        "--json",
        action="store_true",
        help="输出逐行 JSON 事件",
    )
    exec_parser.add_argument(
        "--helix",
        action="store_true",
        help="启动或复用本地 Helix 并接入其 MCP 工具",
    )

    batch_parser = subparsers.add_parser(
        "batch",
        help="批量执行星图",
        description="装载一个或多个星图并按指定模式执行。",
    )
    batch_parser.add_argument(
        "sources",
        nargs="+",
        metavar="SOURCE",
        help="星图文件、'-'、inline: 内容或 URL",
    )
    batch_parser.add_argument(
        "--mode",
        choices=MODES,
        required=True,
        help="批量任务使用的运行模式",
    )
    batch_parser.add_argument(
        "--access",
        choices=sorted(ACCESS_MODE_SET),
        default=DEFAULT_ACCESS_MODE,
        help=f"工具访问模式，默认 {DEFAULT_ACCESS_MODE}",
    )
    batch_parser.add_argument(
        "--helix",
        action="store_true",
        help="启动或复用本地 Helix 并接入其 MCP 工具",
    )

    agent_parser = subparsers.add_parser(
        "agent",
        help="管理远端任务订阅",
    )
    agent_subparsers = agent_parser.add_subparsers(
        dest="agent_command",
        metavar="COMMAND",
        required=True,
    )
    listen_parser = agent_subparsers.add_parser(
        "listen",
        help="监听远端下发任务",
    )
    listen_parser.add_argument(
        "--helix",
        action="store_true",
        help="启动或复用本地 Helix 并接入其 MCP 工具",
    )

    helix_parser = subparsers.add_parser(
        "helix",
        help="管理 Helix provider",
    )
    helix_subparsers = helix_parser.add_subparsers(
        dest="helix_command",
        metavar="COMMAND",
        required=True,
    )
    helix_subparsers.add_parser(
        "upgrade",
        help="更新 Helix 运行组件",
    )

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="检查本地运行环境",
        description="只读检查配置、运行组件和本地工具。",
    )
    doctor_parser.add_argument(
        "--json",
        action="store_true",
        help="输出单个 JSON 诊断报告",
    )

    subparsers.add_parser(
        "mcp-server",
        help="以 stdio 启动 Mind MCP 服务",
        description="通过标准输入输出提供 Mind agent 工具。",
    )

    return parser


def _stream_is_interactive(stream: typing.TextIO) -> bool:
    """判断输入流是否连接到交互终端。"""
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError, ValueError):
        return False


def _read_exec_prompt(
    parser: argparse.ArgumentParser,
    prompt: str | None,
    input_stream: typing.TextIO,
) -> str:
    """解析位置参数或标准输入中的单次任务内容。"""
    should_read = prompt == "-" or (
        prompt is None
        and not _stream_is_interactive(input_stream)
    )
    value = input_stream.read() if should_read else prompt
    normalized = str(value or "").strip()
    if normalized:
        return normalized
    parser.error("mind exec requires PROMPT or non-empty stdin")


def _optional_string(
    parser: argparse.ArgumentParser,
    values: dict[str, object],
    key: str,
) -> str | None:
    """读取一个可选字符串参数。"""
    value = values.get(key)
    if value is None or isinstance(value, str):
        return value
    parser.error(f"invalid {key}: expected string")


def _run_mode(
    parser: argparse.ArgumentParser,
    values: dict[str, object],
) -> RunMode:
    """读取并验证运行模式参数。"""
    value = values.get("mode")
    if value == "chat":
        return "chat"
    if value == "fast":
        return "fast"
    if value == "xtra":
        return "xtra"
    parser.error(f"invalid mode: {value}")


def _access_mode(
    parser: argparse.ArgumentParser,
    values: dict[str, object],
) -> AccessMode:
    """读取并验证工具访问模式参数。"""
    value = values.get("access")
    if value == "safe":
        return "safe"
    if value == "full":
        return "full"
    parser.error(f"invalid access mode: {value}")


def _sources(
    parser: argparse.ArgumentParser,
    values: dict[str, object],
) -> tuple[str, ...]:
    """读取并验证批处理来源列表。"""
    value = values.get("sources")
    if not isinstance(value, list):
        parser.error("invalid sources: expected one or more strings")

    sources: list[str] = []
    for item in value:
        if not isinstance(item, str):
            parser.error("invalid sources: expected one or more strings")
        sources.append(item)
    return tuple(sources)


def parse_cli_command(
    arguments: typing.Sequence[str] | None = None,
    *,
    input_stream: typing.TextIO | None = None,
) -> ParsedCommand:
    """解析参数并返回强类型命令。"""
    parser = create_cli_parser()
    namespace = parser.parse_args(arguments)
    values: dict[str, object] = vars(namespace)
    command = _optional_string(parser, values, "command")

    if command is None:
        return InteractiveCommand()

    if command == "exec":
        prompt = _read_exec_prompt(
            parser,
            _optional_string(parser, values, "prompt"),
            sys.stdin if input_stream is None else input_stream,
        )
        output_format: OutputFormat = "json" if bool(values["json"]) else "text"
        return ExecCommand(
            prompt=prompt,
            mode=_run_mode(parser, values),
            access_mode=_access_mode(parser, values),
            output_format=output_format,
            helix=bool(values["helix"]),
        )

    if command == "batch":
        return BatchCommand(
            sources=_sources(parser, values),
            mode=_run_mode(parser, values),
            access_mode=_access_mode(parser, values),
            helix=bool(values["helix"]),
        )

    if command == "agent" and values.get("agent_command") == "listen":
        return AgentListenCommand(helix=bool(values["helix"]))

    if command == "helix" and values.get("helix_command") == "upgrade":
        return HelixUpgradeCommand()

    if command == "doctor":
        output_format: OutputFormat = "json" if bool(values["json"]) else "text"
        return DoctorCommand(output_format=output_format)

    if command == "mcp-server":
        return McpServerCommand()

    parser.error(f"unsupported command: {command}")


if __name__ == '__main__':
    pass
