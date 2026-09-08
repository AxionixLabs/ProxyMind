"""提供逻辑 frame 事实及跨帧原子性断言。"""

import enum
from dataclasses import dataclass


class FrameKind(enum.Enum):
    NORMAL = "normal"
    ASSISTANT_HANDOFF = "assistant_handoff"
    APPROVAL_COMPLETED = "approval_completed"
    TERMINAL = "terminal"


class FrameIndicator(enum.Enum):
    HIDDEN = "hidden"
    THINKING = "thinking"
    WORKING = "working"
    APPROVAL = "approval"
    RETRYING = "retrying"


class RuntimeInvariantError(AssertionError):
    """表示场景执行违反了稳定的 Agent Runtime 不变量。"""


@dataclass(frozen=True, slots=True)
class LogicalFrame:
    """描述一次渲染后用户实际可见的逻辑画面。"""

    turn_id: str
    indicator: FrameIndicator
    assistant_text: str = ""
    kind: FrameKind = FrameKind.NORMAL
    tool_leases: int = 0
    approval_leases: int = 0


class FrameTrace:
    """记录逻辑画面并验证跨帧原子性和生命周期。"""

    def __init__(self) -> None:
        self._frames: list[LogicalFrame] = []

    @property
    def frames(self) -> tuple[LogicalFrame, ...]:
        """返回当前记录的不可变帧序列。"""
        return tuple(self._frames)

    def append(self, frame: LogicalFrame) -> None:
        """追加一帧并立即验证完整画面契约。"""
        self._frames.append(frame)
        self.assert_contract()

    def assert_contract(self) -> None:
        """验证正文交接、终态、身份和 lease 不变量。"""
        terminal_turns: set[str] = set()
        observed_turns: set[str] = set()
        newest_turn_id = ""

        for index, frame in enumerate(self._frames, start=1):
            if frame.turn_id != newest_turn_id:
                if frame.turn_id in observed_turns:
                    raise RuntimeInvariantError(
                        f"frame {index}: an old turn became visible again"
                    )
                observed_turns.add(frame.turn_id)
                newest_turn_id = frame.turn_id
            has_assistant = bool(frame.assistant_text.strip())
            if frame.indicator is FrameIndicator.THINKING and has_assistant:
                raise RuntimeInvariantError(
                    f"frame {index}: Thinking and assistant content coexist"
                )
            if frame.kind is FrameKind.ASSISTANT_HANDOFF and (
                frame.indicator is not FrameIndicator.HIDDEN
                or not has_assistant
            ):
                raise RuntimeInvariantError(
                    f"frame {index}: assistant handoff is not atomic"
                )
            if frame.kind is FrameKind.TERMINAL:
                if frame.indicator is not FrameIndicator.HIDDEN:
                    raise RuntimeInvariantError(
                        f"frame {index}: terminal frame retained activity"
                    )
                terminal_turns.add(frame.turn_id)
            elif frame.turn_id in terminal_turns and (
                frame.indicator is not FrameIndicator.HIDDEN
                or has_assistant
                or frame.tool_leases > 0
                or frame.approval_leases > 0
            ):
                raise RuntimeInvariantError(
                    f"frame {index}: content or activity returned after terminal"
                )
            if frame.kind is FrameKind.APPROVAL_COMPLETED and (
                frame.tool_leases <= 0
            ):
                raise RuntimeInvariantError(
                    f"frame {index}: approval completion lost tool lease"
                )
