# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from prompt_toolkit.completion import (
    Completer,
    Completion
)
from .skills import (
    is_skill_token,
    skill_completions
)

StreamCommandPolicy = typing.Literal["reject", "background_barrier", "interrupt"]


@dataclass(frozen=True, slots=True)
class TuiCommandSpec(object):
    """描述一项 TUI 命令的输入、帮助和别名信息。"""

    key: str
    command: str
    completion_meta: str
    help_detail: str
    aliases: tuple[str, ...] = ()
    completion_text: str | None = None
    completion_match: str | None = None
    help_usage: str | None = None
    show_in_help: bool = True
    parameterized: bool = False
    stream_policy: StreamCommandPolicy = "reject"

    @property
    def names(self) -> tuple[str, ...]:
        """返回规范命令及其全部可执行别名。"""
        return self.command, *self.aliases

    @property
    def insertion_text(self) -> str:
        """返回补全选中后写入输入框的文本。"""
        return self.completion_text or self.command

    @property
    def usage(self) -> str:
        """返回帮助视图使用的命令格式。"""
        return self.help_usage or ", ".join(self.names)


TUI_COMMANDS: typing.Final[tuple[TuiCommandSpec, ...]] = (
    TuiCommandSpec(
        "chat", "/chat", "切换到 Chat 模式",
        "对话模式（交互能力协作/自然语言交互）",
    ),
    TuiCommandSpec(
        "fast", "/fast", "切换到 Fast 模式",
        "高速模式（高吞吐任务流/数据媒体直达）",
    ),
    TuiCommandSpec(
        "xtra", "/xtra", "切换到 Xtra 模式",
        "外接模式（外部 MCP 工具 + 通用工具 + 编码工具）",
    ),
    TuiCommandSpec(
        "new", "/new", "开始新对话",
        "开始新对话（保留模式、模型和待发送附件）",
    ),
    TuiCommandSpec(
        "resume", "/resume", "恢复最近会话",
        "从当前模式最近 24 小时会话中恢复",
    ),
    TuiCommandSpec(
        "attach", "/attach", "添加本轮待发送附件",
        "添加本轮待发送附件（任意文件）",
        completion_text="/attach ",
        help_usage="/attach <path|dir|glob>",
        parameterized=True,
    ),
    TuiCommandSpec(
        "attachments", "/attachments", "查看待发送附件",
        "查看当前待发送附件",
    ),
    TuiCommandSpec(
        "detach", "/detach", "移除待发送附件",
        "移除一个待发送附件",
        completion_text="/detach ",
        help_usage="/detach <index|path>",
        parameterized=True,
    ),
    TuiCommandSpec(
        "attach_clear", "/attach-clear", "清空待发送附件",
        "清空当前待发送附件",
    ),
    TuiCommandSpec(
        "permissions", "/permissions", "切换权限模式",
        "切换权限模式",
    ),
    TuiCommandSpec(
        "model", "/model", "设置主模型 ID",
        "持久化主模型 ID；省略 model-id 表示清空",
        completion_text="/model ",
        help_usage="/model <model-id>",
        parameterized=True,
    ),
    TuiCommandSpec(
        "effort", "/effort", "设置主模型推理强度",
        "设置主模型推理强度",
    ),
    TuiCommandSpec(
        "preferences", "/preferences", "打开偏好配置页面",
        "打开偏好配置页面",
    ),
    TuiCommandSpec(
        "compact", "/compact", "压缩当前对话上下文",
        "压缩当前对话上下文",
    ),
    TuiCommandSpec(
        "tools", "/tools", "查看可用 MCP 工具",
        "查看当前可用 MCP 工具",
    ),
    TuiCommandSpec(
        "diff", "/diff", "查看本轮补丁净差异",
        "查看本轮补丁净差异",
    ),
    TuiCommandSpec(
        "copy", "/copy", "复制最近一次助手回复原文",
        "复制最近一次助手回复原文",
    ),
    TuiCommandSpec(
        "ps", "/ps", "管理后台命令",
        "查看或停止后台命令",
    ),
    TuiCommandSpec(
        "mcp", "/mcp", "管理外部 MCP 服务",
        "管理外部 MCP 服务",
    ),
    TuiCommandSpec(
        "helix_link", "/helix-link", "接入 Helix MCP",
        "接入 Helix MCP",
        stream_policy="background_barrier",
    ),
    TuiCommandSpec(
        "helix_unlink", "/helix-unlink", "移除 Helix MCP",
        "移除当前会话的 Helix MCP",
    ),
    TuiCommandSpec(
        "helix_home", "/helix-home", "打开 Helix 首页",
        "打开 Helix 首页",
    ),
    TuiCommandSpec(
        "helix_stop", "/helix-stop", "停止 Helix 服务",
        "停止 Helix 服务",
    ),
    TuiCommandSpec(
        "skills", "/skills", "打开 skills 列表", "",
        completion_text="$",
        completion_match="/skills",
        show_in_help=False,
    ),
    TuiCommandSpec(
        "help", "/help", "查看帮助", "指令索引（用法/示例/约定）",
        aliases=("/h",),
    ),
    TuiCommandSpec(
        "license", "/license", "查看授权", "授权许可（License/特性）",
        aliases=("/lic",),
    ),
    TuiCommandSpec(
        "shutdown", "/shutdown", "停止本地后台服务并退出",
        "关闭前台并停止本地运行时",
        stream_policy="interrupt",
    ),
    TuiCommandSpec(
        "quit", "/quit", "退出会话", "断开会话（安全退出）",
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


def _completion_items() -> tuple[dict[str, str], ...]:
    """生成补全器使用的有序命令条目。"""
    items: list[dict[str, str]] = []
    for command in TUI_COMMANDS:
        item = {
            "text"    : command.insertion_text,
            "display" : command.command,
            "meta"    : command.completion_meta
        }

        if command.completion_match:
            item["match"] = command.completion_match
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

    def get_completions(self, document, complete_event):
        """根据当前输入内容生成补全项。"""
        text     = document.text_before_cursor
        stripped = text.lstrip()

        if is_skill_token(text):
            yield from skill_completions(text)
            return

        if not stripped.startswith("/"):
            return

        token = stripped.splitlines()[-1]
        if " " in token and not token.startswith(("/attach", "/detach")):
            return

        if token == "/":
            candidates = [
                item for item in self.COMMANDS if item["display"] in self.TOP_LEVEL
            ]
            visible_limit = len(self.TOP_LEVEL)
        else:
            candidates = [
                item for item in self.COMMANDS
                if item["display"].startswith(token)
                or item["text"].startswith(token)
                or str(item.get("match") or "").startswith(token)
            ]
            visible_limit = 7

        for item in candidates[:visible_limit]:
            yield Completion(
                item["text"],
                start_position=-len(token),
                display=item["display"],
                display_meta=item["meta"]
            )


if __name__ == '__main__':
    pass
