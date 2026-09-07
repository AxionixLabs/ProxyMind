# -*- coding: utf-8 -*-

import asyncio
import threading
from dataclasses import FrozenInstanceError
from unittest.mock import Mock

import pytest
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from infrastructure.config.schema import (
    ConfigValidationError,
    normalize_config,
)
from frontends.tui.core.keymap import (
    TuiRuntimeKeymap,
    key_action_matches,
)
from frontends.tui.core.models import FragmentBlock
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.features.transcript_export import TranscriptExporter


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


def test_tui_keymap_exposes_frozen_stable_action_contexts() -> None:
    keymap = TuiRuntimeKeymap.defaults()
    action_ids = {action.action_id for action in keymap.actions()}

    assert {
        "global.open_transcript",
        "chat.interrupt_turn",
        "composer.submit",
        "editor.insert_newline",
        "pager.close",
        "list.accept",
        "approval.accept_selected",
    } <= action_ids
    assert keymap.bindings_for("global.copy_last_response")[0].label == (
        "Ctrl+O"
    )
    with pytest.raises(KeyError):
        keymap.bindings_for("composer.missing")
    with pytest.raises(FrozenInstanceError):
        keymap.chat.interrupt_turn = ()


def test_codex_editor_list_and_approval_aliases_are_runtime_facts() -> None:
    keymap = TuiRuntimeKeymap.defaults()

    assert [item.label for item in keymap.composer.history_search_previous] == [
        "Ctrl+R"
    ]
    assert [item.label for item in keymap.composer.history_search_next] == [
        "Ctrl+S"
    ]
    assert [item.label for item in keymap.editor.move_line_start] == [
        "Home",
        "Ctrl+A",
    ]
    assert [item.label for item in keymap.editor.move_word_right] == [
        "Alt+F",
        "Alt+Right",
        "Ctrl+Right",
    ]
    assert [item.label for item in keymap.list.move_down] == [
        "Down",
        "Ctrl+N",
        "Ctrl+J",
        "J",
    ]
    assert [item.label for item in keymap.approval.accept_session] == ["A"]
    assert [item.label for item in keymap.approval.deny] == ["D"]
    assert [item.label for item in keymap.approval.cancel] == ["C"]


def test_runtime_key_matching_normalizes_named_adapter_events() -> None:
    keymap = TuiRuntimeKeymap.defaults()

    assert key_action_matches(keymap.list.accept, ("enter",))
    assert key_action_matches(keymap.list.cancel, ("escape",))
    assert key_action_matches(keymap.list.move_right, ("right",))


def test_tui_components_consume_runtime_keymap_contexts() -> None:
    runtime = TuiRuntime()
    keymap = runtime.keymap

    assert runtime.input_model.keymap is keymap
    assert runtime.screen.keymap is keymap
    assert runtime.screen.menu.keymap is keymap.list
    assert runtime.screen.approval.keymap is keymap.approval

    input_sequences = {
        tuple(binding.keys)
        for binding in runtime.input_model.key_bindings.bindings
    }
    menu_sequences = {
        tuple(binding.keys)
        for binding in runtime.screen.menu.key_bindings.bindings
    }
    approval_sequences = {
        tuple(binding.keys)
        for binding in runtime.screen.approval.key_bindings.bindings
    }

    assert keymap.global_keys.copy_last_response[0].keys in input_sequences
    assert keymap.chat.interrupt_turn[0].keys in input_sequences
    assert keymap.composer.submit[0].keys in input_sequences
    assert keymap.list.accept[0].keys in menu_sequences
    assert keymap.approval.accept_selected[0].keys in approval_sequences


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


def test_tui_scrollback_reflow_line_limit_is_normalized_and_validated() -> None:
    assert normalize_config({})["tui"][
        "scrollback_reflow_line_limit"
    ] == 10_000
    assert normalize_config({
        "tui": {"scrollback_reflow_line_limit": 320},
    })["tui"]["scrollback_reflow_line_limit"] == 320

    for invalid in (True, 0, -1, 1.5, "320"):
        with pytest.raises(
            ConfigValidationError,
            match="scrollback_reflow_line_limit",
        ):
            normalize_config({
                "tui": {"scrollback_reflow_line_limit": invalid},
            })


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

    with pytest.raises(ValueError, match="transcript_page_up.*open_transcript"):
        TuiRuntimeKeymap.from_config({
            "tui": {
                "keymap": {
                    "global": {"open_transcript": "page-up"},
                }
            }
        })

    with pytest.raises(ValueError, match="copy_last_response.*open_transcript"):
        TuiRuntimeKeymap.from_config({
            "tui": {
                "keymap": {
                    "global": {"open_transcript": "ctrl-o"},
                }
            }
        })


def test_all_runtime_contexts_support_remapping_and_explicit_unbinding() -> None:
    config = normalize_config({
        "tui": {
            "keymap": {
                "global": {"copy_last_response": "f13"},
                "chat": {"edit_queued_message": "f14"},
                "composer": {"submit": "f15", "queue": []},
                "editor": {"move_left": "f16"},
                "pager": {"scroll_up": "f17"},
                "list": {"accept": "f18"},
                "approval": {"accept_selected": "f19"},
            }
        }
    })

    assert set(config["tui"]["keymap"]) == {
        "global",
        "chat",
        "composer",
        "editor",
        "pager",
        "list",
        "approval",
    }
    keymap = TuiRuntimeKeymap.from_config(config)
    assert keymap.global_keys.copy_last_response[0].label == "F13"
    assert keymap.chat.edit_queued_message[0].label == "F14"
    assert keymap.composer.submit[0].label == "F15"
    assert keymap.composer.queue == ()
    assert keymap.editor.move_left[0].label == "F16"
    assert keymap.pager.scroll_up[0].label == "F17"
    assert keymap.list.accept[0].label == "F18"
    assert keymap.approval.accept_selected[0].label == "F19"


def test_composer_local_config_precedes_global_fallback_and_defaults() -> None:
    fallback = TuiRuntimeKeymap.from_config({
        "tui": {"keymap": {"global": {"submit": "f20", "queue": []}}},
    })
    assert fallback.composer.submit[0].label == "F20"
    assert fallback.composer.queue == ()
    assert fallback.composer.toggle_shortcuts[0].label == "?"

    local = TuiRuntimeKeymap.from_config({
        "tui": {
            "keymap": {
                "global": {"submit": "f20"},
                "composer": {"submit": "f21"},
            }
        },
    })
    assert local.composer.submit[0].label == "F21"


@pytest.mark.parametrize(
    ("config", "message"),
    (
        (
            {"global": {"open_transcript": "ctrl-c"}},
            "fixed safety",
        ),
        (
            {"composer": {"submit": "x"}},
            "printable keys",
        ),
        (
            {"global": {"copy_last_response": "ctrl-alt-e"}},
            "AltGr",
        ),
        (
            {"composer": {"toggle_shortcuts": "x ctrl-s"}},
            "chord prefix",
        ),
        (
            {"composer": {"toggle_shortcuts": "ctrl-x esc"}},
            "Esc is reserved",
        ),
        (
            {
                "global": {"copy_last_response": "f24"},
                "composer": {"submit": "f24"},
            },
            "conflicts",
        ),
        (
            {"global": {"copy_last_response": "ctrl-m"}},
            "indistinguishable from Enter",
        ),
        (
            {"global": {"copy_last_response": "ctrl-i"}},
            "indistinguishable from Tab",
        ),
        (
            {"global": {"copy_last_response": "ctrl-h"}},
            "indistinguishable from Backspace",
        ),
        (
            {"global": {"copy_last_response": "ctrl-x alt-o"}},
            "Alt-modified second chord stroke",
        ),
    ),
)
def test_keymap_rejects_unsafe_or_ambiguous_config(config, message) -> None:
    with pytest.raises(ValueError, match=message):
        TuiRuntimeKeymap.from_config({"tui": {"keymap": config}})


def test_keymap_supports_two_stroke_chords_aliases_and_modified_named_keys() -> None:
    keymap = TuiRuntimeKeymap.from_config({
        "tui": {
            "keymap": {
                "global": {"open_transcript": "option-x control-t"},
                "editor": {"move_word_left": "ctrl-shift-left"},
            }
        }
    })

    binding = keymap.global_keys.open_transcript[0]
    assert binding.is_chord
    assert binding.label == "Alt+X Ctrl+T"
    assert len(binding.strokes) == 2
    assert keymap.editor.move_word_left[0].label == "Ctrl+Shift+Left"


@pytest.mark.anyio
async def test_chord_dispatch_cancel_and_timeout_follow_codex_semantics() -> None:
    keymap = TuiRuntimeKeymap.from_config({
        "tui": {
            "keymap": {
                "global": {"open_transcript": "ctrl-x ctrl-t"},
            }
        }
    })
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(
            input_obj=pipe_input,
            output_obj=DummyOutput(),
            keymap=keymap,
        )
        toggle_transcript = Mock(
            wraps=runtime.screen._toggle_transcript_overlay
        )
        runtime.screen._toggle_transcript_overlay = toggle_transcript
        await runtime.open()
        try:
            pipe_input.send_text("\x18\x1b")
            await asyncio.sleep(0.15)
            assert runtime.active, runtime._application_lifecycle.exception()
            assert not runtime.screen.transcript_overlay.active
            assert not runtime.input_model.history_backtrack_primed
            assert toggle_transcript.call_count == 0

            pipe_input.send_text("\x18")
            await asyncio.sleep(1.1)
            pipe_input.send_text("\x14")
            await asyncio.sleep(0.1)
            assert runtime.active, runtime._application_lifecycle.exception()
            assert not runtime.screen.transcript_overlay.active
            assert toggle_transcript.call_count == 0

            pipe_input.send_text("\x18\x14")
            for _index in range(100):
                await asyncio.sleep(0.01)
                if runtime.screen.transcript_overlay.active:
                    break
            assert toggle_transcript.call_count == 1
            assert runtime.screen.transcript_overlay.active
        finally:
            await runtime.close()


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
async def test_empty_composer_question_mark_opens_runtime_shortcuts() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(
            input_obj=pipe_input,
            output_obj=DummyOutput(),
        )
        await runtime.open()
        try:
            pipe_input.send_text("?")
            for _index in range(100):
                await asyncio.sleep(0.01)
                if runtime.screen.static_pager.active:
                    break

            assert runtime.screen.static_pager.active
            assert runtime.screen.static_pager.title == "Keyboard shortcuts"
            text = "\n".join(
                "".join(value for _style, value in line)
                for line in runtime.screen.static_pager.lines
            )
            assert "Ctrl+O" in text
            assert "copy last response" in text
            assert runtime.screen.input.buffer.text == ""
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_question_mark_remains_text_in_nonempty_composer() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(
            input_obj=pipe_input,
            output_obj=DummyOutput(),
        )
        await runtime.open()
        try:
            runtime.screen.input.buffer.text = "draft"
            runtime.screen.input.buffer.cursor_position = len("draft")
            pipe_input.send_text("?")
            for _index in range(100):
                await asyncio.sleep(0.01)
                if runtime.screen.input.buffer.text.endswith("?"):
                    break

            assert not runtime.screen.static_pager.active
            assert runtime.screen.input.buffer.text == "draft?"
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


@pytest.mark.anyio
async def test_transcript_export_uses_trigger_format_after_mode_switch(
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
        runtime.append_block(
            _block("rendered"),
            kind="assistant",
            raw_text="**source**",
        )

        await runtime.open()
        try:
            runtime.toggle_transcript_overlay()
            pipe_input.send_text("e")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if started.is_set():
                    break

            overlay = runtime.screen.transcript_overlay
            assert started.is_set()
            overlay.toggle_raw_mode()
            release.set()
            for _ in range(100):
                await asyncio.sleep(0.01)
                if not overlay.export_in_progress:
                    break

            assert calls == ["markdown"]
            assert overlay.raw_mode
            assert "Exported markdown:" in overlay.export_status
            assert tuple(tmp_path.glob("*.md"))
            assert not tuple(tmp_path.glob("*.txt"))
        finally:
            release.set()
            await runtime.close()


@pytest.mark.anyio
async def test_closing_transcript_ignores_pending_export_completion(
    tmp_path,
) -> None:
    exporter = TranscriptExporter(tmp_path)
    started = threading.Event()
    release = threading.Event()

    def delayed_export(cells, output_format):
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
            pipe_input.send_text("e")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if started.is_set():
                    break

            overlay = runtime.screen.transcript_overlay
            assert started.is_set()
            runtime.toggle_transcript_overlay()
            release.set()
            await asyncio.sleep(0.05)

            assert not overlay.active
            assert not overlay.export_in_progress
            assert overlay.export_status == ""
            assert not overlay.export_failed
        finally:
            release.set()
            await runtime.close()


@pytest.mark.anyio
async def test_runtime_close_cancels_pending_export_ui_update(tmp_path) -> None:
    exporter = TranscriptExporter(tmp_path)
    started = threading.Event()
    release = threading.Event()

    def delayed_export(cells, output_format):
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
        runtime.toggle_transcript_overlay()
        pipe_input.send_text("e")
        for _ in range(100):
            await asyncio.sleep(0.01)
            if started.is_set():
                break

        assert started.is_set()
        close_task = asyncio.create_task(runtime.close())
        await asyncio.sleep(0.02)
        release.set()
        await close_task

        overlay = runtime.screen.transcript_overlay
        assert not overlay.export_in_progress
        assert overlay.export_status == ""


if __name__ == '__main__':
    pass
