# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import ANY, Mock

import pytest

from agent.application import (
    ModelCapabilityError,
    ModelStreamRequest,
)
from agent.adapters import protocol_client as model_adapter
from agent.composition import open_model_capability
from mind_nova.tool_approval import (
    ToolApprovalSnapshot,
    ToolApprovalSnapshotItem,
)


def _model_event(event_type: str = "turn.start") -> SimpleNamespace:
    return SimpleNamespace(
        type=event_type,
        proto="mind.chat",
        cid="cid_test",
        sid="sid_test",
        turn_id="turn_test",
        event_seq=1,
        presentation_epoch=1,
    )


def _environment_snapshot() -> dict:
    return {
        "snapshot_id": "envsnap_test",
        "source": "client",
        "captured_at": "2026-08-29T12:00:00Z",
        "environment_id": "local",
        "cwd": "D:\\workspace\\project",
        "status": "available",
        "status_detail": None,
        "shell": {
            "name": "powershell",
            "syntax": "powershell",
            "executable": "powershell.exe",
            "prefix": ["powershell.exe", "-NoProfile", "-Command"],
            "source": "path",
        },
        "workspace": {
            "root": "D:\\workspace",
            "allowed_roots": [],
            "source": "client",
        },
        "tools": {},
        "providers": {},
        "extensions": {},
    }


def _request(*, turn_id: str = "turn_test") -> ModelStreamRequest:
    return ModelStreamRequest(
        cid="cid_test",
        sid="sid_test",
        turn_id=turn_id,
        pref_config={"primary": {"model": "test-model"}},
        message="inspect",
        tools=({"name": "read_file"},),
        attachments=({"path": "screen.png"},),
        environment_snapshot=_environment_snapshot(),
        metadata={"origin": "test"},
        options={
            "permissions": {
                "sandbox_mode": "workspace-write",
                "approval_policy": "on-request",
                "approvals_reviewer": "user",
            },
        },
        timeout=12.0,
    )


def test_model_stream_request_freezes_and_copies_protocol_values() -> None:
    metadata = {"origin": {"source": "test"}}
    options = {"permissions": {"sandbox_mode": "read-only"}}
    environment_snapshot = _environment_snapshot()
    request = ModelStreamRequest(
        cid="cid_test",
        sid="sid_test",
        turn_id="turn_test",
        pref_config={},
        message="inspect",
        tools=({"name": "read_file"},),
        environment_snapshot=environment_snapshot,
        metadata=metadata,
        options=options,
    )
    options["permissions"]["sandbox_mode"] = "danger-full-access"
    metadata["origin"]["source"] = "changed"
    environment_snapshot["cwd"] = "D:\\changed"

    assert request.option_values() == {
        "permissions": {"sandbox_mode": "read-only"},
    }
    returned = request.option_values()
    returned["permissions"]["sandbox_mode"] = "mutated"
    assert request.option_values() == {
        "permissions": {"sandbox_mode": "read-only"},
    }
    assert request.metadata_value() == {"origin": {"source": "test"}}
    assert request.environment_snapshot_value()["cwd"] == "D:\\workspace\\project"
    returned_environment = request.environment_snapshot_value()
    returned_environment["cwd"] = "D:\\mutated"
    assert request.environment_snapshot_value()["cwd"] == "D:\\workspace\\project"


def test_model_stream_request_rejects_runtime_objects() -> None:
    with pytest.raises(TypeError, match="non-serializable"):
        ModelStreamRequest(
            cid="cid_test",
            sid="sid_test",
            turn_id="turn_test",
            pref_config={},
            message="inspect",
            tools=(),
            options={"callback": lambda: None},
        )


def test_model_stream_request_rejects_reserved_options() -> None:
    with pytest.raises(ValueError, match="reserved fields: timeout"):
        ModelStreamRequest(
            cid="cid_test",
            sid="sid_test",
            turn_id="turn_test",
            pref_config={},
            message="inspect",
            tools=(),
            options={"timeout": 1.0},
        )


def test_model_stream_request_rejects_exec_env_in_generic_options() -> None:
    with pytest.raises(ValueError, match="reserved fields: exec_env"):
        ModelStreamRequest(
            cid="cid_test",
            sid="sid_test",
            turn_id="turn_test",
            pref_config={},
            message="inspect",
            tools=(),
            options={"exec_env": _environment_snapshot()},
        )


def test_model_stream_request_rejects_coordinates_in_metadata() -> None:
    with pytest.raises(ValueError, match="reserved fields: cid, turn_id"):
        ModelStreamRequest(
            cid="cid_test",
            sid="sid_test",
            turn_id="turn_test",
            pref_config={},
            message="inspect",
            tools=(),
            metadata={"cid": "other", "turn_id": "other"},
        )


def test_protocol_client_translates_frozen_request(monkeypatch) -> None:
    event_stream = object()
    stream_chat = Mock(return_value=event_stream)
    reconnect = Mock()
    approval = Mock()
    monkeypatch.setattr(model_adapter, "stream_chat", stream_chat)

    capability = open_model_capability()
    result = capability.stream(
        _request(),
        on_reconnect_status=reconnect,
        on_approval_snapshot=approval,
    )

    assert isinstance(result, model_adapter.ProtocolModelEventStream)
    stream_chat.assert_called_once_with(
        {"primary": {"model": "test-model"}},
        "inspect",
        [{"name": "read_file"}],
        attachments=[{"path": "screen.png"}],
        timeout=12.0,
        on_reconnect_status=reconnect,
        on_approval_snapshot=ANY,
        initial_event_seq=0,
        exec_env=_environment_snapshot(),
        metadata={
            "origin": "test",
            "cid": "cid_test",
            "sid": "sid_test",
        },
        permissions={
            "sandbox_mode": "workspace-write",
            "approval_policy": "on-request",
            "approvals_reviewer": "user",
        },
        turn_id="turn_test",
    )
    snapshot_callback = stream_chat.call_args.kwargs["on_approval_snapshot"]
    assert callable(snapshot_callback)
    assert snapshot_callback is not approval


@pytest.mark.anyio
async def test_protocol_client_reuses_settled_session_cursor(monkeypatch) -> None:
    class RawStream:
        def __init__(self, *, turn_id: str, last_event_seq: int) -> None:
            self.turn_id = turn_id
            self.last_event_seq = last_event_seq
            self.end_reason = None
            self.closed = False

        def __aiter__(self):
            return self._iterate()

        async def _iterate(self):
            yield SimpleNamespace(
                type="turn.logical_settled",
                proto="mind.chat",
                cid="cid_test",
                sid="sid_test",
                turn_id=self.turn_id,
                event_seq=self.last_event_seq,
                presentation_epoch=1,
            )
            self.end_reason = "settled"

        async def aclose(self) -> None:
            self.closed = True

    first_raw = RawStream(turn_id="turn_test", last_event_seq=9)
    second_raw = RawStream(turn_id="turn_next", last_event_seq=12)
    stream_chat = Mock(side_effect=(first_raw, second_raw))
    monkeypatch.setattr(model_adapter, "stream_chat", stream_chat)
    client = model_adapter.MindChatProtocolClient()

    first_stream = client.stream(_request())
    assert [event.event_seq async for event in first_stream] == [9]
    second_stream = client.stream(_request(turn_id="turn_next"))
    await second_stream.aclose()

    assert stream_chat.call_args_list[0].kwargs["initial_event_seq"] == 0
    assert stream_chat.call_args_list[1].kwargs["initial_event_seq"] == 9
    assert first_raw.closed is True


@pytest.mark.anyio
async def test_protocol_stream_exposes_canonical_item_projection(monkeypatch) -> None:
    class RawStream:
        end_reason = None
        last_event_seq = 3

        def __aiter__(self):
            return self._iterate()

        async def _iterate(self):
            for event_type, event_seq, values in (
                ("text.delta", 1, {"text": "partial"}),
                ("text.done", 2, {"final_text": "complete"}),
                ("turn.logical_settled", 3, {}),
            ):
                projection = (
                    {
                        "item_id": "segment_test",
                        "item_kind": "text",
                        "item_status": (
                            "in_progress"
                            if event_type == "text.delta"
                            else "completed"
                        ),
                        "segment_id": "segment_test",
                    }
                    if event_type.startswith("text.")
                    else {}
                )
                yield SimpleNamespace(
                    type=event_type,
                    proto="mind.chat",
                    cid="cid_test",
                    sid="sid_test",
                    turn_id="turn_test",
                    event_seq=event_seq,
                    presentation_epoch=1,
                    round=1,
                    **projection,
                    **values,
                )
            self.end_reason = "settled"

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(model_adapter, "stream_chat", Mock(return_value=RawStream()))
    stream = model_adapter.MindChatProtocolClient().stream(_request())
    observed_items = []
    observed_sequences = []
    async for event in stream:
        observed_sequences.append(event.event_seq)
        observed_items.append(
            stream.current_item.item_id
            if stream.current_item is not None
            else None
        )

    assert observed_sequences == [1, 2, 3]
    assert observed_items == ["segment_test", "segment_test", None]

    assert stream.assistant_text == "complete"
    assert len(stream.canonical_items) == 1
    assert stream.canonical_items[0].payload_value() == {"text": "complete"}
    assert stream.canonical_item_history == stream.canonical_items
    assert stream.sources == ()


@pytest.mark.anyio
async def test_protocol_stream_accepts_nonpersistent_retained_gap() -> None:
    class RawStream:
        end_reason = "settled"
        last_event_seq = 5

        def __aiter__(self):
            return self._iterate()

        async def _iterate(self):
            yield SimpleNamespace(
                type="stream.gap",
                proto="",
                cid="cid_test",
                sid="sid_test",
                turn_id="turn_test",
                event_seq=None,
                presentation_epoch=1,
            )

        async def aclose(self) -> None:
            return None

    stream = model_adapter.ProtocolModelEventStream(
        RawStream(),
        cid="cid_test",
        sid="sid_test",
        turn_id="turn_test",
        event_cursors=model_adapter.ProtocolEventCursorStore(),
    )

    assert [event.type async for event in stream] == ["stream.gap"]
    assert stream.canonical_items == ()


@pytest.mark.anyio
async def test_protocol_client_reduces_approval_snapshot_before_callback(
    monkeypatch,
) -> None:
    class RawStream:
        end_reason = None
        last_event_seq = 0

        def __aiter__(self):
            return self._iterate()

        async def _iterate(self):
            if False:
                yield None

        async def aclose(self) -> None:
            return None

    stream_chat = Mock(return_value=RawStream())
    observed_pending: list[tuple[str, ...]] = []

    async def external_callback(_snapshot: object) -> None:
        observed_pending.append(tuple(
            item.item_id for item in stream.pending_approval_items
        ))

    monkeypatch.setattr(model_adapter, "stream_chat", stream_chat)
    event_cursors = model_adapter.ProtocolEventCursorStore()
    stream = model_adapter.MindChatProtocolClient(event_cursors).stream(
        _request(),
        on_approval_snapshot=external_callback,
    )
    snapshot = ToolApprovalSnapshot(
        cid="cid_test",
        sid="sid_test",
        turn_id="turn_test",
        turn_status="waiting_approval",
        turn_settled=False,
        last_event_seq=4,
        approvals=(ToolApprovalSnapshotItem(
            approval_id="approval_test",
            turn_id="turn_test",
            call_id="call_test",
            kind="command",
            approval={
                "type": "tool.approval_required",
                "item_id": "approval_test",
                "item_kind": "approval",
                "item_status": "waiting_approval",
                "event_seq": 3,
                "presentation_epoch": 1,
                "approval_id": "approval_test",
                "call_id": "call_test",
                "kind": "command",
            },
            status="pending",
            ack=None,
        ),),
    )

    await stream_chat.call_args.kwargs["on_approval_snapshot"](snapshot)

    assert observed_pending == [("approval_test",)]
    assert [item.item_id for item in stream.pending_approval_items] == [
        "approval_test",
    ]
    assert stream.canonical_items[0].payload_value()["snapshot_status"] == (
        "pending"
    )
    assert event_cursors.current(cid="cid_test", sid="sid_test") == 0


@pytest.mark.anyio
async def test_protocol_client_does_not_commit_fatal_stream_cursor(monkeypatch) -> None:
    class RawStream:
        end_reason = "fatal"
        last_event_seq = 8

        def __aiter__(self):
            return self._iterate()

        async def _iterate(self):
            raise TimeoutError("read timed out")
            yield

        async def aclose(self) -> None:
            return None

    stream_chat = Mock(side_effect=(RawStream(), RawStream()))
    monkeypatch.setattr(model_adapter, "stream_chat", stream_chat)
    client = model_adapter.MindChatProtocolClient()

    with pytest.raises(ModelCapabilityError):
        async for _event in client.stream(_request()):
            pass
    await client.stream(_request(turn_id="turn_next")).aclose()

    assert stream_chat.call_args_list[0].kwargs["initial_event_seq"] == 0
    assert stream_chat.call_args_list[1].kwargs["initial_event_seq"] == 0


@pytest.mark.anyio
async def test_protocol_model_event_stream_normalizes_iteration_failure_and_closes() -> None:
    class RawStream:
        end_reason = "fatal"
        last_event_seq = 9

        def __init__(self) -> None:
            self.closed = False

        def __aiter__(self):
            return self._iterate()

        async def _iterate(self):
            yield _model_event()
            raise TimeoutError("read timed out")

        async def aclose(self) -> None:
            self.closed = True

    raw = RawStream()
    stream = model_adapter.ProtocolModelEventStream(
        raw,
        cid="cid_test",
        sid="sid_test",
        turn_id="turn_test",
        event_cursors=model_adapter.ProtocolEventCursorStore(),
    )

    with pytest.raises(ModelCapabilityError) as captured:
        async for _event in stream:
            pass

    error = captured.value
    assert error.code == "model_transport_timeout"
    assert error.retryable is True
    assert error.details == {"exception_type": "TimeoutError"}
    assert raw.closed is True
    assert stream.last_event_seq == 9


@pytest.mark.anyio
async def test_protocol_model_event_stream_rejects_invalid_event_and_closes() -> None:
    class RawStream:
        end_reason = "protocol_error"
        last_event_seq = 0

        def __init__(self) -> None:
            self.closed = False

        def __aiter__(self):
            return self._iterate()

        async def _iterate(self):
            yield {"type": "turn.start"}

        async def aclose(self) -> None:
            self.closed = True

    raw = RawStream()
    stream = model_adapter.ProtocolModelEventStream(
        raw,
        cid="cid_test",
        sid="sid_test",
        turn_id="turn_test",
        event_cursors=model_adapter.ProtocolEventCursorStore(),
    )

    with pytest.raises(ModelCapabilityError) as captured:
        async for _event in stream:
            pass

    error = captured.value
    assert error.code == "model_protocol_error"
    assert error.retryable is False
    assert error.details == {"exception_type": "TypeError"}
    assert raw.closed is True


@pytest.mark.anyio
async def test_protocol_model_event_stream_rejects_invalid_event_coordinates() -> None:
    class RawStream:
        end_reason = "protocol_error"
        last_event_seq = 0

        def __init__(self) -> None:
            self.closed = False

        def __aiter__(self):
            return self._iterate()

        async def _iterate(self):
            yield SimpleNamespace(
                type="turn.start",
                proto="mind.chat",
                cid="cid_test",
                sid="sid_test",
                turn_id="turn_test",
                event_seq=True,
                presentation_epoch=1,
            )

        async def aclose(self) -> None:
            self.closed = True

    raw = RawStream()
    stream = model_adapter.ProtocolModelEventStream(
        raw,
        cid="cid_test",
        sid="sid_test",
        turn_id="turn_test",
        event_cursors=model_adapter.ProtocolEventCursorStore(),
    )

    with pytest.raises(ModelCapabilityError) as captured:
        async for _event in stream:
            pass

    error = captured.value
    assert error.code == "model_protocol_error"
    assert error.retryable is False
    assert error.details == {"exception_type": "ValueError"}
    assert raw.closed is True


def test_model_capability_error_freezes_details() -> None:
    details = {"status_code": 503, "nested": {"attempt": 1}}
    error = ModelCapabilityError(
        "model_transport_http_error",
        "service unavailable",
        retryable=True,
        details=details,
    )
    details["nested"]["attempt"] = 2

    assert error.to_dict() == {
        "code": "model_transport_http_error",
        "message": "service unavailable",
        "retryable": True,
        "details": {"status_code": 503, "nested": {"attempt": 1}},
    }
