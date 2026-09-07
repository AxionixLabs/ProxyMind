import argparse
import asyncio
import sys
import typing
from pathlib import Path

from agent.application.approvals.coordinator import ApprovalCoordinator
from agent.application.views.builders.tools import build_native_tool_result_view
from agent.application.views.builders.tools import build_tool_start_view
from agent.protocol.json_value import ThawedJsonValue
from agent.ports import OutputSurfaceContext
from agent.ports import RetryChanged
from frontends.interaction.contracts import PromptContext
from frontends.terminal.capabilities import detect_terminal_capabilities
from frontends.terminal.probe import RgbColor
from frontends.terminal.probe import TerminalDefaultColors
from frontends.terminal.probe import TerminalDefaultColorsCache
from frontends.terminal.probe import TerminalProbeMethod
from frontends.tui.adapters.input import create_tui_input
from frontends.tui.adapters.session import create_tui_output_session
from frontends.tui.contracts.text import FragmentBlock
from frontends.tui.core.queued import TuiSubmission
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.core.styles import failure_text_block
from observability import reset_sinks
from tests.support.pty.tui_scenario import ScenarioFacts


_SURFACE = OutputSurfaceContext(
    surface_id="surface-pty-color",
    cid="cid-pty-color",
    sid="sid-pty-color",
    turn_id="turn-pty-color",
    agent_id="root",
)
_RESET_SENTINEL = "PTY RESET SENTINEL"


class _FixedColorProbe:
    """为真实 PTY 场景提供确定且可计数的终端主题响应。"""

    def __init__(
        self,
        foreground: RgbColor | None,
        background: RgbColor | None,
    ) -> None:
        self.foreground = foreground
        self.background = background
        self.calls = 0

    def __call__(
        self,
        _input_stream: typing.IO[str],
        _output_stream: typing.IO[str],
        _timeout: float,
    ) -> TerminalDefaultColors:
        self.calls += 1
        return TerminalDefaultColors(
            foreground=self.foreground,
            background=self.background,
            attempted=True,
            method=TerminalProbeMethod.CUSTOM,
        )


async def _wait_until(
    predicate: typing.Callable[[], bool],
    description: str,
    *,
    timeout: float = 10.0,
) -> None:
    """等待真实交互或父进程进入指定阶段。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return None
        await asyncio.sleep(0.01)
    raise TimeoutError(f"timed out waiting for {description}")


async def _checkpoint(
    runtime: TuiRuntime,
    facts: ScenarioFacts,
    stage: str,
) -> None:
    """发布业务事实并等待父进程完成当前 Screen 断言。"""
    render_counter = runtime.screen.application.render_counter
    runtime.invalidate()
    await _wait_until(
        lambda: runtime.screen.application.render_counter > render_counter,
        f"rendered frame for {stage}",
    )
    facts.stage = stage
    facts.write()
    acknowledgment = facts.path.with_suffix(f".{stage}.ack")
    await _wait_until(
        acknowledgment.exists,
        f"parent acknowledgment for {stage}",
    )


def _theme_colors(
    theme: str,
) -> tuple[RgbColor | None, RgbColor | None]:
    """返回验收主题使用的稳定默认前景和背景。"""
    if theme == "dark":
        return (238, 238, 238), (17, 17, 17)
    if theme == "light":
        return (17, 17, 17), (250, 250, 250)
    if theme == "partial":
        return (238, 238, 238), None
    if theme == "unknown":
        return None, None
    raise ValueError(f"unsupported PTY color theme: {theme}")


def _style_fact(runtime: TuiRuntime, style_class: str) -> dict[str, ThawedJsonValue]:
    """读取 Application 最终用于渲染的具名样式属性。"""
    attrs = runtime.screen.application.style.get_attrs_for_style_str(
        f"class:{style_class}"
    )
    return {
        "foreground": attrs.color or "default",
        "background": attrs.bgcolor or "default",
        "bold": attrs.bold,
        "dim": attrs.dim,
        "italic": attrs.italic,
        "reverse": attrs.reverse,
    }


def _style_snapshot(runtime: TuiRuntime) -> dict[str, ThawedJsonValue]:
    """截取实际场景所用语义样式，供 Screen 单元格逐项对照。"""
    classes = (
        "input-surface",
        "footer.brand",
        "footer.model",
        "completion-menu.completion.current",
        "queue.text",
        "queue.text.queued",
        "terminal.attention.plain",
        "terminal.failure",
        "terminal.success",
        "approval-card",
        "approval-option-selected",
        "approval-mcp-destructive",
        "transcript.overlay.search-query",
        "transcript.overlay.search-match",
    )
    return {
        style_class: _style_fact(runtime, style_class)
        for style_class in classes
    }


def _submission(value: str, suffix: str) -> TuiSubmission:
    """创建带稳定身份的真实队列消息。"""
    return TuiSubmission(
        value=value,
        editable_text=value,
        paste_store={},
        client_message_id=f"color-{suffix}",
    )


def _patch_payload() -> dict[str, ThawedJsonValue]:
    """返回同时包含新增和删除行的稳定补丁事实。"""
    return {
        "files": [{
            "path": "color_sample.py",
            "source_path": None,
            "action": "modify",
        }],
        "delta": {
            "exact": True,
            "changes": [{
                "path": "color_sample.py",
                "action": "modify",
                "old_content": "COLOR DIFF REMOVE\n",
                "new_content": "COLOR DIFF ADD\n",
                "source_path": None,
                "hunks": [{
                    "lines": [
                        {
                            "kind": "remove",
                            "text": "COLOR DIFF REMOVE",
                            "old_line": 1,
                            "new_line": None,
                        },
                        {
                            "kind": "add",
                            "text": "COLOR DIFF ADD",
                            "old_line": None,
                            "new_line": 1,
                        },
                    ],
                }],
            }],
        },
    }


async def _run_completion(runtime: TuiRuntime, facts: ScenarioFacts) -> None:
    """通过真实键盘输入打开 slash completion。"""
    reader = asyncio.create_task(runtime.read_message(PromptContext(
        model="color-model",
        workspace_label="color-workspace",
        permissions_label="full access",
    )))
    await _checkpoint(runtime, facts, "input_ready")
    await _wait_until(
        lambda: (
            runtime.screen.input.buffer.text == "/f"
            and runtime.screen.input.buffer.complete_state is not None
        ),
        "slash completion",
    )
    state = runtime.screen.input.buffer.complete_state
    facts.set_detail("completion_open", state is not None)
    facts.set_detail(
        "completion_value",
        state.current_completion.text if state is not None else "",
    )
    await _checkpoint(runtime, facts, "completion")
    await _wait_until(
        lambda: runtime.screen.input.buffer.complete_state is None,
        "completion close",
    )
    reader.cancel()
    await asyncio.gather(reader, return_exceptions=True)


async def _run_turn_surfaces(runtime: TuiRuntime, facts: ScenarioFacts) -> None:
    """驱动真实 Turn 的流式、重试、队列、工具和 diff 表面。"""
    pending = _submission("COLOR PENDING STEER", "pending")
    rejected = _submission("COLOR REJECTED STEER", "rejected")
    queued = _submission("COLOR QUEUED MESSAGE", "queued")
    runtime.append_submitted_query("COLOR USER REQUEST", _SURFACE.turn_id)
    runtime.set_execution_active(True)
    runtime.track_pending_steer(pending)
    runtime.defer_rejected_steer(rejected)
    runtime.defer_submission(queued)
    session = create_tui_output_session(
        str(facts.path.with_suffix(".color.log")),
        context=_SURFACE,
        animate=False,
        runtime=runtime,
    )
    await session.open()
    try:
        await session.control.append_assistant_delta(
            "COLOR STREAM ACTIVE "
            "[PTY COLOR LINK](https://example.test/pty-color)\n"
        )
        await session.control.settle_stream()
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
        await session.control.append_assistant_delta("COLOR STREAM REGENERATED")
        facts.set_detail("pending_active", runtime.submissions.pending_steers.active)
        facts.set_detail("rejected_active", runtime.submissions.rejected_steers.active)
        facts.set_detail("queued_active", runtime.submissions.queued_messages.active)
        facts.set_detail("active_kind", runtime.document.active_kind or "")
        await _checkpoint(runtime, facts, "turn_surfaces")

        await session.activity.emit(RetryChanged(
            surface_id=_SURFACE.surface_id,
            turn_id=_SURFACE.turn_id,
            source="provider",
            state="completed",
            presentation_epoch=1,
            round=1,
            attempt=2,
        ))
        await session.control.settle_stream()
        runtime.resolve_pending_steer(pending.client_message_id)
        runtime.discard_rejected_steer(rejected.client_message_id)
        runtime.submissions.queued_messages.remove(queued.client_message_id)

        patch_arguments = {"patch": (
            "*** Begin Patch\n"
            "*** Update File: color_sample.py\n"
            "@@\n"
            "-COLOR DIFF REMOVE\n"
            "+COLOR DIFF ADD\n"
            "*** End Patch"
        )}
        patch_payload = _patch_payload()
        await session.presentation.emit(build_tool_start_view(
            "apply_patch",
            patch_arguments,
            patch_preview=patch_payload,
            call_id="color-patch",
        ))
        await session.presentation.emit(build_native_tool_result_view(
            "apply_patch",
            patch_arguments,
            ok=True,
            data=patch_payload,
            call_id="color-patch",
        ))
        facts.set_detail("diff_rendered", True)
        await _checkpoint(runtime, facts, "diff")

        approval = asyncio.create_task(ApprovalCoordinator(runtime).request({
            "kind": "mcp_tool_call",
            "approval_id": "color-approval",
            "call_id": "color-call",
            "server": "color-server",
            "tool_name": "delete_color_record",
            "tool_title": "Delete color record",
            "tool_description": "Remove one test record.",
            "arguments": {"record": "COLOR APPROVAL TARGET"},
            "annotations": {"destructive_hint": True},
            "available_decisions": ["accept", "decline"],
        }))
        await _wait_until(
            lambda: runtime.screen.approval.state is not None,
            "approval surface",
        )
        facts.set_detail("approval_selected_index", runtime.screen.approval.selected_index)
        await _checkpoint(runtime, facts, "approval_initial")
        await _wait_until(
            lambda: runtime.screen.approval.selected_index == 1,
            "approval selection move",
        )
        facts.set_detail("approval_moved_index", runtime.screen.approval.selected_index)
        await _checkpoint(runtime, facts, "approval_moved")
        decision = await approval
        facts.set_detail("approval_decision", decision)
    finally:
        if session.is_open:
            await session.close(blink=False)
        runtime.set_execution_active(False)


async def _run_overlay(runtime: TuiRuntime, facts: ScenarioFacts) -> None:
    """通过真实按键验证 transcript 着色、滚动和关闭。"""
    for index in range(28):
        runtime.append_block(
            FragmentBlock(((
                "class:terminal.primary",
                f"COLOR OVERLAY ROW {index:02d} match-{index % 3}",
            ),)),
            kind="assistant",
            raw_text=f"COLOR RAW ROW {index:02d} match-{index % 3}",
        )
    runtime.invalidate()
    await _checkpoint(runtime, facts, "overlay_ready")
    await _wait_until(
        lambda: runtime.screen.transcript_overlay.active,
        "transcript overlay open",
    )
    initial_offset = runtime.screen.transcript_overlay.scroll_offset
    facts.set_detail("overlay_initial_offset", initial_offset)
    await _checkpoint(runtime, facts, "overlay_open")
    await _wait_until(
        lambda: runtime.screen.transcript_overlay.scroll_offset != initial_offset,
        "transcript scroll",
    )
    facts.set_detail(
        "overlay_scrolled_offset",
        runtime.screen.transcript_overlay.scroll_offset,
    )
    facts.set_detail("styles_final", _style_snapshot(runtime))
    await _checkpoint(runtime, facts, "overlay_scrolled")
    await _wait_until(
        lambda: not runtime.screen.transcript_overlay.active,
        "transcript overlay close",
    )


async def _run_full(runtime: TuiRuntime, facts: ScenarioFacts) -> None:
    """运行颜色矩阵使用的完整真实 TUI 交互。"""
    await _run_completion(runtime, facts)
    await _run_turn_surfaces(runtime, facts)
    await _run_overlay(runtime, facts)


async def _run_exit(runtime: TuiRuntime, facts: ScenarioFacts, mode: str) -> None:
    """在着色帧后触发取消或异常退出。"""
    runtime.append_block(
        failure_text_block(f"COLOR {mode.upper()} EXIT"),
        kind="error",
    )
    runtime.invalidate()
    await _checkpoint(runtime, facts, "exit_styled")
    if mode == "cancel":
        raise asyncio.CancelledError
    raise RuntimeError("injected color scenario failure")


async def _execute(
    scenario: str,
    theme: str,
    facts_path: Path,
) -> None:
    """探测一次能力并在真实终端中运行颜色场景。"""
    reset_sinks()
    facts = ScenarioFacts(facts_path, scenario)
    foreground, background = _theme_colors(theme)
    probe = _FixedColorProbe(foreground, background)
    capabilities = detect_terminal_capabilities(
        input_stream=sys.stdin,
        output_stream=sys.stdout,
        color_probe=probe,
        default_colors_cache=TerminalDefaultColorsCache(),
    )
    runtime = TuiRuntime(
        input_obj=create_tui_input(sys.stdin),
        terminal_capabilities=capabilities,
    )
    runtime.set_prompt_context(PromptContext(
        model="color-model",
        workspace_label="color-workspace",
        permissions_label="full access",
    ))
    facts.set_detail("theme", theme)
    facts.set_detail("identity", capabilities.identity.kind.value)
    facts.set_detail("raw_color_level", capabilities.color_support.raw_level.value)
    facts.set_detail(
        "effective_color_level",
        capabilities.color_support.effective_level.value,
    )
    facts.set_detail("color_depth", runtime.screen.application.color_depth.value)
    facts.set_detail("styles_initial", _style_snapshot(runtime))
    await runtime.open()
    try:
        if scenario == "full":
            await _run_full(runtime, facts)
            facts.stage = "complete"
        elif scenario in {"cancel", "failure"}:
            await _run_exit(runtime, facts, scenario)
        else:
            raise ValueError(f"unsupported PTY color scenario: {scenario}")
    finally:
        facts.set_detail("probe_calls", probe.calls)
        await runtime.close()
        facts.write()
        sys.stdout.write(f"\n{_RESET_SENTINEL}\n")
        sys.stdout.flush()


def main() -> int:
    """解析真实 TUI 颜色场景入口。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=("full", "cancel", "failure"))
    parser.add_argument("theme", choices=("dark", "light", "partial", "unknown"))
    parser.add_argument("facts", type=Path)
    arguments = parser.parse_args()
    try:
        asyncio.run(_execute(arguments.scenario, arguments.theme, arguments.facts))
    except asyncio.CancelledError:
        return 23
    except RuntimeError as error:
        if str(error) == "injected color scenario failure":
            return 24
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
