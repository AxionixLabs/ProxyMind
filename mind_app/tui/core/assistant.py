# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====


class TuiAssistantStream(object):
    """保存当前 assistant 正文原文及其待处理段落边界。"""

    def __init__(self) -> None:
        self.text: str = ""
        self.boundary_pending: bool = False

    @property
    def active(self) -> bool:
        """返回当前是否存在 assistant 正文。"""
        return bool(self.text)

    def prepare_delta(self, delta: str) -> str:
        """消费段落边界并返回可直接追加的正文增量。"""
        value = str(delta or "")
        if not value:
            return ""
        if not self.boundary_pending:
            return value

        self.boundary_pending = False
        if not self.text or self.text.endswith("\n") or value.startswith("\n"):
            return value
        return f"\n{value}"

    def append(self, delta: str) -> None:
        """追加一段已经处理过边界的正文。"""
        self.text += str(delta or "")

    def mark_boundary(self) -> None:
        """标记下一段正文前需要保留段落边界。"""
        self.boundary_pending = True

    def discard_boundary(self) -> None:
        """消费不再属于当前正文块的待处理边界。"""
        self.boundary_pending = False

    def clear(self) -> None:
        """清空当前正文和段落边界。"""
        self.text = ""
        self.boundary_pending = False


if __name__ == '__main__':
    pass
