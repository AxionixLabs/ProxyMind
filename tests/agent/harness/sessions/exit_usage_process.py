"""以独立客户端进程验收真实本地存储；远端事实及删除回执使用确定性测试替身。"""

import argparse
import asyncio
import json
from dataclasses import asdict
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from prompt_toolkit.output.plain_text import PlainTextOutput

from agent.adapters.protocol.context_usage import context_usage_record
from agent.ports.session_deletion import LocalDeletionTarget
from frontends.cli.bootstrap import finalize_application
from frontends.tui.core.runtime import TuiRuntime
from infrastructure.persistence.conversation_history import LocalConversationHistory
from protocol.schema.stream_events import (
    ContextUsageUpdatedEvent,
    parse_stream_event,
)
from tests.agent.harness.sessions.test_session_deletion_lifecycle import _Remote
from tests.agent.stores.sessions.deletion_fixture import store
from tests.composition.test_controller_runtime_cleanup import _root_session


async def run(directory: Path, action: str, fixtures: Path) -> None:
    """在显式隔离目录中恢复或删除，再通过正式统一收尾交付快照。"""
    fixtures = fixtures / "protocol"
    payload = json.loads((fixtures / "context_usage.json").read_text(encoding="utf-8"))["event"]
    payload["sid"] = "sid_alpha_m0000000_abcdef"
    payload["context_usage"]["total_token_usage"] = json.loads(
        (fixtures / "session_token_usage.json").read_text(encoding="utf-8"),
    )["complete"]
    event = parse_stream_event(payload)
    assert isinstance(event, ContextUsageUpdatedEvent)
    record = context_usage_record(event)
    owner = LocalDeletionTarget(record.cid, record.sid, ())
    backend = store(directory, owner)
    if action == "seed":
        backend.history.touch_session(cid=record.cid, sid=record.sid, source="tui")
        backend.history.save_context_usage(record)
        path = backend.transcripts.path_for_session(record.sid)
        assert path
        writer = backend.transcripts.writer(path, session_id=record.sid)
        writer.open()
        try:
            writer.append("message.created", actor="assistant", payload={"content": "retained answer"})
        finally:
            writer.close()
        print(json.dumps({"seeded": True}))
        return
    if action == "verify_deleted":
        assert backend.history.load_context_usage(record.cid, record.sid) is None
        assert backend.history.find_session(record.sid) is None
        assert backend.transcripts.existing_path_for_session(record.sid) == ""
        assert backend.transcripts.path_for_session(record.sid) == ""
        print(json.dumps({"deleted": True, "pending": len(backend.pending())}))
        return

    cached = backend.history.load_context_usage(record.cid, record.sid)
    assert cached == record
    session, resources = _root_session()
    session._history = LocalConversationHistory(
        backend.history, existing_transcript_path_for=backend.transcripts.existing_path_for_session,
        transcript_entries_for=lambda path: backend.transcripts.reader(path).read(),
    )
    resources.context_recovery.load.return_value = record
    resources.shutdown_root.return_value = ()
    session._transcript_factory = backend.transcripts.writer
    session._transcript_path_for = backend.transcripts.path_for_session
    session._session_deletion_store, session._session_deletion_remote = backend, _Remote()

    async def end_lifecycle(*_args, **kwargs):
        kwargs["before_dispatch"]()
        return True

    resources.lifecycle.end.side_effect = end_lifecycle
    await session.bind(record.cid, record.sid)
    assert session.turn_count == 0
    if action == "delete":
        assert (await session.delete_current("delete_usage_process")).complete
        assert backend.history.load_context_usage(record.cid, record.sid) is None
    stdout = StringIO()
    runtime = TuiRuntime(output_obj=PlainTextOutput(stdout))
    captured = []
    print_summary = runtime.print_exit_summary
    close_resources = AsyncMock()

    def collect(snapshot):
        assert not runtime.active
        close_resources.assert_awaited_once()
        captured.append(snapshot)
        print_summary(snapshot)

    runtime.print_exit_summary = collect

    async def await_cleanup(awaitable):
        return await awaitable

    host = SimpleNamespace(
        conversation=session, frontend=SimpleNamespace(runtime=runtime),
        lifecycle=SimpleNamespace(exit_code=0, await_cleanup=await_cleanup),
        resources=SimpleNamespace(close=close_resources),
    )
    await finalize_application(host, output_mode="tui", completed=True)
    assert len(captured) == 1
    assert captured[0].record == record
    assert session.context_usage.view.record is None
    assert session.take_exit_snapshot() is None
    print(json.dumps({
        "snapshot": asdict(captured[0]), "rendered": stdout.getvalue(), "turn_count": session.turn_count,
        "history_exists": backend.history.find_session(record.sid) is not None,
        "cache_exists": backend.history.load_context_usage(record.cid, record.sid) is not None,
        "transcript_exists": bool(backend.transcripts.existing_path_for_session(record.sid)),
    }))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("action", choices=("seed", "resume", "delete", "verify_deleted"))
    parser.add_argument("--fixtures", type=Path, required=True)
    arguments = parser.parse_args()
    asyncio.run(run(arguments.directory, arguments.action, arguments.fixtures))
