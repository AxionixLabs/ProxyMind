# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from collections.abc import (
    Awaitable,
    Callable
)
from dataclasses import dataclass
from .events import AppEvent
from .transcript import TranscriptCellKind

WriteBlock = Callable[[str, TranscriptCellKind], None]
RequestApproval = Callable[[dict], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class ProjectionActions:
    """定义业务事件可产生的界面操作。"""

    set_status: Callable[[str], None]
    write_stream: Callable[[str], None]
    finish_stream: Callable[[], None]
    write_block: WriteBlock
    request_approval: RequestApproval


class AppEventProjector:
    """把结构化应用事件投影为终端界面操作。"""

    def __init__(self, actions: ProjectionActions) -> None:
        """初始化事件投影操作。"""
        self.actions = actions

    async def project(self, event: AppEvent) -> None:
        """投影单条应用事件。"""
        payload = event.payload or {}
        if event.kind == "turn.start":
            return
        if event.kind == "turn.thinking":
            self.actions.set_status("Working")
            return
        if event.kind == "turn.failed":
            self.actions.set_status("")
            error = payload.get("error") or event.text or "unknown error"
            self.actions.write_block(f"• Turn failed\n  └ {error}", "error")
            return
        if event.kind == "text.delta":
            self.actions.write_stream(event.text)
            return
        if event.kind == "text.done":
            self.actions.finish_stream()
            self.actions.set_status("Waiting")
            return
        if event.kind == "text.meta":
            return
        if event.kind == "tool.builtin.call":
            name = str(payload.get("name") or "builtin").replace("_", " ")
            self.actions.set_status(f"Running {name}")
            return
        if event.kind == "tool.builtin.done":
            name = str(payload.get("name") or "builtin").replace("_", " ").title()
            summary = str(payload.get("summary") or "Completed")
            self.actions.set_status("")
            self.actions.write_block(f"• {name}\n  └ {summary}", "trace")
            return
        if event.kind == "tool.calls.start":
            self.actions.set_status("Preparing tools")
            return
        if event.kind == "tool.approval_required":
            self.actions.set_status("")
            try:
                decision = await self.actions.request_approval(payload)
            except BaseException:
                if event.reply is not None and not event.reply.done():
                    event.reply.cancel()
                raise
            if event.reply is not None and not event.reply.done():
                event.reply.set_result(decision)
            label = {
                "accept": "approved",
                "acceptForSession": "approved for this session",
                "decline": "declined"
            }.get(decision, "declined")
            self.actions.write_block(f"• Approval {label}", "approval")
            return
        if event.kind == "tool.call":
            name = str(payload.get("name") or "tool")
            raw_arguments = payload.get("arguments")
            arguments = raw_arguments if isinstance(raw_arguments, dict) else {}
            command = str(arguments.get("command") or arguments or "")
            self.actions.set_status(f"Running {name}")
            self.actions.write_block(f"• Called {name}\n  └ {command}", "trace")
            return
        if event.kind == "tool.output":
            output = str(payload.get("output") or "Completed")
            self.actions.write_block(f"• Tool output\n  └ {output}", "trace")
            self.actions.set_status("Waiting")
            return
        if event.kind == "display.block":
            kind = str(payload.get("kind") or "trace")
            if kind not in {
                "header",
                "user",
                "assistant",
                "trace",
                "approval",
                "error",
                "lifecycle"
            }:
                kind = "trace"
            self.actions.write_block(event.text, kind)
            return
        if event.kind == "status.update":
            self.actions.set_status(event.text)
            return
        if event.kind == "tool.calls.done":
            self.actions.set_status("Thinking")
            return
        if event.kind == "lifecycle.display":
            message = str(
                payload.get("message")
                or payload.get("text")
                or "Lifecycle update"
            )
            self.actions.write_block(f"• {message}", "lifecycle")
            return
        if event.kind == "turn.done":
            self.actions.set_status("")


if __name__ == '__main__':
    pass
