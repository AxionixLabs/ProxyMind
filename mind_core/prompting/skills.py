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


SKILL_EYE_WIDTH = 16
SKILL_PREFIX_RE = re.compile(r"^\$[A-Za-z0-9_.-]*$")


class SkillTokenLexer(Lexer):
    """输入框 skill token 高亮。"""

    def lex_document(self, document: Document) -> typing.Callable[[int], StyleAndTextTuples]:
        """返回指定行的格式化文本。"""
        line_offsets = self._line_offsets(document.text)

        def get_line(line_number: int) -> StyleAndTextTuples:
            line        = document.lines[line_number]
            line_offset = line_offsets[line_number] if line_number < len(line_offsets) else 0

            parts: StyleAndTextTuples = []

            pos = 0
            for start, end, _name in iter_known_skill_tokens(line, offset=line_offset):
                line_start = start - line_offset
                line_end = end - line_offset
                if line_start > pos:
                    parts.append(("", line[pos:line_start]))

                parts.append(("class:skill-token", line[line_start:line_end]))
                pos = line_end
            if pos < len(line):
                parts.append(("", line[pos:]))

            return parts

        return get_line

    @staticmethod
    def _line_offsets(text: str) -> list[int]:
        """返回每行在完整文档中的起始偏移。"""
        offsets = [0]
        for index, char in enumerate(text):
            if char == "\n":
                offsets.append(index + 1)
        return offsets


def known_skill_names() -> frozenset[str]:
    """返回当前可用 skill 名称白名单。"""
    return frozenset(skill.name.lower() for skill in available_skills())


def sorted_known_skill_names() -> tuple[str, ...]:
    """返回按长度优先匹配的 skill 名称白名单。"""
    return tuple(sorted(known_skill_names(), key=len, reverse=True))


def is_skill_boundary(text: str, index: int) -> bool:
    """判断 index 是否处在 skill token 边界。"""
    if index < 0 or index >= len(text):
        return True

    char = text[index]
    return not (char.isalnum() or char in "_-")


def match_known_skill_at(text: str, start: int) -> tuple[int, str] | None:
    """在指定位置匹配一个白名单 skill token。"""
    if start < 0 or start >= len(text) or text[start] != "$":
        return None
    if not is_skill_boundary(text, start - 1):
        return None

    lower_text = text.lower()

    for name in sorted_known_skill_names():
        token = f"${name}"
        end   = start + len(token)

        if lower_text.startswith(token, start) and is_skill_boundary(text, end):
            return end, name

    return None


def iter_known_skill_tokens(text: str, *, offset: int = 0) -> typing.Iterator[tuple[int, int, str]]:
    """迭代文本中的白名单 skill token。"""
    cursor = 0
    while True:
        start = text.find("$", cursor)
        if start < 0:
            return

        matched = match_known_skill_at(text, start)
        if matched is None:
            cursor = start + 1
            continue

        end, name = matched
        yield offset + start, offset + end, name
        cursor = end


def is_skill_token(text: str) -> bool:
    """判断当前光标是否位于 skill token。"""
    current_line = text.splitlines()[-1] if text.splitlines() else text
    if not current_line or current_line[-1].isspace():
        return False

    token = current_line.split()[-1] if current_line.split() else current_line
    if not token.startswith("$"):
        return False
    if not SKILL_PREFIX_RE.fullmatch(token):
        return False

    query = token[1:].strip().lower()
    if not query:
        return True

    names = known_skill_names()
    if query in names:
        return False

    return any(name.startswith(query) for name in names)


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
    return pad_display_width(truncate_display_width(text, SKILL_EYE_WIDTH), SKILL_EYE_WIDTH)


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
