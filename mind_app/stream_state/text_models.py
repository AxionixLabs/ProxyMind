# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from mind_app.presentation.models import TextSpan

TextFinalKind = typing.Literal["markdown", "spans"]


@dataclass(frozen=True, slots=True)
class TextFinalUnit(object):
    """描述最终落版中的一项 Markdown 或样式文本单元。"""

    kind: TextFinalKind
    text: str
    spans: tuple[TextSpan, ...] = ()
    gap_before: bool = False


if __name__ == '__main__':
    pass
