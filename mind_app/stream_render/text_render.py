# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from rich.console import Group
from rich.text import Text
from mind_app.presentation.models import StyledBlock
from mind_app.presentation.rich.styles import rich_style
from mind_app.stream_state.markdown import render_markdown
from mind_app.stream_state.text_models import TextFinalUnit


def render_rich_block(
    block: StyledBlock,
    *,
    default_style: str | None = None,
    trim_trailing: bool = False,
) -> Text:
    """把中立展示块转换为 Rich 文本对象。"""
    out = Text()
    spans = block.spans or ()
    if not spans and block.plain_text:
        out.append(block.plain_text, style=default_style)
    for span in spans:
        if span.text:
            out.append(span.text, style=rich_style(span.style) or default_style)
    if trim_trailing:
        out.rstrip()
    return out


def render_rich_final(units: tuple[TextFinalUnit, ...]) -> typing.Any:
    """把最终文本单元转换为 Rich 落版对象。"""
    renderables: list[typing.Any] = []
    for unit in units:
        if unit.gap_before:
            renderables.append(Text(""))
        if unit.kind == "markdown":
            renderables.append(render_markdown(unit.text))
            continue
        renderables.append(render_rich_block(
            StyledBlock(plain_text=unit.text, spans=unit.spans),
            default_style="bold",
            trim_trailing=True,
        ))

    if not renderables:
        return Text()
    if len(renderables) == 1:
        return renderables[0]
    return Group(*renderables)


if __name__ == '__main__':
    pass
