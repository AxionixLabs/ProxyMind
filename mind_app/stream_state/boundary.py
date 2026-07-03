# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from .spacing import segment_prefix


@dataclass(slots=True)
class ExternalOutputBoundary(object):
    """记录已直接打印到终端的外部输出边界。"""

    display: str
    trailing_newlines: int
    has_text: bool

    @classmethod
    def from_output(cls, *, display: str, text: str) -> "ExternalOutputBoundary":
        """根据一段外部输出构造边界状态。"""
        value = str(text or "")
        return cls(
            display=display,
            trailing_newlines=OutputBoundaryState.count_trailing_newlines(value),
            has_text=bool(value.strip())
        )

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "ExternalOutputBoundary":
        """从旧 dict 结构构造边界状态。"""
        return cls(
            display=str(value.get("display") or ""),
            trailing_newlines=max(0, int(value.get("trailing_newlines") or 0)),
            has_text=bool(value.get("has_text"))
        )

    def as_dict(self) -> dict[str, typing.Any]:
        """返回兼容旧调用方的 dict 表示。"""
        return {
            "display"           : self.display,
            "trailing_newlines" : self.trailing_newlines,
            "has_text"          : self.has_text
        }

    def with_spacing(self, text: str) -> "ExternalOutputBoundary":
        """返回合并外部空白后的边界状态。"""
        value = str(text or "")
        trailing = OutputBoundaryState.raw_trailing_newlines(
            value,
            previous_trailing=self.trailing_newlines
        )
        return ExternalOutputBoundary(
            display=self.display,
            trailing_newlines=trailing,
            has_text=self.has_text or bool(value.strip())
        )


class OutputBoundaryState(object):
    """记录一条输出通道的段间边界状态。"""

    def __init__(
        self,
        *,
        stream_display: str,
        last_display: str | None = None,
        trailing_newlines: int = 0,
        at_line_start: bool = True
    ) -> None:
        """初始化输出边界状态。"""
        self.stream_display = stream_display
        self.last_display = last_display
        self.trailing_newlines = max(0, int(trailing_newlines or 0))
        self.at_line_start = bool(at_line_start)

    def prefix(
        self,
        *,
        for_display: str,
        incoming_text: str | None = None
    ) -> str:
        """返回下一段输出前需要补充的段间前缀。"""
        return segment_prefix(
            last_display=self.last_display,
            trailing_newlines=self.trailing_newlines,
            stream_display=self.stream_display,
            for_display=for_display,
            incoming_text=incoming_text
        )

    def observe_display(self, *, display: str, text: str) -> None:
        """记录已完成归一化的一段常规输出。"""
        value = str(text or "")
        self.at_line_start = value.endswith("\n")
        self.trailing_newlines = self.count_trailing_newlines(value)
        self.last_display = display

    def observe_raw(self, text: str) -> None:
        """记录 raw 输出；纯换行边界需要和上一段尾部累计。"""
        value = str(text or "")
        self.at_line_start = value.endswith("\n")
        self.trailing_newlines = self.raw_trailing_newlines(
            value,
            previous_trailing=self.trailing_newlines
        )

    def clear(self) -> None:
        """重置边界状态。"""
        self.last_display = None
        self.trailing_newlines = 0
        self.at_line_start = True

    @classmethod
    def raw_trailing_newlines(
        cls,
        text: str,
        *,
        previous_trailing: int
    ) -> int:
        """计算 raw 写入后的尾部换行数。"""
        trailing = cls.count_trailing_newlines(text)
        if trailing and text == "\n" * len(text):
            return max(0, int(previous_trailing or 0)) + trailing
        return trailing

    @staticmethod
    def count_trailing_newlines(text: str) -> int:
        """统计文本尾部连续换行数量。"""
        count = 0
        for ch in reversed(str(text or "")):
            if ch != "\n":
                break
            count += 1
        return count


if __name__ == '__main__':
    pass
