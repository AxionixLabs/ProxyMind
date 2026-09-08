# -*- coding: utf-8 -*-

from types import SimpleNamespace

import pytest
from prompt_toolkit.utils import get_cwidth

from agent.domain.transcripts import (
    TranscriptEntry,
    TranscriptReplay,
)
from frontends.tui.core.document import TuiDocument
from frontends.tui.core.render import fragments_text
from frontends.tui.features import history
from frontends.tui.core.hyperlinks import terminal_hyperlink_from_style
from frontends.tui.contracts.resume import (
    ResumeDensity,
    ResumeFilterMode,
    ResumePreviewStatus,
    ResumeRow,
    ResumeSessionStatus,
    ResumeSortKey,
)


def _history_host(reader) -> SimpleNamespace:
    """构造只暴露正式会话历史端口的测试宿主。"""
    return SimpleNamespace(
        conversation=SimpleNamespace(
            history=SimpleNamespace(read_transcript=reader),
        ),
    )


@pytest.mark.anyio
async def test_history_session_builds_resume_request_and_maps_row() -> None:
    record = {
        "cid": "cid_test_12345678",
        "sid": "sid_test_1_abcdef",
        "title": "explain the current architecture",
        "workspace": r"D:\PycharmProjects\ProxyMind",
        "source": "tui",
        "branch": "feature/resume",
        "status": "archived",
        "created_at": 1,
        "updated_at": 1,
    }
    requests = []

    class Runtime(object):
        async def view_resume_picker(self, request):
            requests.append(request)
            return request.rows[0]

    selected = await history.choose_history_session(
        Runtime(),
        [record],
        filter_workspace=r"D:\PycharmProjects\ProxyMind",
    )

    assert selected is record
    request = requests[0]
    assert request.rows[0].cid == record["cid"]
    assert request.rows[0].sid == record["sid"]
    assert request.rows[0].title == record["title"]
    assert request.rows[0].workspace == record["workspace"]
    assert request.rows[0].source == "tui"
    assert request.rows[0].created_at_ms == 1
    assert request.rows[0].updated_at_ms == 1
    assert request.rows[0].branch == "feature/resume"
    assert request.rows[0].status is ResumeSessionStatus.ARCHIVED
    assert request.filter_workspace == "d:/PycharmProjects/ProxyMind"
    assert request.initial_filter is ResumeFilterMode.CWD
    assert request.initial_sort is ResumeSortKey.UPDATED
    assert request.initial_density is ResumeDensity.DENSE


@pytest.mark.anyio
async def test_history_session_empty_and_invalid_records_still_open_picker() -> None:
    record = {
        "cid": "invalid",
        "sid": "invalid",
    }
    requests = []

    class Runtime(object):
        async def view_resume_picker(self, request):
            requests.append(request)
            return None

    selected = await history.choose_history_session(
        Runtime(),
        [record],
        show_workspace=True,
    )

    assert selected is None
    assert requests[0].rows == ()
    assert requests[0].show_workspace
    assert requests[0].initial_filter is ResumeFilterMode.ALL


@pytest.mark.anyio
async def test_history_resume_preview_loader_returns_recent_conversation() -> None:
    row = ResumeRow(
        cid="cid_test_12345678",
        sid="sid_test_1_abcdef",
        title="continue",
        workspace="D:/workspace",
        source="tui",
        created_at_ms=1,
        updated_at_ms=2,
    )
    entries = tuple(
        TranscriptEntry(
            timestamp="2026-08-02T00:00:00.000Z",
            event="message.created",
            session_id=row.sid,
            turn_id=f"turn_{index}",
            actor=actor,
            payload={"content": text},
        )
        for index, (actor, text) in enumerate((
            ("user", "recent user"),
            ("assistant", "recent assistant"),
        ))
    )

    controller = _history_host(lambda _session_id: entries)
    preview = await history.HistoryResumePreviewLoader(controller).load(
        row,
        width=40,
    )

    assert preview.row_key == row.key
    assert preview.status is ResumePreviewStatus.READY
    assert preview.blocks == (
        (("class:resume-picker.preview.user", "recent user"),),
        (("class:resume-picker.preview.assistant", "recent assistant"),),
    )


@pytest.mark.anyio
async def test_history_resume_transcript_loader_preserves_full_rendered_blocks() -> None:
    row = ResumeRow(
        cid="cid_test_12345678",
        sid="sid_test_1_abcdef",
        title="continue",
        workspace="D:/workspace",
        source="tui",
        created_at_ms=1,
        updated_at_ms=2,
    )
    entries = tuple(
        TranscriptEntry(
            timestamp="2026-08-02T00:00:00.000Z",
            event="message.created",
            session_id=row.sid,
            turn_id=f"turn_{index}",
            actor=actor,
            payload={"content": text},
        )
        for index, (actor, text) in enumerate((
            ("user", "recent user"),
            ("assistant", "recent assistant"),
        ))
    )

    transcript = await history.HistoryResumeTranscriptLoader(
        _history_host(lambda _session_id: entries)
    ).load(row, width=40)

    assert transcript.row_key == row.key
    assert transcript.status is ResumePreviewStatus.READY
    assert len(transcript.blocks) == 2
    assert "recent user" in history.sanitize_terminal_text(
        "".join(value for _style, value in transcript.blocks[0])
    )


def test_history_transcript_replays_messages_and_tool_result() -> None:
    def entry(
        event: str,
        *,
        actor: str,
        payload: dict,
    ) -> TranscriptEntry:
        return TranscriptEntry(
            timestamp="2026-08-02T00:00:00.000Z",
            event=event,
            session_id="session_test",
            turn_id="turn_test",
            actor=actor,
            payload=payload,
        )

    entries = (
        entry(
            "message.created",
            actor="user",
            payload={
                "content": "show the workspace",
                "attachments": [{"filename": "screen.png"}],
                "extras": {"selection": "src/app.py"},
            },
        ),
        entry(
            "tool.started",
            actor="tool",
            payload={
                "call_id": "call_1",
                "name": "shell_command",
                "arguments": {"command": "pwd"},
            },
        ),
        entry(
            "tool.completed",
            actor="tool",
            payload={
                "call_id": "call_1",
                "duration_ms": 120,
                "result": {
                    "ok": True,
                    "text": "D:/workspace",
                    "attachments": [],
                    "data": {"output": "D:/workspace"},
                },
            },
        ),
        entry(
            "message.created",
            actor="assistant",
            payload={"content": "**Done**"},
        ),
        entry(
            "context.compacted",
            actor="system",
            payload={"summary": "Context compacted · 20 -> 4 items"},
        ),
    )

    def read_transcript(session_id):
        assert session_id == "session_test"
        return entries

    blocks = history.load_history_transcript(
        _history_host(read_transcript),
        "session_test",
        terminal_width=60,
    )

    assert [block.kind for block in blocks] == [
        "user",
        "operation",
        "assistant",
        "notice",
    ]
    assert blocks[0].turn_id == "turn_test"
    assert blocks[0].prompt == "show the workspace"
    assert blocks[0].attachments == ({"filename": "screen.png"},)
    assert blocks[0].extras == {"selection": "src/app.py"}
    assert blocks[0].source is entries[0]
    assert blocks[0].raw_text == "show the workspace"
    assert isinstance(blocks[1].source, TranscriptEntry)
    assert blocks[1].source.event == "tool.completed"
    assert blocks[1].raw_text == "pwd\nD:/workspace"
    assert blocks[2].source is entries[3]
    assert blocks[2].raw_text == "**Done**"
    assert blocks[3].source is entries[4]
    assert blocks[3].raw_text == "Context compacted · 20 -> 4 items"
    assert "pwd" in "".join(
        text for _style, text in blocks[1].transcript_block.fragments
    )
    assert "D:/workspace" in "".join(
        text for _style, text in blocks[1].transcript_block.fragments
    )
    assert "(no output)" not in "".join(
        text for _style, text in blocks[1].transcript_block.fragments
    )
    assert "✓ • 120ms" in "".join(
        text for _style, text in blocks[1].transcript_block.fragments
    )
    assert "".join(
        text for _style, text in blocks[1].transcript_block.fragments
    ).splitlines()[-1] == "✓ • 120ms"
    assert "Done" in "".join(
        text for _style, text in blocks[2].display_block.fragments
    )
    assert "20 -> 4" in "".join(
        text for _style, text in blocks[3].display_block.fragments
    )


def test_history_transcript_merges_patch_lifecycle_into_one_cell() -> None:
    raw_patch = (
        "*** Begin Patch\n"
        "*** Update File: sample.py\n"
        "@@\n"
        "-old\n"
        "+new\n"
        "*** End Patch"
    )
    preview = {
        "files": [{
            "path": "sample.py",
            "source_path": None,
            "action": "modify",
        }],
        "delta": {
            "exact": True,
            "changes": [{
                "path": "sample.py",
                "source_path": None,
                "action": "modify",
                "old_content": "old\n",
                "new_content": "new\n",
                "hunks": [{"lines": [
                    {"kind": "remove", "text": "old", "old_line": 1, "new_line": None},
                    {"kind": "add", "text": "new", "old_line": None, "new_line": 1},
                ]}],
            }],
        },
    }

    def entry(event: str, payload: dict) -> TranscriptEntry:
        return TranscriptEntry(
            timestamp="2026-08-02T00:00:00.000Z",
            event=event,
            session_id="session_patch",
            turn_id="turn_patch",
            actor="tool",
            payload=payload,
        )

    entries = (
        entry("tool.started", {
            "call_id": "call_patch",
            "name": "apply_patch",
            "arguments": {"patch": raw_patch},
            "patch_preview": preview,
        }),
        entry("tool.completed", {
            "call_id": "call_patch",
            "name": "apply_patch",
            "ok": True,
            "duration_ms": 14,
            "result": {
                "ok": True,
                "text": "",
                "attachments": [],
                "data": {
                    "files": [{
                        "path": "sample.py",
                        "source_path": None,
                        "action": "modify",
                        "added_lines": 1,
                        "removed_lines": 1,
                    }],
                    "delta": {
                        "exact": True,
                        "changes": [{
                            "path": "sample.py",
                            "source_path": None,
                            "action": "modify",
                            "old_content": "old\n",
                            "new_content": "new\n",
                            "hunks": [{"lines": [
                                {"kind": "remove", "text": "old", "old_line": 1, "new_line": None},
                                {"kind": "add", "text": "new", "old_line": None, "new_line": 1},
                            ]}],
                        }],
                    },
                },
            },
        }),
    )

    def read_transcript(session_id):
        assert session_id == "session_patch"
        return entries

    blocks = history.load_history_transcript(
        _history_host(read_transcript),
        "session_patch",
        terminal_width=60,
    )
    display = "".join(
        text for _style, text in blocks[0].display_block.fragments
    )

    assert len(blocks) == 1
    assert blocks[0].source.event == "tool.completed"
    assert blocks[0].raw_text == raw_patch
    assert display == (
        "• Edited sample.py (+1 -1)\n"
        "    1 -old\n"
        "    1 +new"
    )
    assert "Applying patch" not in display


def test_history_patch_failure_reads_current_result_envelope() -> None:
    entry = TranscriptEntry(
        timestamp="2026-08-02T00:00:00.000Z",
        event="tool.failed",
        session_id="session_patch",
        turn_id="turn_patch",
        actor="tool",
        payload={
            "call_id": "call_patch",
            "name": "apply_patch",
            "arguments": {"patch": "*** Begin Patch\n*** End Patch"},
            "ok": False,
            "result": {
                "ok": False,
                "text": "patch context mismatch",
                "attachments": [],
                "data": {"reason": "patch_context_mismatch"},
            },
        },
    )

    blocks = history._render_replay_blocks((entry,), terminal_width=60)
    display = "".join(
        text for _style, text in blocks[0].display_block.fragments
    )

    assert display == (
        "✘ Failed to apply patch\n"
        "  reason: patch_context_mismatch"
    )


def test_history_drops_patch_start_without_new_protocol_preview() -> None:
    entry = TranscriptEntry(
        timestamp="2026-08-02T00:00:00.000Z",
        event="tool.started",
        session_id="session_patch",
        turn_id="turn_patch",
        actor="tool",
        payload={
            "call_id": "call_patch",
            "name": "apply_patch",
            "arguments": {"patch": "patch"},
        },
    )

    assert TranscriptReplay((entry,)).build() == ()


def test_history_patch_failure_without_result_uses_recorded_exception() -> None:
    entry = TranscriptEntry(
        timestamp="2026-08-02T00:00:00.000Z",
        event="tool.failed",
        session_id="session_patch",
        turn_id="turn_patch",
        actor="tool",
        payload={
            "call_id": "call_patch",
            "name": "apply_patch",
            "arguments": {"patch": "*** Begin Patch\n*** End Patch"},
            "ok": False,
            "error": "OSError: disk full",
        },
    )

    blocks = history._render_replay_blocks((entry,), terminal_width=60)
    display = "".join(
        text for _style, text in blocks[0].display_block.fragments
    )

    assert display == (
        "✘ Failed to apply patch\n"
        "  error: OSError: disk full"
    )


def test_history_patch_failure_keeps_persisted_preview_cell() -> None:
    preview = {
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
                "old_content": "old\n",
                "new_content": "new\n",
                "hunks": [{"lines": [
                    {"kind": "remove", "text": "old", "old_line": 1, "new_line": None},
                    {"kind": "add", "text": "new", "old_line": None, "new_line": 1},
                ]}],
                "source_path": None,
            }],
        },
    }
    entries = (
        TranscriptEntry(
            timestamp="2026-08-02T00:00:00.000Z",
            event="tool.started",
            session_id="session_patch",
            turn_id="turn_patch",
            actor="tool",
            payload={
                "call_id": "call_patch",
                "name": "apply_patch",
                "arguments": {"patch": "patch"},
                "patch_preview": preview,
            },
        ),
        TranscriptEntry(
            timestamp="2026-08-02T00:00:00.001Z",
            event="tool.failed",
            session_id="session_patch",
            turn_id="turn_patch",
            actor="tool",
            payload={
                "call_id": "call_patch",
                "name": "apply_patch",
                "arguments": {"patch": "patch"},
                "ok": False,
                "result": {
                    "ok": False,
                    "text": "patch context mismatch",
                    "attachments": [],
                    "data": {"reason": "patch_context_mismatch"},
                },
            },
        ),
    )

    replay = TranscriptReplay(entries).build()
    blocks = history._render_replay_blocks(replay, terminal_width=80)
    displays = tuple(
        "".join(text for _style, text in block.display_block.fragments)
        for block in blocks
    )

    assert displays == (
        "• Edited sample.py (+1 -1)\n"
        "    1 -old\n"
        "    1 +new",
        "✘ Failed to apply patch\n"
        "  reason: patch_context_mismatch",
    )


def test_history_tool_result_rejects_unwrapped_payload() -> None:
    with pytest.raises(TypeError, match="result ok must be a boolean"):
        history._transcript_tool_result(
            {"reason": "patch_context_mismatch"},
            expected_ok=False,
            error=None,
        )


def test_history_transcript_keeps_javascript_source_out_of_result_block() -> None:
    source = "const value = 1;"
    entries = (
        TranscriptEntry(
            timestamp="2026-08-02T00:00:00.000Z",
            event="tool.started",
            session_id="session_test",
            turn_id="turn_test",
            actor="tool",
            payload={
                "call_id": "call_js",
                "name": "js_repl",
                "arguments": {"code": source},
            },
        ),
        TranscriptEntry(
            timestamp="2026-08-02T00:00:00.000Z",
            event="tool.completed",
            session_id="session_test",
            turn_id="turn_test",
            actor="tool",
            payload={
                "call_id": "call_js",
                "ok": True,
                "result": {
                    "ok": True,
                    "text": "done",
                    "attachments": [],
                    "data": {"output": "done"},
                },
            },
        ),
    )

    blocks = history.load_history_transcript(
        _history_host(lambda _session_id: entries),
        "session_test",
        terminal_width=80,
    )

    assert [block.kind for block in blocks] == ["operation", "operation"]
    assert blocks[0].raw_text == source
    assert blocks[1].raw_text == "done"
    first_transcript = "".join(
        text for _style, text in blocks[0].transcript_block.fragments
    )
    second_transcript = "".join(
        text for _style, text in blocks[1].transcript_block.fragments
    )
    assert source in first_transcript
    assert source not in second_transcript
    assert "done" in second_transcript


def test_history_generic_tool_reads_current_result_envelope_text() -> None:
    entry = TranscriptEntry(
        timestamp="2026-09-05T00:00:00.000Z",
        event="tool.completed",
        session_id="session_generic",
        turn_id="turn_generic",
        actor="tool",
        payload={
            "call_id": "call_generic",
            "name": "external_lookup",
            "arguments": {"query": "current contract"},
            "ok": True,
            "result": {
                "ok": True,
                "text": "lookup complete",
                "attachments": [],
                "data": {"items": [{"title": "result"}]},
            },
        },
    )

    blocks = history._render_replay_blocks((entry,), terminal_width=80)
    display = "".join(
        text for _style, text in blocks[0].display_block.fragments
    )

    assert "lookup complete" in display
    assert '"attachments"' not in display
    assert '"data"' not in display


def test_history_transcript_falls_back_to_legacy_cursor_title() -> None:
    blocks = history.load_history_transcript(
        _history_host(lambda _session_id: ()),
        "session_legacy",
        terminal_width=60,
        record={"title": "legacy question"},
    )

    assert len(blocks) == 1
    assert blocks[0].kind == "user"
    assert blocks[0].prompt == "legacy question"
    assert blocks[0].raw_text == "legacy question"


def test_history_transcript_uses_placeholder_when_content_is_unavailable() -> None:
    blocks = history.load_history_transcript(
        _history_host(lambda _session_id: ()),
        "session_missing",
        terminal_width=60,
    )

    assert len(blocks) == 1
    assert blocks[0].kind == "notice"
    assert blocks[0].raw_text == (
        "Earlier transcript content is unavailable (session_missing)."
    )


@pytest.mark.parametrize(
    ("event", "payload", "expected"),
    [
        ("turn.failed", {"error": "request failed"}, "■ request failed"),
        (
            "turn.incomplete",
            {"error": "max_output_tokens"},
            "■ max_output_tokens",
        ),
        ("turn.interrupted", {}, "Turn interrupted"),
    ],
)
def test_history_transcript_marks_terminal_notices(event, payload, expected) -> None:
    entry = TranscriptEntry(
        timestamp="2026-08-02T00:00:00.000Z",
        event=event,
        session_id="session_notice",
        turn_id="turn_notice",
        actor="system",
        payload=payload,
    )

    blocks = history.load_history_transcript(
        _history_host(lambda _session_id: (entry,)),
        "session_notice",
        terminal_width=60,
    )

    assert "".join(
        text for _style, text in blocks[0].display_block.fragments
    ) == expected


def test_history_transcript_restores_markdown_hyperlink_metadata() -> None:
    entry = TranscriptEntry(
        timestamp="2026-08-02T00:00:00.000Z",
        event="message.created",
        session_id="session_link",
        turn_id="turn_link",
        actor="assistant",
        payload={"content": "[docs](https://example.com/docs)"},
    )

    linked = history.load_history_transcript(
        _history_host(lambda _session_id: (entry,)),
        "session_link",
        terminal_width=60,
        hyperlinks=True,
    )
    plain = history.load_history_transcript(
        _history_host(lambda _session_id: (entry,)),
        "session_link",
        terminal_width=60,
        hyperlinks=False,
    )

    assert linked[0].raw_text == "[docs](https://example.com/docs)"
    assert any(
        terminal_hyperlink_from_style(style) == "https://example.com/docs"
        for style, _text in linked[0].transcript_block.fragments
    )
    assert all(
        style != "[ZeroWidthEscape]"
        for style, _text in plain[0].transcript_block.fragments
    )


def test_history_transcript_restores_responsive_markdown_tables() -> None:
    source = (
        "| Name | Status | Description |\n"
        "|---|---|---|\n"
        "| API | Ready | Service is available |"
    )
    entry = TranscriptEntry(
        timestamp="2026-08-02T00:00:00.000Z",
        event="message.created",
        session_id="session_table",
        turn_id="turn_table",
        actor="assistant",
        payload={"content": source},
    )

    cell = history.load_history_transcript(
        _history_host(lambda _session_id: (entry,)),
        "session_table",
        terminal_width=24,
    )[0]
    narrow = "".join(text for _style, text in cell.display_block.fragments)

    assert cell.source_renderer is not None
    assert cell.source_render_width == 24
    assert "  Description" in narrow
    assert all(get_cwidth(line) <= 24 for line in narrow.splitlines())

    wide = cell.source_renderer(source, 60)
    wide_text = "".join(text for _style, text in wide.fragments)

    assert "━" in wide_text
    assert "Name  Status" in wide_text


def test_history_shell_display_reflows_without_changing_transcript() -> None:
    command = "python3 -c \"print('" + "界" * 80 + "')\""
    output_lines = [
        f"output {index} " + "👩\u200d💻" * 30
        for index in range(8)
    ]

    def entry(event: str, payload: dict) -> TranscriptEntry:
        return TranscriptEntry(
            timestamp="2026-08-02T00:00:00.000Z",
            event=event,
            session_id="session_shell_resize",
            turn_id="turn_shell_resize",
            actor="tool",
            payload=payload,
        )

    entries = (
        entry("tool.started", {
            "call_id": "call_shell_resize",
            "name": "shell_command",
            "arguments": {"command": command},
        }),
        entry("tool.completed", {
            "call_id": "call_shell_resize",
            "result": {
                "ok": True,
                "text": "\n".join(output_lines),
                "attachments": [],
                "data": {
                    "command": command,
                    "output_lines": output_lines,
                },
            },
        }),
    )

    blocks = history.load_history_transcript(
        _history_host(lambda _session_id: entries),
        "session_shell_resize",
        terminal_width=20,
    )
    document = TuiDocument()
    document.replace_blocks(blocks)

    narrow = fragments_text(document.fragments(width=20))
    transcript = fragments_text(document.transcript_fragments(width=80))
    wide = fragments_text(document.fragments(width=80))

    assert wide != narrow
    assert get_cwidth(narrow.splitlines()[0]) <= 20
    assert get_cwidth(wide.splitlines()[0]) <= 80
    assert get_cwidth(wide.splitlines()[0]) > get_cwidth(
        narrow.splitlines()[0]
    )
    assert "output 0" in narrow
    assert "output 7" in narrow
    assert "output 7" in wide
    assert "output 2" not in narrow
    assert "output 2" not in wide
    assert "… +4 lines" in narrow
    assert "… +4 lines" in wide
    assert fragments_text(document.transcript_fragments(width=80)) == transcript
    assert command in transcript
    assert output_lines[-1] in transcript
