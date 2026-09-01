# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====


def segment_prefix(
    *,
    last_display: str | None,
    trailing_newlines: int,
    stream_display: str,
    for_display: str,
    incoming_text: str | None = None,
) -> str:
    """返回下一段流式输出前需要补充的段间前缀。"""
    if last_display is None:
        return ""

    if last_display == stream_display and for_display == stream_display:
        return ""

    trailing = max(0, int(trailing_newlines or 0))
    if trailing >= 2:
        return ""

    if (
        for_display == stream_display
        and incoming_text
        and incoming_text.startswith("\n")
    ):
        return ""

    return "\n" * (2 - trailing)


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

    @staticmethod
    def count_trailing_newlines(text: str) -> int:
        """统计文本尾部连续换行数量。"""
        count = 0
        for ch in reversed(str(text or "")):
            if ch != "\n":
                break
            count += 1
        return count

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


if __name__ == '__main__':
    pass
