from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal

TuiEventKind = Literal[
    "status",
    "text.delta",
    "text.block",
    "approval.required",
    "done"
]


@dataclass(frozen=True, slots=True)
class TuiEvent:
    """表示独立界面可消费的一条流式事件。"""

    kind: TuiEventKind
    text: str = ""
    payload: dict[str, Any] | None = None


async def simulate_stream(
    message: str,
    *,
    shell_mode: bool = False,
    delay: float = 0.025
) -> AsyncIterator[TuiEvent]:
    """生成覆盖状态、工具块、审批和正文增量的模拟事件。"""
    pause = max(0.0, float(delay))

    yield TuiEvent("status", "Running command" if shell_mode else "Thinking")
    await asyncio.sleep(pause * 4)

    if shell_mode:
        yield TuiEvent("text.block", f"• Ran local command\n  └ {message}\n")
        await asyncio.sleep(pause * 2)
        response = "Command simulation completed without changing the workspace."
    else:
        yield TuiEvent(
            "text.block",
            "• Explored\n  └ Read the current workspace and inspected related modules.\n"
        )
        await asyncio.sleep(pause * 2)

        if message.strip().lower() == "/approval":
            yield TuiEvent(
                "approval.required",
                payload=demo_approval_payload()
            )
            await asyncio.sleep(pause)

        response = (
            "The TUI stream is updating through discrete events while the input, "
            "status line, transcript viewport, and footer remain independently rendered."
        )

    yield TuiEvent("status", "Writing")
    for chunk in _stream_chunks(response):
        yield TuiEvent("text.delta", chunk)
        await asyncio.sleep(pause)

    yield TuiEvent("status", "")
    yield TuiEvent("done")


def demo_approval_payload() -> dict[str, Any]:
    """返回用于浮层演示的审批请求。"""
    return {
        "id": "tui-demo-approval",
        "title": "Review command",
        "tool": "shell_command",
        "prompt": "Allow Mind to run this command?",
        "command": "pytest mind_app/tui/tests -q",
        "availableDecisions": ["accept", "acceptForSession", "decline"]
    }


def _stream_chunks(text: str) -> list[str]:
    """把模拟正文切成保留空白的增量片段。"""
    return re.findall(r"\S+\s*|\s+", text)

