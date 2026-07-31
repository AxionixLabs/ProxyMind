# -*- coding: utf-8 -*-

import asyncio

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


def _block(text: str) -> FragmentBlock:
    return FragmentBlock((("", text),))


def test_tui_keymap_resolves_defaults_remaps_and_explicit_unbinding() -> None:
    defaults = TuiRuntimeKeymap.defaults()

    assert defaults.open_transcript_label == "Ctrl+T"
    assert [binding.label for binding in defaults.pager.scroll_up] == [
        "Up",
        "K",
    ]

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


if __name__ == '__main__':
    pass
