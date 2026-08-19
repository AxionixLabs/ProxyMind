# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing
from prompt_toolkit.completion import Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.utils import get_cwidth
from mind_core.skills import SkillSpec
from .paste import iter_paste_placeholders

SKILL_NAME_TRUNCATE_WIDTH = 28
SKILL_CATEGORY_TAG        = "[Skill]"
SKILL_PREFIX_RE           = re.compile(r"^\$[A-Za-z0-9_.-]*$")


class SkillTokenLexer(Lexer):
    """输入框 skill token 高亮。"""

    def __init__(
        self,
        paste_placeholders: typing.Callable[[], typing.Iterable[str]] | None = None,
        skills: typing.Callable[[], tuple[SkillSpec, ...]] | None = None
    ) -> None:
        """绑定当前输入模型提供的活动粘贴占位符。"""
        self._paste_placeholders = paste_placeholders or (lambda: ())

        self._skills = skills or (lambda: ())

    @staticmethod
    def _line_offsets(text: str) -> list[int]:
        """返回每行在完整文档中的起始偏移。"""
        offsets = [0]
        for index, char in enumerate(text):
            if char == "\n":
                offsets.append(index + 1)
        return offsets

    def lex_document(self, document: Document) -> typing.Callable[[int], StyleAndTextTuples]:
        """返回指定行的格式化文本。"""
        line_offsets       = self._line_offsets(document.text)
        paste_placeholders = tuple(self._paste_placeholders())

        def get_line(line_number: int) -> StyleAndTextTuples:
            line        = document.lines[line_number]
            line_offset = line_offsets[line_number] if line_number < len(line_offsets) else 0

            parts: StyleAndTextTuples = []

            pos: int = 0
            for start, end, style in iter_prompt_tokens(
                line,
                paste_placeholders=paste_placeholders,
                skills=self._skills(),
                offset=line_offset,
            ):
                line_start = start - line_offset
                line_end   = end - line_offset

                if line_start > pos:
                    parts.append(("", line[pos:line_start]))

                parts.append((style, line[line_start:line_end]))
                pos = line_end

            if pos < len(line):
                parts.append(("", line[pos:]))

            return parts

        return get_line


def known_skill_names(skills: typing.Iterable[SkillSpec] = ()) -> frozenset[str]:
    """返回当前可用 skill 名称白名单。"""
    return frozenset(skill.name.lower() for skill in skills)


def sorted_known_skill_names(skills: typing.Iterable[SkillSpec] = ()) -> tuple[str, ...]:
    """返回按长度优先匹配的 skill 名称白名单。"""
    return tuple(sorted(known_skill_names(skills), key=len, reverse=True))


def is_skill_boundary(text: str, index: int) -> bool:
    """判断 index 是否处在 skill token 边界。"""
    if index < 0 or index >= len(text):
        return True

    char = text[index]
    return not (char.isalnum() or char in "_-")


def match_known_skill_at(
    text: str,
    start: int,
    *,
    skills: typing.Iterable[SkillSpec] = ()
) -> tuple[int, str] | None:
    """在指定位置匹配一个白名单 skill token。"""
    if start < 0 or start >= len(text) or text[start] != "$":
        return None

    if not is_skill_boundary(text, start - 1):
        return None

    lower_text = text.lower()

    for name in sorted_known_skill_names(skills):
        token = f"${name}"
        end   = start + len(token)

        if lower_text.startswith(token, start) and is_skill_boundary(text, end):
            return end, name

    return None


def iter_known_skill_tokens(
    text: str,
    *,
    skills: typing.Iterable[SkillSpec] = (),
    offset: int = 0
) -> typing.Iterator[tuple[int, int, str]]:
    """迭代文本中的白名单 skill token。"""
    cursor: int = 0

    while True:
        start = text.find("$", cursor)
        if start < 0:
            return

        matched = match_known_skill_at(text, start, skills=skills)
        if matched is None:
            cursor = start + 1
            continue

        end, name = matched
        yield offset + start, offset + end, name
        cursor = end


def iter_paste_placeholder_tokens(
    text: str,
    *,
    paste_placeholders: typing.Iterable[str] = (),
    offset: int = 0
) -> typing.Iterator[tuple[int, int, str]]:
    """迭代折叠粘贴内容的可见占位文本。"""
    active = {
        str(placeholder or "")
        for placeholder in paste_placeholders
        if placeholder
    }

    for start, end, placeholder in iter_paste_placeholders(text):
        if placeholder not in active:
            continue
        active.remove(placeholder)
        yield offset + start, offset + end, "class:paste-placeholder"


def iter_prompt_tokens(
    text: str,
    *,
    paste_placeholders: typing.Iterable[str] = (),
    skills: typing.Iterable[SkillSpec] = (),
    offset: int = 0
) -> typing.Iterator[tuple[int, int, str]]:
    """迭代输入框中需要高亮的文本片段。"""
    tokens = [
        *iter_paste_placeholder_tokens(
            text,
            paste_placeholders=paste_placeholders,
            offset=offset,
        ),
        *((start, end, "class:skill-token") for start, end, _ in iter_known_skill_tokens(
            text,
            skills=skills,
            offset=offset,
        ))
    ]
    if text.startswith("!"):
        tokens.append((offset, offset + 1, "class:shell-escape"))

    cursor = offset
    for start, end, style in sorted(tokens, key=lambda item: (item[0], item[1])):
        if start < cursor:
            continue
        yield start, end, style
        cursor = end


def skill_query_token(text: str) -> str | None:
    """返回当前光标所在的 skill 查询 token。"""
    current_line = text.rpartition("\n")[2]
    if not current_line or current_line[-1].isspace():
        return None

    token = current_line.split()[-1] if current_line.split() else current_line
    if not token.startswith("$"):
        return None
    if not SKILL_PREFIX_RE.fullmatch(token):
        return None
    return token


def skill_completions(
    text: str,
    skills: typing.Iterable[SkillSpec] = ()
) -> typing.Iterator[Completion]:
    """生成 skill 补全项。"""
    token = skill_query_token(text)
    if token is None:
        return
    query = token[1:].strip().lower()

    matches = sorted(
        (
            (rank, skill)
            for skill in skills
            if (
                rank := skill_match_rank(
                    str(skill.name or ""),
                    query,
                )
            ) is not None
        ),
        key=lambda item: item[0],
    )

    for _rank, skill in matches:
        yield Completion(
            f"${skill.name} ",
            start_position=-len(token),
            display=skill_display_text(skill.name),
            display_meta=skill_meta_text(skill),
        )


def skill_match_rank(
    name: str,
    query: str
) -> tuple[int, int, int, str] | None:
    """返回 skill 名称匹配排序权重。"""
    folded_name  = str(name or "").casefold()
    folded_query = str(query or "").casefold()

    if not folded_query:
        return 0, 0, 0, folded_name

    if folded_name.startswith(folded_query):
        return 0, 0, len(folded_name), folded_name

    score = subsequence_match_score(folded_name, folded_query)
    if score is None:
        return None

    return 1, score, len(folded_name), folded_name


def subsequence_match_score(text: str, query: str) -> int | None:
    """返回非连续匹配跨度分数。"""
    if not query:
        return 0

    start: int | None = None
    cursor: int       = 0

    for char in query:
        index = text.find(char, cursor)
        if index < 0:
            return None
        if start is None:
            start = index
        cursor = index + 1

    if start is None:
        return 0

    return cursor - start


def skill_display_text(name: str) -> str:
    """返回 skill 菜单名称列。"""
    text = str(name or "")
    return truncate_display_width(text, SKILL_NAME_TRUNCATE_WIDTH)


def skill_meta_text(skill: SkillSpec) -> str:
    """返回 skill 菜单说明列。"""
    return combined_skill_meta_text(SKILL_CATEGORY_TAG, skill.description)


def combined_skill_meta_text(category_tag: str, description: str) -> str:
    """组合菜单类别标签和描述文本。"""
    tag    = str(category_tag or "").strip()
    detail = skill_description_text(description)

    if tag and detail:
        return f"{tag} {detail}"
    return tag or detail


def skill_description_text(description: str) -> str:
    """把 skill 描述规范化为单行文本。"""
    return " ".join(str(description or "").split())


def truncate_display_width(text: str, limit: int) -> str:
    """按终端显示宽度截断文本。"""
    if get_cwidth(text) <= limit:
        return text

    out: list[str] = []

    used: int   = 0
    target: int = max(0, limit - 3)

    for char in text:
        size = get_cwidth(char)
        if used + size > target:
            break
        out.append(char)
        used += size

    return "".join(out).rstrip() + "..."


if __name__ == '__main__':
    pass
