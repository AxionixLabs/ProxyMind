from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class TuiState:
    """保存独立终端界面的可变展示状态。"""

    transcript: str
    status_text: str = ""
    status_phase: int = 0
    shell_mode: bool = False
    follow_tail: bool = True
    approval_visible: bool = False
    submitted: list[str] = field(default_factory=list)

    def append(self, text: str) -> None:
        """向会话正文追加文本。"""
        self.transcript += str(text or "")

    def set_status(self, text: str) -> None:
        """设置固定状态行并重置动画相位。"""
        self.status_text = str(text or "").strip()
        self.status_phase = 0

    def tick_status(self) -> None:
        """推进固定状态行的动画相位。"""
        self.status_phase += 1


def consume_shell_prefix(
    text: str,
    cursor_position: int,
    *,
    shell_mode: bool
) -> tuple[str, int, bool]:
    """消费输入开头的叹号并返回新的输入状态。"""
    if shell_mode or not text.startswith("!"):
        return text, cursor_position, shell_mode

    return text[1:], max(0, cursor_position - 1), True

