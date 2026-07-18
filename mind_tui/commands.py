# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from collections.abc import Iterable

from prompt_toolkit.completion import Completer, CompleteEvent, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.filters import has_completions
from prompt_toolkit.layout.containers import ConditionalContainer, VSplit, Window
from prompt_toolkit.layout.menus import CompletionsMenu

from mind_core.prompting.commands import SlashCommandCompleter


class CommandCompleter(Completer):
    """提供与现有交互循环一致的斜杠命令补全。"""

    def __init__(self) -> None:
        self._delegate = SlashCommandCompleter()

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        """返回仅用于菜单展示和输入填充的命令候选。"""
        if not document.text_before_cursor.lstrip().startswith("/"):
            return

        for completion in self._delegate.get_completions(document, complete_event):
            if completion.display_text == "/skills":
                yield Completion(
                    "/skills",
                    start_position=completion.start_position,
                    display=completion.display,
                    display_meta=completion.display_meta
                )
                continue
            yield completion


def create_command_menu() -> ConditionalContainer:
    """创建无边框、无背景色的斜杠命令列表。"""
    menu = CompletionsMenu(
        max_height=8,
        scroll_offset=1
    )
    return ConditionalContainer(
        VSplit([
            Window(width=1, dont_extend_width=True),
            menu,
            Window()
        ]),
        filter=has_completions
    )


if __name__ == '__main__':
    pass
