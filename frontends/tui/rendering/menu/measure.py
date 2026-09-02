# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from prompt_toolkit.formatted_text import StyleAndTextTuples

from ..fragments import split_formatted_lines


def line_count(fragments: StyleAndTextTuples) -> int:
    """返回格式化片段去除尾部换行后的逻辑行数。"""
    lines = split_formatted_lines(fragments)
    if lines and not lines[-1]:
        lines.pop()
    return len(lines)


if __name__ == '__main__':
    pass
