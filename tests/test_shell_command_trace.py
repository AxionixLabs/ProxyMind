# -*- coding: utf-8 -*-

import io
import os
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rich.console import Console
from mind_app.runtime.calling import with_mcp_guard
from mind_app.stream_ui import StreamUI
from mind_nova.modes import DEFAULT_RUN_MODE
from mind_core.prompting import CommandAutoSuggest
from mind_app.stream_state.status import StatusState
from mind_app.stream_events.tool_traces.native import (
    MISSING,
    render_tool_result_preview,
    render_tool_trace,
)
from mind_app.stream_events.tool_traces.common import (
    COMMAND_FLAG_STYLE,
    COMMAND_HEAD_STYLE,
    COMMAND_NUMBER_STYLE,
    COMMAND_OPERATOR_STYLE,
    COMMAND_PATH_STYLE,
    COMMAND_STRING_STYLE,
    ERROR_PREVIEW_HEAD_STYLE,
    ERROR_PREVIEW_LINE_STYLE,
    ERROR_PREVIEW_MESSAGE_STYLE,
    ERROR_PREVIEW_TEXT_STYLE,
    PREVIEW_PATH_STYLE,
    PREVIEW_TEXT_STYLE,
)
from mind_app.stream_events.tool_traces.render import render_tool_trace_parts
from mind_app.stream_events.worked import worked_footer_text
from mind_app.stream_events.compact_rule import COMPACT_RULE_TERMINAL_MARGIN
from mind_app.stream_state.markdown import render_markdown
from mind_app.stream_state.text import TextState


class ShellCommandTraceTest(unittest.TestCase):

    def _quiet_stream_ui(self, stream_ui: StreamUI) -> StreamUI:
        async def noop(*_args, **_kwargs) -> None:
            return None

        stream_ui._print_direct = lambda _renderable: None
        stream_ui._print_raw = lambda _text: None
        stream_ui.coordinator.text_renderer.show = noop
        stream_ui.coordinator.text_renderer.suspend = noop
        stream_ui.coordinator.text_renderer.stop = noop
        return stream_ui

    def _final_text_lines(self, state: TextState) -> list[str]:
        console = Console(record=True, force_terminal=True, width=80, file=io.StringIO())
        console.print(state.final_renderable())
        return [line.rstrip() for line in console.export_text().splitlines()]

    def _markdown_lines(self, text: str, *, width: int = 80) -> list[str]:
        console = Console(record=True, force_terminal=True, width=width, file=io.StringIO())
        console.print(render_markdown(text))
        return [line.rstrip() for line in console.export_text().splitlines()]

    def _ghost_suggestion(self, text: str, mode: str = "xtra") -> str | None:
        class Buffer:
            complete_state = None

        class Document:
            def __init__(self, text_before_cursor: str) -> None:
                self.text_before_cursor = text_before_cursor

        suggest = CommandAutoSuggest()
        suggest.set_mode(mode)
        item = suggest.get_suggestion(Buffer(), Document(text))
        return item.text if item is not None else None

    def test_ghost_aliases_are_coding_agent_focused_across_modes(self) -> None:
        cases = {
            "review": " current changes and identify risks",
            "debug": " reproduce, inspect, patch, and verify",
            "patch": " 最小改动并验证",
            "verify": " 运行验证并汇总结论",
            "审查": "当前改动并指出风险",
            "修复": "问题并运行验证",
            "调用链": "跟踪并定位问题",
        }

        for mode in ("chat", "fast", "plan", "xtra"):
            for text, expected in cases.items():
                with self.subTest(mode=mode, text=text):
                    self.assertEqual(self._ghost_suggestion(text, mode=mode), expected)

    def test_intent_suggestions_prefer_coding_agent_tasks_across_modes(self) -> None:
        cases = {
            "查看": "当前改动",
            "分析": "失败原因",
            "定位": "问题根因",
            "运行": "相关测试",
            "跑": "相关测试",
            "fix": " the issue and run tests",
            "run": " relevant tests",
        }

        for mode in ("chat", "fast", "plan", "xtra"):
            for text, expected in cases.items():
                with self.subTest(mode=mode, text=text):
                    self.assertEqual(self._ghost_suggestion(text, mode=mode), expected)

    def test_mode_overlays_remain_available_after_shared_coding_agent_ghosts(self) -> None:
        self.assertEqual(self._ghost_suggestion("adb", mode="chat"), " 设备信息")
        self.assertEqual(self._ghost_suggestion("http", mode="fast"), " 接口")
        self.assertEqual(self._ghost_suggestion("循环", mode="plan"), "执行 3 次")
        self.assertEqual(self._ghost_suggestion("dbhub", mode="xtra"), " 查询数据库")

    def test_markdown_headings_are_left_aligned(self) -> None:
        console = Console(record=True, force_terminal=True, width=80, file=io.StringIO())
        console.print(render_markdown(
            "# 总数\n\n"
            "## 按模块分布\n\n"
            "| 模块 | 数量 |\n"
            "|---|---:|\n"
            "| backend/mcp_tools/coding | 4 |\n"
        ))
        lines = [line.rstrip() for line in console.export_text().splitlines() if line.strip()]

        self.assertEqual(lines[0], "总数")
        self.assertEqual(lines[1], "按模块分布")
        table_line = next(line for line in lines if "backend/mcp_tools/coding" in line)
        self.assertIn(" 4", table_line)
        self.assertNotIn("      4", table_line)

    def test_markdown_ordered_lists_include_period_marker(self) -> None:
        console = Console(record=True, force_terminal=True, width=80, file=io.StringIO())
        console.print(render_markdown(
            "1. 第一项\n"
            "2. 第二项\n"
        ))
        lines = [line.rstrip() for line in console.export_text().splitlines() if line.strip()]

        self.assertEqual(lines, [" 1. 第一项", " 2. 第二项"])

    def test_markdown_blockquote_preserves_source_line_breaks(self) -> None:
        console = Console(record=True, force_terminal=True, width=80, file=io.StringIO())
        console.print(render_markdown(
            "> 第一行引用。\n"
            "> 第二行引用。\n"
        ))
        lines = [line.rstrip() for line in console.export_text().splitlines() if line.strip()]

        self.assertEqual(lines, ["▌ 第一行引用。", "▌ 第二行引用。"])

    def test_markdown_mixed_reply_display_contract(self) -> None:
        lines = [
            line for line in self._markdown_lines(
                "# 标题\n\n"
                "段落包含 `code`、**bold** 和 [OpenAI](https://openai.com)。\n\n"
                "- 无序一\n"
                "- 无序二\n"
                "  - 子项\n\n"
                "1. 编号一\n"
                "2. 编号二\n\n"
                "> 引用一\n"
                "> 引用二\n\n"
                "```python\n"
                "print(\"hello\")\n"
                "```\n\n"
                "| 名称 | 状态 |\n"
                "|---|---:|\n"
                "| alpha | 1 |\n",
                width=80,
            )
            if line.strip()
        ]

        self.assertEqual(lines[0], "标题")
        self.assertIn("段落包含 code、bold 和 OpenAI (https://openai.com)。", lines)
        self.assertIn(" • 无序一", lines)
        self.assertIn(" • 无序二", lines)
        self.assertIn("    • 子项", lines)
        self.assertIn(" 1. 编号一", lines)
        self.assertIn(" 2. 编号二", lines)
        self.assertIn("▌ 引用一", lines)
        self.assertIn("▌ 引用二", lines)
        self.assertIn('print("hello")', lines)
        self.assertTrue(any("名称" in line and "状态" in line for line in lines))
        self.assertTrue(any("alpha" in line and "1" in line for line in lines))

    def test_markdown_narrow_width_wraps_without_losing_list_markers(self) -> None:
        lines = [
            line for line in self._markdown_lines(
                "1. 这是一个很长很长很长很长很长的编号列表项，需要观察换行缩进。\n"
                "2. 第二项\n\n"
                "- 这是一个很长很长很长很长很长的无序列表项，需要观察换行缩进。\n",
                width=34,
            )
            if line.strip()
        ]

        self.assertTrue(lines[0].startswith(" 1. "))
        self.assertTrue(lines[1].startswith("    "))
        self.assertTrue(any(line.startswith(" 2. ") for line in lines))
        bullet_index = next(index for index, line in enumerate(lines) if line.startswith(" • "))
        self.assertTrue(lines[bullet_index + 1].startswith("   "))

    def test_markdown_blockquote_keeps_blank_separated_blocks(self) -> None:
        lines = self._markdown_lines(
            "> 第一段\n"
            ">\n"
            "> 第二段\n\n"
            "普通段落",
            width=60,
        )
        visible = [line for line in lines if line.strip()]

        self.assertEqual(visible, ["▌ 第一段", "▌ 第二段", "普通段落"])

    def test_repl_prints_gap_before_model_turn_body(self) -> None:
        source = Path("mind_app/modes/repl.py").read_text(encoding="utf-8")
        branch = source.split("pref_config = await mind.fresh_pref_config(ttl_sec=0.0)", 1)[1]
        branch = branch.split("await run_model_turn(raw, mode, pref_config)", 1)[0]

        self.assertIn("print_turn_body_gap()", branch)

    def test_default_run_mode_is_xtra(self) -> None:
        repl_source = Path("mind_app/modes/repl.py").read_text(encoding="utf-8")
        calling_source = Path("mind_app/runtime/calling.py").read_text(encoding="utf-8")
        core_source = Path("mind_app/mind_core.py").read_text(encoding="utf-8")

        self.assertEqual(DEFAULT_RUN_MODE, "xtra")
        self.assertIn("mode: RunMode = DEFAULT_RUN_MODE", repl_source)
        self.assertIn("mode: RunMode = DEFAULT_RUN_MODE", calling_source)
        self.assertIn("mode: RunMode = DEFAULT_RUN_MODE", core_source)

    def test_with_mcp_guard_prints_worked_footer_after_cleanup(self) -> None:
        events: list[str] = []

        class FakeMind:
            async def start_anim(self, mode: str) -> None:
                events.append(f"start:{mode}")

            async def stop_anim(self) -> None:
                events.append("stop")

            async def await_cleanup(self, awaitable) -> None:
                events.append("cleanup-before")
                await awaitable
                events.append("cleanup-after")

        async def runner(**_kwargs) -> None:
            events.append("runner")

        with patch("mind_app.runtime.calling.print_worked_footer") as print_footer:
            asyncio.run(with_mcp_guard(FakeMind(), runner))

        self.assertEqual(
            events,
            [f"start:{DEFAULT_RUN_MODE}", "runner", "cleanup-before", "stop", "cleanup-after"],
        )
        print_footer.assert_called_once()
        self.assertGreaterEqual(print_footer.call_args.args[0], 0.0)

    def test_wait_status_accepts_separate_animation_delay(self) -> None:
        source = Path("mind_app/stream_ui.py").read_text(encoding="utf-8")
        method = source.split("async def begin_reply_wait_status(", 1)[1]
        method = method.split("async def begin_heal_status(", 1)[0]

        self.assertIn("animate_after_sec: float | None = None", method)
        self.assertIn("animate_after = delay_sec if animate_after_sec is None", method)
        self.assertIn("animate_after_sec=animate_after", method)

    def test_tool_status_starts_animated_after_delay(self) -> None:
        source = Path("mind_app/stream_ui.py").read_text(encoding="utf-8")
        method = source.split("async def begin_custom_tool_status(", 1)[1]
        method = method.split("async def begin_code_status(", 1)[0]

        self.assertIn("show_delay_sec=0.18", method)
        self.assertIn("animate_after_sec=0.18", method)
        self.assertIn("family=\"tool\"", method)
        self.assertIn("initial_animated=True", method)

    def test_tool_status_first_visible_update_is_animated(self) -> None:
        async def run_case() -> list[tuple[str, str, bool]]:
            stream_ui = StreamUI("unused.log")
            calls: list[tuple[str, str, bool]] = []

            async def fake_set_status(
                text: str,
                *,
                family: str,
                animated: bool,
                reset_phase_on_text_change: bool = True,
            ) -> None:
                _ = reset_phase_on_text_change
                calls.append((text, family, animated))

            stream_ui.coordinator.set_status = fake_set_status
            stream_ui.coordinator.hold_status_slot = lambda: None

            await stream_ui.begin_tool_status()
            await asyncio.sleep(0.24)
            await stream_ui.stop(blink=False)
            return calls

        self.assertEqual(
            asyncio.run(run_case()),
            [("function calling", "tool", True)],
        )

    def test_status_static_and_animated_share_family_layout(self) -> None:
        cases = [
            (StatusState.FAMILY_BUILTIN, "listing files"),
            (StatusState.FAMILY_TOOL, "function calling"),
            (StatusState.FAMILY_CODE, "native coding"),
            (StatusState.FAMILY_MODE, "chat mode"),
            (StatusState.FAMILY_LOOP, "loop steps"),
            (StatusState.FAMILY_HEAL, "restoring signal"),
            (StatusState.FAMILY_WAIT, "thinking"),
        ]

        for family, text in cases:
            with self.subTest(family=family):
                state = StatusState()
                state.set_status(text, family=family, animated=False)
                static = state.renderable().plain

                state.set_status(text, family=family, animated=True)
                animated = state.renderable().plain

                self.assertEqual(len(static), len(animated))
                self.assertEqual(static[:2], animated[:2])
                self.assertIn(text.split()[0], static)
                self.assertIn(text.split()[0], animated)

    def test_stream_boundary_uses_markdown_paragraph_in_raw_text(self) -> None:
        state = TextState()

        state.append("第一段", display=TextState.STREAM)
        state.append(
            "\n第二段",
            display=TextState.STREAM,
            display_chunk="\n第二段",
            raw_chunk="\n\n第二段",
        )

        self.assertEqual(state.display_text, "第一段\n第二段")
        self.assertEqual(state.raw_text, "第一段\n\n第二段")

    def test_stream_boundary_prefix_applies_to_next_stream_text(self) -> None:
        stream_ui = StreamUI("unused.log")
        stream_ui.coordinator.text_state.append("第一段", display=TextState.STREAM)
        stream_ui.mark_stream_boundary()

        prefix = stream_ui._consume_stream_boundary_prefix(incoming_text="第二段")

        self.assertEqual(prefix, "\n")

    def test_stream_boundary_prefix_applies_to_next_block_text(self) -> None:
        stream_ui = StreamUI("unused.log")
        stream_ui.coordinator.text_state.append("第一段", display=TextState.STREAM)
        stream_ui.mark_stream_boundary()

        prefix = stream_ui._consume_stream_boundary_prefix(incoming_text="工具结果")

        self.assertEqual(prefix, "\n")

    def test_stream_boundary_prefix_is_skipped_for_prefixed_text(self) -> None:
        stream_ui = StreamUI("unused.log")
        stream_ui.coordinator.text_state.append("第一段", display=TextState.STREAM)
        stream_ui.mark_stream_boundary()

        prefix = stream_ui._consume_stream_boundary_prefix(incoming_text="\n第二段")

        self.assertEqual(prefix, "")

    def test_stream_boundary_prefix_is_recorded_before_direct_block(self) -> None:
        async def run_case() -> str:
            with tempfile.TemporaryDirectory() as tmpdir:
                log_file = os.path.join(tmpdir, "stream.log")
                stream_ui = self._quiet_stream_ui(StreamUI(log_file))
                await stream_ui.open()
                stream_ui.coordinator.text_state.append("第一段", display=TextState.STREAM)
                stream_ui.mark_stream_boundary()
                await stream_ui.print_block("工具结果\n")
                await stream_ui.stop(blink=False)
                with open(log_file, "r", encoding="utf-8") as fp:
                    return fp.read()

        self.assertEqual(asyncio.run(run_case()), "\n工具结果\n")

    def test_direct_block_adds_spacing_before_next_block(self) -> None:
        async def run_case() -> tuple[str, str, str]:
            with tempfile.TemporaryDirectory() as tmpdir:
                log_file = os.path.join(tmpdir, "stream.log")
                stream_ui = self._quiet_stream_ui(StreamUI(log_file))
                await stream_ui.open()
                await stream_ui.print_block("审批通过")
                after_direct_display = stream_ui.coordinator.text_state.display_text
                await stream_ui.feed("• Ran command", display=StreamUI.BLOCK)
                after_feed_display = stream_ui.coordinator.text_state.display_text
                await stream_ui.stop(blink=False)
                with open(log_file, "r", encoding="utf-8") as fp:
                    return after_direct_display, after_feed_display, fp.read()

        after_direct_display, after_feed_display, record_text = asyncio.run(run_case())

        self.assertEqual(after_direct_display, "")
        self.assertEqual(after_feed_display, "\n• Ran command\n")
        self.assertEqual(record_text, "审批通过\n\n• Ran command\n")

    def test_direct_block_preserves_gap_before_final_stream_renderable(self) -> None:
        async def run_case() -> list[str]:
            with tempfile.TemporaryDirectory() as tmpdir:
                log_file = os.path.join(tmpdir, "stream.log")
                stream_ui = self._quiet_stream_ui(StreamUI(log_file))
                await stream_ui.open()
                await stream_ui.print_block("• You denied command")
                await stream_ui.feed("本地命令未执行成功。", display=StreamUI.STREAM)
                lines = self._final_text_lines(stream_ui.coordinator.text_state)
                await stream_ui.stop(blink=False)
                return lines

        self.assertEqual(asyncio.run(run_case()), ["", "本地命令未执行成功。"])

    def test_external_boundary_with_two_newlines_does_not_add_extra_final_gap(self) -> None:
        state = TextState()

        state.remember_external_output(display=TextState.BLOCK, text="审批拒绝\n\n")
        state.append("本地命令未执行成功。", display=TextState.STREAM)

        self.assertEqual(self._final_text_lines(state), ["本地命令未执行成功。"])

    def test_clear_resets_external_boundary(self) -> None:
        state = TextState()

        state.remember_external_output(display=TextState.BLOCK, text="审批拒绝\n")
        state.clear()
        state.append("重新开始", display=TextState.STREAM)

        self.assertEqual(state.display_text, "重新开始")
        self.assertEqual(self._final_text_lines(state), ["重新开始"])

    def test_external_boundary_preserves_display_gap_before_block_and_stream(self) -> None:
        state = TextState()

        state.remember_external_output(display=TextState.BLOCK, text="审批拒绝\n")
        state.append("• Ran command", display=TextState.BLOCK)
        state.append("继续说明。", display=TextState.STREAM)

        self.assertEqual(state.display_text, "\n• Ran command\n\n继续说明。")
        self.assertEqual(
            self._final_text_lines(state),
            ["", "• Ran command", "", "继续说明。"],
        )

    def test_stream_boundary_prefix_applies_to_feed_block(self) -> None:
        async def run_case() -> tuple[str, str]:
            with tempfile.TemporaryDirectory() as tmpdir:
                log_file = os.path.join(tmpdir, "stream.log")
                stream_ui = self._quiet_stream_ui(StreamUI(log_file))
                await stream_ui.open()
                await stream_ui.feed("第一段", display=StreamUI.STREAM)
                stream_ui.mark_stream_boundary()
                await stream_ui.feed("工具结果\n", display=StreamUI.BLOCK)
                display_text = stream_ui.coordinator.text_state.display_text
                await stream_ui.stop(blink=False)
                with open(log_file, "r", encoding="utf-8") as fp:
                    return display_text, fp.read()

        display_text, record_text = asyncio.run(run_case())

        self.assertEqual(display_text, "第一段\n\n工具结果\n")
        self.assertEqual(record_text, "第一段\n\n工具结果\n")

    def test_final_renderable_preserves_gap_between_tool_result_and_reply(self) -> None:
        state = TextState()
        state.append(
            "Ran command\nok",
            display=TextState.BLOCK,
            display_parts=[
                {"text": "Ran command", "style": "bold"},
                {"text": "\n", "style": None},
                {"text": "└ ok", "style": "dim"},
            ],
        )
        state.append("已在本地执行，输出是 ok。", display=TextState.STREAM)

        text = "\n".join(self._final_text_lines(state))

        self.assertIn("└ ok\n\n已在本地执行，输出是 ok。", text)

    def test_final_renderable_does_not_expand_gaps_between_reply_and_tools(self) -> None:
        state = TextState()
        state.append("assistant one", display=TextState.STREAM)
        state.append(
            "Tool refresh\nttl_sec=1",
            display=TextState.BLOCK,
            display_parts=[
                {"text": "• Tool refresh", "style": "bold"},
                {"text": "\n└ ttl_sec=1", "style": "dim"},
            ],
        )
        state.append(
            "tool result\nok",
            display=TextState.BLOCK,
            display_parts=[
                {"text": "└ tool result", "style": "dim"},
                {"text": "\n  ok", "style": "dim"},
            ],
        )
        state.append("assistant two", display=TextState.STREAM)

        lines = self._final_text_lines(state)
        text = "\n".join(lines)

        self.assertEqual(lines[:3], ["assistant one", "", "• Tool refresh"])
        self.assertIn("└ tool result\n  ok\n\nassistant two", text)
        self.assertNotIn("\n\n\n• Tool refresh", text)
        self.assertNotIn("ok\n\n\nassistant two", text)

    def test_final_renderable_matches_display_boundaries_for_mixed_segments(self) -> None:
        state = TextState()
        state.append("one", display=TextState.STREAM)
        state.append(
            "tool start\nargs",
            display=TextState.BLOCK,
            display_parts=[
                {"text": "• Tool start", "style": "bold"},
                {"text": "\n└ args", "style": "dim"},
            ],
        )
        state.append(
            "tool result\nok",
            display=TextState.BLOCK,
            display_parts=[
                {"text": "└ result", "style": "dim"},
                {"text": "\n  ok", "style": "dim"},
            ],
        )
        state.append("two", display=TextState.STREAM)

        display_lines = state.display_text.splitlines()
        final_lines = self._final_text_lines(state)

        self.assertEqual(final_lines, display_lines)

    def test_pure_stream_final_renderable_keeps_markdown_paragraphs(self) -> None:
        state = TextState()
        state.append("第一段", display=TextState.STREAM)
        state.append(
            "\n第二段",
            display=TextState.STREAM,
            display_chunk="\n第二段",
            raw_chunk="\n\n第二段",
        )

        lines = self._final_text_lines(state)

        self.assertEqual(lines, ["第一段", "", "第二段"])
        self.assertEqual(state.raw_text, "第一段\n\n第二段")

    def test_prepare_external_output_flushes_text_done_boundary(self) -> None:
        async def run_case() -> str:
            with tempfile.TemporaryDirectory() as tmpdir:
                log_file = os.path.join(tmpdir, "stream.log")
                stream_ui = self._quiet_stream_ui(StreamUI(log_file))
                await stream_ui.open()
                await stream_ui.feed("第一段", display=StreamUI.STREAM)
                stream_ui.mark_stream_boundary()
                await stream_ui.prepare_external_output()
                await stream_ui.stop(blink=False)
                with open(log_file, "r", encoding="utf-8") as fp:
                    return fp.read()

        self.assertEqual(asyncio.run(run_case()), "第一段\n")

    def test_failed_shell_command_title_has_no_failed_suffix(self) -> None:
        args = {
            "command": 'Get-Content -LiteralPath "build.py" | Select-Object -Skip 200 -First 240'
        }
        data = {
            "ok": False,
            "stdout": "",
            "stderr": "",
        }

        title = render_tool_trace("shell_command", args, ok=False, data=data)

        self.assertEqual(title, f"• Ran {args['command']}")
        self.assertNotIn(" failed", title)

    def test_ran_command_title_uses_layered_command_styles(self) -> None:
        title = '• Ran Get-Content -LiteralPath "services\\domain\\mind\\api\\plan.py" -TotalCount 155'

        parts = render_tool_trace_parts(title)
        text = "".join(str(part.get("text") or "") for part in parts)
        styles = {
            str(part.get("text") or ""): part.get("style")
            for part in parts
            if str(part.get("text") or "").strip()
        }

        self.assertEqual(text, title)
        self.assertEqual(styles["Get-Content"], COMMAND_HEAD_STYLE)
        self.assertEqual(styles["-LiteralPath"], COMMAND_FLAG_STYLE)
        self.assertEqual(styles['"services\\domain\\mind\\api\\plan.py"'], COMMAND_PATH_STYLE)
        self.assertEqual(styles["-TotalCount"], COMMAND_FLAG_STYLE)
        self.assertEqual(styles["155"], COMMAND_NUMBER_STYLE)

    def test_ran_command_title_styles_strings_and_operators(self) -> None:
        title = '• Ran rg -n "shell_calls" . | Select-Object -First 20'

        parts = render_tool_trace_parts(title)
        text = "".join(str(part.get("text") or "") for part in parts)
        styles = {
            str(part.get("text") or ""): part.get("style")
            for part in parts
            if str(part.get("text") or "").strip()
        }

        self.assertEqual(text, title)
        self.assertEqual(styles["rg"], COMMAND_HEAD_STYLE)
        self.assertEqual(styles["-n"], COMMAND_FLAG_STYLE)
        self.assertEqual(styles['"shell_calls"'], COMMAND_STRING_STYLE)
        self.assertEqual(styles["."], COMMAND_PATH_STYLE)
        self.assertEqual(styles["|"], COMMAND_OPERATOR_STYLE)
        self.assertEqual(styles["Select-Object"], COMMAND_HEAD_STYLE)

    def test_shell_command_empty_output_preview(self) -> None:
        preview = render_tool_result_preview(
            "shell_command",
            {"ok": True, "stdout": "", "stderr": ""},
            arguments={"command": "echo"},
        )

        self.assertEqual(preview.full, "(no output)")

    def test_failed_shell_command_empty_output_preview_shows_failure_summary(self) -> None:
        preview = render_tool_result_preview(
            "shell_command",
            {
                "ok": False,
                "exit_code": 1,
                "stdout": "",
                "stderr": "",
            },
            arguments={"command": "blocked"},
        )

        self.assertEqual(preview.full, "Command failed with exit code 1 (stderr empty)")

    def test_failed_shell_command_empty_output_preview_includes_runtime(self) -> None:
        preview = render_tool_result_preview(
            "shell_command",
            {
                "ok": False,
                "exit_code": 1,
                "stdout": "",
                "stderr": "",
                "runtime": {
                    "name": "pwsh",
                    "prefix": ["pwsh", "-NoProfile", "-Command"],
                },
            },
            arguments={"command": "echo foo &&"},
        )

        self.assertEqual(preview.full, "Command failed with exit code 1 (runtime: pwsh, stderr empty)")
        self.assertNotIn("Line |", preview.full)

    def test_failed_shell_command_stderr_preview_omits_exit_code(self) -> None:
        stderr = (
            "rg: regex parse error:\n"
            "    (?:name=\\)\n"
            "    ^\n"
            "error: unclosed group"
        )
        preview = render_tool_result_preview(
            "shell_command",
            {
                "ok": False,
                "exit_code": 2,
                "stdout": "",
                "stderr": stderr,
            },
            arguments={"command": "rg bad"},
        )

        self.assertIn("rg: regex parse error:", preview.full)
        self.assertIn("error: unclosed group", preview.full)
        self.assertNotIn("exit_code", preview.full)
        self.assertNotIn("rg bad", preview.full)

    def test_shell_command_tool_error_styles_header_marker_and_message(self) -> None:
        stderr = (
            "rg: regex parse error:\n"
            "    (?:[abc)\n"
            "       ^\n"
            "error: unclosed character class"
        )
        preview = render_tool_result_preview(
            "shell_command",
            {"ok": False, "stdout": "", "stderr": stderr},
            arguments={"command": 'rg -n "[abc" .'},
        )
        parts = render_tool_trace_parts('• Ran rg -n "[abc" .', preview=preview, ok=False)
        styles = {
            str(part.get("text") or ""): part.get("style")
            for part in parts
            if str(part.get("text") or "").strip()
        }

        self.assertEqual(styles["rg: regex parse error:"], ERROR_PREVIEW_HEAD_STYLE)
        self.assertEqual(styles["^"], ERROR_PREVIEW_MESSAGE_STYLE)
        self.assertEqual(styles["error: unclosed character class"], ERROR_PREVIEW_TEXT_STYLE)

    def test_shell_command_error_styles_cover_common_windows_macos_linux_shapes(self) -> None:
        cases = [
            (
                "powershell_line",
                "python - <<'PY'",
                (
                    "ParserError:\n"
                    "Line |\n"
                    "   2 |  python - <<'PY'\n"
                    "     |            ~\n"
                    "     | Missing file specification after redirection operator."
                ),
                {
                    "ParserError:": ERROR_PREVIEW_HEAD_STYLE,
                    "Line |": ERROR_PREVIEW_LINE_STYLE,
                    "     |": ERROR_PREVIEW_LINE_STYLE,
                    "            ~": ERROR_PREVIEW_MESSAGE_STYLE,
                },
            ),
            (
                "powershell_at_line",
                "echo foo &&",
                (
                    "At line:1 char:10\n"
                    "+ echo foo &&\n"
                    "+          ~~\n"
                    "The token '&&' is not a valid statement separator in this version."
                ),
                {
                    "At line:1 char:10": ERROR_PREVIEW_LINE_STYLE,
                    "+ echo foo &&": ERROR_PREVIEW_LINE_STYLE,
                    "~~": ERROR_PREVIEW_MESSAGE_STYLE,
                    "The token '&&' is not a valid statement separator in this version.": ERROR_PREVIEW_TEXT_STYLE,
                },
            ),
            (
                "cmd_error",
                "type missing.py",
                "The system cannot find the file specified.",
                {
                    "The system cannot find the file specified.": ERROR_PREVIEW_TEXT_STYLE,
                },
            ),
            (
                "ripgrep_error",
                'rg -n "[abc" .',
                (
                    "rg: regex parse error:\n"
                    "    (?:[abc)\n"
                    "       ^\n"
                    "error: unclosed character class"
                ),
                {
                    "rg: regex parse error:": ERROR_PREVIEW_HEAD_STYLE,
                    "^": ERROR_PREVIEW_MESSAGE_STYLE,
                    "error: unclosed character class": ERROR_PREVIEW_TEXT_STYLE,
                },
            ),
            (
                "python_traceback",
                "python -c \"raise ValueError('bad value')\"",
                (
                    "Traceback (most recent call last):\n"
                    "  File \"<string>\", line 1, in <module>\n"
                    "ValueError: bad value"
                ),
                {
                    "Traceback (most recent call last):": ERROR_PREVIEW_HEAD_STYLE,
                    "  File \"<string>\", line 1, in <module>": ERROR_PREVIEW_LINE_STYLE,
                    "ValueError:": ERROR_PREVIEW_HEAD_STYLE,
                    " bad value": ERROR_PREVIEW_TEXT_STYLE,
                },
            ),
            (
                "posix_shell",
                "if true; then echo ok",
                "zsh: parse error near `fi'",
                {
                    "zsh: parse error near `fi'": ERROR_PREVIEW_HEAD_STYLE,
                },
            ),
            (
                "bash_not_found",
                "missing-command",
                "bash: missing-command: command not found",
                {
                    "bash: missing-command: command not found": ERROR_PREVIEW_TEXT_STYLE,
                },
            ),
        ]

        for name, command, stderr, expected_styles in cases:
            with self.subTest(name=name):
                preview = render_tool_result_preview(
                    "shell_command",
                    {
                        "ok": False,
                        "stdout": "",
                        "stderr": stderr,
                        "command": command,
                    },
                    arguments={"command": command},
                )
                parts = render_tool_trace_parts(
                    f"• Ran {command}",
                    preview=preview,
                    ok=False,
                )
                styles = {
                    str(part.get("text") or ""): part.get("style")
                    for part in parts
                    if str(part.get("text") or "").strip()
                }

                for text, style in expected_styles.items():
                    self.assertEqual(styles[text], style)

    def test_shell_command_empty_and_generic_error_styles_are_error_text(self) -> None:
        command = "echo foo &&"
        preview = render_tool_result_preview(
            "shell_command",
            {
                "ok": False,
                "exit_code": 1,
                "stdout": "",
                "stderr": "",
                "runtime": {"name": "pwsh"},
            },
            arguments={"command": command},
        )
        parts = render_tool_trace_parts(
            f"• Ran {command}",
            preview=preview,
            ok=False,
        )
        styles = {
            str(part.get("text") or ""): part.get("style")
            for part in parts
            if str(part.get("text") or "").strip()
        }

        self.assertEqual(preview.full, "Command failed with exit code 1 (runtime: pwsh, stderr empty)")
        self.assertEqual(styles["Command failed with exit code 1 (runtime: pwsh, stderr empty)"], ERROR_PREVIEW_TEXT_STYLE)

        command = "custom tool"
        preview = render_tool_result_preview(
            "shell_command",
            {
                "ok": False,
                "stdout": "",
                "stderr": "\n".join(f"plain failure line {index}" for index in range(10)),
                "command": command,
            },
            arguments={"command": command},
        )
        parts = render_tool_trace_parts(
            f"• Ran {command}",
            preview=preview,
            ok=False,
        )
        styles = {
            str(part.get("text") or ""): part.get("style")
            for part in parts
            if str(part.get("text") or "").strip()
        }

        self.assertIn("plain failure line 9", preview.full)
        self.assertIn("plain failure line 7", preview.screen)
        self.assertEqual(styles["plain failure line 7"], ERROR_PREVIEW_TEXT_STYLE)

    def test_shell_command_parser_error_preview_keeps_structure(self) -> None:
        stderr = (
            "ParserError:\n"
            "Line |\n"
            "   2 |  rg -n \"return f\\\".*failed|return \\\".*failed|failed\\\"\" mind_app\\stream бн\n"
            "     |                    ~\n"
            "     | Missing property name after reference operator."
        )
        preview = render_tool_result_preview(
            "shell_command",
            {
                "ok": False,
                "stdout": "",
                "stderr": stderr,
            },
            arguments={"command": "rg bad quoting"},
        )

        self.assertIn("ParserError:", preview.full)
        self.assertIn("Line |", preview.full)
        self.assertNotIn("rg bad quoting", preview.full)

    def test_shell_command_long_powershell_parser_error_keeps_diagnostic_block(self) -> None:
        stderr = (
            "debug prelude 1\n"
            "debug prelude 2\n"
            "ParserError:\n"
            "Line |\n"
            "   2 |  python - <<'PY'\n"
            "     |            ~\n"
            "     | Missing file specification after redirection operator.\n"
            "CategoryInfo          : ParserError: (:) [], ParentContainsErrorRecordException\n"
            "FullyQualifiedErrorId : MissingFileSpecification"
        )
        preview = render_tool_result_preview(
            "shell_command",
            {
                "ok": False,
                "stdout": "",
                "stderr": stderr,
            },
            arguments={"command": "python - <<'PY'"},
        )

        self.assertIn("ParserError:", preview.full)
        self.assertIn("Line |", preview.full)
        self.assertIn("python - <<'PY'", preview.full)
        self.assertIn("Missing file specification", preview.full)
        self.assertNotIn("debug prelude", preview.full)
        self.assertNotIn("CategoryInfo", preview.full)

    def test_shell_command_powershell_parser_error_styles_diagnostic_parts(self) -> None:
        stderr = (
            "ParserError:\n"
            "Line |\n"
            "   2 |  python - <<'PY'\n"
            "     |            ~\n"
            "     | Missing file specification after redirection operator."
        )
        preview = render_tool_result_preview(
            "shell_command",
            {"ok": False, "stdout": "", "stderr": stderr},
            arguments={"command": "python - <<'PY'"},
        )
        parts = render_tool_trace_parts("• Ran python - <<'PY'", preview=preview, ok=False)
        styles = {
            str(part.get("text") or ""): part.get("style")
            for part in parts
            if str(part.get("text") or "").strip()
        }

        self.assertEqual(styles["ParserError:"], ERROR_PREVIEW_HEAD_STYLE)
        self.assertEqual(styles["Line |"], ERROR_PREVIEW_LINE_STYLE)
        self.assertEqual(styles["     |"], ERROR_PREVIEW_LINE_STYLE)
        self.assertEqual(styles["            ~"], ERROR_PREVIEW_MESSAGE_STYLE)

    def test_shell_command_powershell_at_line_error_keeps_block_head(self) -> None:
        stderr = (
            "noise before\n"
            "At line:1 char:10\n"
            "+ echo foo &&\n"
            "+          ~~\n"
            "The token '&&' is not a valid statement separator in this version.\n"
            "noise after"
        )
        preview = render_tool_result_preview(
            "shell_command",
            {"ok": False, "stdout": "", "stderr": stderr},
            arguments={"command": "echo foo &&"},
        )

        self.assertIn("At line:1 char:10", preview.full)
        self.assertIn("+ echo foo &&", preview.full)
        self.assertIn("not a valid statement separator", preview.full)
        self.assertNotIn("noise before", preview.full)

    def test_shell_command_posix_shell_error_keeps_syntax_line(self) -> None:
        stderr = (
            "setup noise\n"
            "zsh: parse error near `fi'\n"
            "cleanup noise"
        )
        preview = render_tool_result_preview(
            "shell_command",
            {"ok": False, "stdout": "", "stderr": stderr},
            arguments={"command": "if true; then echo ok"},
        )

        self.assertEqual(preview.full, "zsh: parse error near `fi'\ncleanup noise")

    def test_shell_command_long_python_traceback_keeps_head_and_exception(self) -> None:
        stderr = "\n".join([
            "debug before",
            "Traceback (most recent call last):",
            '  File "a.py", line 1, in <module>',
            "    main()",
            '  File "b.py", line 2, in main',
            "    step()",
            '  File "c.py", line 3, in step',
            "    fail()",
            "ValueError: bad value",
        ])
        preview = render_tool_result_preview(
            "shell_command",
            {"ok": False, "stdout": "", "stderr": stderr},
            arguments={"command": "python a.py"},
        )

        self.assertIn("Traceback (most recent call last):", preview.full)
        self.assertIn("… +", preview.full)
        self.assertIn("ValueError: bad value", preview.full)
        self.assertNotIn("debug before", preview.full)

    def test_shell_command_generic_long_error_keeps_tail_when_no_diagnostic_block(self) -> None:
        stderr = "\n".join(f"plain failure line {index}" for index in range(10))
        preview = render_tool_result_preview(
            "shell_command",
            {"ok": False, "stdout": "", "stderr": stderr},
            arguments={"command": "custom tool"},
        )

        self.assertIn("… +4 lines (ctrl + t to view transcript)", preview.full)
        self.assertIn("plain failure line 9", preview.full)
        self.assertNotIn("plain failure line 0", preview.full)

    def test_failed_shell_error_structures_are_extractable(self) -> None:
        cases = [
            (
                "powershell_multiline",
                "Get-Content -LiteralPath missing.py",
                (
                    "Get-Content:\n"
                    "Line |\n"
                    "   2 |  Get-Content -LiteralPath missing.py\n"
                    "     |  ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\n"
                    "     | Cannot find path 'missing.py' because it does not exist."
                ),
                [
                    "Get-Content:",
                    "Line |",
                    "|  Get-Content -LiteralPath missing.py",
                    "     |  ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~",
                ],
            ),
            (
                "cmd_error",
                "type missing.py",
                "The system cannot find the file specified.",
                [
                    "The system cannot find the file specified.",
                ],
            ),
            (
                "ripgrep_error",
                'rg -n "(" .',
                (
                    "rg: regex parse error:\n"
                    "    (\n"
                    "    ^\n"
                    "error: unclosed group"
                ),
                [
                    "rg: regex parse error:",
                    "error: unclosed group",
                ],
            ),
            (
                "python_traceback",
                "python missing.py",
                (
                    "Traceback (most recent call last):\n"
                    "  File \"missing.py\", line 1, in <module>\n"
                    "FileNotFoundError: [Errno 2] No such file or directory: 'missing.py'"
                ),
                [
                    "Traceback (most recent call last):",
                    "  File \"missing.py\", line 1, in <module>",
                    "FileNotFoundError: [Errno 2] No such file or directory: 'missing.py'",
                ],
            ),
        ]

        for name, command, stderr, expected_fragments in cases:
            with self.subTest(name=name):
                preview = render_tool_result_preview(
                    "shell_command",
                    {
                        "ok": False,
                        "stdout": "",
                        "stderr": stderr,
                        "command": command,
                    },
                    arguments={"command": command},
                )
                parts = render_tool_trace_parts(
                    f"• Ran {command}",
                    preview=preview,
                    ok=False,
                )
                text = "".join(str(part.get("text") or "") for part in parts)

                for fragment in expected_fragments:
                    self.assertIn(fragment, text)

    def test_shell_batch_failure_summaries_reuse_common_error_diagnostics(self) -> None:
        cases = [
            (
                "powershell_line",
                "python - <<'PY'",
                (
                    "ParserError:\n"
                    "Line |\n"
                    "   2 |  python - <<'PY'\n"
                    "     |            ~\n"
                    "     | Missing file specification after redirection operator."
                ),
                "ParserError: Missing file specification after redirection operator.",
            ),
            (
                "powershell_at_line",
                "echo foo &&",
                (
                    "At line:1 char:10\n"
                    "+ echo foo &&\n"
                    "+          ~~\n"
                    "The token '&&' is not a valid statement separator in this version."
                ),
                "At line:1 char:10: The token '&&' is not a valid statement separator in this version.",
            ),
            (
                "ripgrep_error",
                'rg -n "[abc" .',
                (
                    "rg: regex parse error:\n"
                    "    (?:[abc)\n"
                    "       ^\n"
                    "error: unclosed character class"
                ),
                "rg: regex parse error: error: unclosed character class",
            ),
            (
                "python_traceback",
                "python -c \"raise ValueError('bad value')\"",
                (
                    "Traceback (most recent call last):\n"
                    "  File \"<string>\", line 1, in <module>\n"
                    "ValueError: bad value"
                ),
                "ValueError: bad value",
            ),
            (
                "posix_shell",
                "if true; then echo ok",
                "zsh: parse error near `fi'",
                "zsh: parse error near `fi'",
            ),
        ]

        for name, command, stderr, expected in cases:
            with self.subTest(name=name):
                preview = render_tool_result_preview(
                    "shell_command",
                    {
                        "ok": False,
                        "results": [
                            {
                                "tool": "shell_command",
                                "ok": False,
                                "args": {"command": command},
                                "result": {
                                    "data": {
                                        "ok": False,
                                        "command": command,
                                        "stdout": "",
                                        "stderr": stderr,
                                    }
                                },
                            },
                            {
                                "tool": "shell_command",
                                "ok": True,
                                "args": {"command": "echo ok"},
                                "result": {
                                    "data": {
                                        "ok": True,
                                        "command": "echo ok",
                                        "stdout": "ok",
                                        "stderr": "",
                                    }
                                },
                            },
                        ],
                    },
                    arguments={"items": [{"command": command}]},
                )

                self.assertEqual(preview.kind, "tree")
                self.assertIn(expected, preview.full)
                self.assertIn(expected, preview.screen)

    def test_multiline_shell_command_trace_preview_omits_command_block(self) -> None:
        command = (
            'Write-Host "start"\n'
            'Write-Host "Current directory:"\n'
            'Get-Location'
        )

        title = render_tool_trace(
            "shell_command",
            {"command": command},
            ok=True,
            data={"ok": True, "command": command},
        )
        preview = render_tool_result_preview(
            "shell_command",
            {"ok": True, "command": command, "stdout": "start\nok"},
            arguments={"command": command},
        )

        self.assertEqual(title, '• Ran Write-Host "start"')
        self.assertEqual(preview.full, "start\nok")
        self.assertNotIn('Write-Host "Current directory:"', preview.full)
        self.assertNotIn("Get-Location", preview.full)
        self.assertNotIn("inline.txt", preview.full)

    def test_shell_stdout_preview_does_not_promote_path_like_text(self) -> None:
        stdout = "path_like_text_/Users/demo/project_segment_001"
        preview = render_tool_result_preview(
            "shell_command",
            {"ok": True, "stdout": stdout, "stderr": ""},
            arguments={"command": "echo"},
        )
        parts = render_tool_trace_parts("• Ran echo", preview=preview, ok=True)
        styles = {
            str(part.get("text") or ""): part.get("style")
            for part in parts
            if str(part.get("text") or "").strip()
        }

        self.assertEqual(preview.kind, "plain")
        self.assertEqual(styles[stdout], PREVIEW_TEXT_STYLE)
        self.assertNotEqual(styles[stdout], PREVIEW_PATH_STYLE)

    def test_write_file_title_uses_added_or_edited(self) -> None:
        args = {"path": "demo.md", "content": "hello\nworld\n"}
        data = {"ok": True, "path": "demo.md", "bytes": 12}

        added = render_tool_trace(
            "workspace_write_file",
            args,
            ok=True,
            data=data,
            before_exists=False,
        )
        edited = render_tool_trace(
            "workspace_write_file",
            args,
            ok=True,
            data=data,
            before_exists=True,
        )

        self.assertEqual(added, "• Added demo.md (+2 -0)")
        self.assertEqual(edited, "• Edited demo.md (+2 -0)")

    def test_write_file_content_preview_uses_file_tree(self) -> None:
        args = {
            "path": "tmp/patch_preview/prompt_chat.py",
            "content": (
                "# line 1\n"
                "# line 2\n"
                "# line 3\n"
                "from .markdown import load_prompt_md\n"
                "# line 5\n"
            ),
        }
        data = {"ok": True, "path": args["path"], "bytes": len(args["content"])}

        preview = render_tool_result_preview(
            "workspace_write_file",
            data,
            arguments=args,
        )
        text = "".join(
            part["text"]
            for part in render_tool_trace_parts(
                "• Added tmp/patch_preview/prompt_chat.py (+5 -0)",
                preview=preview,
                ok=True,
            )
        )

        self.assertEqual(preview.kind, "file_tree")
        self.assertIn("└─ tmp/patch_preview/prompt_chat.py", text)
        self.assertIn("   1 +# line 1", text)
        self.assertIn("   4 +from .markdown import load_prompt_md", text)
        self.assertNotIn("└    1", text)
        self.assertNotIn("└ └─ tmp/patch_preview/prompt_chat.py", text)

    def test_unified_patch_title_uses_file_actions(self) -> None:
        cases = [
            ("create", "• Added demo.md (+1 -0)"),
            ("modify", "• Edited demo.md (+1 -1)"),
            ("delete", "• Deleted demo.md (+0 -1)"),
        ]

        for action, expected in cases:
            with self.subTest(action=action):
                title = render_tool_trace(
                    "workspace_apply_unified_patch",
                    {},
                    ok=True,
                    data={
                        "ok": True,
                        "files": [{"path": "demo.md", "action": action}],
                        "added_lines": 1 if action != "delete" else 0,
                        "removed_lines": 1 if action != "create" else 0,
                    },
                    before_exists=MISSING,
                )

                self.assertEqual(title, expected)

    def test_unified_patch_multi_file_title_stays_edited(self) -> None:
        title = render_tool_trace(
            "workspace_apply_unified_patch",
            {},
            ok=True,
            data={
                "ok": True,
                "files": [
                    {"path": "a.txt", "action": "create"},
                    {"path": "b.txt", "action": "delete"},
                    {"path": "c.txt", "action": "modify"},
                ],
                "added_lines": 2,
                "removed_lines": 3,
            },
        )

        self.assertEqual(title, "• Edited 3 files (+2 -3)")

    def test_unified_patch_preview_groups_files(self) -> None:
        patch = (
            "--- a/mind_app/stream_events/tool_traces/native.py\n"
            "+++ b/mind_app/stream_events/tool_traces/native.py\n"
            "@@ -21,2 +21,2 @@\n"
            " from .native_helpers import (\n"
            "-    _failure_preview_lines,\n"
            "+    _error_preview_lines,\n"
            "--- a/mind_app/stream_events/tool_traces/title.py\n"
            "+++ b/mind_app/stream_events/tool_traces/title.py\n"
            "@@ -122,2 +122,2 @@\n"
            "-        return _failure_body_parts(body, base_style=base_style, ok=ok)\n"
            "+        return _plain_body_parts(body, base_style=base_style, ok=ok)\n"
        )

        preview = render_tool_result_preview(
            "workspace_apply_unified_patch",
            {"ok": True},
            arguments={"patch": patch},
        )

        self.assertEqual(preview.kind, "patch_tree")
        self.assertIn("└─ mind_app/stream_events/tool_traces/native.py (+1 -1)", preview.full)
        self.assertIn("└─ mind_app/stream_events/tool_traces/title.py (+1 -1)", preview.full)
        self.assertIn("  22 -    _failure_preview_lines,", preview.full)
        self.assertIn("  22 +    _error_preview_lines,", preview.full)
        self.assertIn(" 122 -        return _failure_body_parts", preview.full)
        self.assertIn(" 122 +        return _plain_body_parts", preview.full)

    def test_unified_patch_file_delta_header_sets_current_path(self) -> None:
        preview = render_tool_result_preview(
            "workspace_apply_unified_patch",
            {"ok": True},
            arguments={
                "patch": (
                    "--- a/app.py\n"
                    "+++ b/app.py\n"
                    "@@ -1,1 +1,1 @@\n"
                    "-old_value = 1\n"
                    "+new_value = 2\n"
                )
            },
        )

        parts = render_tool_trace_parts("• Edited app.py (+1 -1)", preview=preview, ok=True)
        text = "".join(part["text"] for part in parts)

        self.assertIn("└─ app.py (+1 -1)", text)
        self.assertNotIn("└ └─ app.py", text)
        self.assertIn("new_value", text)

    def test_unified_patch_tree_preview_preserves_repeated_line_numbers(self) -> None:
        patch = (
            "--- a/services/infra/llm/prompts/prompt_chat.py\n"
            "+++ b/services/infra/llm/prompts/prompt_chat.py\n"
            "@@ -3,3 +3,3 @@\n"
            " \n"
            "-from .markdown import load_prompt_md\n"
            "+from .loaders import load_prompt_md\n"
            " \n"
            "--- a/services/infra/llm/prompts/prompt_fast.py\n"
            "+++ b/services/infra/llm/prompts/prompt_fast.py\n"
            "@@ -3,3 +3,3 @@\n"
            " \n"
            "-from .markdown import load_prompt_md\n"
            "+from .loaders import load_prompt_md\n"
            " \n"
        )

        preview = render_tool_result_preview(
            "workspace_apply_unified_patch",
            {"ok": True},
            arguments={"patch": patch},
        )
        text = "".join(
            part["text"]
            for part in render_tool_trace_parts("• Edited 2 files (+2 -2)", preview=preview, ok=True)
        )

        self.assertIn("└─ services/infra/llm/prompts/prompt_chat.py (+1 -1)", text)
        self.assertIn("└─ services/infra/llm/prompts/prompt_fast.py (+1 -1)", text)
        self.assertIn("   4 -from .markdown import load_prompt_md", text)
        self.assertIn("   4 +from .loaders import load_prompt_md", text)
        self.assertGreaterEqual(text.count("   4 -from .markdown import load_prompt_md"), 2)
        self.assertGreaterEqual(text.count("   4 +from .loaders import load_prompt_md"), 2)


if __name__ == "__main__":
    unittest.main()
