# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from prompt_toolkit.styles import Style

TUI_STYLE = Style.from_dict({
    "header": "bold #E4E8ED",
    "tip.text": "#AEB5BE",
    "user": "bold #E4E8ED",
    "assistant": "#D6DAE0",
    "trace": "#8E98A3",
    "trace.title": "bold #AFC7D8",
    "input.prompt": "bold #E4E8ED",
    "input.prompt.shell": "bold #FF6B6B",
    "input": "#F1F3F5",
    "status.glyph": "bold #5FD7AF",
    "status.text": "#AEB5BE",
    "queue.title": "bold #AFC7D8",
    "queue.arrow": "#667380",
    "queue.text": "#D6DAE0",
    "queue.more": "#7F8994",
    "composer.hint": "#6F7883",
    "footer.model": "bold #AEB5BE",
    "footer.separator": "#666E78",
    "footer.workspace": "#858E99",
    "scrollbar.track": "#343A40",
    "scrollbar.thumb": "bold #89919B",
    "completion-menu.completion": "#C7CDD4",
    "completion-menu.completion.current": "bold #5FD7AF",
    "completion-menu.meta.completion": "#7F8994",
    "completion-menu.meta.completion.current": "bold #AFC7D8",
    "approval.border": "#667380",
    "approval.title": "bold #E4E8ED",
    "approval.command": "#B7C5D3",
    "approval.option": "#AAB3BD",
    "approval.selected": "bold #5FD7AF"
})


if __name__ == '__main__':
    pass
