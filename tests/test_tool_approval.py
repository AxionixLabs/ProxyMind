# -*- coding: utf-8 -*-

import asyncio
import unittest
from pathlib import Path

from mind_app.approval import (
    APPROVAL_MENU_STYLE,
    approval_menu_content_lines,
    approval_menu_plain_text,
    prompt_tool_approval_decision,
    approval_from_event,
    render_bordered_approval_menu,
)
from mind_app.stream_events.approval_trace import (
    APPROVAL_RES_STYLE,
    APPROVAL_SUMMARY_MAX_CHARS,
    approval_summary,
    render_approval_approved_trace,
    render_approval_trace_parts,
)
from mind_app.stream_events.tool_traces.common import (
    COMMAND_FLAG_STYLE,
    COMMAND_HEAD_STYLE,
    COMMAND_OPERATOR_STYLE,
    COMMAND_STRING_STYLE,
)
from mind_nova import const


class ToolApprovalTest(unittest.TestCase):

    def _approval_card_text(
        self,
        approval: dict | None,
        *,
        decisions: list[str] | None = None,
        selected_index: int = 0,
        max_width: int = 80,
    ) -> str:
        lines = approval_menu_content_lines(
            decisions or ["accept", "decline"],
            approval=approval,
            selected_index=selected_index,
        )
        return approval_menu_plain_text(
            render_bordered_approval_menu(
                lines,
                max_width=max_width,
                title="Approval required" if approval is not None else None,
            )
        )

    def _approval_card_parts(
        self,
        approval: dict | None,
        *,
        decisions: list[str] | None = None,
        selected_index: int = 0,
        max_width: int = 80,
    ) -> list[tuple[str, str]]:
        lines = approval_menu_content_lines(
            decisions or ["accept", "decline"],
            approval=approval,
            selected_index=selected_index,
        )
        return render_bordered_approval_menu(
            lines,
            max_width=max_width,
            title="Approval required" if approval is not None else None,
        )

    def _assert_prompt_toolkit_styles_are_declared(
        self,
        parts: list[tuple[str, str]],
    ) -> None:
        style_rules = getattr(APPROVAL_MENU_STYLE, "style_rules", [])
        declared = {
            str(rule[0])
            for rule in style_rules
        }

        for style, _text in parts:
            if not style:
                continue
            self.assertTrue(style.startswith("class:"), style)
            class_names = style.removeprefix("class:").split()
            self.assertEqual(class_names, [style.removeprefix("class:")], style)
            self.assertIn(class_names[0], declared, style)

    def test_stream_approval_branch_waits_only_after_denial(self) -> None:
        source = Path("mind_app/modes/stream.py").read_text(encoding="utf-8")
        branch = source.split('if event_type == "tool.approval_required":', 1)[1]
        branch = branch.split('if event_type == "tool.call":', 1)[0]
        after_post = branch.split("await request.post_tool_approval(", 1)[1]

        self.assertIn("if not approved:", after_post)
        self.assertIn("begin_reply_wait_status(delay_sec=0.15, animate_after_sec=0.85)", after_post)
        self.assertNotIn("begin_reply_wait_status(delay_sec=0.0)", after_post)

    def test_approval_approved_trace_shows_scope(self) -> None:
        approval = {"command": "Get-Content main.py"}

        self.assertEqual(
            render_approval_approved_trace(approval, decision="accept"),
            f"✔ You approved {const.APP_NAME} to run Get-Content main.py this time",
        )
        self.assertEqual(
            render_approval_approved_trace(approval, decision="acceptForSession"),
            f"✔ You approved {const.APP_NAME} to run Get-Content main.py for this session",
        )

    def test_approval_summary_truncates_long_command(self) -> None:
        command = (
            "$root = (Resolve-Path -LiteralPath '.').Path.TrimEnd('\\'); "
            "$cacheDirs = @(Get-ChildItem -LiteralPath $root -Recurse -Force)"
        )

        summary = approval_summary({"command": command})

        self.assertLessEqual(len(summary), APPROVAL_SUMMARY_MAX_CHARS)
        self.assertTrue(summary.endswith(" ..."))
        self.assertIn("$root = (Resolve-Path", summary)

    def test_approved_trace_dims_command_summary(self) -> None:
        command = "$root = (Resolve-Path -LiteralPath '.').Path.TrimEnd('\\') ..."
        approval = {"command": command}
        title = render_approval_approved_trace(approval)

        parts = render_approval_trace_parts(title, approval=approval, state="approved")
        text = "".join(str(part.get("text") or "") for part in parts)
        styles = {
            str(part.get("text") or ""): part.get("style")
            for part in parts
            if str(part.get("text") or "")
        }

        self.assertEqual(text, f"✔ You approved {const.APP_NAME} to run {command} this time")
        self.assertEqual(styles[command], APPROVAL_RES_STYLE)

    def test_approval_card_styles_are_valid_prompt_toolkit_classes(self) -> None:
        cases = [
            self._approval_card_parts({"command": "adb devices"}),
            self._approval_card_parts(
                {"command": "python -c \"print('ok')\" " + " ".join(f"--flag-{i}=value-{i}" for i in range(12))},
                max_width=160,
            ),
            self._approval_card_parts({
                "command": ["python", "-c", "print('one')\nprint('two')"],
            }),
            self._approval_card_parts(None),
        ]

        for parts in cases:
            self._assert_prompt_toolkit_styles_are_declared(parts)

    def test_approval_card_highlights_only_selected_label(self) -> None:
        lines = approval_menu_content_lines(
            ["accept", "decline"],
            approval={"command": "adb devices"},
            selected_index=0,
        )
        selected = next(line for line in lines if any("Yes, proceed" in text for _style, text in line))

        self.assertEqual(selected[0], ("class:radio", "› 1. "))
        self.assertIn(("class:radio-selected", "Yes, proceed"), selected)

    def test_approval_card_uses_tool_trace_command_coloring(self) -> None:
        parts = self._approval_card_parts({
            "command": 'rg -n "shell_calls" . | Select-Object -First 20',
        })
        styles = {
            text: style
            for style, text in parts
            if text.strip()
        }

        self.assertEqual(styles["rg"], "class:command-head")
        self.assertEqual(styles["-n"], "class:command-flag")
        self.assertEqual(styles['"shell_calls"'], "class:command-string")
        self.assertEqual(styles["|"], "class:command-operator")
        self.assertEqual(styles["Select-Object"], "class:command-head")
        self.assertIn(COMMAND_HEAD_STYLE, str(APPROVAL_MENU_STYLE.style_rules))
        self.assertIn(COMMAND_FLAG_STYLE, str(APPROVAL_MENU_STYLE.style_rules))
        self.assertIn(COMMAND_STRING_STYLE, str(APPROVAL_MENU_STYLE.style_rules))
        self.assertIn(COMMAND_OPERATOR_STYLE, str(APPROVAL_MENU_STYLE.style_rules))

    def test_approval_card_omits_approval_metadata_summary(self) -> None:
        text = self._approval_card_text({
            "command": "adb devices",
            "cwd": ".",
            "reason": "shell_string_requires_approval",
            "risk": "dangerous",
            "category": "shell_string",
        })

        self.assertNotIn("cwd=.", text)
        self.assertNotIn("reason=shell_string_requires_approval", text)
        self.assertNotIn("risk=dangerous", text)
        self.assertNotIn("category=shell_string", text)

    def test_approval_card_wraps_long_command_without_truncating(self) -> None:
        command = " ".join([f"--flag-{index}=value-{index}" for index in range(20)])
        approval = {"command": f"python run.py {command}"}

        lines = approval_menu_content_lines(["accept", "decline"], approval=approval)
        parts = render_bordered_approval_menu(lines, max_width=64)
        text = approval_menu_plain_text(parts)
        rendered_lines = text.splitlines()

        self.assertIn("--flag-0=value-0", text)
        self.assertIn("--flag-19=value-19", text)
        self.assertLessEqual(max(len(line) for line in rendered_lines), 64)
        command_lines = [line for line in rendered_lines if "--flag-" in line]
        self.assertGreater(len(command_lines), 1)
        self.assertTrue(command_lines[1].startswith("     "))

    def test_approval_card_wraps_command_before_terminal_width(self) -> None:
        command = " ".join([f"--very-long-flag-{index}=value-{index}" for index in range(16)])
        approval = {"command": f"python run.py {command}"}

        lines = approval_menu_content_lines(["accept", "decline"], approval=approval)
        text = approval_menu_plain_text(render_bordered_approval_menu(lines, max_width=180))
        rendered_lines = [line for line in text.splitlines() if line]

        self.assertLessEqual(max(len(line) for line in rendered_lines), 104)
        self.assertIn("--very-long-flag-15=value-15", text)

    def test_approval_card_accounts_for_chinese_width(self) -> None:
        approval = {"command": "python -c \"print('你好世界')\"", "reason": "需要检查设备状态"}
        lines = approval_menu_content_lines(["accept", "decline"], approval=approval)
        text = approval_menu_plain_text(render_bordered_approval_menu(lines, max_width=60))
        rendered_lines = [line for line in text.splitlines() if line]

        self.assertLessEqual(max(len(line) for line in rendered_lines), 60)
        self.assertIn("你好世界", text)

    def test_approval_card_show_prompt_false_contains_only_options(self) -> None:
        text = self._approval_card_text(None)

        self.assertNotIn("Approval required", text)
        self.assertNotIn("$", text)
        self.assertIn("› 1. Yes, proceed (y)", text)
        self.assertIn("2. No, and tell Mind what to do differently (n/esc)", text)

    def test_approval_prompt_accepts_n_as_decline_fallback(self) -> None:
        decision = asyncio.run(prompt_tool_approval_decision(
            {"command": "adb devices"},
            input_func=lambda _prompt: "n",
        ))

        self.assertEqual(decision, "decline")

    def test_approval_card_renders_multiline_command_as_raw_block(self) -> None:
        approval = {
            "tool": "shell_command",
            "command": [
                "python",
                "-c",
                "print('one')\nprint('two')",
            ]
        }

        text = self._approval_card_text(approval, max_width=96)

        self.assertIn("$ python -c print('one')", text)
        self.assertIn("print('two')", text)
        self.assertNotIn("inline.py", text)

    def test_approval_card_prefers_single_item_command(self) -> None:
        approval = {
            "tool": "shell_command",
            "command": "WRONG fallback",
            "arguments": {
                "items": [
                    {
                        "command": "echo from items",
                        "cwd": ".",
                        "timeout_sec": 60,
                    }
                ]
            },
        }

        text = self._approval_card_text(approval, max_width=96)

        self.assertIn("$ echo from items", text)
        self.assertNotIn("WRONG fallback", text)

    def test_approval_card_renders_multiple_item_commands(self) -> None:
        approval = {
            "tool": "shell_command",
            "arguments": {
                "items": [
                    {"command": "echo one", "cwd": ".", "timeout_sec": 60},
                    {"command": "echo two", "cwd": ".", "timeout_sec": 60},
                ]
            },
        }

        text = self._approval_card_text(approval, max_width=96)

        self.assertIn("$ 2 commands", text)
        self.assertIn("├─ echo one", text)
        self.assertIn("└─ echo two", text)

    def test_approval_card_renders_multiline_batch_item_as_raw_block(self) -> None:
        approval = {
            "tool": "shell_command",
            "arguments": {
                "items": [
                    {"command": "echo one", "cwd": ".", "timeout_sec": 60},
                    {
                        "command": (
                            "Write-Host \"start\"\n"
                            "foreach ($i in 1..2) {\n"
                            "    Write-Host (\"line={0}\" -f $i)\n"
                            "}"
                        ),
                        "cwd": ".",
                        "timeout_sec": 60,
                    },
                ]
            },
        }

        text = self._approval_card_text(approval, max_width=96)

        self.assertIn("$ 2 commands", text)
        self.assertIn("├─ echo one", text)
        self.assertIn("└─ Write-Host \"start\"", text)
        self.assertIn("   foreach ($i in 1..2) {", text)
        self.assertIn("       Write-Host", text)
        self.assertNotIn("inline.txt", text)

    def test_approval_card_truncates_command_area_to_keep_decisions_visible(self) -> None:
        approval = {
            "tool": "shell_command",
            "command": "\n".join(f"Write-Host line-{index}" for index in range(30)),
        }

        lines = approval_menu_content_lines(
            ["acceptForSession", "decline"],
            approval=approval,
        )
        text = approval_menu_plain_text(
            render_bordered_approval_menu(lines, max_width=96, max_height=14)
        )
        rendered_lines = text.splitlines()

        self.assertLessEqual(len(rendered_lines), 14)
        self.assertIn("… +", text)
        self.assertIn("command lines", text)
        self.assertIn("› 1. Yes, for this session (s)", text)
        self.assertIn("2. No, and tell Mind what to do differently (n/esc)", text)
        self.assertIn("Write-Host line-0", text)
        self.assertNotIn("Write-Host line-29", text)

    def test_approval_card_truncates_wrapped_long_command_by_visible_lines(self) -> None:
        command = " ".join(f"--very-long-option-{index}=value-{index}" for index in range(40))
        approval = {"tool": "shell_command", "command": f"python run.py {command}"}

        lines = approval_menu_content_lines(["accept", "decline"], approval=approval)
        text = approval_menu_plain_text(
            render_bordered_approval_menu(lines, max_width=64, max_height=13)
        )
        rendered_lines = text.splitlines()

        self.assertLessEqual(len(rendered_lines), 13)
        self.assertIn("… +", text)
        self.assertIn("› 1. Yes, proceed (y)", text)
        self.assertIn("2. No, and tell Mind what to do differently (n/esc)", text)
        self.assertIn("--very-long-option-0=value-0", text)
        self.assertNotIn("--very-long-option-39=value-39", text)

    def test_approval_from_event_merges_server_approval_fields(self) -> None:
        event = {
            "name": "shell_command",
            "arguments": {
                "items": [
                    {"command": "echo from event args", "cwd": ".", "timeout_sec": 60}
                ]
            },
            "approval": {
                "id": "appr_xxx",
                "title": "Review command",
            },
            "meta": {
                "approval": {
                    "availableDecisions": ["acceptForSession", "decline"],
                }
            },
        }

        approval = approval_from_event(event)

        self.assertEqual(approval["tool"], "shell_command")
        self.assertEqual(approval["arguments"], event["arguments"])
        self.assertEqual(approval["availableDecisions"], ["acceptForSession", "decline"])

    def test_approval_from_event_falls_back_to_execution_canonical_arguments(self) -> None:
        event = {
            "tool": "shell_command",
            "approval": {"id": "appr_xxx"},
            "execution": {
                "canonicalArguments": {
                    "items": [
                        {"command": "echo from execution", "cwd": ".", "timeout_sec": 60}
                    ]
                }
            },
        }

        approval = approval_from_event(event)

        self.assertEqual(approval["arguments"], event["execution"]["canonicalArguments"])


if __name__ == "__main__":
    unittest.main()
