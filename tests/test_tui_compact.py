# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace

import pytest

from mind_app.modes import compact as compact_mode
from mind_app.runtime.hooks.runtime import HookRuntime
from mind_app.runtime.hooks.scope import HookExecutionScope
from mind_app.tui.features import conversation
from mind_core.hooks import resolve_hook_definitions
from mind_core.permissions import preset_permissions


class _RecordingHookRunner(object):
    def __init__(self) -> None:
        self.calls = []

    async def execute(self, definition, payload):
        self.calls.append((definition.event, payload))
        return SimpleNamespace(data={})


class _HookedCompactMind(object):
    def __init__(self, tmp_path, runtime) -> None:
        self.history_workspace = str(tmp_path)
        self.permissions = preset_permissions("auto")
        self.conversation = SimpleNamespace(
            snapshot=lambda: {"cid": "cid", "sid": "sid"},
        )
        self._runtime = runtime

    async def await_cleanup(self, awaitable):
        await awaitable

    def hook_scope(self, context):
        return HookExecutionScope(context=context, dispatcher=self._runtime)


def _compact_hook_runtime(tmp_path, runner) -> HookRuntime:
    definitions = resolve_hook_definitions(
        {
            "PreCompact": [{
                "command": "guard",
                "matcher": "manual",
            }],
            "PostCompact": [{
                "command": "audit",
                "matcher": "manual",
            }],
        },
        source_scope="user",
        source_path=tmp_path / "hooks.toml",
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
            await awaitable

        def hook_scope(self, context):
            return HookExecutionScope.empty(context)

    monkeypatch.setattr(compact_mode, "stream_compact_events", empty_stream)

    mind = MindStub()
    status = await conversation.compact_current_conversation(
        mind,
        run_mode="chat",
        pref_config={},
    )
    await conversation.finish_compact_activity(mind)
    conversation.render_compact_result(mind, status)

    kind, final = snapshots[-1]
    assert kind == "compact"
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
            await awaitable

        def hook_scope(self, context):
            return HookExecutionScope.empty(context)

    monkeypatch.setattr(compact_mode, "stream_compact_events", completed_stream)

    mind = MindStub()
    result = await conversation.compact_current_conversation(
        mind,
        run_mode="chat",
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
            await awaitable

        def hook_scope(self, context):
            return HookExecutionScope.empty(context)

    monkeypatch.setattr(compact_mode, "stream_compact_events", pending_stream)

    mind = MindStub()
    task = asyncio.create_task(conversation.compact_current_conversation(
        mind,
        run_mode="chat",
        pref_config={},
    ))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    await conversation.finish_compact_activity(mind)
    conversation.render_compact_interrupted(mind)

    assert mind.stopped == [("compact", False)]
    assert any(view.type == "tui.compact.interrupted" for view in mind.views)
    assert not any(view.type == "tui.compact.status" for view in mind.views)


@pytest.mark.anyio
async def test_pre_compact_hook_blocks_remote_operation(monkeypatch, tmp_path) -> None:
    remote_calls = []

    async def remote_stream(_payload):
        remote_calls.append(True)
        if False:
            yield {}

    definitions = resolve_hook_definitions(
        {
            "PreCompact": [{
                "command": "guard",
                "matcher": "manual",
            }],
            "PostCompact": [{
                "command": "audit",
                "matcher": "manual",
            }],
        },
        source_scope="user",
        source_path=tmp_path / "hooks.toml",
    )

    class Runner(object):
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, definition, payload):
            self.calls.append((definition.event, payload))
            return SimpleNamespace(data={
                "continue": False,
                "reason": "keep current context",
            })

    runner = Runner()
    runtime = HookRuntime(definitions, command_runner=runner)

    class MindStub(object):
        history_workspace = str(tmp_path)
        permissions = preset_permissions("auto")
        conversation = SimpleNamespace(
            snapshot=lambda: {"cid": "cid", "sid": "sid"},
        )

        async def await_cleanup(self, awaitable):
            await awaitable

        def hook_scope(self, context):
            return HookExecutionScope(context=context, dispatcher=runtime)

    monkeypatch.setattr(compact_mode, "stream_compact_events", remote_stream)

    result = await compact_mode.compact_conversation(
        MindStub(),
        run_mode="chat",
        pref_config={},
        source="test",
    )

    assert not result.ok
    assert result.message == "Context compaction blocked: keep current context"
    assert not remote_calls
    assert [event for event, _payload in runner.calls] == ["PreCompact"]


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
        {
            "PreCompact": [{
                "command": "guard",
                "matcher": "manual",
            }],
            "PostCompact": [{
                "command": "audit",
                "matcher": "manual",
            }],
        },
        source_scope="user",
        source_path=tmp_path / "hooks.toml",
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
        history_workspace = str(tmp_path)
        permissions = preset_permissions("auto")
        conversation = SimpleNamespace(
            snapshot=lambda: {"cid": "cid", "sid": "sid"},
        )

        async def await_cleanup(self, awaitable):
            await awaitable

        def hook_scope(self, context):
            return HookExecutionScope(context=context, dispatcher=runtime)

    monkeypatch.setattr(compact_mode, "stream_compact_events", remote_stream)

    result = await compact_mode.compact_conversation(
        MindStub(),
        run_mode="xtra",
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
    assert pre_payload["conversation_id"] == "cid"
    assert pre_payload["session_id"] == "sid"
    assert pre_payload["model"] == "test-model"
    assert pre_payload["source"] == "test"
    assert post_payload["outcome"] == "completed"
    assert post_payload["before_items"] == 12
    assert post_payload["after_items"] == 4


@pytest.mark.anyio
async def test_compact_failure_reports_failed_post_hook(
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
        run_mode="chat",
        pref_config={},
        source="test",
    )

    assert result.outcome == "failed"
    assert result.message == "remote compact failed"
    assert [event for event, _payload in runner.calls] == [
        "PreCompact",
        "PostCompact",
    ]
    assert runner.calls[1][1]["outcome"] == "failed"
    assert runner.calls[1][1]["message"] == "remote compact failed"


@pytest.mark.anyio
async def test_compact_cancellation_reports_interrupted_post_hook(
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
        run_mode="chat",
        pref_config={},
        source="test",
    ))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert [event for event, _payload in runner.calls] == [
        "PreCompact",
        "PostCompact",
    ]
    assert runner.calls[1][1]["outcome"] == "interrupted"
    assert runner.calls[1][1]["message"] == "Context compaction interrupted."


if __name__ == '__main__':
    pass
