# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from prompt_toolkit.styles import Style

TUI_STYLE = Style.from_dict({
    "header": "#AEB5BE",
    "header.dim": "dim #AEB5BE",
    "header.brand": "bold #F1F3F5",
    "tip.text": "#AEB5BE",
    "user": "bold #E4E8ED",
    "assistant": "#D6DAE0",
    "markdown.heading.1": "bold #F1F3F5",
    "markdown.heading.2": "bold #E4E8ED",
    "markdown.heading.3": "bold #D6DAE0",
    "markdown.strong": "bold",
    "markdown.emphasis": "italic",
    "markdown.code.inline": "#AFC7D8",
    "markdown.code.block": "#C7CDD4",
    "markdown.code.language": "italic #7F8994",
    "markdown.link": "underline #87D7FF",
    "markdown.image": "italic #AEB5BE",
    "markdown.quote": "#667380",
    "markdown.list.marker": "bold #8E98A3",
    "markdown.rule": "#4B5560",
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
    "approval.card": "#30363D bg:#D5D9DE",
    "approval.title": "bold #20242A",
    "approval.command": "#30363D",
    "approval.option": "#4B535C",
    "approval.selected": "bold #176B4D"
})


if __name__ == '__main__':
    pass
