# -*- coding: utf-8 -*-

import asyncio
import functools
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.adapters.protocol import compaction as compact_protocol
from agent.harness.execution import compaction as compact_mode
from agent.harness.hooks.runtime import HookRuntime
from agent.harness.hooks.scope import HookExecutionScope
from frontends.tui.features import conversation
from infrastructure.hooks.discovery import resolve_hook_definitions
from infrastructure.platform.hook_command import HookCommandOutput
from agent.domain.policies import preset_permissions
from protocol.schema.stream_events import parse_compact_event
from protocol.schema.stream_events import parse_stream_event


def _compact_event(status="completed", *, before_items=None, after_items=None):
    payload = {
        "proto": "mind.chat",
        "type": f"context.compaction.{status}",
        "cid": "cid",
        "sid": "sid",
        "turn_id": "compact-turn-1",
        "item_id": "compaction-1",
        "item_kind": "context_compaction",
        "item_status": status,
        "phase": "standalone",
        "trigger": "manual",
        "reason": "summary_failed" if status == "failed" else "user_requested",
        "presentation_epoch": 1,
        "before_items": before_items,
        "after_items": after_items,
    }
    if status == "failed":
        payload.update(error_type="summary_failed", retryable=True)
    return parse_compact_event(payload)


def _compact_hooks():
    return {
        "PreCompact": [{
            "hooks": [{"type": "command", "command": "guard"}],
            "matcher": "manual",
        }],
        "PostCompact": [{
            "hooks": [{"type": "command", "command": "audit"}],
            "matcher": "manual",
        }],
    }


class _RecordingHookRunner(object):
    def __init__(self, outputs=None) -> None:
        self.calls = []
        self.outputs = dict(outputs or {})

    async def execute(self, definition, payload):
        self.calls.append((definition.event, payload))
        return HookCommandOutput(data=self.outputs.get(definition.event, {}))


class _DiscardTranscriptWriter(object):
    def open(self) -> None:
        return None

    def append(self, *_args, **_kwargs) -> None:
        return None

    def close(self) -> None:
        return None


class _TranscriptStore(object):
    def path_for_session(self, _session_id: str) -> str:
        return "D:/sessions/session.jsonl"

    def writer(self, *_args, **_kwargs) -> _DiscardTranscriptWriter:
        return _DiscardTranscriptWriter()


class _CompactionSession:
    """把测试宿主的会话状态适配为压缩用例端口。"""

    def __init__(self, host) -> None:
        self._host = host

    @property
    def workspace_root(self) -> str:
        return str(self._host.history_workspace)

    @property
    def permissions(self):
        return self._host.permissions

    @property
    def transcript_factory(self):
        return self._host.transcripts.writer

    def snapshot(self):
        return self._host.conversation.snapshot()

    def transcript_path_for_session(self, sid):
        return self._host.transcripts.path_for_session(sid)

    @property
    def hook_scope_provider(self):
        return self._host

    async def await_cleanup(self, awaitable):
        return await self._host.await_cleanup(awaitable)

    def queue_turn_context(self, contexts):
        self._host.conversation.queue_turn_context(contexts)

    def record_context_usage(self, record):
        self._host.conversation.record_context_usage(record)


async def _compact(host, **kwargs):
    return await compact_mode.compact_conversation(
        _CompactionSession(host),
        compact_protocol.ProtocolCompactionClient(),
        **kwargs,
    )


class _HookedCompactHost(object):
    def __init__(self, tmp_path, runtime) -> None:
        self.history_workspace = str(tmp_path)
        self.permissions = preset_permissions("auto")
        self.transcripts = _TranscriptStore()
        self.conversation = SimpleNamespace(
            snapshot=lambda: {"cid": "cid", "sid": "sid"},
            queue_turn_context=lambda *_args, **_kwargs: None,
        )
        self._runtime = runtime

    async def await_cleanup(self, awaitable):
        return await awaitable

    def hook_scope(self, context):
        return HookExecutionScope(context=context, dispatcher=self._runtime)


def _compact_hook_runtime(tmp_path, runner, hooks=None) -> HookRuntime:
    definitions = resolve_hook_definitions(
        hooks if hooks is not None else _compact_hooks(),
        source_scope="user",
        source_path=tmp_path / "config.toml",
    )
    return HookRuntime(definitions, command_runner=runner)


@pytest.mark.anyio
async def test_compact_empty_stream_finishes_failed_activity_status(monkeypatch) -> None:
    snapshots = []

    async def empty_stream(_payload):
        if False:
            yield {}

    class ConversationStub(object):
        def snapshot(self):
            return {"cid": "cid", "sid": "sid"}

    class ApplicationHostStub(object):
        conversation = ConversationStub()
        history_workspace = "."
        permissions = preset_permissions("auto")
        transcripts = _TranscriptStore()

        def __init__(self):
            self.views = []
            self.activity = SimpleNamespace(
                enabled=True,
                start_compact=self._start_compact,
            )
            self.frontend = SimpleNamespace(
                application=SimpleNamespace(
                    emit=self.views.append,
                    viewport=SimpleNamespace(width=80),
                ),
            )

        async def _start_compact(self, snapshot):
            snapshots.append(snapshot)

        async def await_cleanup(self, awaitable):
            return await awaitable

        def hook_scope(self, context):
            return HookExecutionScope.empty(context)

    monkeypatch.setattr(compact_protocol, "stream_compact_events", empty_stream)

    host = ApplicationHostStub()
    status = await conversation.compact_current_conversation(
        host,
        functools.partial(_compact, host),
        pref_config={},
    )
    conversation.render_compact_result(host, status)

    final = snapshots[0]()
    assert final["done"] is True
    assert final["summary"] == "Context compaction failed. Please try again."
    assert final["detail_limit"] == 0
    status = next(view for view in host.views if view.type == "tui.compact.status")
    assert status.renderable.plain_text == (
        "■ Context compaction failed. Please try again."
    )


@pytest.mark.anyio
async def test_compact_success_is_committed_to_tui(monkeypatch) -> None:
    closed = []
    async def completed_stream(_payload):
        try:
            yield parse_stream_event({
                "type": "context.usage.updated", "proto": "mind.chat",
                "cid": "cid", "sid": "sid", "turn_id": "",
                "event_seq": 11, "presentation_epoch": 1,
                "context_usage": {
                    "model_context_window": 100_000,
                    "last_token_usage": {"total_tokens": 13_000},
                    "total_token_usage": {"total_tokens": 250_000},
                    "usage_source": "estimate", "model": "test-model", "route": "responses",
                },
            })
            yield _compact_event(before_items=18, after_items=6)
        finally:
            closed.append(True)

    class ApplicationHostStub(object):
        transcripts = _TranscriptStore()
        history_workspace = "."
        permissions = preset_permissions("auto")
        conversation = SimpleNamespace(
            snapshot=lambda: {"cid": "cid", "sid": "sid"},
            record_context_usage=Mock(),
        )

        def __init__(self):
            self.views = []
            self.activity = SimpleNamespace(enabled=False)
            self.frontend = SimpleNamespace(
                application=SimpleNamespace(
                    emit=self.views.append,
                    viewport=SimpleNamespace(width=80),
                ),
            )

        async def await_cleanup(self, awaitable):
            return await awaitable

        def hook_scope(self, context):
            return HookExecutionScope.empty(context)

    monkeypatch.setattr(compact_protocol, "stream_compact_events", completed_stream)

    host = ApplicationHostStub()
    result = await conversation.compact_current_conversation(
        host,
        functools.partial(_compact, host),
        pref_config={},
    )
    conversation.render_compact_result(host, result)

    record = host.conversation.record_context_usage.call_args.args[0]
    assert record.last_total_tokens == 13_000
    assert record.total_tokens == 250_000
    assert closed == [True]

    status = next(view for view in host.views if view.type == "tui.compact.status")
    assert status.renderable.plain_text == (
        "■ Context compacted. · 18 -> 6 items"
    )


@pytest.mark.anyio
async def test_compact_cancellation_clears_animation_without_failure(
    monkeypatch,
) -> None:
    started = asyncio.Event()

    async def pending_stream(_payload):
        started.set()
        await asyncio.Future()
        if False:
            yield {}

    class ApplicationHostStub(object):
        transcripts = _TranscriptStore()
        history_workspace = "."
        permissions = preset_permissions("auto")
        conversation = SimpleNamespace(
            snapshot=lambda: {"cid": "cid", "sid": "sid"},
        )

        def __init__(self):
            self.views = []
            self.stopped = []
            self.activity = SimpleNamespace(
                enabled=True,
                start_compact=self._start_compact,
            )
            self.frontend = SimpleNamespace(
                application=SimpleNamespace(
                    emit=self.views.append,
                    viewport=SimpleNamespace(width=80),
                ),
            )

        async def _start_compact(self, _snapshot):
            return None

        async def await_cleanup(self, awaitable):
            return await awaitable

        def hook_scope(self, context):
            return HookExecutionScope.empty(context)

    monkeypatch.setattr(compact_protocol, "stream_compact_events", pending_stream)

    host = ApplicationHostStub()
    task = asyncio.create_task(conversation.compact_current_conversation(
        host,
        functools.partial(_compact, host),
        pref_config={},
    ))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    conversation.render_compact_interrupted(host)

    assert host.stopped == []
    assert any(view.type == "tui.compact.interrupted" for view in host.views)
    interrupted = next(
        view for view in host.views
        if view.type == "tui.compact.interrupted"
    )
    assert "".join(
        text for _style, text in interrupted.renderable.fragments
    ) == "• Context compaction · interrupted"
    assert not any(view.type == "tui.compact.status" for view in host.views)


def test_fork_interruption_uses_the_shared_neutral_prefix() -> None:
    views = []
    host = SimpleNamespace(
        frontend=SimpleNamespace(
            application=SimpleNamespace(
                emit=views.append,
                viewport=SimpleNamespace(width=80),
            ),
        ),
    )

    conversation.render_fork_interrupted(host)

    assert "".join(
        text for _style, text in views[0].renderable.fragments
    ) == "• Conversation fork · interrupted"


@pytest.mark.anyio
async def test_pre_compact_hook_blocks_remote_operation(monkeypatch, tmp_path) -> None:
    remote_calls = []

    async def remote_stream(_payload):
        remote_calls.append(True)
        if False:
            yield {}

    definitions = resolve_hook_definitions(
        _compact_hooks(),
        source_scope="user",
        source_path=tmp_path / "config.toml",
    )

    class Runner(object):
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, definition, payload):
            self.calls.append((definition.event, payload))
            return HookCommandOutput(data={
                "continue": False,
                "stopReason": "keep current context",
            })

    runner = Runner()
    runtime = HookRuntime(definitions, command_runner=runner)

    class ApplicationHostStub(object):
        transcripts = _TranscriptStore()
        history_workspace = str(tmp_path)
        permissions = preset_permissions("auto")
        conversation = SimpleNamespace(
            snapshot=lambda: {"cid": "cid", "sid": "sid"},
        )

        async def await_cleanup(self, awaitable):
            return await awaitable

        def hook_scope(self, context):
            return HookExecutionScope(context=context, dispatcher=runtime)

    monkeypatch.setattr(compact_protocol, "stream_compact_events", remote_stream)

    result = await _compact(
        ApplicationHostStub(),
        pref_config={},
        source="test",
    )

    assert not result.ok
    assert result.message == "Context compaction blocked: keep current context"
    assert not remote_calls
    assert [event for event, _payload in runner.calls] == ["PreCompact"]


@pytest.mark.anyio
async def test_pre_compact_hook_failure_does_not_block(monkeypatch, tmp_path) -> None:
    remote_calls = []

    async def remote_stream(_payload):
        remote_calls.append(True)
        yield _compact_event()

    class Runner(object):
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, definition, payload):
            self.calls.append((definition.event, payload))
            if definition.event == "PreCompact":
                raise RuntimeError("compact hook failed")
            return HookCommandOutput(data={})

    runner = Runner()
    runtime = _compact_hook_runtime(tmp_path, runner)
    host = _HookedCompactHost(tmp_path, runtime)
    monkeypatch.setattr(compact_protocol, "stream_compact_events", remote_stream)

    result = await _compact(
        host,
        pref_config={},
        source="test",
    )

    assert result.ok
    assert remote_calls == [True]
    assert [event for event, _payload in runner.calls] == [
        "PreCompact",
        "PostCompact",
    ]


@pytest.mark.anyio
async def test_compact_hooks_share_operation_scope(monkeypatch, tmp_path) -> None:
    async def remote_stream(_payload):
        yield _compact_event(before_items=12, after_items=4)

    definitions = resolve_hook_definitions(
        _compact_hooks(),
        source_scope="user",
        source_path=tmp_path / "config.toml",
    )

    class Runner(object):
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, definition, payload):
            self.calls.append((definition.event, payload))
            return HookCommandOutput(data={})

    runner = Runner()
    runtime = HookRuntime(definitions, command_runner=runner)

    class ApplicationHostStub(object):
        transcripts = _TranscriptStore()
        history_workspace = str(tmp_path)
        permissions = preset_permissions("auto")
        conversation = SimpleNamespace(
            snapshot=lambda: {"cid": "cid", "sid": "sid"},
        )

        async def await_cleanup(self, awaitable):
            return await awaitable

        def hook_scope(self, context):
            return HookExecutionScope(context=context, dispatcher=runtime)

    monkeypatch.setattr(compact_protocol, "stream_compact_events", remote_stream)

    result = await _compact(
        ApplicationHostStub(),
        pref_config={"primary": {"model": "test-model"}},
        source="test",
    )

    assert result.ok
    assert [event for event, _payload in runner.calls] == [
        "PreCompact",
        "PostCompact",
    ]
    pre_payload = runner.calls[0][1]
    post_payload = runner.calls[1][1]
    assert pre_payload == {
        "trigger": "manual",
        "session_id": "sid",
        "transcript_path": "D:/sessions/session.jsonl",
        "cwd": str(tmp_path),
        "hook_event_name": "PreCompact",
        "model": "test-model",
        "turn_id": "",
    }
    assert post_payload == {
        **pre_payload,
        "hook_event_name": "PostCompact",
    }


@pytest.mark.anyio
async def test_compact_failure_skips_post_hook(
    monkeypatch,
    tmp_path,
) -> None:
    async def failed_stream(_payload):
        yield _compact_event("failed")

    runner = _RecordingHookRunner()
    host = _HookedCompactHost(
        tmp_path,
        _compact_hook_runtime(tmp_path, runner),
    )
    monkeypatch.setattr(compact_protocol, "stream_compact_events", failed_stream)

    result = await _compact(
        host,
        pref_config={},
        source="test",
    )

    assert result.outcome == "failed"
    assert result.message == "Context compaction failed. Please try again."
    assert [event for event, _payload in runner.calls] == ["PreCompact"]


@pytest.mark.anyio
async def test_compact_cancellation_skips_post_hook(
    monkeypatch,
    tmp_path,
) -> None:
    started = asyncio.Event()

    async def pending_stream(_payload):
        started.set()
        await asyncio.Future()
        if False:
            yield {}

    runner = _RecordingHookRunner()
    host = _HookedCompactHost(
        tmp_path,
        _compact_hook_runtime(tmp_path, runner),
    )
    monkeypatch.setattr(compact_protocol, "stream_compact_events", pending_stream)
    task = asyncio.create_task(_compact(
        host,
        pref_config={},
        source="test",
    ))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert [event for event, _payload in runner.calls] == ["PreCompact"]


@pytest.mark.anyio
async def test_post_compact_hook_controls_next_turn(monkeypatch, tmp_path) -> None:
    async def completed_stream(_payload):
        yield _compact_event(before_items=20, after_items=5)

    queued = []
    runner = _RecordingHookRunner({
        "PostCompact": {
            "continue": False,
            "stopReason": "review compacted state",
            "systemMessage": "Check the compacted summary before proceeding.",
        },
    })
    hooks = {
        **_compact_hooks(),
        "SessionStart": [{
            "matcher": "compact",
            "hooks": [{"type": "command", "command": "refresh"}],
        }],
    }
    host = _HookedCompactHost(
        tmp_path,
        _compact_hook_runtime(tmp_path, runner, hooks),
    )
    host.conversation.queue_turn_context = (
        lambda contexts, *, system_message="": queued.append(
            (tuple(contexts), system_message)
        )
    )
    monkeypatch.setattr(compact_protocol, "stream_compact_events", completed_stream)

    result = await _compact(
        host,
        pref_config={},
        source="test",
        trigger_source="server",
    )

    assert result.outcome == "completed"
    assert not result.ok
    assert not result.continue_execution
    assert result.summary == "Context compacted."
    assert result.message == (
        "Context compacted. Post-compact continuation blocked: "
        "review compacted state"
    )
    assert queued == []
    assert [event for event, _payload in runner.calls] == [
        "PreCompact",
        "PostCompact",
    ]
    assert runner.calls[1][1]["trigger"] == "manual"


@pytest.mark.anyio
async def test_compact_session_start_queues_next_turn_context(
    monkeypatch,
    tmp_path,
) -> None:
    async def completed_stream(_payload):
        yield _compact_event()

    hooks = {
        **_compact_hooks(),
        "SessionStart": [{
            "matcher": "compact",
            "hooks": [{"type": "command", "command": "refresh"}],
        }],
    }
    runner = _RecordingHookRunner({
        "SessionStart": {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": "Use the compacted project summary.",
            },
        },
    })
    host = _HookedCompactHost(
        tmp_path,
        _compact_hook_runtime(tmp_path, runner, hooks),
    )
    queued = []
    host.conversation.queue_turn_context = (
        lambda contexts: queued.append(tuple(contexts))
    )
    monkeypatch.setattr(compact_protocol, "stream_compact_events", completed_stream)

    result = await _compact(
        host,
        pref_config={},
        source="test",
    )

    assert result.ok
    assert [event for event, _payload in runner.calls] == [
        "PreCompact",
        "PostCompact",
        "SessionStart",
    ]
    assert runner.calls[2][1]["source"] == "compact"
    assert queued == [("Use the compacted project summary.",)]


@pytest.mark.anyio
async def test_compact_session_start_can_block_continuation(
    monkeypatch,
    tmp_path,
) -> None:
    async def completed_stream(_payload):
        yield _compact_event()

    hooks = {
        **_compact_hooks(),
        "SessionStart": [{
            "matcher": "compact",
            "hooks": [{"type": "command", "command": "guard"}],
        }],
    }
    runner = _RecordingHookRunner({
        "SessionStart": {
            "continue": False,
            "stopReason": "review compacted state",
        },
    })
    host = _HookedCompactHost(
        tmp_path,
        _compact_hook_runtime(tmp_path, runner, hooks),
    )
    monkeypatch.setattr(compact_protocol, "stream_compact_events", completed_stream)

    result = await _compact(
        host,
        pref_config={},
        source="test",
    )

    assert result.outcome == "completed"
    assert not result.ok
    assert not result.continue_execution
    assert result.message == (
        "Context compacted. Compact session start blocked: "
        "review compacted state"
    )


if __name__ == '__main__':
    pass
