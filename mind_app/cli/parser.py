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
from .help import CliArgumentParser

EXEC_HELP          = "Run a task non-interactively"
BATCH_HELP         = "Run one or more schematics"
AGENT_HELP         = "Manage remote task subscriptions"
AGENT_LISTEN_HELP  = "Listen for remotely dispatched tasks"
HELIX_HELP         = "Manage the Helix provider"
HELIX_UPGRADE_HELP = "Update Helix runtime components"
DOCTOR_HELP        = "Diagnose the local runtime environment"
MCP_SERVER_HELP    = "Start the MCP server over stdio"
HELP_HELP          = "Print this message or the help of the given subcommand(s)"
ROOT_COMMANDS      = frozenset({
    "exec",
    "batch",
    "agent",
    "helix",
    "doctor",
    "mcp-server",
    "help",
})


def create_cli_parser() -> CliArgumentParser:
    """创建应用命令行解析器。"""
    parser = CliArgumentParser(
        prog=const.APP_NAME,
        help_title=f"{const.APP_DESC} CLI",
        description=(
            "If no subcommand is specified, options will be forwarded "
            "to the interactive CLI."
        ),
        usage=(
            "%(prog)s [OPTIONS] [PROMPT]\n"
            "       %(prog)s [OPTIONS] <COMMAND> [ARGS]"
        ),
        add_help=False,
    )
    subparsers = parser.add_subparsers(
        title="Commands",
        dest="command",
        metavar="",
    )

    exec_parser = subparsers.add_parser(
        "exec",
        prog=f"{const.APP_NAME} exec",
        help=EXEC_HELP,
        description="执行单次非交互任务。PROMPT 使用 '-' 时从标准输入读取。",
        help_title=f"{const.APP_DESC} Exec",
        usage="%(prog)s [OPTIONS] [PROMPT]",
        add_help=False,
    )
    exec_arguments = exec_parser.add_argument_group("Arguments")
    exec_arguments.add_argument(
        "prompt",
        nargs="?",
        metavar="PROMPT",
        help="任务内容；使用 '-' 或管道时从标准输入读取",
    )
    exec_options = exec_parser.add_argument_group("Options")
    exec_options.add_argument(
        "--mode",
        choices=MODES,
        default=DEFAULT_RUN_MODE,
        metavar="MODE",
        help=f"运行模式，默认 {DEFAULT_RUN_MODE}",
    )
    exec_options.add_argument(
        "--access",
        choices=sorted(ACCESS_MODE_SET),
        default=DEFAULT_ACCESS_MODE,
        metavar="ACCESS_MODE",
        help=f"工具访问模式，默认 {DEFAULT_ACCESS_MODE}",
    )
    exec_options.add_argument(
        "--json",
        action="store_true",
        help="输出逐行 JSON 事件",
    )
    exec_options.add_argument(
        "--helix",
        action="store_true",
        help="启动或复用本地 Helix 并接入其 MCP 工具",
    )
    exec_options.add_argument(
        "-h",
        "--help",
        action="help",
        help="显示此命令的帮助信息",
    )

    batch_parser = subparsers.add_parser(
        "batch",
        prog=f"{const.APP_NAME} batch",
        help=BATCH_HELP,
        description="装载一个或多个星图并按指定模式执行。",
        help_title=f"{const.APP_DESC} Batch",
        usage="%(prog)s [OPTIONS] <SOURCE>...",
        add_help=False,
    )
    batch_arguments = batch_parser.add_argument_group("Arguments")
    batch_arguments.add_argument(
        "sources",
        nargs="+",
        metavar="SOURCE",
        help="星图文件、'-'、inline: 内容或 URL",
    )
    batch_options = batch_parser.add_argument_group("Options")
    batch_options.add_argument(
        "--mode",
        choices=MODES,
        required=True,
        metavar="MODE",
        help="批量任务使用的运行模式",
    )
    batch_options.add_argument(
        "--access",
        choices=sorted(ACCESS_MODE_SET),
        default=DEFAULT_ACCESS_MODE,
        metavar="ACCESS_MODE",
        help=f"工具访问模式，默认 {DEFAULT_ACCESS_MODE}",
    )
    batch_options.add_argument(
        "--helix",
        action="store_true",
        help="启动或复用本地 Helix 并接入其 MCP 工具",
    )
    batch_options.add_argument(
        "-h",
        "--help",
        action="help",
        help="显示此命令的帮助信息",
    )

    agent_parser = subparsers.add_parser(
        "agent",
        prog=f"{const.APP_NAME} agent",
        help=AGENT_HELP,
        description="管理远端任务订阅。",
        help_title=f"{const.APP_DESC} Agent",
        usage="%(prog)s <COMMAND> [ARGS]",
        add_help=False,
    )
    agent_subparsers = agent_parser.add_subparsers(
        title="Commands",
        dest="agent_command",
        metavar="",
        required=True,
    )
    listen_parser = agent_subparsers.add_parser(
        "listen",
        prog=f"{const.APP_NAME} agent listen",
        help=AGENT_LISTEN_HELP,
        description="监听远端下发任务并维持订阅连接。",
        help_title=f"{const.APP_DESC} Agent Listen",
        usage="%(prog)s [OPTIONS]",
        add_help=False,
    )
    listen_options = listen_parser.add_argument_group("Options")
    listen_options.add_argument(
        "--helix",
        action="store_true",
        help="启动或复用本地 Helix 并接入其 MCP 工具",
    )
    listen_options.add_argument(
        "-h",
        "--help",
        action="help",
        help="显示此命令的帮助信息",
    )
    agent_options = agent_parser.add_argument_group("Options")
    agent_options.add_argument(
        "-h",
        "--help",
        action="help",
        help="显示此命令的帮助信息",
    )

    helix_parser = subparsers.add_parser(
        "helix",
        prog=f"{const.APP_NAME} helix",
        help=HELIX_HELP,
        description="管理 Helix provider 运行组件。",
        help_title=f"{const.APP_DESC} Helix",
        usage="%(prog)s <COMMAND> [ARGS]",
        add_help=False,
    )
    helix_subparsers = helix_parser.add_subparsers(
        title="Commands",
        dest="helix_command",
        metavar="",
        required=True,
    )
    upgrade_parser = helix_subparsers.add_parser(
        "upgrade",
        prog=f"{const.APP_NAME} helix upgrade",
        help=HELIX_UPGRADE_HELP,
        description="下载或更新当前平台的 Helix 运行组件。",
        help_title=f"{const.APP_DESC} Helix Upgrade",
        usage="%(prog)s [OPTIONS]",
        add_help=False,
    )
    upgrade_options = upgrade_parser.add_argument_group("Options")
    upgrade_options.add_argument(
        "-h",
        "--help",
        action="help",
        help="显示此命令的帮助信息",
    )
    helix_options = helix_parser.add_argument_group("Options")
    helix_options.add_argument(
        "-h",
        "--help",
        action="help",
        help="显示此命令的帮助信息",
    )

    doctor_parser = subparsers.add_parser(
        "doctor",
        prog=f"{const.APP_NAME} doctor",
        help=DOCTOR_HELP,
        description="只读检查配置、运行组件和本地工具。",
        help_title=f"{const.APP_DESC} Doctor",
        usage="%(prog)s [OPTIONS]",
        add_help=False,
    )
    doctor_options = doctor_parser.add_argument_group("Options")
    doctor_options.add_argument(
        "--json",
        action="store_true",
        help="输出单个 JSON 诊断报告",
    )
    doctor_options.add_argument(
        "-h",
        "--help",
        action="help",
        help="显示此命令的帮助信息",
    )

    mcp_parser = subparsers.add_parser(
        "mcp-server",
        prog=f"{const.APP_NAME} mcp-server",
        help=MCP_SERVER_HELP,
        description=f"通过标准输入输出提供 {const.APP_DESC} agent 工具。",
        help_title=f"{const.APP_DESC} MCP Server",
        usage="%(prog)s [OPTIONS]",
        add_help=False,
    )
    mcp_options = mcp_parser.add_argument_group("Options")
    mcp_options.add_argument(
        "-h",
        "--help",
        action="help",
        help="显示此命令的帮助信息",
    )

    help_parser = subparsers.add_parser(
        "help",
        prog=f"{const.APP_NAME} help",
        help=HELP_HELP,
        description="显示根命令或指定子命令的完整帮助。",
        help_title=f"{const.APP_DESC} Help",
        usage="%(prog)s [COMMAND]...",
        add_help=False,
    )
    help_arguments = help_parser.add_argument_group("Arguments")
    help_arguments.add_argument(
        "help_topics",
        nargs="*",
        metavar="COMMAND",
        help="需要查看的命令路径，例如 agent listen",
    )
    help_options = help_parser.add_argument_group("Options")
    help_options.add_argument(
        "-h",
        "--help",
        action="help",
        help="显示此命令的帮助信息",
    )

    root_arguments = parser.add_argument_group("Arguments")
    root_arguments.add_argument(
        "root_prompt",
        nargs="?",
        metavar="PROMPT",
        help="Optional user prompt to start the session",
    )

    root_options = parser.add_argument_group("Options")
    root_options.add_argument(
        "-i",
        "--image",
        action="extend",
        nargs="+",
        default=[],
        dest="images",
        metavar="FILE",
        help="Optional image(s) to attach to the initial prompt",
    )
    root_options.add_argument(
        "-m",
        "--model",
        metavar="MODEL",
        help="Model the agent should use",
    )
    root_options.add_argument(
        "-h",
        "--help",
        action="help",
        help="Print help (see a summary with '-h')",
    )
    root_options.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"{const.APP_DESC} {const.APP_VERSION}",
        help="Print version",
    )

    command_parsers: dict[
        tuple[str, ...],
        CliArgumentParser,
    ] = {
        ("exec",): exec_parser,
        ("batch",): batch_parser,
        ("agent",): agent_parser,
        ("agent", "listen"): listen_parser,
        ("helix",): helix_parser,
        ("helix", "upgrade"): upgrade_parser,
        ("doctor",): doctor_parser,
        ("mcp-server",): mcp_parser,
        ("help",): help_parser,
    }
    for path, command_parser in command_parsers.items():
        parser.register_command_help(path, command_parser)

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

    parser.error(
        f"{const.APP_NAME} exec requires PROMPT or non-empty stdin"
    )


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


def _help_topics(
    parser: argparse.ArgumentParser,
    values: dict[str, object],
) -> tuple[str, ...]:
    """读取并验证帮助命令路径。"""
    value = values.get("help_topics")
    if not isinstance(value, list):
        parser.error("invalid help topic: expected command path")

    topics: list[str] = []
    for item in value:
        if not isinstance(item, str):
            parser.error("invalid help topic: expected command path")
        topics.append(item)
    return tuple(topics)


def _interactive_command(
    parser: argparse.ArgumentParser,
    arguments: tuple[str, ...],
) -> InteractiveCommand | None:
    """解析不含子命令的交互入口参数。"""
    if arguments and arguments[0] in ROOT_COMMANDS:
        return None
    if any(argument in {"-h", "--help", "-V", "--version"} for argument in arguments):
        return None

    interactive_parser = argparse.ArgumentParser(
        prog=const.APP_NAME,
        add_help=False,
    )
    interactive_parser.add_argument(
        "-i",
        "--image",
        action="extend",
        nargs="+",
        default=[],
        dest="images",
        metavar="FILE",
    )
    interactive_parser.add_argument("-m", "--model", metavar="MODEL")
    interactive_parser.add_argument("prompt", nargs="?")
    namespace = interactive_parser.parse_args(arguments)
    values: dict[str, object] = vars(namespace)

    images = values.get("images")
    if not isinstance(images, list) or not all(
        isinstance(image, str) for image in images
    ):
        parser.error("invalid image arguments")

    prompt = _optional_string(parser, values, "prompt")
    model = _optional_string(parser, values, "model")
    return InteractiveCommand(
        prompt=(prompt.strip() or None) if prompt is not None else None,
        images=tuple(images),
        model=(model.strip() or None) if model is not None else None,
    )


def parse_cli_command(
    arguments: typing.Sequence[str] | None = None,
    *,
    input_stream: typing.TextIO | None = None,
) -> ParsedCommand:
    """解析参数并返回强类型命令。"""
    parser = create_cli_parser()
    raw_arguments = tuple(
        sys.argv[1:] if arguments is None else arguments
    )
    interactive_command = _interactive_command(parser, raw_arguments)
    if interactive_command is not None:
        return interactive_command

    namespace = parser.parse_args(raw_arguments)

    values: dict[str, object] = vars(namespace)

    command = _optional_string(parser, values, "command")
    if command is None:
        return InteractiveCommand(
            prompt=_optional_string(parser, values, "root_prompt"),
        )

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

    if command == "help":
        parser.print_command_help(_help_topics(parser, values))

    parser.error(f"unsupported command: {command}")


if __name__ == '__main__':
    pass
