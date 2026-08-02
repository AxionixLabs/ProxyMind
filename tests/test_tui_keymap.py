# -*- coding: utf-8 -*-

import asyncio
import threading

import pytest
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from mind_core.config import (
    ConfigValidationError,
    normalize_config,
)
from mind_app.tui.core.keymap import TuiRuntimeKeymap
from mind_app.tui.core.models import FragmentBlock
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.features.transcript_export import TranscriptExporter


def _block(text: str) -> FragmentBlock:
    return FragmentBlock((("", text),))


def test_tui_keymap_resolves_defaults_remaps_and_explicit_unbinding() -> None:
    defaults = TuiRuntimeKeymap.defaults()

    assert defaults.open_transcript_label == "Ctrl+T"
    assert [binding.label for binding in defaults.pager.scroll_up] == [
        "Up",
        "K",
    ]
    assert [binding.label for binding in defaults.pager.toggle_raw] == ["R"]
    assert [binding.label for binding in defaults.pager.search] == ["/"]
    assert [binding.label for binding in defaults.pager.search_next] == ["N"]
    assert [binding.label for binding in defaults.pager.search_previous] == [
        "Shift+N",
    ]
    assert [binding.label for binding in defaults.pager.export] == ["E"]

    config = normalize_config({
        "tui": {
            "keymap": {
                "global": {"open_transcript": "f12"},
                "pager": {
                    "scroll_down": ["n", "ctrl-n"],
                    "page_down": [],
                },
            }
        }
    })
    resolved = TuiRuntimeKeymap.from_config(config)

    assert resolved.open_transcript_label == "F12"
    assert [binding.label for binding in resolved.pager.scroll_down] == [
        "N",
        "Ctrl+N",
    ]
    assert resolved.pager.search_next == ()
    assert resolved.pager.page_down == ()

    unbound = TuiRuntimeKeymap.from_config({
        "tui": {
            "keymap": {
                "global": {"open_transcript": []},
            }
        }
    })
    assert unbound.open_transcript == ()
    assert unbound.open_transcript_label == ""


@pytest.mark.parametrize(
    "config",
    (
        {"tui": {"keymap": {"pager": {"unknown": "x"}}}},
        {"tui": {"keymap": {"pager": {"close": 1}}}},
    ),
)
def test_tui_keymap_schema_rejects_unknown_actions_and_invalid_values(
    config,
) -> None:
    with pytest.raises(ConfigValidationError, match="tui.keymap.pager"):
        normalize_config(config)


def test_tui_keymap_rejects_context_and_main_input_conflicts() -> None:
    with pytest.raises(ValueError, match="invalid key binding"):
        TuiRuntimeKeymap.from_config({
            "tui": {
                "keymap": {
                    "global": {"open_transcript": "not-a-key"},
                }
            }
        })

    with pytest.raises(ValueError, match="scroll_down.*scroll_up"):
        TuiRuntimeKeymap.from_config({
            "tui": {
                "keymap": {
                    "pager": {
                        "scroll_up": "ctrl-u",
                        "scroll_down": "ctrl-u",
                    }
                }
            }
        })

    with pytest.raises(ValueError, match="fixed transcript edit_previous"):
        TuiRuntimeKeymap.from_config({
            "tui": {
                "keymap": {
                    "pager": {"close": "esc"},
                }
            }
        })

    keymap = TuiRuntimeKeymap.from_config({
        "tui": {
            "keymap": {
                "global": {"open_transcript": "page-up"},
            }
        }
    })
    with pytest.raises(ValueError, match="tui.transcript.page_up"):
        TuiRuntime(keymap=keymap)


@pytest.mark.anyio
async def test_configured_transcript_keys_replace_default_dispatch() -> None:
    keymap = TuiRuntimeKeymap.from_config({
        "tui": {
            "keymap": {
                "global": {"open_transcript": "f12"},
                "pager": {
                    "scroll_down": "n",
                    "close": "x",
                },
            }
        }
    })

    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(
            input_obj=pipe_input,
            output_obj=DummyOutput(),
            keymap=keymap,
        )
        runtime.screen._output_size = lambda: (40, 10)
        runtime.append_block(
            _block("compact"),
            kind="operation",
            transcript_block=_block(
                "\n".join(f"line {index}" for index in range(30))
            ),
        )
        await runtime.open()
        try:
            pipe_input.send_text("\x14")
            await asyncio.sleep(0.05)
            assert not runtime.screen.transcript_overlay.active

            pipe_input.send_text("\x1b[24~")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if runtime.screen.transcript_overlay.active:
                    break
            assert runtime.screen.transcript_overlay.active

            overlay = runtime.screen.transcript_overlay
            primary_help = "".join(
                text
                for _style, text in (
                    runtime.screen._transcript_overlay_primary_help_fragments()
                )
            )
            secondary_help = "".join(
                text
                for _style, text in (
                    runtime.screen._transcript_overlay_secondary_help_fragments()
                )
            )
            assert "Up/N to scroll" in primary_help
            assert "X/Ctrl+T to quit" in secondary_help

            overlay.jump_top()
            pipe_input.send_text("j")
            await asyncio.sleep(0.05)
            assert overlay.scroll_offset == 0

            pipe_input.send_text("n")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if overlay.scroll_offset == 1:
                    break
            assert overlay.scroll_offset == 1

            pipe_input.send_text("x")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if not overlay.active:
                    break
            assert not overlay.active
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_transcript_search_captures_text_and_steps_results() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(
            input_obj=pipe_input,
            output_obj=DummyOutput(),
        )
        runtime.screen._output_size = lambda: (40, 10)
        runtime.append_block(_block("first target"), kind="assistant")
        for index in range(8):
            runtime.append_block(_block(f"filler {index}"), kind="assistant")
        runtime.append_block(_block("second target"), kind="assistant")

        await runtime.open()
        try:
            runtime.toggle_transcript_overlay()
            overlay = runtime.screen.transcript_overlay
            overlay.jump_top()

            pipe_input.send_text("/target\r")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if overlay.search_result_position == (1, 2):
                    break

            assert not overlay.search_editing
            assert overlay.search_query == "target"
            assert overlay.search_result_position == (1, 2)
            assert overlay.scroll_offset == 0

            pipe_input.send_text("n")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if overlay.search_result_position == (2, 2):
                    break

            assert overlay.search_result_position == (2, 2)
            assert overlay.scroll_offset > 0

            pipe_input.send_text("N")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if overlay.search_result_position == (1, 2):
                    break

            assert overlay.search_result_position == (1, 2)
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_transcript_export_key_writes_current_representation(tmp_path) -> None:
    exporter = TranscriptExporter(tmp_path)
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(
            input_obj=pipe_input,
            output_obj=DummyOutput(),
            export_transcript=exporter.export,
        )
        runtime.append_block(
            _block("rendered answer"),
            kind="assistant",
            raw_text="**source answer**",
        )

        await runtime.open()
        try:
            runtime.toggle_transcript_overlay()
            overlay = runtime.screen.transcript_overlay

            pipe_input.send_text("e")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if tuple(tmp_path.glob("*.md")):
                    break

            markdown_path = tuple(tmp_path.glob("*.md"))[0]
            assert "**source answer**" in markdown_path.read_text(encoding="utf-8")
            assert not overlay.export_failed
            assert "Exported markdown:" in overlay.export_status

            pipe_input.send_text("re")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if tuple(tmp_path.glob("*.txt")):
                    break

            raw_path = tuple(tmp_path.glob("*.txt"))[0]
            assert raw_path.read_text(encoding="utf-8") == "**source answer**\n"
            assert overlay.raw_mode
            assert "Exported raw:" in overlay.export_status
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_transcript_export_failure_stays_inside_overlay() -> None:
    def fail_export(cells, output_format):
        _ = cells, output_format
        raise OSError("disk\x1b[31m full")

    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(
            input_obj=pipe_input,
            output_obj=DummyOutput(),
            export_transcript=fail_export,
        )
        runtime.append_block(_block("content"), kind="assistant")

        await runtime.open()
        try:
            runtime.toggle_transcript_overlay()
            pipe_input.send_text("e")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if runtime.screen.transcript_overlay.export_failed:
                    break

            overlay = runtime.screen.transcript_overlay
            assert overlay.active
            assert overlay.export_failed
            assert overlay.export_status == "Export failed: disk full"
            assert "\x1b" not in overlay.export_status
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_transcript_export_runs_off_loop_and_rejects_duplicate_request(
    tmp_path,
) -> None:
    exporter = TranscriptExporter(tmp_path)
    started = threading.Event()
    release = threading.Event()
    calls = []

    def delayed_export(cells, output_format):
        calls.append(output_format)
        started.set()
        release.wait(timeout=2)
        return exporter.export(cells, output_format)

    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(
            input_obj=pipe_input,
            output_obj=DummyOutput(),
            export_transcript=delayed_export,
        )
        runtime.append_block(_block("content"), kind="assistant")

        await runtime.open()
        try:
            runtime.toggle_transcript_overlay()
            pipe_input.send_text("ee")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if started.is_set():
                    break

            overlay = runtime.screen.transcript_overlay
            assert started.is_set()
            assert overlay.export_in_progress
            assert overlay.export_status == "Exporting markdown..."
            assert calls == ["markdown"]

            overlay.scroll_line(1)
            assert overlay.active

            release.set()
            for _ in range(100):
                await asyncio.sleep(0.01)
                if not overlay.export_in_progress:
                    break

            assert not overlay.export_in_progress
            assert tuple(tmp_path.glob("*.md"))
        finally:
            release.set()
            await runtime.close()


if __name__ == '__main__':
    pass
