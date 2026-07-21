# -*- coding: utf-8 -*-

from unittest.mock import (
    AsyncMock,
    patch,
)

import pytest

from mind_app.output.content import (
    AssistantTextDelta,
    SourcesOutput,
)
from mind_app.presentation.approval_views import build_approval_view
from mind_app.presentation.tool_views import build_tool_start_view
from mind_app.tui.adapters.content import TuiContentSink
from mind_app.tui.adapters.output import TuiOutputControl
from mind_app.tui.adapters.presentation import TuiPresentationSink
from mind_app.tui.core.document import (
    TuiBlockKind,
    TuiDocument,
)
from mind_app.tui.core.models import FragmentBlock
from mind_app.tui.core.render import display_line_count
from mind_app.tui.core.runtime import TuiRuntime


def _block(text: str) -> FragmentBlock:
    return FragmentBlock((("", text),))


def _document_text(document: TuiDocument) -> str:
    return "".join(text for _style, text in document.fragments(width=80))


@pytest.mark.parametrize(
    ("kinds", "expected"),
    [
        (("assistant", "operation"), "first\n\nsecond"),
        (("operation", "operation"), "first\nsecond"),
        (("operation", "approval"), "first\n\nsecond"),
        (("approval", "operation"), "first\n\nsecond"),
        (("approval", "approval"), "first\nsecond"),
        (("assistant", "approval"), "first\n\nsecond"),
        (("approval", "assistant"), "first\n\nsecond"),
        (("operation", "assistant"), "first\n\nsecond"),
        (("user", "assistant"), "first\n\nsecond"),
        (("assistant", "assistant"), "first\n\nsecond"),
        (("user", "user"), "first\n\nsecond"),
        (("operation", "notice"), "first\n\nsecond"),
        (("notice", "notice"), "first\nsecond"),
        (("system", "system"), "first\nsecond"),
    ],
)
def test_document_spacing_follows_semantic_transition(
    kinds: tuple[TuiBlockKind, TuiBlockKind],
    expected: str,
) -> None:
    document = TuiDocument()

    document.append_block(_block("first"), kind=kinds[0])
    document.append_block(_block("second"), kind=kinds[1])

    assert _document_text(document) == expected


def test_explicit_gap_overrides_compact_operation_transition() -> None:
    document = TuiDocument()

    document.append_block(_block("first"), kind="operation")
    document.request_gap()
    document.append_block(_block("second"), kind="operation")

    assert _document_text(document) == "first\n\nsecond"


def test_block_outer_newlines_do_not_duplicate_document_spacing() -> None:
    document = TuiDocument()

    document.append_block(_block("\nfirst\n"), kind="operation")
    document.append_block(_block("\nsecond\n"), kind="operation")

    assert _document_text(document) == "first\nsecond"


def test_active_block_keeps_spacing_while_it_is_updated_and_committed() -> None:
    document = TuiDocument()
    document.append_block(_block("tool"), kind="operation")

    document.set_active(_block("draft"), kind="assistant")
    document.set_active(_block("final"), kind="assistant")
    document.commit_active(_block("final"))

    assert _document_text(document) == "tool\n\nfinal"
    assert document.blocks[-1].kind == "assistant"


@pytest.mark.anyio
async def test_presentation_commits_each_tool_as_compact_document_block() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    await output.append_assistant_delta("model output")
    await output.prepare_external_output()
    await presentation.emit(build_tool_start_view(
        "shell_command",
        {"command": "echo one"},
        call_id="one",
    ))
    await presentation.emit(build_tool_start_view(
        "shell_command",
        {"command": "echo two"},
        call_id="two",
    ))

    assert [item.kind for item in runtime.document.blocks] == [
        "assistant",
        "operation",
        "operation",
    ]
    assert [item.gap_before for item in runtime.document.blocks] == [
        False,
        True,
        False,
    ]
    assert runtime.document.active_block is None


@pytest.mark.anyio
async def test_approval_sequence_separates_resumed_operation() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    approval = {
        "tool": "shell_command",
        "arguments": {"command": "echo approved"},
    }

    await presentation.emit(build_approval_view(approval, decision="accept"))
    await presentation.emit(build_approval_view(approval, decision="acceptForSession"))
    await presentation.emit(build_tool_start_view(
        "shell_command",
        approval["arguments"],
        call_id="approved",
    ))

    assert [item.kind for item in runtime.document.blocks] == [
        "approval",
        "approval",
        "operation",
    ]
    assert [item.gap_before for item in runtime.document.blocks] == [
        False,
        False,
        True,
    ]
    assert _document_text(runtime.document).count("\n\n") == 1


@pytest.mark.anyio
async def test_tool_approval_tool_sequence_separates_human_boundary() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    approval = {
        "tool": "shell_command",
        "arguments": {"command": "echo approved"},
    }

    await presentation.emit(build_tool_start_view(
        "shell_command",
        {"command": "echo before"},
        call_id="before",
    ))
    await presentation.emit(build_approval_view(approval, decision="accept"))
    await presentation.emit(build_tool_start_view(
        "shell_command",
        {"command": "echo after"},
        call_id="after",
    ))

    assert [item.kind for item in runtime.document.blocks] == [
        "operation",
        "approval",
        "operation",
    ]
    assert [item.gap_before for item in runtime.document.blocks] == [
        False,
        True,
        True,
    ]


@pytest.mark.anyio
async def test_tui_stream_keeps_long_live_line_without_rich_clipping() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    source = "x" * 200

    await output.append_assistant_delta(source)

    assert runtime.document.active_block is not None
    active_text = "".join(
        text for _style, text in runtime.document.active_block.fragments
    )
    assert active_text == f"• {source}"
    assert " ..." not in active_text

    await output.prepare_external_output()

    assert _document_text(runtime.document) == f"• {source}"


@pytest.mark.anyio
async def test_wide_character_stream_reflows_without_losing_content() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    source = "中文" * 60

    await output.append_assistant_delta(source)

    visible = _document_text(runtime.document)
    assert visible == f"• {source}"
    assert display_line_count(visible, width=40) > display_line_count(
        visible,
        width=80,
    )


@pytest.mark.anyio
async def test_text_done_boundary_adds_one_assistant_continuation_line() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)

    await output.append_assistant_delta("first")
    output.mark_stream_boundary()
    await output.append_assistant_delta("second")

    assert _document_text(runtime.document) == "• first\n  second"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("first\n", "second"),
        ("first", "\nsecond"),
    ],
)
async def test_text_done_boundary_does_not_duplicate_existing_newline(
    first: str,
    second: str,
) -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)

    await output.append_assistant_delta(first)
    output.mark_stream_boundary()
    await output.append_assistant_delta(second)

    assert _document_text(runtime.document) == "• first\n  second"


@pytest.mark.anyio
async def test_animated_stream_uses_the_same_text_done_boundary() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=True)

    with patch(
        "mind_app.tui.adapters.output.asyncio.sleep",
        new=AsyncMock(),
    ):
        await output.append_assistant_delta("first")
        output.mark_stream_boundary()
        await output.append_assistant_delta("second")

    await output.settle_stream()

    assert _document_text(runtime.document) == "• first\n  second"


@pytest.mark.anyio
async def test_model_tool_model_sequence_has_one_blank_row_at_each_boundary() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    await output.append_assistant_delta("before")
    await output.prepare_external_output()
    await presentation.emit(build_tool_start_view(
        "shell_command",
        {"command": "echo tool"},
        call_id="tool",
    ))
    await output.append_assistant_delta("after")
    await output.prepare_external_output()

    assert [item.kind for item in runtime.document.blocks] == [
        "assistant",
        "operation",
        "assistant",
    ]
    assert [item.gap_before for item in runtime.document.blocks] == [
        False,
        True,
        True,
    ]
    rendered = _document_text(runtime.document)
    assert rendered.startswith("• before\n\n")
    assert rendered.endswith("\n\n• after")
    assert rendered.count("\n\n") == 2


@pytest.mark.anyio
async def test_assistant_commit_renders_markdown_without_final_units_bridge() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)

    await output.append_assistant_delta("**bold** and `code`")
    await output.prepare_external_output()

    fragments = runtime.document.blocks[-1].block.fragments
    assert "".join(text for _style, text in fragments) == "• bold and code"
    assert any("bold" in style and text == "bold" for style, text in fragments)
    assert any("fg:" in style and text == "code" for style, text in fragments)


@pytest.mark.anyio
async def test_sources_are_assistant_metadata_instead_of_operation_output() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    content = TuiContentSink(output)

    await content.emit(AssistantTextDelta("answer"))
    await content.emit(SourcesOutput(({
        "title": "Reference",
        "url": "https://example.com/reference",
    },)))

    assert [item.kind for item in runtime.document.blocks] == [
        "assistant",
        "assistant",
    ]
    assert [item.gap_before for item in runtime.document.blocks] == [
        False,
        True,
    ]
    assert runtime.document.active_block is None
    assert "Sources:" in _document_text(runtime.document)
    assert all(
        "bold" not in style
        for style, text in runtime.document.blocks[-1].block.fragments
        if text.strip()
    )
