# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from mind_core.skills import SkillSpec
from prompt_toolkit.completion import (
    Completer,
    Completion
)
from .skills import (
    skill_completions,
    skill_query_token
)

StreamCommandPolicy = typing.Literal[
    "reject",
    "background_barrier",
    "interrupt",
    "local_snapshot",
    "interactive_panel",
]


@dataclass(frozen=True, slots=True)
class TuiCommandSpec(object):
    """描述一项 TUI 命令的输入和别名信息。"""
    key: str
    command: str
    completion_meta: str
    aliases: tuple[str, ...] = ()
    completion_text: str | None = None
    parameterized: bool = False
    subcommands: tuple[str, ...] = ()
    surface_on_bare: bool = False
    stream_policy: StreamCommandPolicy = "reject"

    @property
    def names(self) -> tuple[str, ...]:
        """返回规范命令及其全部可执行别名。"""
        return self.command, *self.aliases

    @property
    def insertion_text(self) -> str:
        """返回补全选中后写入输入框的文本。"""
        return self.completion_text or self.command


TUI_COMMANDS: typing.Final[tuple[TuiCommandSpec, ...]] = (
    TuiCommandSpec(
        "new", "/new", "开始新对话",
    ),
    TuiCommandSpec(
        "resume", "/resume", "恢复最近会话",
        surface_on_bare=True,
    ),
    TuiCommandSpec(
        "fork", "/fork", "复制当前对话上下文",
    ),
    TuiCommandSpec(
        "permissions", "/permissions", "切换权限模式",
        surface_on_bare=True,
    ),
    TuiCommandSpec(
        "model", "/model", "设置主模型 ID",
        completion_text="/model ",
        parameterized=True,
    ),
    TuiCommandSpec(
        "effort", "/effort", "设置主模型推理强度",
        surface_on_bare=True,
    ),
    TuiCommandSpec(
        "preferences", "/preferences", "打开偏好配置页面",
    ),
    TuiCommandSpec(
        "compact", "/compact", "压缩当前对话上下文",
    ),
    TuiCommandSpec(
        "tools", "/tools", "查看可用 MCP 工具",
    ),
    TuiCommandSpec(
        "hooks", "/hooks", "管理生命周期 Hooks",
        surface_on_bare=True,
    ),
    TuiCommandSpec(
        "agent", "/agent", "查看和管理子代理线程",
        surface_on_bare=True,
        stream_policy="interactive_panel",
    ),
    TuiCommandSpec(
        "diff", "/diff", "查看本轮补丁净差异",
    ),
    TuiCommandSpec(
        "copy", "/copy", "复制最近一次助手回复原文",
    ),
    TuiCommandSpec(
        "ps", "/ps", "管理后台命令",
        surface_on_bare=True,
        stream_policy="local_snapshot",
    ),
    TuiCommandSpec(
        "mcp", "/mcp", "管理外部 MCP 服务",
        subcommands=("start", "force", "stop", "restart", "status"),
        surface_on_bare=True,
    ),
    TuiCommandSpec(
        "helix_link", "/helix-link", "接入 Helix MCP",
        surface_on_bare=True,
        stream_policy="background_barrier",
    ),
    TuiCommandSpec(
        "helix_mode", "/helix-mode", "选择 Helix 工具过滤模式",
        surface_on_bare=True,
    ),
    TuiCommandSpec(
        "helix_unlink", "/helix-unlink", "移除 Helix MCP",
    ),
    TuiCommandSpec(
        "helix_home", "/helix-home", "打开 Helix 首页",
    ),
    TuiCommandSpec(
        "helix_stop", "/helix-stop", "停止 Helix 服务",
    ),
    TuiCommandSpec(
        "skills", "/skills", "打开 skills 列表",
        surface_on_bare=True,
    ),
    TuiCommandSpec(
        "shutdown", "/shutdown", "停止本地后台服务并退出",
        stream_policy="interrupt",
    ),
    TuiCommandSpec(
        "quit", "/quit", "退出会话",
        aliases=("/q", "quit", "exit"),
        stream_policy="interrupt",
    ),
)

_COMMAND_BY_KEY: typing.Final[dict[str, TuiCommandSpec]] = {
    command.key: command for command in TUI_COMMANDS
}

_COMMAND_NAMES_BY_KEY: typing.Final[dict[str, frozenset[str]]] = {
    key: frozenset(command.names)
    for key, command in _COMMAND_BY_KEY.items()
}

_COMMAND_BY_NAME: typing.Final[dict[str, TuiCommandSpec]] = {
    name.casefold(): command
    for command in TUI_COMMANDS
    for name in command.names
}


def command_spec(key: str) -> TuiCommandSpec:
    """返回指定标识对应的命令描述。"""
    return _COMMAND_BY_KEY[key]


def command_names(key: str) -> frozenset[str]:
    """返回指定命令接受的规范名称和别名。"""
    return _COMMAND_NAMES_BY_KEY[key]


def matches_command(value: str, key: str) -> bool:
    """判断输入值是否匹配指定命令。"""
    return value in command_names(key)


def resolve_tui_command(value: str) -> TuiCommandSpec | None:
    """返回完整输入匹配的 TUI 命令描述。"""
    normalized = str(value or "").strip().casefold()
    if not normalized:
        return None

    direct = _COMMAND_BY_NAME.get(normalized)
    if direct is not None:
        return direct

    head, separator, tail = normalized.partition(" ")

    command  = _COMMAND_BY_NAME.get(head)
    argument = tail.strip()

    if command is None or not separator or not argument:
        return None
    if command.parameterized or argument in command.subcommands:
        return command

    return None


def resolve_slash_command(value: str) -> TuiCommandSpec | None:
    """返回完整输入匹配的斜杠命令描述。"""
    normalized = str(value or "").strip()
    if not normalized.startswith("/"):
        return None
    return resolve_tui_command(normalized)


def canonical_command_label(value: str) -> str:
    """返回命令输入对应的规范展示名称。"""
    normalized = str(value or "").strip().casefold()

    command = resolve_tui_command(normalized)
    if command is None:
        return normalized.split(maxsplit=1)[0] if normalized else "command"

    parts = normalized.split()

    if command.subcommands and len(parts) >= 2:
        return f"{command.command} {parts[1]}"

    return command.command


def is_unrecognized_slash_command(value: str) -> bool:
    """判断输入是否是非空且未注册的斜杠命令。"""
    normalized = str(value or "").strip()

    return bool(
        normalized.startswith("/")
        and normalized != "/"
        and resolve_slash_command(normalized) is None
    )


def unrecognized_slash_command_message(value: str) -> str:
    """生成未知斜杠命令提示。"""
    command = str(value or "").strip().split(maxsplit=1)[0]

    return (
        f"Unrecognized command '{command}'. "
        'Type "/" for a list of supported commands.'
    )


def slash_command_notice_message(value: str) -> str:
    """返回无效斜杠输入需要展示的提示。"""
    normalized = str(value or "").strip()
    if normalized == "/":
        return "Choose a slash command from the menu or type its full name."
    if is_unrecognized_slash_command(normalized):
        return unrecognized_slash_command_message(normalized)
    return ""


def parameterized_command_texts() -> tuple[str, ...]:
    """返回选中补全后继续保留编辑状态的命令文本。"""
    return tuple(
        command.insertion_text
        for command in TUI_COMMANDS
        if command.parameterized
    )


def stream_command_policy(value: str) -> StreamCommandPolicy | None:
    """返回输入在流式或前台忙碌期间使用的命令策略。"""
    normalized = str(value or "").strip().casefold()
    if not normalized:
        return None
    if normalized.startswith("!"):
        return "reject"

    direct = _COMMAND_BY_NAME.get(normalized)
    if direct is not None:
        return direct.stream_policy

    parts = normalized.split()
    head  = parts[0]

    if head in command_names("mcp") and len(parts) == 2:
        if parts[1] in {"start", "force"}:
            return "background_barrier"
        return "reject"
    if head.startswith("/"):
        return "reject"

    return None


def stream_command_label(value: str) -> str:
    """返回适合运行期拒绝提示使用的命令标签。"""
    normalized = str(value or "").strip().casefold()
    if not normalized:
        return "command"

    parts = normalized.split()
    if parts[0] in command_names("mcp") and len(parts) >= 2:
        return f"{parts[0]} {parts[1]}"

    return parts[0]


def submission_uses_transient_surface(value: str) -> bool:
    """判断完整命令是否会用临时交互表面接管输入区。"""
    normalized = str(value or "").strip().casefold()
    command    = _COMMAND_BY_NAME.get(normalized)

    return bool(command is not None and command.surface_on_bare)


def submission_replaces_query(value: str) -> bool:
    """判断输入是否由后续结果块直接替代。"""
    return str(value or "").startswith("!")


def _completion_items() -> tuple[dict[str, str], ...]:
    """生成补全器使用的有序命令条目。"""
    items: list[dict[str, str]] = []

    for command in TUI_COMMANDS:
        item = {
            "text"    : command.insertion_text,
            "display" : command.command,
            "meta"    : command.completion_meta
        }

        items.append(item)

        items.extend(
            {
                "text"    : alias,
                "display" : alias,
                "meta"    : command.completion_meta
            }
            for alias in command.aliases
            if alias.startswith("/")
        )

    return tuple(items)


class SlashCommandCompleter(Completer):
    """命令补全视图。"""

    COMMANDS: typing.Final[tuple[dict[str, str], ...]] = _completion_items()

    TOP_LEVEL: typing.Final[tuple[str, ...]] = tuple(
        command.command for command in TUI_COMMANDS
    )

    def __init__(
        self,
        skills: typing.Callable[[], tuple[SkillSpec, ...]] | None = None,
    ) -> None:
        self._skills = skills or (lambda: ())

    def get_completions(self, document, complete_event):
        """根据当前输入内容生成补全项。"""
        _ = complete_event
        yield from self.matching_completions(document)

    def matching_completions(self, document) -> tuple[Completion, ...]:
        """返回当前文档可以实际改写输入内容的补全项。"""
        completions = self.menu_completions(document)
        if completions is None:
            return ()
        if len(completions) > 1:
            return completions

        return tuple(
            completion
            for completion in completions
            if completion_changes_input(document, completion)
        )

    def menu_completions(
        self,
        document
    ) -> tuple[Completion, ...] | None:
        """返回当前命令或 skill 查询的全部菜单项。"""
        completions = self.skill_completions(document)
        if completions is not None:
            return completions
        return self.slash_completions(document)

    def skill_completions(
        self,
        document
    ) -> tuple[Completion, ...] | None:
        """返回 skill 查询阶段的全部匹配项。"""
        text = document.text_before_cursor
        if skill_query_token(text) is None:
            return None
        return tuple(skill_completions(text, self._skills()))

    def slash_completions(
        self,
        document
    ) -> tuple[Completion, ...] | None:
        """返回命令名输入阶段的全部斜杠命令匹配项。"""
        text     = document.text_before_cursor
        stripped = text.lstrip()

        if not stripped.startswith("/"):
            return None
        if "\n" in stripped:
            return None

        token = stripped.splitlines()[-1]
        if " " in token:
            return None

        if token == "/":
            candidates = [
                item for item in self.COMMANDS if item["display"] in self.TOP_LEVEL
            ]
            visible_limit = len(self.TOP_LEVEL)
        else:
            folded = token.casefold()

            candidates = [
                item for item in self.COMMANDS
                if item["display"].casefold().startswith(folded)
                or item["text"].casefold().startswith(folded)
            ]

            candidates.sort(
                key=lambda item: 0
                if item["display"].casefold() == folded
                else 1
            )
            visible_limit = 7

        return tuple(
            Completion(
                item["text"],
                start_position=-len(token),
                display=item["display"],
                display_meta=item["meta"]
            )
            for item in candidates[:visible_limit]
        )


def completion_changes_input(document, completion: Completion) -> bool:
    """判断补全项是否会改写光标前的匹配文本。"""
    before = document.text_before_cursor
    start  = len(before) + completion.start_position

    return before[start:] != completion.text


if __name__ == '__main__':
    pass
