# -*- coding: utf-8 -*-

from mind_app.stream_state.text import TextState


def test_rich_stream_preview_is_clipped_but_final_text_is_complete() -> None:
    state = TextState(width_provider=lambda: 80)
    source = "x" * 200

    state.append(source)

    assert len(state.display_text) == 74
    assert state.display_text.endswith(" ...")
    assert state.final_units()[0].text == source


def test_rich_block_preview_is_clipped_but_final_text_is_complete() -> None:
    state = TextState(width_provider=lambda: 80)
    source = ("1234567890" * 4 + "\n") * 5

    state.append(source, display=TextState.BLOCK)

    assert " ...\n" in state.display_text
    assert state.final_units()[0].text == source.rstrip("\n")

