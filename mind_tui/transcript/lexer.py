# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from collections.abc import Callable
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.lexers import Lexer
from .render import RenderedTranscript


class TranscriptLexer(Lexer):
    """为只读会话正文提供 Markdown 和内容类型样式。"""

    def __init__(
        self,
        get_rendered: Callable[[], RenderedTranscript] | None = None
    ) -> None:
        """初始化可选的会话投影读取函数。"""
        self._get_rendered = get_rendered

    def lex_document(self, document: Document):
        """返回逐行样式解析函数。"""
        rendered = self._get_rendered() if self._get_rendered is not None else None
        document_lines = document.lines

        def get_line(lineno: int) -> StyleAndTextTuples:
            if (
                rendered is not None
                and rendered.text == document.text
                and lineno < len(rendered.lines)
            ):
                return list(rendered.lines[lineno])
            if lineno >= len(document_lines):
                return []
            line = document_lines[lineno]
            return [(self._line_style(line), line)]

        return get_line

    @staticmethod
    def _line_style(line: str) -> str:
        """返回缺少结构化投影时的单行样式。"""
        stripped = line.lstrip()
        if line.startswith(">_ Mind"):
            return "class:header"
        if stripped.startswith("Tip:"):
            return "class:tip.text"
        if line.startswith(("› ", "! ")):
            return "class:user"
        if stripped.startswith(("└", "├", "│")):
            return "class:trace"
        if stripped.startswith("• "):
            return "class:trace.title"
        return "class:assistant"


if __name__ == '__main__':
    pass
