# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from enum import IntEnum


class Decision(IntEnum):
    """表示允许、询问和禁止三种策略结果。"""

    Allow = 0
    Prompt = 1
    Forbidden = 2

    @classmethod
    def parse(cls, value: object) -> "Decision":
        """把规则文件中的决定文本转换为策略决定。"""
        if isinstance(value, cls):
            return value
        normalized = str(value or "").strip().casefold()
        values = {
            "allow": cls.Allow,
            "prompt": cls.Prompt,
            "forbidden": cls.Forbidden,
        }
        try:
            return values[normalized]
        except KeyError as error:
            raise ValueError(f"unknown decision: {value!r}") from error

    @classmethod
    def strictest(cls, *decisions: "Decision | None") -> "Decision | None":
        """返回输入决定中的最严格结果。"""
        present = [decision for decision in decisions if decision is not None]
        return max(present) if present else None


if __name__ == '__main__':
    pass
