# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import typing
import argparse
from metadata import const
from .arguments import (
    HELIX_FLAGS,
    PROMPT_SHORT_VALUE_PREFIXES,
    PROMPT_VALUE_OPTIONS,
    PROMPT_VALUE_PREFIXES,
    create_cli_parser,
    create_interactive_parser,
    root_command_names,
)
from .commands import (
    AgentListenCommand,
    CliInvocation,
    CompletionCommand,
    COMPLETION_SHELLS,
    CompletionShell,
    DoctorCommand,
    ExecCommand,
    HELIX_PROFILES,
    InteractiveCommand,
    McpServerCommand,
    OutputFormat,
    ParsedCommand,
    ResumeCommand,
    RuntimeUpgradeCommand,
    SessionArchiveCommand,
)
from .help import CliArgumentParser
from .invocation import extract_invocation_options
from .mcp_parser import (
    parse_mcp_command,
    split_mcp_stdio_command,
)


def _stream_is_interactive(stream: typing.TextIO) -> bool:
    """判断输入流是否连接到交互终端。"""
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError, ValueError):
        return False


def _read_exec_prompt(
    parser: argparse.ArgumentParser,
    prompt: str | None,
    input_stream: typing.TextIO
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
    key: str
) -> str | None:
    """读取一个可选字符串参数。"""
    value = values.get(key)
    if value is None or isinstance(value, str):
        return value
    parser.error(f"invalid {key}: expected string")


def _image_paths(
    parser: argparse.ArgumentParser,
    values: dict[str, object],
    key: str = "images"
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
    values: dict[str, object]
) -> tuple[str, ...]:
    """按根选项和子命令选项的顺序合并图片路径。"""
    return (
        *_image_paths(parser, values, "root_images"),
        *_image_paths(parser, values),
    )


def _selected_model(
    parser: argparse.ArgumentParser,
    values: dict[str, object]
) -> str | None:
    """优先返回子命令模型，否则返回根命令模型。"""
    model = _optional_string(parser, values, "model")
    if model is None:
        model = _optional_string(parser, values, "root_model")
    return str(model or "").strip() or None


def _selected_helix_profile(
    parser: argparse.ArgumentParser,
    values: dict[str, object]
) -> typing.Literal["app", "api"] | None:
    """优先返回子命令的 Helix 配置，否则返回根命令配置。"""
    profile = _optional_string(parser, values, "helix_profile")
    if profile is None:
        profile = _optional_string(parser, values, "root_helix_profile")
    if profile is None:
        return None
    if profile in HELIX_PROFILES:
        return typing.cast(typing.Literal["app", "api"], profile)
    parser.error(f"invalid Helix profile: {profile}")


def _normalize_helix_arguments(
    arguments: tuple[str, ...]
) -> tuple[str, ...]:
    """消除可选 Helix 配置与位置参数之间的解析歧义。"""
    normalized: list[str] = []

    index: int = 0
    while index < len(arguments):
        token = arguments[index]
        if token == "--":
            normalized.extend(arguments[index:])
            break
        if token not in HELIX_FLAGS:
            normalized.append(token)
            index += 1
            continue

        profile = "app"
        if index + 1 < len(arguments) and arguments[index + 1] in HELIX_PROFILES:
            profile = arguments[index + 1]
            index += 1

        normalized.append(f"--helix={profile}")
        index += 1

    return tuple(normalized)


def _completion_shell(
    parser: argparse.ArgumentParser,
    values: dict[str, object]
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
    key: str
) -> str:
    """读取并验证一个非空字符串参数。"""
    value = _optional_string(parser, values, key)

    normalized = str(value or "").strip()
    if normalized:
        return normalized

    parser.error(f"invalid {key}: expected non-empty string")


def _help_topics(
    parser: argparse.ArgumentParser,
    values: dict[str, object]
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
    parser: CliArgumentParser,
    arguments: tuple[str, ...]
) -> InteractiveCommand | None:
    """解析不含子命令的交互入口参数。"""
    if _contains_root_command(parser, arguments):
        return None
    if any(argument in {"-h", "--help", "-V", "--version"} for argument in arguments):
        return None

    interactive_parser = create_interactive_parser()

    namespace = interactive_parser.parse_args(arguments)

    values: dict[str, object] = vars(namespace)

    prompt = _optional_string(parser, values, "prompt")
    model = _optional_string(parser, values, "model")

    return InteractiveCommand(
        prompt=(prompt.strip() or None) if prompt is not None else None,
        images=_image_paths(parser, values),
        model=(model.strip() or None) if model is not None else None,
        helix_profile=_selected_helix_profile(parser, values),
    )


def _contains_root_command(
    parser: CliArgumentParser,
    arguments: tuple[str, ...]
) -> bool:
    """判断参数中的首个位置项是否为已注册根命令。"""
    commands = root_command_names(parser)

    index: int = 0
    while index < len(arguments):
        token = arguments[index]
        if token == "--":
            return False
        if token in PROMPT_VALUE_OPTIONS:
            index += 2
            continue
        if token.startswith(PROMPT_VALUE_PREFIXES) or (
            token.startswith(PROMPT_SHORT_VALUE_PREFIXES) and len(token) > 2
        ):
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        return token in commands

    return False


def _parse_cli_command(
    parser: CliArgumentParser,
    arguments: typing.Sequence[str],
    *,
    input_stream: typing.TextIO | None = None
) -> ParsedCommand:
    """解析参数并返回强类型命令。"""
    raw_arguments = _normalize_helix_arguments(tuple(arguments))
    interactive_command = _interactive_command(parser, raw_arguments)

    if interactive_command is not None:
        return interactive_command

    parser_arguments, stdio_command, stdio_separated = (
        split_mcp_stdio_command(raw_arguments)
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
            helix_profile=_selected_helix_profile(parser, values),
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
            output_format=output_format,
            helix_profile=_selected_helix_profile(parser, values),
            bypass_hook_trust=bool(values["bypass_hook_trust"]),
        )

    if command == "resume":
        session_id = _optional_string(parser, values, "session_id")
        prompt = _optional_string(parser, values, "resume_prompt")
        model = _selected_model(parser, values)
        last = bool(values["last"])

        if last and session_id is not None:
            if prompt is not None:
                parser.error("--last cannot be used with SESSION_ID")
            prompt = session_id
            session_id = None

        session_id = str(session_id or "").strip() or None
        prompt = str(prompt or "").strip() or None

        return ResumeCommand(
            session_id=session_id,
            prompt=prompt,
            images=_merged_image_paths(parser, values),
            model=model,
            last=last,
            all_workspaces=bool(values["all_workspaces"]),
            include_non_interactive=bool(values["include_non_interactive"]),
            helix_profile=_selected_helix_profile(parser, values),
        )

    if command in {"archive", "unarchive"}:
        return SessionArchiveCommand(
            action=command,
            target=_required_string(parser, values, "target"),
        )

    if command == "agent" and values.get("agent_command") == "listen":
        return AgentListenCommand(
            helix_profile=_selected_helix_profile(parser, values),
        )

    if command == "upgrade" and values.get("upgrade_component") == "helix":
        return RuntimeUpgradeCommand()

    if command == "doctor":
        output_format: OutputFormat = "json" if bool(values["json"]) else "text"
        return DoctorCommand(output_format=output_format)

    if command == "mcp":
        return parse_mcp_command(parser, values)

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
    input_stream: typing.TextIO | None = None
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
    input_stream: typing.TextIO | None = None
) -> ParsedCommand:
    """解析参数并返回强类型命令。"""
    return parse_cli_invocation(
        arguments,
        input_stream=input_stream,
    ).command


if __name__ == "__main__":
    pass
