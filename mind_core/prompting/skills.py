# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing
from prompt_toolkit.completion import Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.utils import get_cwidth
from mind_core.skills import available_skills


SKILL_DISPLAY_WIDTH = 16


class SkillTokenLexer(Lexer):
    """输入框 skill token 高亮。"""

    TOKEN_RE = re.compile(r"(\$[A-Za-z0-9_.-]+)")

    def lex_document(self, document: Document) -> typing.Callable[[int], StyleAndTextTuples]:
        """返回指定行的格式化文本。"""
        def get_line(line_number: int) -> StyleAndTextTuples:
            line = document.lines[line_number]

            parts: StyleAndTextTuples = []

            pos: int = 0
            for match in self.TOKEN_RE.finditer(line):
                if match.start() > pos:
                    parts.append(("", line[pos:match.start()]))
                parts.append(("class:skill-token", match.group(0)))
                pos = match.end()
            if pos < len(line):
                parts.append(("", line[pos:]))

            return parts

        return get_line


def is_skill_token(text: str) -> bool:
    """判断当前光标是否位于 skill token。"""
    current_line = text.splitlines()[-1] if text.splitlines() else text
    if not current_line or current_line[-1].isspace():
        return False
    token = current_line.split()[-1] if current_line.split() else current_line
    return token.startswith("$")


def skill_completions(text: str) -> typing.Iterator[Completion]:
    """生成 skill 补全项。"""
    current_line = text.splitlines()[-1] if text.splitlines() else text
    token = current_line.split()[-1] if current_line.split() else current_line
    query = token[1:].strip().lower()

    skills = [
        skill for skill in available_skills()
        if not query or skill.name.lower().startswith(query)
    ]

    for skill in skills[:8]:
        yield Completion(
            f"${skill.name} ",
            start_position=-len(token),
            display=skill_display_text(skill.name),
            display_meta=f"[Skill] {skill_meta_description(skill.description)}",
        )


def skill_display_text(name: str) -> str:
    """返回 skill 菜单名称列。"""
    text = str(name or "")
    return pad_display_width(truncate_display_width(text, SKILL_DISPLAY_WIDTH), SKILL_DISPLAY_WIDTH)


def skill_meta_description(description: str) -> str:
    """返回 skill 菜单摘要。"""
    text = " ".join(str(description or "").split())
    return truncate_display_width(text, 42)


def truncate_display_width(text: str, limit: int) -> str:
    """按终端显示宽度截断文本。"""
    if get_cwidth(text) <= limit:
        return text

    out: list[str] = []
    used = 0
    target = max(0, limit - 3)
    for char in text:
        size = get_cwidth(char)
        if used + size > target:
            break
        out.append(char)
        used += size

    return "".join(out).rstrip() + "..."


def pad_display_width(text: str, width: int) -> str:
    """按终端显示宽度补齐文本。"""
    size = get_cwidth(text)
    if size >= width:
        return text
    return text + (" " * (width - size))


if __name__ == '__main__':
    pass
