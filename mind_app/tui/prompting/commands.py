# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from pathlib import Path
from mind_core.skills import SkillSpec
from prompt_toolkit.completion import (
    Completer,
    Completion
)
from .skills import (
    skill_completions,
    skill_query_token
)
from .files import (
    FileSearchManager,
    file_completions,
)

StreamCommandPolicy = typing.Literal[
    "reject",
    "background_barrier",
    "interrupt",
    "local_snapshot",
    "interactive_panel",
    "settings_settlement",
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
    accepts_arguments: bool = False
    subcommands: tuple[str, ...] = ()
    surface_on_bare: bool = False
    stream_policy: StreamCommandPolicy = "reject"
    stream_subcommand_policies: tuple[
        tuple[str, StreamCommandPolicy], ...
    ] = ()

    @property
    def names(self) -> tuple[str, ...]:
        """返回规范命令及其全部可执行别名。"""
        return self.command, *self.aliases

    @property
    def insertion_text(self) -> str:
        """返回补全选中后写入输入框的文本。"""
        return self.completion_text or self.command

    @property
    def available_during_task(self) -> bool:
        """返回命令是否至少有一个运行中可执行入口。"""
        return bool(
            self.stream_policy != "reject"
            or any(
                policy != "reject"
                for _subcommand, policy in self.stream_subcommand_policies
            )
        )

    @property
    def requires_stream_action(self) -> bool:
        """返回命令是否需要会话层提供流式动作实现。"""
        policies = (
            self.stream_policy,
            *(policy for _name, policy in self.stream_subcommand_policies),
        )
        return any(
            policy not in {"reject", "interrupt"}
            for policy in policies
        )

    def policy_during_task(
        self,
        subcommand: str | None = None,
    ) -> StreamCommandPolicy:
        """返回命令或指定子命令在活动轮次中的执行策略。"""
        if subcommand is None:
            return self.stream_policy
        return next(
            (
                policy
                for name, policy in self.stream_subcommand_policies
                if name == subcommand
            ),
            self.stream_policy,
        )


@dataclass(frozen=True, slots=True)
class SlashCommandQuery(object):
    """记录斜杠命令补全查询。"""
    token: str
    start_position: int


TUI_COMMANDS: typing.Final[tuple[TuiCommandSpec, ...]] = (
    TuiCommandSpec(
        "new", "/new", "开始新对话",
        accepts_arguments=True,
    ),
    TuiCommandSpec(
        "resume", "/resume", "恢复最近会话",
        surface_on_bare=True,
    ),
    TuiCommandSpec(
        "archive", "/archive", "归档当前会话并退出",
    ),
    TuiCommandSpec(
        "fork", "/fork", "复制当前对话上下文",
    ),
    TuiCommandSpec(
        "permissions", "/permissions", "切换权限模式",
        surface_on_bare=True,
        stream_policy="settings_settlement",
    ),
    TuiCommandSpec(
        "model", "/model", "设置主模型 ID",
        completion_text="/model ",
        parameterized=True,
        stream_policy="settings_settlement",
    ),
    TuiCommandSpec(
        "provider", "/provider", "切换模型 Provider",
        surface_on_bare=True,
        stream_policy="settings_settlement",
    ),
    TuiCommandSpec(
        "effort", "/effort", "设置主模型推理强度",
        surface_on_bare=True,
        stream_policy="settings_settlement",
    ),
    TuiCommandSpec(
        "preferences", "/preferences", "打开偏好配置页面",
        stream_policy="local_snapshot",
    ),
    TuiCommandSpec(
        "compact", "/compact", "压缩当前对话上下文",
    ),
    TuiCommandSpec(
        "tools", "/tools", "查看可用 MCP 工具",
        stream_policy="local_snapshot",
    ),
    TuiCommandSpec(
        "hooks", "/hooks", "管理生命周期 Hooks",
        surface_on_bare=True,
        stream_policy="interactive_panel",
    ),
    TuiCommandSpec(
        "agent", "/agent", "查看和管理子代理线程",
        surface_on_bare=True,
        stream_policy="interactive_panel",
    ),
    TuiCommandSpec(
        "listen", "/listen", "管理远端请求监听器",
        subcommands=("start", "stop", "status"),
        surface_on_bare=True,
        stream_policy="interactive_panel",
        stream_subcommand_policies=(
            ("start", "background_barrier"),
            ("stop", "background_barrier"),
            ("status", "local_snapshot"),
        ),
    ),
    TuiCommandSpec(
        "mailbox", "/mailbox", "查看和处理远端请求消息",
        surface_on_bare=True,
        stream_policy="interactive_panel",
    ),
    TuiCommandSpec(
        "diff", "/diff", "查看 Git 工作区差异（包含未跟踪文件）",
        stream_policy="local_snapshot",
    ),
    TuiCommandSpec(
        "copy", "/copy", "复制最近一次助手回复原文",
        stream_policy="local_snapshot",
    ),
    TuiCommandSpec(
        "ps", "/ps", "查看后台终端",
        surface_on_bare=True,
        stream_policy="local_snapshot",
    ),
    TuiCommandSpec(
        "stop", "/stop", "停止全部后台终端",
        stream_policy="background_barrier",
    ),
    TuiCommandSpec(
        "mcp", "/mcp", "管理外部 MCP 服务",
        subcommands=("start", "force", "stop", "restart", "status"),
        surface_on_bare=True,
        stream_subcommand_policies=(
            ("start", "background_barrier"),
            ("force", "background_barrier"),
            ("stop", "reject"),
            ("restart", "reject"),
            ("status", "local_snapshot"),
        ),
    ),
    TuiCommandSpec(
        "helix_link", "/helix-link", "接入 Helix MCP",
        surface_on_bare=True,
        stream_policy="background_barrier",
    ),
    TuiCommandSpec(
        "helix_mode", "/helix-mode", "选择 Helix 工具过滤模式",
        surface_on_bare=True,
        stream_policy="settings_settlement",
    ),
    TuiCommandSpec(
        "helix_unlink", "/helix-unlink", "移除 Helix MCP",
    ),
    TuiCommandSpec(
        "helix_home", "/helix-home", "打开 Helix 首页",
        stream_policy="background_barrier",
    ),
    TuiCommandSpec(
        "helix_stop", "/helix-stop", "停止 Helix 服务",
    ),
    TuiCommandSpec(
        "skills", "/skills", "打开 skills 列表",
        surface_on_bare=True,
        stream_policy="interactive_panel",
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


def _completion_items() -> tuple[dict[str, str], ...]:
    """生成补全器使用的有序命令条目。"""
    items: list[dict[str, str]] = []

    for command in TUI_COMMANDS:
        item = {
            "text": command.insertion_text,
            "display": command.command,
            "meta": command.completion_meta
        }

        items.append(item)

        items.extend(
            {
                "text": alias,
                "display": alias,
                "meta": command.completion_meta
            }
            for alias in command.aliases
            if alias.startswith("/")
        )

    return tuple(items)


def _slash_command_bounds(document) -> tuple[str, int, int, int] | None:
    """返回首行斜杠命令 token 的边界。"""
    token = _slash_command_token(document)
    if token is None or not is_first_input_line(document):
        return None

    first_line, slash_start, token_end = token

    cursor = document.cursor_position
    if cursor < slash_start:
        return None

    if cursor > token_end:
        return None

    return first_line, slash_start, token_end, cursor


def _slash_command_token(document) -> tuple[str, int, int] | None:
    """返回首行斜杠命令 token。"""
    text = document.text

    first_line_end = text.find("\n")
    if first_line_end < 0:
        first_line_end = len(text)

    first_line = text[:first_line_end]
    if not first_line.startswith("/"):
        return None

    slash_start: int = 0

    token_end = next(
        (
            index
            for index, char in enumerate(
                first_line[slash_start:],
                start=slash_start,
            )
            if char.isspace()
        ),
        len(first_line),
    )
    return first_line, slash_start, token_end


def slash_command_query(document) -> SlashCommandQuery | None:
    """返回当前首行斜杠命令补全查询。"""
    bounds = _slash_command_bounds(document)
    if bounds is None:
        return None

    first_line, slash_start, token_end, cursor = bounds

    query_end = token_end if cursor <= slash_start + 1 else cursor

    return SlashCommandQuery(
        token=first_line[slash_start:query_end],
        start_position=slash_start - cursor,
    )


def slash_command_dismissal_token(document) -> str | None:
    """返回用于保持关闭状态的斜杠命令 token。"""
    token = _slash_command_token(document)
    if token is None:
        return None

    first_line, slash_start, token_end = token

    return first_line[slash_start:token_end]


def is_first_input_line(document) -> bool:
    """判断光标是否位于输入文档首行。"""
    return document.cursor_position_row == 0


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
    if (
        command.parameterized
        or command.accepts_arguments
        or argument in command.subcommands
    ):
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

    parts = normalized.split()

    command = _COMMAND_BY_NAME.get(parts[0])
    if command is None:
        if parts[0].startswith("/"):
            return "reject"
        return None

    if len(parts) == 1:
        return command.policy_during_task()

    argument = " ".join(parts[1:])
    if command.subcommands:
        if argument not in command.subcommands:
            return "reject"
        return command.policy_during_task(argument)
    if command.parameterized or command.accepts_arguments:
        return command.policy_during_task()

    if parts[0].startswith("/"):
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


def completion_changes_input(document, completion: Completion) -> bool:
    """判断补全项是否会改写光标前的匹配文本。"""
    slash_bounds = _slash_command_bounds(document)
    if slash_bounds is not None:

        token_end = slash_bounds[2]
        cursor    = slash_bounds[3]

        if cursor < token_end:
            return True

        cursor = document.cursor_position
        start  = cursor + completion.start_position

        return document.text[:start] + completion.text != document.text

    before = document.text_before_cursor
    start  = len(before) + completion.start_position

    return before[start:] != completion.text


class SlashCommandCompleter(Completer):
    """命令补全视图。"""

    COMMANDS: typing.Final[tuple[dict[str, str], ...]] = _completion_items()

    TOP_LEVEL: typing.Final[tuple[str, ...]] = tuple(
        command.command for command in TUI_COMMANDS
    )

    def __init__(
        self,
        skills: typing.Callable[[], tuple[SkillSpec, ...]] | None = None,
        workspace_root: typing.Callable[[], Path | str | None] | None = None,
        file_search: FileSearchManager | None = None,
    ) -> None:
        self._skills = skills or (lambda: ())
        self._workspace_root = workspace_root or (lambda: Path.cwd())
        self._file_search = file_search or FileSearchManager()

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

        # 非立即派发的 slash 命令让精确候选与前缀候选共用同一个 popup。
        # Enter 提交未改写的命令，Tab 保持 popup；`/skills` 仍是立即派发的
        # 特例。fallback 渲染器会产生不同的列布局和对齐位置。
        if slash_command_query(document) is not None:
            return completions

        return tuple(
            completion
            for completion in completions
            if completion_changes_input(document, completion)
        )

    def menu_completions(self, document) -> tuple[Completion, ...] | None:
        """返回当前命令或 skill 查询的全部菜单项。"""
        completions = self.skill_completions(document)
        if completions is not None:
            return completions
        return self.slash_completions(document)

    def skill_completions(self, document) -> tuple[Completion, ...] | None:
        """返回 skill 查询阶段的全部匹配项。"""
        text = document.text_before_cursor
        token = skill_query_token(text)
        if token is None:
            return None
        if token.startswith("@"):
            return tuple(
                (*skill_completions(text, self._skills()),
                 *file_completions(
                     text,
                     workspace_root=self._workspace_root(),
                     search=self._file_search,
                 ))
            )
        return tuple(skill_completions(text, self._skills()))

    def slash_completions(self, document) -> tuple[Completion, ...] | None:
        """返回命令名输入阶段的全部斜杠命令匹配项。"""
        query = slash_command_query(document)
        if query is None:
            return None

        token = query.token
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
                start_position=query.start_position,
                display=item["display"],
                display_meta=item["meta"]
            )
            for item in candidates[:visible_limit]
        )


if __name__ == '__main__':
    pass
