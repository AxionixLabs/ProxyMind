# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace

import pytest

from mind_app.runtime import compaction as compact_mode
from agent.harness.hooks.runtime import HookRuntime
from mind_app.runtime.hooks.scope import HookExecutionScope
from mind_app.tui.features import conversation
from infrastructure.hooks.discovery import resolve_hook_definitions
from agent.domain.policies import preset_permissions


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
        return SimpleNamespace(data=self.outputs.get(definition.event, {}))


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


class _HookedCompactMind(object):
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

    class MindStub(object):
        animate = True
        conversation = ConversationStub()
        history_workspace = "."
        permissions = preset_permissions("auto")
        transcripts = _TranscriptStore()

        def __init__(self):
            self.views = []
            self.frontend = SimpleNamespace(
                application=SimpleNamespace(emit=self.views.append),
            )

        async def start_compact_anim(self, snapshot):
            snapshots.append(snapshot)

        async def stop_anim(self, kind=None, *, settle=True):
            _ = settle
            snapshots.append((kind, snapshots[0]()))

        async def await_cleanup(self, awaitable):
            return await awaitable

        def hook_scope(self, context):
            return HookExecutionScope.empty(context)

    monkeypatch.setattr(compact_mode, "stream_compact_events", empty_stream)

    mind = MindStub()
    status = await conversation.compact_current_conversation(
        mind,
        pref_config={},
    )
    conversation.render_compact_result(mind, status)

    final = snapshots[0]()
    assert final["done"] is True
    assert final["summary"] == "Context compaction failed. Please try again."
    assert final["detail_limit"] == 0
    status = next(view for view in mind.views if view.type == "tui.compact.status")
    assert status.renderable.plain_text == (
        "■ Context compaction failed. Please try again."
    )


@pytest.mark.anyio
async def test_compact_success_is_committed_to_tui(monkeypatch) -> None:
    async def completed_stream(_payload):
        yield {
            "type": "conversation.compact",
            "message": "Context compacted.",
            "before_items": 18,
            "after_items": 6,
        }

    class MindStub(object):
        transcripts = _TranscriptStore()
        animate = False
        history_workspace = "."
        permissions = preset_permissions("auto")
        conversation = SimpleNamespace(
            snapshot=lambda: {"cid": "cid", "sid": "sid"},
        )

        def __init__(self):
            self.views = []
            self.frontend = SimpleNamespace(
                application=SimpleNamespace(emit=self.views.append),
            )

        async def await_cleanup(self, awaitable):
            return await awaitable

        def hook_scope(self, context):
            return HookExecutionScope.empty(context)

    monkeypatch.setattr(compact_mode, "stream_compact_events", completed_stream)

    mind = MindStub()
    result = await conversation.compact_current_conversation(
        mind,
        pref_config={},
    )
    conversation.render_compact_result(mind, result)

    status = next(view for view in mind.views if view.type == "tui.compact.status")
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

    class MindStub(object):
        transcripts = _TranscriptStore()
        animate = True
        history_workspace = "."
        permissions = preset_permissions("auto")
        conversation = SimpleNamespace(
            snapshot=lambda: {"cid": "cid", "sid": "sid"},
        )

        def __init__(self):
            self.views = []
            self.stopped = []
            self.frontend = SimpleNamespace(
                application=SimpleNamespace(emit=self.views.append),
            )

        async def start_compact_anim(self, _snapshot):
            return None

        async def stop_anim(self, kind=None, *, settle=True):
            self.stopped.append((kind, settle))

        async def await_cleanup(self, awaitable):
            return await awaitable

        def hook_scope(self, context):
            return HookExecutionScope.empty(context)

    monkeypatch.setattr(compact_mode, "stream_compact_events", pending_stream)

    mind = MindStub()
    task = asyncio.create_task(conversation.compact_current_conversation(
        mind,
        pref_config={},
    ))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    conversation.render_compact_interrupted(mind)

    assert mind.stopped == []
    assert any(view.type == "tui.compact.interrupted" for view in mind.views)
    interrupted = next(
        view for view in mind.views
        if view.type == "tui.compact.interrupted"
    )
    assert "".join(
        text for _style, text in interrupted.renderable.fragments
    ) == "• Context compaction · interrupted"
    assert not any(view.type == "tui.compact.status" for view in mind.views)


def test_fork_interruption_uses_the_shared_neutral_prefix() -> None:
    views = []
    mind = SimpleNamespace(
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        ),
    )

    conversation.render_fork_interrupted(mind)

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
            return SimpleNamespace(data={
                "continue": False,
                "stopReason": "keep current context",
            })

    runner = Runner()
    runtime = HookRuntime(definitions, command_runner=runner)

    class MindStub(object):
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

    monkeypatch.setattr(compact_mode, "stream_compact_events", remote_stream)

    result = await compact_mode.compact_conversation(
        MindStub(),
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
        yield {
            "type": "conversation.compact",
            "message": "Context compacted.",
        }

    class Runner(object):
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, definition, payload):
            self.calls.append((definition.event, payload))
            if definition.event == "PreCompact":
                raise RuntimeError("compact hook failed")
            return SimpleNamespace(data={})

    runner = Runner()
    runtime = _compact_hook_runtime(tmp_path, runner)
    mind = _HookedCompactMind(tmp_path, runtime)
    monkeypatch.setattr(compact_mode, "stream_compact_events", remote_stream)

    result = await compact_mode.compact_conversation(
        mind,
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
        yield {
            "type": "conversation.compact",
            "message": "Context compacted.",
            "before_items": 12,
            "after_items": 4,
        }

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
            return SimpleNamespace(data={})

    runner = Runner()
    runtime = HookRuntime(definitions, command_runner=runner)

    class MindStub(object):
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

    monkeypatch.setattr(compact_mode, "stream_compact_events", remote_stream)

    result = await compact_mode.compact_conversation(
        MindStub(),
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
        yield {
            "type": "conversation.compact.failed",
            "message": "remote compact failed",
        }

    runner = _RecordingHookRunner()
    mind = _HookedCompactMind(
        tmp_path,
        _compact_hook_runtime(tmp_path, runner),
    )
    monkeypatch.setattr(compact_mode, "stream_compact_events", failed_stream)

    result = await compact_mode.compact_conversation(
        mind,
        pref_config={},
        source="test",
    )

    assert result.outcome == "failed"
    assert result.message == "remote compact failed"
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
    mind = _HookedCompactMind(
        tmp_path,
        _compact_hook_runtime(tmp_path, runner),
    )
    monkeypatch.setattr(compact_mode, "stream_compact_events", pending_stream)
    task = asyncio.create_task(compact_mode.compact_conversation(
        mind,
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
        yield {
            "type": "conversation.compact",
            "message": "Context compacted.",
            "summary": "Earlier work was summarized.",
            "before_items": 20,
            "after_items": 5,
        }

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
    mind = _HookedCompactMind(
        tmp_path,
        _compact_hook_runtime(tmp_path, runner, hooks),
    )
    mind.conversation.queue_turn_context = (
        lambda contexts, *, system_message="": queued.append(
            (tuple(contexts), system_message)
        )
    )
    monkeypatch.setattr(compact_mode, "stream_compact_events", completed_stream)

    result = await compact_mode.compact_conversation(
        mind,
        pref_config={},
        source="test",
        trigger_source="server",
    )

    assert result.outcome == "completed"
    assert not result.ok
    assert not result.continue_execution
    assert result.summary == "Earlier work was summarized."
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
        yield {
            "type": "conversation.compact",
            "message": "Context compacted.",
        }

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
    mind = _HookedCompactMind(
        tmp_path,
        _compact_hook_runtime(tmp_path, runner, hooks),
    )
    queued = []
    mind.conversation.queue_turn_context = (
        lambda contexts: queued.append(tuple(contexts))
    )
    monkeypatch.setattr(compact_mode, "stream_compact_events", completed_stream)

    result = await compact_mode.compact_conversation(
        mind,
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
        yield {
            "type": "conversation.compact",
            "message": "Context compacted.",
        }

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
    mind = _HookedCompactMind(
        tmp_path,
        _compact_hook_runtime(tmp_path, runner, hooks),
    )
    monkeypatch.setattr(compact_mode, "stream_compact_events", completed_stream)

    result = await compact_mode.compact_conversation(
        mind,
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
