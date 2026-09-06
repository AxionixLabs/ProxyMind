import argparse
import asyncio
import sys
import typing
from pathlib import Path

from agent.application.views.builders.approval import build_approval_view
from agent.application.views.builders.tools import build_generic_tool_result_view
from agent.application.views.builders.tools import build_native_tool_result_view
from agent.application.views.builders.tools import build_tool_start_view
from agent.protocol.json_value import ThawedJsonValue
from agent.ports import ApprovalCompleted
from agent.ports import ApprovalStarted
from agent.ports import OutputSurfaceContext
from agent.ports import RetryChanged
from agent.ports import TerminalWaitCompleted
from agent.ports import TerminalWaitStarted
from agent.ports import ToolCompleted
from agent.ports import ToolStarted
from frontends.interaction.contracts import PromptContext
from frontends.terminal.progress import create_terminal_progress
from frontends.tui.adapters.input import create_tui_input
from frontends.tui.adapters.session import create_tui_output_session
from frontends.tui.contracts.text import FragmentBlock
from frontends.tui.core.queued import TuiSubmission
from frontends.tui.core.runtime import TuiRuntime
from observability import reset_sinks
from tests.support.pty.tui_scenario import ScenarioFacts


_SURFACE = OutputSurfaceContext(
    surface_id="surface-pty-render",
    cid="cid-pty-render",
    sid="sid-pty-render",
    turn_id="turn-pty-render",
    agent_id="root",
)


async def _wait_until(
    predicate: typing.Callable[[], bool],
    description: str,
    *,
    timeout: float = 10.0,
) -> None:
    """等待真实终端或父进程进入指定阶段。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return None
        await asyncio.sleep(0.01)
    raise TimeoutError(f"timed out waiting for {description}")


async def _checkpoint(facts: ScenarioFacts, stage: str) -> None:
    """发布阶段事实并等待父进程完成 Screen 断言。"""
    facts.stage = stage
    facts.write()
    acknowledgment = facts.path.with_suffix(f".{stage}.ack")
    await _wait_until(
        acknowledgment.exists,
        f"parent acknowledgment for {stage}",
    )


def _submission(value: str) -> TuiSubmission:
    """创建具有稳定身份的布局测试消息。"""
    return TuiSubmission(
        value=value,
        editable_text=value,
        paste_store={},
        client_message_id=f"message-{value.lower().replace(' ', '-')}",
    )


def _document_text(runtime: TuiRuntime) -> str:
    """返回包含稳定块和活动块的当前完整记录文本。"""
    return "".join(
        text
        for _style, text in runtime.document.transcript_fragments(
            width=runtime.terminal_width,
        )
    )


def _record_document(facts: ScenarioFacts, runtime: TuiRuntime) -> None:
    """记录真实渲染对应的文档所有权与去重事实。"""
    text = _document_text(runtime)
    facts.set_detail("document_text", text)
    facts.set_detail(
        "block_kinds",
        [block.kind for block in runtime.document.blocks],
    )
    facts.set_detail("active_kind", runtime.document.active_kind or "")
    facts.set_detail("scrollback_lines", runtime.document.scrollback_line_count)
    facts.set_detail("terminal_width", runtime.terminal_width)
    facts.set_detail("terminal_height", runtime.terminal_height)


def _patch_payload() -> dict[str, ThawedJsonValue]:
    """返回同时驱动结构化工具与 diff 重排的稳定补丁事实。"""
    return {
        "files": [{
            "path": "sample.py",
            "source_path": None,
            "action": "modify",
        }],
        "delta": {
            "exact": True,
            "changes": [{
                "path": "sample.py",
                "action": "modify",
                "old_content": "old value\n",
                "new_content": "new value with a longer rendered line\n",
                "source_path": None,
                "hunks": [{
                    "lines": [
                        {
                            "kind": "remove",
                            "text": "old value",
                            "old_line": 1,
                            "new_line": None,
                        },
                        {
                            "kind": "add",
                            "text": "new value with a longer rendered line",
                            "old_line": None,
                            "new_line": 1,
                        },
                    ],
                }],
            }],
        },
    }


async def _run_first_frame(
    runtime: TuiRuntime,
    facts: ScenarioFacts,
) -> None:
    """冻结产品启动首帧供多尺寸 Screen golden 核对。"""
    _record_document(facts, runtime)
    await _checkpoint(facts, "first_frame")


async def _run_layout(runtime: TuiRuntime, facts: ScenarioFacts) -> None:
    """组合正文、状态、队列和多行输入验证区域边界。"""
    runtime.append_submitted_query(
        "LAYOUT USER request with a line that must remain visible",
        "turn-layout",
    )
    runtime.append_block(
        FragmentBlock((("", "LAYOUT TRANSCRIPT response"),)),
        kind="assistant",
    )
    runtime.set_execution_active(True)
    runtime.defer_submission(_submission("LAYOUT QUEUED next turn"))
    runtime.track_pending_steer(_submission("LAYOUT PENDING steer"))
    runtime.set_process_status_label("LAYOUT STATUS one background process")
    runtime.replace_input_text("LAYOUT DRAFT first line\nsecond line\nthird line")
    runtime.invalidate()
    await asyncio.sleep(0.05)
    _record_document(facts, runtime)
    facts.set_detail("queued_active", runtime.submissions.queued_messages.active)
    facts.set_detail("pending_active", runtime.submissions.pending_steers.active)
    await _checkpoint(facts, "layout")


async def _run_stream_retry(
    runtime: TuiRuntime,
    facts: ScenarioFacts,
) -> None:
    """驱动流式正文、provider 重试与最终去重闭环。"""
    runtime.append_submitted_query("STREAM USER request", _SURFACE.turn_id)
    runtime.set_execution_active(True)
    runtime.begin_terminal_progress()
    session = create_tui_output_session(
        str(facts.path.with_suffix(".stream.log")),
        context=_SURFACE,
        animate=False,
        runtime=runtime,
    )
    await session.open()
    try:
        await session.control.append_assistant_delta(
            "STREAM OLD attempt line one\n"
            "STREAM OLD width marker 宽🙂é\n"
        )
        _record_document(facts, runtime)
        await _checkpoint(facts, "old_attempt")

        await session.control.append_assistant_delta(
            "STREAM TAIL https://example.test/a/very/long/path?value=1234567890\n"
            f"STREAM LONGWORD {'x' * 180}"
        )
        await session.control.settle_stream()
        _record_document(facts, runtime)
        await _checkpoint(facts, "stream_settled")

        await session.control.supersede_assistant_presentation()
        await session.activity.emit(RetryChanged(
            surface_id=_SURFACE.surface_id,
            turn_id=_SURFACE.turn_id,
            source="provider",
            state="started",
            presentation_epoch=1,
            round=1,
            attempt=2,
        ))
        _record_document(facts, runtime)
        await _checkpoint(facts, "retrying")

        await session.activity.emit(RetryChanged(
            surface_id=_SURFACE.surface_id,
            turn_id=_SURFACE.turn_id,
            source="provider",
            state="completed",
            presentation_epoch=1,
            round=1,
            attempt=2,
        ))
        await session.control.append_assistant_delta(
            "STREAM NEW regenerated answer\n"
        )
        await session.control.settle_stream()
        _record_document(facts, runtime)
        await _checkpoint(facts, "new_attempt")

        await session.control.prepare_external_output()
        await session.close(blink=False)
        runtime.end_terminal_progress()
        runtime.set_execution_active(False)
        _record_document(facts, runtime)
        document_text = _document_text(runtime)
        facts.set_detail("old_count", document_text.count("STREAM OLD attempt"))
        facts.set_detail("retry_count", document_text.count("Previous attempt"))
        facts.set_detail("new_count", document_text.count("STREAM NEW regenerated"))
        await _checkpoint(facts, "stream_final")
    finally:
        if session.is_open:
            await session.close(blink=False)
        runtime.end_terminal_progress()
        runtime.set_execution_active(False)


async def _run_operations(
    runtime: TuiRuntime,
    facts: ScenarioFacts,
) -> None:
    """驱动工具、审批、effect 与 shell 展示从活动态收敛到稳定块。"""
    runtime.append_submitted_query("OPERATIONS USER request", _SURFACE.turn_id)
    runtime.set_execution_active(True)
    session = create_tui_output_session(
        str(facts.path.with_suffix(".operations.log")),
        context=_SURFACE,
        animate=False,
        runtime=runtime,
    )
    await session.open()
    try:
        await session.activity.emit(ToolStarted(
            surface_id=_SURFACE.surface_id,
            turn_id=_SURFACE.turn_id,
            tool_id="tool-1",
            tool_kind="client",
            name="exec_command",
        ))
        await _checkpoint(facts, "tool_active")

        await session.presentation.emit(build_tool_start_view(
            "exec_command",
            {"command": "echo PTY SHELL"},
            call_id="tool-1",
        ))
        await session.activity.emit(ApprovalStarted(
            surface_id=_SURFACE.surface_id,
            turn_id=_SURFACE.turn_id,
            approval_id="approval-1",
            call_id="tool-1",
        ))
        await session.presentation.emit(build_approval_view(
            {
                "tool": "exec_command",
                "arguments": {"command": "echo PTY SHELL"},
            },
            decision="accept",
        ))
        await session.activity.emit(ApprovalCompleted(
            surface_id=_SURFACE.surface_id,
            turn_id=_SURFACE.turn_id,
            approval_id="approval-1",
            call_id="tool-1",
        ))
        await _checkpoint(facts, "approval_settled")

        await session.activity.emit(TerminalWaitStarted(
            surface_id=_SURFACE.surface_id,
            turn_id=_SURFACE.turn_id,
            call_id="tool-1",
            session_id="shell-1",
            command="echo PTY SHELL",
        ))
        await _checkpoint(facts, "shell_waiting")
        await session.activity.emit(TerminalWaitCompleted(
            surface_id=_SURFACE.surface_id,
            turn_id=_SURFACE.turn_id,
            call_id="tool-1",
            session_id="shell-1",
            command="echo PTY SHELL",
        ))
        await session.presentation.emit(build_native_tool_result_view(
            "exec_command",
            {"command": "echo PTY SHELL"},
            ok=True,
            data={"command": "echo PTY SHELL", "output_lines": ["PTY SHELL DONE"]},
            cost_ms=12,
            call_id="tool-1",
        ))
        await session.presentation.emit(build_generic_tool_result_view(
            "effect.apply",
            "PTY EFFECT COMMITTED",
            ok=True,
            call_id="effect-1",
        ))
        await session.activity.emit(ToolCompleted(
            surface_id=_SURFACE.surface_id,
            turn_id=_SURFACE.turn_id,
            tool_id="tool-1",
            tool_kind="client",
            name="exec_command",
        ))
        await session.close(blink=False)
        runtime.set_execution_active(False)
        _record_document(facts, runtime)
        text = _document_text(runtime)
        facts.set_detail("shell_result_count", text.count("PTY SHELL DONE"))
        facts.set_detail("effect_result_count", text.count("PTY EFFECT COMMITTED"))
        await _checkpoint(facts, "operations_final")
    finally:
        if session.is_open:
            await session.close(blink=False)
        runtime.set_execution_active(False)


async def _run_resize(runtime: TuiRuntime, facts: ScenarioFacts) -> None:
    """在活动 Markdown 下等待父进程 resize storm 并核对最终重排。"""
    runtime.append_submitted_query("RESIZE USER request", _SURFACE.turn_id)
    runtime.set_execution_active(True)
    session = create_tui_output_session(
        str(facts.path.with_suffix(".resize.log")),
        context=_SURFACE,
        animate=False,
        runtime=runtime,
    )
    await session.open()
    try:
        patch_arguments = {
            "patch": (
                "*** Begin Patch\n"
                "*** Update File: sample.py\n"
                "@@\n-old value\n+new value with a longer rendered line\n"
                "*** End Patch"
            ),
        }
        patch_payload = _patch_payload()
        await session.presentation.emit(build_tool_start_view(
            "apply_patch",
            patch_arguments,
            patch_preview=patch_payload,
            call_id="resize-patch",
        ))
        await session.presentation.emit(build_native_tool_result_view(
            "apply_patch",
            patch_arguments,
            ok=True,
            data=patch_payload,
            call_id="resize-patch",
        ))
        lines = [
            f"RESIZE ROW {index:02d} 中文宽度🙂 é "
            "https://example.test/no-space/abcdefghijklmnopqrstuvwxyz0123456789"
            for index in range(18)
        ]
        await session.control.append_assistant_delta("\n".join(lines))
        await session.control.settle_stream()
        _record_document(facts, runtime)
        await _checkpoint(facts, "resize_ready")
        expected_width = 51 if sys.platform == "win32" else 52
        await _wait_until(
            lambda: (
                runtime.terminal_width == expected_width
                and runtime.terminal_height == 14
            ),
            "final 52x14 terminal geometry",
        )
        await asyncio.sleep(0.25)
        _record_document(facts, runtime)
        facts.set_detail("row_zero_count", _document_text(runtime).count("RESIZE ROW 00"))
        facts.set_detail("row_last_count", _document_text(runtime).count("RESIZE ROW 17"))
        await _checkpoint(facts, "resize_final")
    finally:
        await session.close(blink=False)
        runtime.set_execution_active(False)


async def _run_overlay(runtime: TuiRuntime, facts: ScenarioFacts) -> None:
    """验证 transcript 搜索、滚动、raw/rich 与活动输出的跨尺寸状态。"""
    for index in range(36):
        runtime.append_block(
            FragmentBlock((("", f"OVERLAY ROW {index:02d} target-{index % 4}"),)),
            kind="assistant",
            raw_text=f"OVERLAY RAW {index:02d} target-{index % 4}",
        )
    runtime.invalidate()
    await _checkpoint(facts, "overlay_ready")
    await _wait_until(
        lambda: runtime.screen.transcript_overlay.active,
        "transcript overlay open",
    )
    await _checkpoint(facts, "overlay_open")
    await _wait_until(
        lambda: (
            runtime.screen.transcript_overlay.search_query == "target-2"
            and not runtime.screen.transcript_overlay.search_editing
        ),
        "transcript search confirmation",
    )
    search_offset = runtime.screen.transcript_overlay.scroll_offset
    facts.set_detail("search_offset", search_offset)
    await _checkpoint(facts, "overlay_search")
    await _wait_until(
        lambda: runtime.screen.transcript_overlay.scroll_offset != search_offset,
        "transcript overlay scroll",
    )
    scrolled_offset = runtime.screen.transcript_overlay.scroll_offset
    runtime.set_active_renderable(
        FragmentBlock((("", "OVERLAY LIVE target-2 update"),)),
        kind="assistant",
        raw_text="OVERLAY LIVE RAW target-2 update",
    )
    await asyncio.sleep(0.05)
    facts.set_detail("scrolled_offset", scrolled_offset)
    facts.set_detail(
        "live_offset",
        runtime.screen.transcript_overlay.scroll_offset,
    )
    await _checkpoint(facts, "overlay_live")
    await _wait_until(
        lambda: (
            runtime.terminal_width == (63 if sys.platform == "win32" else 64)
            and runtime.terminal_height == 16
            and runtime.screen.transcript_overlay.raw_mode
        ),
        "raw overlay at final geometry",
    )
    await asyncio.sleep(0.15)
    facts.set_detail("resized_offset", runtime.screen.transcript_overlay.scroll_offset)
    facts.set_detail(
        "search_result_position",
        list(runtime.screen.transcript_overlay.search_result_position),
    )
    _record_document(facts, runtime)
    await _checkpoint(facts, "overlay_resized")
    await _wait_until(
        lambda: not runtime.screen.transcript_overlay.active,
        "transcript overlay close",
    )


async def _run_sync_failure(
    runtime: TuiRuntime,
    facts: ScenarioFacts,
    *,
    cancelled: bool,
) -> None:
    """在同步输出打开后注入取消或异常，验证生命周期恢复终端。"""
    if not runtime.screen.begin_synchronized_output():
        raise RuntimeError("PTY terminal did not accept synchronized output")
    await _checkpoint(facts, "sync_active")
    if cancelled:
        raise asyncio.CancelledError
    raise RuntimeError("injected PTY render failure")


async def _run(scenario: str, facts_path: Path) -> None:
    """在原生终端中运行指定渲染与生命周期场景。"""
    reset_sinks()
    facts = ScenarioFacts(facts_path, scenario)
    runtime = TuiRuntime(
        input_obj=create_tui_input(sys.stdin),
        terminal_progress=create_terminal_progress(sys.stdout),
    )
    runtime.set_prompt_context(PromptContext(
        model="pty-model",
        workspace_label="pty-workspace",
        permissions_label="test",
    ))
    await runtime.open()
    try:
        if scenario == "first_frame":
            await _run_first_frame(runtime, facts)
        elif scenario == "layout":
            await _run_layout(runtime, facts)
        elif scenario == "stream_retry":
            await _run_stream_retry(runtime, facts)
        elif scenario == "operations":
            await _run_operations(runtime, facts)
        elif scenario == "resize":
            await _run_resize(runtime, facts)
        elif scenario == "overlay":
            await _run_overlay(runtime, facts)
        elif scenario in {"sync_cancel", "sync_exception"}:
            await _run_sync_failure(
                runtime,
                facts,
                cancelled=scenario == "sync_cancel",
            )
        else:
            raise ValueError(f"unsupported PTY render scenario: {scenario}")
        facts.stage = "complete"
    finally:
        await runtime.close()
        facts.write()


def main() -> int:
    """解析真实 TUI 渲染场景入口。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario")
    parser.add_argument("facts", type=Path)
    arguments = parser.parse_args()
    asyncio.run(_run(arguments.scenario, arguments.facts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
