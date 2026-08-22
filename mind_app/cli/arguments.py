# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import argparse
from mind_nova import const
from .commands import (
    COMPLETION_SHELLS,
    HELIX_PROFILES
)
from .help import CliArgumentParser
from .invocation import (
    ArgumentContainer,
    add_invocation_options
)


EXEC_HELP          = "Run a task non-interactively"
RESUME_HELP        = "Resume a previous interactive session"
ARCHIVE_HELP       = "Archive a previous interactive session"
UNARCHIVE_HELP     = "Restore an archived interactive session"
COMPLETION_HELP    = "Generate shell completion scripts"
AGENT_HELP         = "Manage remote task subscriptions"
AGENT_LISTEN_HELP  = "Listen for remotely dispatched tasks"
UPGRADE_HELP       = "Manage runtime component upgrades"
UPGRADE_HELIX_HELP = "Update Helix runtime components"
DOCTOR_HELP        = "Diagnose the local runtime environment"
MCP_HELP           = "Manage external MCP servers"
MCP_SERVER_HELP    = "Start the MCP server over stdio"
HELP_HELP          = "Print this message or the help of the given subcommand(s)"
OPTION_HELP        = "Print help (see a summary with '-h')"

IMAGE_FLAGS = ("-i", "--image")
MODEL_FLAGS = ("-m", "--model")
HELIX_FLAGS = ("-H", "--helix")

PROMPT_VALUE_OPTIONS        = frozenset((*IMAGE_FLAGS, *MODEL_FLAGS))
PROMPT_VALUE_PREFIXES       = ("--image=", "--model=")
PROMPT_SHORT_VALUE_PREFIXES = ("-i", "-m")


def _add_prompt_context_options(
    container: ArgumentContainer,
    *,
    image_dest: str = "images",
    model_dest: str = "model"
) -> None:
    """登记图片和模型选项。"""
    container.add_argument(
        *IMAGE_FLAGS,
        action="append",
        default=[],
        dest=image_dest,
        metavar="FILE",
        help="Image(s) to attach; repeat the option or separate paths with commas",
    )
    container.add_argument(
        *MODEL_FLAGS,
        dest=model_dest,
        metavar="MODEL",
        help="Model the agent should use",
    )


def _add_helix_option(
    container: ArgumentContainer,
    *,
    dest: str = "helix_profile",
) -> None:
    """登记可选工具过滤配置的 Helix 接入参数。"""
    container.add_argument(
        *HELIX_FLAGS,
        nargs="?",
        const="app",
        default=None,
        choices=HELIX_PROFILES,
        dest=dest,
        metavar="PROFILE",
        help=(
            "Start or reuse the local Helix runtime and attach its MCP tools. "
            "When PROFILE is omitted, app is used "
            "[possible values: app, api]"
        ),
    )


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
        "--json",
        action="store_true",
        help="Print newline-delimited JSON events",
    )
    exec_options.add_argument(
        "--dangerously-bypass-hook-trust",
        action="store_true",
        dest="bypass_hook_trust",
        help=(
            "Run enabled hooks without requiring their current content to "
            "be trusted"
        ),
    )
    _add_helix_option(exec_options)
    _add_prompt_context_options(exec_options)
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
    _add_helix_option(resume_options)
    _add_prompt_context_options(resume_options)
    resume_options.add_argument(
        "-h",
        "--help",
        action="help",
        help=OPTION_HELP,
    )

    archive_parsers: dict[str, CliArgumentParser] = {}
    for archive_action, archive_help in (
        ("archive", ARCHIVE_HELP),
        ("unarchive", UNARCHIVE_HELP),
    ):
        archive_parser = subparsers.add_parser(
            archive_action,
            prog=f"{const.APP_NAME} {archive_action}",
            help=archive_help,
            description=(
                f"{archive_help}. The target may be a session id or its title."
            ),
            help_title=f"{const.APP_DESC} {archive_action.title()}",
            usage="%(prog)s SESSION_ID_OR_TITLE",
            add_help=False,
        )
        archive_arguments = archive_parser.add_argument_group("Arguments")
        archive_arguments.add_argument(
            "target",
            metavar="SESSION_ID_OR_TITLE",
            help="Session id or exact history title",
        )
        archive_options = archive_parser.add_argument_group("Options")
        archive_options.add_argument(
            "-h",
            "--help",
            action="help",
            help=OPTION_HELP,
        )
        archive_parsers[archive_action] = archive_parser

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
    _add_helix_option(listen_options)
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

    upgrade_parser = subparsers.add_parser(
        "upgrade",
        prog=f"{const.APP_NAME} upgrade",
        help=UPGRADE_HELP,
        description="Manage runtime component upgrades.",
        help_title=f"{const.APP_DESC} Upgrade",
        usage="%(prog)s <COMPONENT> [ARGS]",
        add_help=False,
    )
    upgrade_subparsers = upgrade_parser.add_subparsers(
        title="Components",
        dest="upgrade_component",
        metavar="",
        required=True,
    )
    upgrade_helix_parser = upgrade_subparsers.add_parser(
        "helix",
        prog=f"{const.APP_NAME} upgrade helix",
        help=UPGRADE_HELIX_HELP,
        description=(
            "Download or update Helix runtime components for this platform."
        ),
        help_title=f"{const.APP_DESC} Upgrade Helix",
        usage="%(prog)s [OPTIONS]",
        add_help=False,
    )
    upgrade_helix_options = upgrade_helix_parser.add_argument_group("Options")
    upgrade_helix_options.add_argument(
        "-h",
        "--help",
        action="help",
        help=OPTION_HELP,
    )
    upgrade_options = upgrade_parser.add_argument_group("Options")
    upgrade_options.add_argument(
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
    _add_prompt_context_options(
        root_options,
        image_dest="root_images",
        model_dest="root_model",
    )
    _add_helix_option(root_options, dest="root_helix_profile")
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
        ("archive",): archive_parsers["archive"],
        ("unarchive",): archive_parsers["unarchive"],
        ("agent",): agent_parser,
        ("agent", "listen"): listen_parser,
        ("upgrade",): upgrade_parser,
        ("upgrade", "helix"): upgrade_helix_parser,
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


def create_interactive_parser() -> argparse.ArgumentParser:
    """创建不含子命令的交互入口解析器。"""
    parser = argparse.ArgumentParser(
        prog=const.APP_NAME,
        add_help=False,
    )
    _add_prompt_context_options(parser)
    _add_helix_option(parser)
    parser.add_argument("prompt", nargs="?")
    return parser


def root_command_names(parser: CliArgumentParser) -> frozenset[str]:
    """返回命令树中已登记的根命令名称。"""
    return frozenset(
        path[0]
        for path in parser.registered_command_parsers()
        if len(path) == 1
    )


if __name__ == "__main__":
    pass
