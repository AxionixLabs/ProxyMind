# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import math
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
    CliInvocation,
    CompletionCommand,
    COMPLETION_SHELLS,
    CompletionShell,
    DoctorCommand,
    ExecCommand,
    HelixUpgradeCommand,
    InteractiveCommand,
    McpAddCommand,
    McpGetCommand,
    McpListCommand,
    McpRemoveCommand,
    McpServerCommand,
    McpSetEnabledCommand,
    OutputFormat,
    ParsedCommand,
    ResumeCommand
)
from .help import CliArgumentParser
from .invocation import (
    add_invocation_options,
    extract_invocation_options
)

EXEC_HELP          = "Run a task non-interactively"
RESUME_HELP        = "Resume a previous interactive session"
COMPLETION_HELP    = "Generate shell completion scripts"
BATCH_HELP         = "Run one or more schematics"
AGENT_HELP         = "Manage remote task subscriptions"
AGENT_LISTEN_HELP  = "Listen for remotely dispatched tasks"
HELIX_HELP         = "Manage the Helix provider"
HELIX_UPGRADE_HELP = "Update Helix runtime components"
DOCTOR_HELP        = "Diagnose the local runtime environment"
MCP_HELP           = "Manage external MCP servers"
MCP_SERVER_HELP    = "Start the MCP server over stdio"
HELP_HELP          = "Print this message or the help of the given subcommand(s)"
OPTION_HELP        = "Print help (see a summary with '-h')"

ROOT_COMMANDS = frozenset({
    "exec",
    "e",
    "resume",
    "completion",
    "batch",
    "agent",
    "helix",
    "doctor",
    "mcp",
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
        aliases=["e"],
        prog=f"{const.APP_NAME} exec",
        help=EXEC_HELP,
        description=(
            "Run a task non-interactively. Use '-' as PROMPT to read from "
            "standard input."
        ),
        help_title=f"{const.APP_DESC} Exec",
        usage="%(prog)s [OPTIONS] [PROMPT]",
        add_help=False,
    )
    exec_arguments = exec_parser.add_argument_group("Arguments")
    exec_arguments.add_argument(
        "prompt",
        nargs="?",
        metavar="PROMPT",
        help="Task instructions; use '-' or a pipe to read from standard input",
    )
    exec_options = exec_parser.add_argument_group("Options")
    exec_options.add_argument(
        "--mode",
        choices=MODES,
        default=DEFAULT_RUN_MODE,
        metavar="MODE",
        help=f"Run mode [default: {DEFAULT_RUN_MODE}]",
    )
    exec_options.add_argument(
        "--access",
        choices=sorted(ACCESS_MODE_SET),
        default=DEFAULT_ACCESS_MODE,
        metavar="ACCESS_MODE",
        help=f"Tool access mode [default: {DEFAULT_ACCESS_MODE}]",
    )
    exec_options.add_argument(
        "--json",
        action="store_true",
        help="Print newline-delimited JSON events",
    )
    exec_options.add_argument(
        "--helix",
        action="store_true",
        help="Start or reuse the local Helix runtime and attach its MCP tools",
    )
    exec_options.add_argument(
        "-i",
        "--image",
        action="append",
        default=[],
        dest="images",
        metavar="FILE",
        help="Image(s) to attach; repeat the option or separate paths with commas",
    )
    exec_options.add_argument(
        "-m",
        "--model",
        metavar="MODEL",
        help="Model the agent should use",
    )
    exec_options.add_argument(
        "-h",
        "--help",
        action="help",
        help=OPTION_HELP,
    )

    resume_parser = subparsers.add_parser(
        "resume",
        prog=f"{const.APP_NAME} resume",
        help=RESUME_HELP,
        description=(
            "Resume a previous interactive session. The session picker opens "
            "by default; use --last to continue the most recent session."
        ),
        help_title=f"{const.APP_DESC} Resume",
        usage="%(prog)s [OPTIONS] [SESSION_ID] [PROMPT]",
        add_help=False,
    )
    resume_arguments = resume_parser.add_argument_group("Arguments")
    resume_arguments.add_argument(
        "session_id",
        nargs="?",
        metavar="SESSION_ID",
        help="Session id to resume directly",
    )
    resume_arguments.add_argument(
        "resume_prompt",
        nargs="?",
        metavar="PROMPT",
        help="Optional user prompt to start the resumed session",
    )
    resume_options = resume_parser.add_argument_group("Options")
    resume_options.add_argument(
        "--last",
        action="store_true",
        help="Continue the most recent session without showing the picker",
    )
    resume_options.add_argument(
        "--all",
        action="store_true",
        dest="all_workspaces",
        help="Show sessions from all working directories",
    )
    resume_options.add_argument(
        "--include-non-interactive",
        action="store_true",
        help="Include sessions created by non-interactive commands",
    )
    resume_options.add_argument(
        "-i",
        "--image",
        action="append",
        default=[],
        dest="images",
        metavar="FILE",
        help="Image(s) to attach; repeat the option or separate paths with commas",
    )
    resume_options.add_argument(
        "-m",
        "--model",
        metavar="MODEL",
        help="Model the agent should use",
    )
    resume_options.add_argument(
        "-h",
        "--help",
        action="help",
        help=OPTION_HELP,
    )

    batch_parser = subparsers.add_parser(
        "batch",
        prog=f"{const.APP_NAME} batch",
        help=BATCH_HELP,
        description="Load and run one or more schematics in the selected mode.",
        help_title=f"{const.APP_DESC} Batch",
        usage="%(prog)s [OPTIONS] <SOURCE>...",
        add_help=False,
    )
    batch_arguments = batch_parser.add_argument_group("Arguments")
    batch_arguments.add_argument(
        "sources",
        nargs="+",
        metavar="SOURCE",
        help="Schematic file, '-', inline: content, or URL",
    )
    batch_options = batch_parser.add_argument_group("Options")
    batch_options.add_argument(
        "--mode",
        choices=MODES,
        required=True,
        metavar="MODE",
        help="Run mode for the batch",
    )
    batch_options.add_argument(
        "--access",
        choices=sorted(ACCESS_MODE_SET),
        default=DEFAULT_ACCESS_MODE,
        metavar="ACCESS_MODE",
        help=f"Tool access mode [default: {DEFAULT_ACCESS_MODE}]",
    )
    batch_options.add_argument(
        "--helix",
        action="store_true",
        help="Start or reuse the local Helix runtime and attach its MCP tools",
    )
    batch_options.add_argument(
        "-h",
        "--help",
        action="help",
        help=OPTION_HELP,
    )

    agent_parser = subparsers.add_parser(
        "agent",
        prog=f"{const.APP_NAME} agent",
        help=AGENT_HELP,
        description="Manage remote task subscriptions.",
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
        description=(
            "Listen for remotely dispatched tasks and keep the subscription "
            "active."
        ),
        help_title=f"{const.APP_DESC} Agent Listen",
        usage="%(prog)s [OPTIONS]",
        add_help=False,
    )
    listen_options = listen_parser.add_argument_group("Options")
    listen_options.add_argument(
        "--helix",
        action="store_true",
        help="Start or reuse the local Helix runtime and attach its MCP tools",
    )
    listen_options.add_argument(
        "-h",
        "--help",
        action="help",
        help=OPTION_HELP,
    )
    agent_options = agent_parser.add_argument_group("Options")
    agent_options.add_argument(
        "-h",
        "--help",
        action="help",
        help=OPTION_HELP,
    )

    helix_parser = subparsers.add_parser(
        "helix",
        prog=f"{const.APP_NAME} helix",
        help=HELIX_HELP,
        description="Manage Helix provider runtime components.",
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
        description=(
            "Download or update Helix runtime components for this platform."
        ),
        help_title=f"{const.APP_DESC} Helix Upgrade",
        usage="%(prog)s [OPTIONS]",
        add_help=False,
    )
    upgrade_options = upgrade_parser.add_argument_group("Options")
    upgrade_options.add_argument(
        "-h",
        "--help",
        action="help",
        help=OPTION_HELP,
    )
    helix_options = helix_parser.add_argument_group("Options")
    helix_options.add_argument(
        "-h",
        "--help",
        action="help",
        help=OPTION_HELP,
    )

    doctor_parser = subparsers.add_parser(
        "doctor",
        prog=f"{const.APP_NAME} doctor",
        help=DOCTOR_HELP,
        description=(
            "Run read-only checks for configuration, runtime components, and "
            "local tools."
        ),
        help_title=f"{const.APP_DESC} Doctor",
        usage="%(prog)s [OPTIONS]",
        add_help=False,
    )
    doctor_options = doctor_parser.add_argument_group("Options")
    doctor_options.add_argument(
        "--json",
        action="store_true",
        help="Print a single JSON diagnostic report",
    )
    doctor_options.add_argument(
        "-h",
        "--help",
        action="help",
        help=OPTION_HELP,
    )

    mcp_parser = subparsers.add_parser(
        "mcp",
        prog=f"{const.APP_NAME} mcp",
        help=MCP_HELP,
        description="Manage external MCP server registrations.",
        help_title=f"{const.APP_DESC} MCP",
        usage="%(prog)s [OPTIONS] <COMMAND>",
        add_help=False,
    )
    mcp_subparsers = mcp_parser.add_subparsers(
        title="Commands",
        dest="mcp_command",
        metavar="",
    )

    mcp_list_parser = mcp_subparsers.add_parser(
        "list",
        prog=f"{const.APP_NAME} mcp list",
        help="List configured MCP servers",
        description="List configured external MCP servers.",
        help_title=f"{const.APP_DESC} MCP List",
        usage="%(prog)s [OPTIONS]",
        add_help=False,
    )
    mcp_list_options = mcp_list_parser.add_argument_group("Options")
    mcp_list_options.add_argument(
        "--json",
        action="store_true",
        help="Print the server list as JSON",
    )
    mcp_list_options.add_argument(
        "-h",
        "--help",
        action="help",
        help="Print help (see more with '--help')",
    )

    mcp_get_parser = mcp_subparsers.add_parser(
        "get",
        prog=f"{const.APP_NAME} mcp get",
        help="Show one configured MCP server",
        description="Show one external MCP server registration.",
        help_title=f"{const.APP_DESC} MCP Get",
        usage="%(prog)s [OPTIONS] <NAME>",
        add_help=False,
    )
    mcp_get_arguments = mcp_get_parser.add_argument_group("Arguments")
    mcp_get_arguments.add_argument(
        "name",
        metavar="NAME",
        help="Name of the MCP server",
    )
    mcp_get_options = mcp_get_parser.add_argument_group("Options")
    mcp_get_options.add_argument(
        "--json",
        action="store_true",
        help="Print the server configuration as JSON",
    )
    mcp_get_options.add_argument(
        "-h",
        "--help",
        action="help",
        help="Print help (see more with '--help')",
    )

    mcp_add_parser = mcp_subparsers.add_parser(
        "add",
        prog=f"{const.APP_NAME} mcp add",
        help="Add an MCP server registration",
        description=(
            "Register a remote URL or a local stdio command as an external "
            "MCP server."
        ),
        help_title=f"{const.APP_DESC} MCP Add",
        usage=(
            "%(prog)s [OPTIONS] <NAME> --url <URL>\n"
            "       %(prog)s [OPTIONS] <NAME> -- <COMMAND>..."
        ),
        add_help=False,
    )
    mcp_add_arguments = mcp_add_parser.add_argument_group("Arguments")
    mcp_add_arguments.add_argument(
        "name",
        metavar="NAME",
        help="Name of the MCP server",
    )
    mcp_add_arguments.add_argument(
        "stdio_command",
        nargs="*",
        metavar="COMMAND",
        help="Local command and arguments after '--'",
    )
    mcp_add_options = mcp_add_parser.add_argument_group("Options")
    mcp_add_options.add_argument(
        "--url",
        metavar="URL",
        help="URL of a streamable HTTP MCP server",
    )
    mcp_add_options.add_argument(
        "--bearer-token-env-var",
        metavar="ENV_VAR",
        help="Environment variable containing a bearer token for a remote server",
    )
    mcp_add_options.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Environment variable for a stdio server (repeatable)",
    )
    mcp_add_options.add_argument(
        "--header",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="HTTP header for a remote server (repeatable)",
    )
    mcp_add_options.add_argument(
        "--env-http-header",
        action="append",
        default=[],
        dest="env_http_headers",
        metavar="HEADER=ENV_VAR",
        help="HTTP header sourced from an environment variable (repeatable)",
    )
    mcp_add_options.add_argument(
        "--cwd",
        metavar="DIR",
        help="Working directory for a stdio server",
    )
    mcp_add_options.add_argument(
        "--disabled",
        action="store_true",
        help="Register the server without enabling it",
    )
    mcp_add_options.add_argument(
        "--required",
        action="store_true",
        help="Fail startup when this server cannot be initialized",
    )
    mcp_add_options.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Allow a tool name or glob pattern (repeatable)",
    )
    mcp_add_options.add_argument(
        "--deny",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Deny a tool name or glob pattern (repeatable)",
    )
    mcp_add_options.add_argument(
        "--startup-timeout-sec",
        type=float,
        metavar="SECONDS",
        help="Startup and discovery timeout in seconds",
    )
    mcp_add_options.add_argument(
        "--tool-timeout-sec",
        type=float,
        metavar="SECONDS",
        help="Tool request timeout in seconds",
    )
    mcp_add_options.add_argument(
        "-h",
        "--help",
        action="help",
        help="Print help (see more with '--help')",
    )

    mcp_remove_parser = mcp_subparsers.add_parser(
        "remove",
        prog=f"{const.APP_NAME} mcp remove",
        help="Remove an MCP server registration",
        description="Remove an external MCP server registration.",
        help_title=f"{const.APP_DESC} MCP Remove",
        usage="%(prog)s [OPTIONS] <NAME>",
        add_help=False,
    )
    mcp_remove_arguments = mcp_remove_parser.add_argument_group("Arguments")
    mcp_remove_arguments.add_argument(
        "name",
        metavar="NAME",
        help="Name of the MCP server",
    )
    mcp_remove_options = mcp_remove_parser.add_argument_group("Options")
    mcp_remove_options.add_argument(
        "-h",
        "--help",
        action="help",
        help="Print help (see more with '--help')",
    )

    mcp_enable_parser = mcp_subparsers.add_parser(
        "enable",
        prog=f"{const.APP_NAME} mcp enable",
        help="Enable an MCP server",
        description="Enable a configured external MCP server.",
        help_title=f"{const.APP_DESC} MCP Enable",
        usage="%(prog)s [OPTIONS] <NAME>",
        add_help=False,
    )
    mcp_enable_arguments = mcp_enable_parser.add_argument_group("Arguments")
    mcp_enable_arguments.add_argument(
        "name",
        metavar="NAME",
        help="Name of the MCP server",
    )
    mcp_enable_options = mcp_enable_parser.add_argument_group("Options")
    mcp_enable_options.add_argument(
        "-h",
        "--help",
        action="help",
        help="Print help (see more with '--help')",
    )

    mcp_disable_parser = mcp_subparsers.add_parser(
        "disable",
        prog=f"{const.APP_NAME} mcp disable",
        help="Disable an MCP server",
        description="Disable a configured external MCP server.",
        help_title=f"{const.APP_DESC} MCP Disable",
        usage="%(prog)s [OPTIONS] <NAME>",
        add_help=False,
    )
    mcp_disable_arguments = mcp_disable_parser.add_argument_group("Arguments")
    mcp_disable_arguments.add_argument(
        "name",
        metavar="NAME",
        help="Name of the MCP server",
    )
    mcp_disable_options = mcp_disable_parser.add_argument_group("Options")
    mcp_disable_options.add_argument(
        "-h",
        "--help",
        action="help",
        help="Print help (see more with '--help')",
    )

    mcp_help_parser = mcp_subparsers.add_parser(
        "help",
        prog=f"{const.APP_NAME} mcp help",
        help=HELP_HELP,
        description="Print help for the MCP command or one of its subcommands.",
        help_title=f"{const.APP_DESC} MCP Help",
        usage="%(prog)s [COMMAND]",
        add_help=False,
    )
    mcp_help_arguments = mcp_help_parser.add_argument_group("Arguments")
    mcp_help_arguments.add_argument(
        "mcp_help_topic",
        nargs="?",
        metavar="COMMAND",
        help="MCP subcommand to show",
    )
    mcp_help_options = mcp_help_parser.add_argument_group("Options")
    mcp_help_options.add_argument(
        "-h",
        "--help",
        action="help",
        help="Print help (see more with '--help')",
    )

    mcp_options = mcp_parser.add_argument_group("Options")
    mcp_options.add_argument(
        "-h",
        "--help",
        action="help",
        help="Print help (see more with '--help')",
    )

    mcp_server_parser = subparsers.add_parser(
        "mcp-server",
        prog=f"{const.APP_NAME} mcp-server",
        help=MCP_SERVER_HELP,
        description=(
            f"Expose the {const.APP_DESC} agent tool over standard input and "
            "output."
        ),
        help_title=f"{const.APP_DESC} MCP Server",
        usage="%(prog)s [OPTIONS]",
        add_help=False,
    )
    mcp_server_options = mcp_server_parser.add_argument_group("Options")
    mcp_server_options.add_argument(
        "-h",
        "--help",
        action="help",
        help=OPTION_HELP,
    )

    completion_parser = subparsers.add_parser(
        "completion",
        prog=f"{const.APP_NAME} completion",
        help=COMPLETION_HELP,
        description="Generate a completion script for the selected shell.",
        help_title=f"{const.APP_DESC} Completion",
        usage="%(prog)s [OPTIONS] [SHELL]",
        add_help=False,
    )
    completion_arguments = completion_parser.add_argument_group("Arguments")
    completion_arguments.add_argument(
        "shell",
        nargs="?",
        choices=COMPLETION_SHELLS,
        default="bash",
        metavar="SHELL",
        help=(
            "Shell to generate completions for [default: bash; possible "
            "values: bash, elvish, fish, powershell, zsh]"
        ),
    )
    completion_options = completion_parser.add_argument_group("Options")
    completion_options.add_argument(
        "-h",
        "--help",
        action="help",
        help=OPTION_HELP,
    )

    help_parser = subparsers.add_parser(
        "help",
        prog=f"{const.APP_NAME} help",
        help=HELP_HELP,
        description="Print full help for the root command or a subcommand.",
        help_title=f"{const.APP_DESC} Help",
        usage="%(prog)s [COMMAND]...",
        add_help=False,
    )
    help_arguments = help_parser.add_argument_group("Arguments")
    help_arguments.add_argument(
        "help_topics",
        nargs="*",
        metavar="COMMAND",
        help="Command path to show, for example agent listen",
    )
    help_options = help_parser.add_argument_group("Options")
    help_options.add_argument(
        "-h",
        "--help",
        action="help",
        help=OPTION_HELP,
    )

    root_arguments = parser.add_argument_group("Arguments")
    root_arguments.add_argument(
        "root_prompt",
        nargs="?",
        metavar="PROMPT",
        help="Optional user prompt to start the session",
    )

    root_options = parser.add_argument_group("Options")
    add_invocation_options(root_options)
    root_options.add_argument(
        "-i",
        "--image",
        action="append",
        default=[],
        dest="root_images",
        metavar="FILE",
        help="Image(s) to attach; repeat the option or separate paths with commas",
    )
    root_options.add_argument(
        "-m",
        "--model",
        dest="root_model",
        metavar="MODEL",
        help="Model the agent should use",
    )
    root_options.add_argument(
        "-h",
        "--help",
        action="help",
        help=OPTION_HELP,
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
        ("e",): exec_parser,
        ("resume",): resume_parser,
        ("batch",): batch_parser,
        ("agent",): agent_parser,
        ("agent", "listen"): listen_parser,
        ("helix",): helix_parser,
        ("helix", "upgrade"): upgrade_parser,
        ("doctor",): doctor_parser,
        ("mcp",): mcp_parser,
        ("mcp", "list"): mcp_list_parser,
        ("mcp", "get"): mcp_get_parser,
        ("mcp", "add"): mcp_add_parser,
        ("mcp", "remove"): mcp_remove_parser,
        ("mcp", "enable"): mcp_enable_parser,
        ("mcp", "disable"): mcp_disable_parser,
        ("mcp", "help"): mcp_help_parser,
        ("mcp-server",): mcp_server_parser,
        ("completion",): completion_parser,
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
    stdin_is_interactive = _stream_is_interactive(input_stream)

    if prompt == "-" or (prompt is None and not stdin_is_interactive):
        value = input_stream.read()
        normalized = str(value or "").strip()
        if normalized:
            return normalized
        parser.error(
            f"{const.APP_NAME} exec requires PROMPT or non-empty stdin"
        )

    normalized_prompt = str(prompt or "").strip()
    if not normalized_prompt:
        parser.error(
            f"{const.APP_NAME} exec requires PROMPT or non-empty stdin"
        )

    if not stdin_is_interactive:
        try:
            stdin_text = str(input_stream.read() or "")
        except (OSError, ValueError):
            stdin_text = ""

        if stdin_text.strip():
            combined = f"{normalized_prompt}\n\n<stdin>\n{stdin_text}"
            if not stdin_text.endswith("\n"):
                combined += "\n"
            return combined + "</stdin>"

    return normalized_prompt


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


def _image_paths(
    parser: argparse.ArgumentParser,
    values: dict[str, object],
    key: str = "images",
) -> tuple[str, ...]:
    """读取并验证图片附件路径。"""
    images = values.get(key, [])
    if not isinstance(images, list) or not all(
        isinstance(image, str) for image in images
    ):
        parser.error("invalid image arguments")

    paths: list[str] = []
    for image in images:
        for item in image.split(","):
            path = item.strip()
            if not path:
                parser.error("invalid image arguments")
            paths.append(path)
    return tuple(paths)


def _merged_image_paths(
    parser: argparse.ArgumentParser,
    values: dict[str, object],
) -> tuple[str, ...]:
    """按根选项和子命令选项的顺序合并图片路径。"""
    return (
        *_image_paths(parser, values, "root_images"),
        *_image_paths(parser, values),
    )


def _selected_model(
    parser: argparse.ArgumentParser,
    values: dict[str, object],
) -> str | None:
    """优先返回子命令模型，否则返回根命令模型。"""
    model = _optional_string(parser, values, "model")
    if model is None:
        model = _optional_string(parser, values, "root_model")
    return str(model or "").strip() or None


def _completion_shell(
    parser: argparse.ArgumentParser,
    values: dict[str, object],
) -> CompletionShell:
    """读取并验证 shell 补全目标。"""
    value = _required_string(parser, values, "shell")
    for shell in COMPLETION_SHELLS:
        if value == shell:
            return shell
    parser.error(f"unsupported completion shell: {value}")


def _required_string(
    parser: argparse.ArgumentParser,
    values: dict[str, object],
    key: str,
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
    key: str,
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
    key: str,
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
    key: str,
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
    key: str,
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
    values: dict[str, object],
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
    if _contains_root_command(arguments):
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
        action="append",
        default=[],
        dest="images",
        metavar="FILE",
    )
    interactive_parser.add_argument("-m", "--model", metavar="MODEL")
    interactive_parser.add_argument("prompt", nargs="?")

    namespace = interactive_parser.parse_args(arguments)

    values: dict[str, object] = vars(namespace)

    prompt = _optional_string(parser, values, "prompt")
    model  = _optional_string(parser, values, "model")

    return InteractiveCommand(
        prompt=(prompt.strip() or None) if prompt is not None else None,
        images=_image_paths(parser, values),
        model=(model.strip() or None) if model is not None else None,
    )


def _contains_root_command(arguments: tuple[str, ...]) -> bool:
    """判断参数中的首个位置项是否为已注册根命令。"""
    value_options = {"-i", "--image", "-m", "--model"}
    value_prefixes = ("--image=", "--model=")

    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token == "--":
            return False
        if token in value_options:
            index += 2
            continue
        if token.startswith(value_prefixes) or (
            token.startswith(("-i", "-m")) and len(token) > 2
        ):
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        return token in ROOT_COMMANDS

    return False


def _split_mcp_stdio_command(
    arguments: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    """从 MCP add 参数中分离双横线后的 stdio 命令。"""
    if arguments[:2] != ("mcp", "add"):
        return arguments, (), False
    try:
        separator = arguments.index("--", 2)
    except ValueError:
        return arguments, (), False

    return arguments[:separator], arguments[separator + 1:], True


def _parse_cli_command(
    parser: CliArgumentParser,
    arguments: typing.Sequence[str],
    *,
    input_stream: typing.TextIO | None = None,
) -> ParsedCommand:
    """解析参数并返回强类型命令。"""
    raw_arguments       = tuple(arguments)
    interactive_command = _interactive_command(parser, raw_arguments)

    if interactive_command is not None:
        return interactive_command

    parser_arguments, stdio_command, stdio_separated = (
        _split_mcp_stdio_command(raw_arguments)
    )

    namespace = parser.parse_args(parser_arguments)

    values: dict[str, object] = vars(namespace)
    values["stdio_separated"] = stdio_separated

    if stdio_command:
        values["stdio_command"] = list(stdio_command)

    command = _optional_string(parser, values, "command")
    if command is None:
        return InteractiveCommand(
            prompt=_optional_string(parser, values, "root_prompt"),
            images=_image_paths(parser, values, "root_images"),
            model=_selected_model(parser, values),
        )

    if command in {"exec", "e"}:
        prompt = _read_exec_prompt(
            parser,
            _optional_string(parser, values, "prompt"),
            sys.stdin if input_stream is None else input_stream,
        )

        output_format: OutputFormat = "json" if bool(values["json"]) else "text"

        return ExecCommand(
            prompt=prompt,
            images=_merged_image_paths(parser, values),
            model=_selected_model(parser, values),
            mode=_run_mode(parser, values),
            access_mode=_access_mode(parser, values),
            output_format=output_format,
            helix=bool(values["helix"]),
        )

    if command == "resume":
        session_id = _optional_string(parser, values, "session_id")
        prompt     = _optional_string(parser, values, "resume_prompt")
        model      = _selected_model(parser, values)
        last       = bool(values["last"])

        if last and session_id is not None:
            if prompt is not None:
                parser.error("--last cannot be used with SESSION_ID")
            prompt = session_id
            session_id = None

        session_id = str(session_id or "").strip() or None
        prompt     = str(prompt or "").strip() or None

        return ResumeCommand(
            session_id=session_id,
            prompt=prompt,
            images=_merged_image_paths(parser, values),
            model=model,
            last=last,
            all_workspaces=bool(values["all_workspaces"]),
            include_non_interactive=bool(values["include_non_interactive"]),
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

    if command == "mcp":
        mcp_command = _optional_string(parser, values, "mcp_command")
        if mcp_command is None:
            parser.print_command_help(("mcp",))

        if mcp_command == "list":
            output_format: OutputFormat = (
                "json" if bool(values["json"]) else "text"
            )
            return McpListCommand(output_format=output_format)

        if mcp_command == "get":
            output_format: OutputFormat = (
                "json" if bool(values["json"]) else "text"
            )
            return McpGetCommand(
                name=_required_string(parser, values, "name"),
                output_format=output_format,
            )

        if mcp_command == "add":
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

        if mcp_command == "remove":
            return McpRemoveCommand(
                name=_required_string(parser, values, "name"),
            )

        if mcp_command in {"enable", "disable"}:
            return McpSetEnabledCommand(
                name=_required_string(parser, values, "name"),
                enabled=mcp_command == "enable",
            )

        if mcp_command == "help":
            topic = _optional_string(parser, values, "mcp_help_topic")
            path = ("mcp",) if topic is None else ("mcp", topic)
            parser.print_command_help(path)

    if command == "mcp-server":
        return McpServerCommand()

    if command == "completion":
        return CompletionCommand(shell=_completion_shell(parser, values))

    if command == "help":
        parser.print_command_help(_help_topics(parser, values))

    parser.error(f"unsupported command: {command}")


def parse_cli_invocation(
    arguments: typing.Sequence[str] | None = None,
    *,
    input_stream: typing.TextIO | None = None,
) -> CliInvocation:
    """解析命令及其进程级配置覆盖。"""
    raw_arguments = tuple(
        sys.argv[1:] if arguments is None else arguments
    )

    parser = create_cli_parser()

    command_arguments, overrides, profile = extract_invocation_options(
        parser,
        raw_arguments,
    )

    return CliInvocation(
        command=_parse_cli_command(
            parser,
            command_arguments,
            input_stream=input_stream,
        ),
        config_overrides=overrides,
        profile=profile,
    )


def parse_cli_command(
    arguments: typing.Sequence[str] | None = None,
    *,
    input_stream: typing.TextIO | None = None,
) -> ParsedCommand:
    """解析参数并返回强类型命令。"""
    return parse_cli_invocation(
        arguments,
        input_stream=input_stream,
    ).command


if __name__ == '__main__':
    pass
