# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import sys
import typing
import argparse
import functools

ANSI_RESET  = "\x1b[0m"
ANSI_ACCENT = "\x1b[38;2;232;235;239m"
ANSI_HEADER = "\x1b[4;38;2;232;235;239m"
ANSI_MUTED  = "\x1b[38;2;138;146;156m"

HELP_SECTIONS = (
    "Usage",
    "Commands",
    "Arguments",
    "Options",
)


def supports_help_color(
    stream: typing.TextIO | None = None,
    environ: typing.Mapping[str, str] | None = None,
) -> bool:
    """判断命令帮助输出是否适合使用 ANSI 样式。"""
    output = sys.stdout if stream is None else stream
    env    = os.environ if environ is None else environ

    if "NO_COLOR" in env:
        return False
    if env.get("FORCE_COLOR") not in {None, "", "0"}:
        return True

    isatty = getattr(output, "isatty", None)

    try:
        return bool(callable(isatty) and isatty())
    except (OSError, ValueError):
        return False


def _styled(text: str, style: str, *, enabled: bool) -> str:
    """为非空文本添加单段 ANSI 样式。"""
    if not enabled or not text:
        return text
    return f"{style}{text}{ANSI_RESET}"


class CliHelpFormatter(argparse.HelpFormatter):
    """使用稳定对齐和终端语义色渲染命令帮助。"""

    def __init__(
        self,
        prog: str,
        *,
        color: bool | None = None,
        **kwargs: typing.Any,
    ) -> None:
        self.color = supports_help_color() if color is None else color
        super().__init__(prog, **kwargs)

    def _format_command_action(self, action: argparse.Action) -> str:
        """按照固定说明列渲染一条命令。"""
        invocation    = str(action.metavar or action.dest)
        indent        = max(0, self._current_indent - 2)
        help_position = 18
        action_width  = help_position - indent - 2

        header = " " * indent + _styled(
            invocation,
            ANSI_ACCENT,
            enabled=self.color,
        )

        if not action.help:
            return header + "\n"

        if len(invocation) <= action_width:
            header += " " * (action_width - len(invocation)) + "  "
            help_indent = 0
        else:
            header += "\n"
            help_indent = help_position

        help_width = max(self._width - help_position, 11)

        help_lines = self._split_lines(self._expand_help(action), help_width)
        if not help_lines:
            return header + "\n"

        parts = [
            header,
            " " * help_indent
            + _styled(help_lines[0], ANSI_MUTED, enabled=self.color)
            + "\n",
        ]
        for line in help_lines[1:]:
            parts.append(
                " " * help_position
                + _styled(line, ANSI_MUTED, enabled=self.color)
                + "\n"
            )
        return "".join(parts)

    def _format_action_invocation(self, action: argparse.Action) -> str:
        """使用统一占位符格式渲染参数。"""
        metavar = str(action.metavar or action.dest.upper())
        if not action.option_strings:
            if action.nargs == "?":
                return f"[{metavar}]"
            if action.nargs == "*":
                return f"[{metavar}]..."
            if action.nargs == "+":
                return f"<{metavar}>..."
            return f"<{metavar}>"

        options = ", ".join(action.option_strings)
        if action.nargs == 0:
            return options
        suffix = "..." if action.nargs in {"*", "+"} else ""
        return f"{options} <{metavar}>{suffix}"

    def _format_usage(
        self,
        usage: str | None,
        actions: typing.Sequence[argparse.Action],
        groups: typing.Sequence[typing.Any],
        prefix: str | None,
    ) -> str:
        text = super()._format_usage(usage, actions, groups, prefix)
        text = text.replace("usage: ", "Usage: ", 1)
        if not self.color:
            return text

        lines = text.splitlines(keepends=True)

        styled_lines: list[str] = []

        for index, line in enumerate(lines):
            body   = line.rstrip("\r\n")
            suffix = line[len(body):]

            if index == 0 and body.startswith("Usage: "):
                label, command = body.split(" ", 1)

                styled_lines.append(
                    _styled(label, ANSI_HEADER, enabled=True)
                    + " "
                    + _styled(command, ANSI_ACCENT, enabled=True)
                    + suffix
                )

            else:
                styled_lines.append(
                    _styled(body, ANSI_ACCENT, enabled=True) + suffix
                )

        return "".join(styled_lines)

    def _format_action(self, action: argparse.Action) -> str:
        if action.nargs == argparse.PARSER:
            return self._join_parts([
                self._format_command_action(subaction)
                for subaction in self._iter_indented_subactions(action)
            ])

        invocation    = self._format_action_invocation(action)
        help_position = 10
        help_width    = max(self._width - help_position, 11)

        parts = [
            " " * self._current_indent
            + _styled(invocation, ANSI_ACCENT, enabled=self.color)
            + "\n"
        ]

        if action.help and action.help.strip():
            paragraphs = self._expand_help(action).split("\n\n")
            for index, paragraph in enumerate(paragraphs):
                if index:
                    parts.append("\n")
                for line in self._split_lines(paragraph, help_width):
                    parts.append(
                        " " * help_position
                        + _styled(line, ANSI_MUTED, enabled=self.color)
                        + "\n"
                    )
        return "".join(parts) + "\n"

    def _fill_text(self, text: str, width: int, indent: str) -> str:
        value = super()._fill_text(text, width, indent)
        return "\n".join(
            _styled(line, ANSI_MUTED, enabled=self.color)
            for line in value.splitlines()
        )

    def format_help(self) -> str:
        text = super().format_help()
        for heading in HELP_SECTIONS[1:]:
            plain = f"{heading}:\n"
            styled = _styled(
                f"{heading}:",
                ANSI_HEADER,
                enabled=self.color,
            ) + "\n"
            text = text.replace(plain, styled)
        return text


class CliArgumentParser(argparse.ArgumentParser):
    """为每一层命令提供统一标题和帮助格式。"""

    def __init__(
        self,
        *args: typing.Any,
        help_title: str = "",
        help_summary: str | None = None,
        color: bool | None = None,
        **kwargs: typing.Any,
    ) -> None:
        self.help_title = help_title
        description = kwargs.pop("description", "")
        self.help_summary = (
            str(description or "")
            if help_summary is None
            else help_summary
        )
        self.help_color = supports_help_color() if color is None else color
        kwargs["formatter_class"] = functools.partial(
            CliHelpFormatter,
            color=self.help_color,
        )
        super().__init__(*args, **kwargs)
        self._command_help: dict[tuple[str, ...], CliArgumentParser] = {}

    def register_command_help(
        self,
        path: tuple[str, ...],
        parser: "CliArgumentParser",
    ) -> None:
        """登记一条可由 help 命令访问的命令路径。"""
        self._command_help[path] = parser

    def print_command_help(self, path: tuple[str, ...]) -> typing.NoReturn:
        """打印指定命令路径的帮助并结束参数解析。"""
        if not path:
            self.print_help()
            self.exit()

        target = self._command_help.get(path)
        if target is None:
            self.error(f"unknown help topic: {' '.join(path)}")
        target.print_help()
        self.exit()

    def format_help(self) -> str:
        """在 argparse 帮助正文前增加命令标题。"""
        body = super().format_help()
        if not self.help_title:
            return body

        title = _styled(
            self.help_title,
            ANSI_ACCENT,
            enabled=self.help_color,
        )

        blocks = [title]

        if self.help_summary:
            blocks.append(_styled(
                self.help_summary,
                ANSI_MUTED,
                enabled=self.help_color,
            ))
        blocks.append(body)
        return "\n\n".join(blocks)


if __name__ == '__main__':
    pass
