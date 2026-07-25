# -*- coding: utf-8 -*-

import io

import pytest
from rich.console import Console

from mind_app.output.rich_control import RichOutputControl


@pytest.mark.anyio
async def test_rich_stream_filters_control_sequences_across_deltas(
    tmp_path,
) -> None:
    stream = io.StringIO()
    log_file = tmp_path / "display.log"
    control = RichOutputControl(
        str(log_file),
        animate=False,
        console=Console(
            file=stream,
            force_terminal=False,
            color_system=None,
            width=80,
        ),
    )
    await control.open()

    await control.feed("safe\x1b]52;c;")
    await control.feed("payload\x1b\\\tdevice\x1bPprivate\x1b\\")
    await control.settle_stream()
    control.mark_stream_boundary()
    await control.feed("\tnext")

    assert control.coordinator.text_state.display_text == (
        "safe    device\n        next"
    )

    await control.settle_stream()
    await control.commit_live()
    await control.record_writer.close()

    visible = stream.getvalue()
    recorded = log_file.read_text(encoding="utf-8")
    assert "safe    device" in visible
    assert "next" in visible
    assert "safe    device\n        next" in recorded
    assert "\x1b]52" not in visible
    assert "\x1bP" not in visible
    assert "payload" not in visible
    assert "private" not in visible
    assert "\x1b" not in recorded
