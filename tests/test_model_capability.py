# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.application import (
    ModelCapabilityError,
    ModelStreamRequest,
)
from agent.capabilities import model as model_adapter
from agent.composition import open_model_capability


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


def _request() -> ModelStreamRequest:
    return ModelStreamRequest(
        pref_config={"primary": {"model": "test-model"}},
        message="inspect",
        tools=({"name": "read_file"},),
        attachments=({"path": "screen.png"},),
        options={
            "metadata": {"cid": "cid_test", "sid": "sid_test"},
            "permissions": {
                "sandbox_mode": "workspace-write",
                "approval_policy": "on-request",
                "approvals_reviewer": "user",
            },
        },
        timeout=12.0,
        initial_event_seq=7,
    )


def test_model_stream_request_freezes_and_copies_protocol_values() -> None:
    options = {"metadata": {"cid": "cid_test"}}
    request = ModelStreamRequest(
        pref_config={},
        message="inspect",
        tools=({"name": "read_file"},),
        options=options,
    )
    options["metadata"]["cid"] = "changed"

    assert request.option_values() == {"metadata": {"cid": "cid_test"}}
    returned = request.option_values()
    returned["metadata"]["cid"] = "mutated"
    assert request.option_values() == {"metadata": {"cid": "cid_test"}}


def test_model_stream_request_rejects_runtime_objects() -> None:
    with pytest.raises(TypeError, match="non-serializable"):
        ModelStreamRequest(
            pref_config={},
            message="inspect",
            tools=(),
            options={"callback": lambda: None},
        )


def test_model_stream_request_rejects_reserved_options() -> None:
    with pytest.raises(ValueError, match="reserved fields: timeout"):
        ModelStreamRequest(
            pref_config={},
            message="inspect",
            tools=(),
            options={"timeout": 1.0},
        )


def test_remote_model_capability_translates_frozen_request(monkeypatch) -> None:
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

    assert isinstance(result, model_adapter.RemoteModelEventStream)
    stream_chat.assert_called_once_with(
        {"primary": {"model": "test-model"}},
        "inspect",
        [{"name": "read_file"}],
        attachments=[{"path": "screen.png"}],
        timeout=12.0,
        on_reconnect_status=reconnect,
        on_approval_snapshot=approval,
        initial_event_seq=7,
        metadata={"cid": "cid_test", "sid": "sid_test"},
        permissions={
            "sandbox_mode": "workspace-write",
            "approval_policy": "on-request",
            "approvals_reviewer": "user",
        },
    )


@pytest.mark.anyio
async def test_remote_model_event_stream_normalizes_iteration_failure_and_closes() -> None:
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
    stream = model_adapter.RemoteModelEventStream(raw)

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
async def test_remote_model_event_stream_rejects_invalid_event_and_closes() -> None:
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
    stream = model_adapter.RemoteModelEventStream(raw)

    with pytest.raises(ModelCapabilityError) as captured:
        async for _event in stream:
            pass

    error = captured.value
    assert error.code == "model_protocol_error"
    assert error.retryable is False
    assert error.details == {"exception_type": "TypeError"}
    assert raw.closed is True


@pytest.mark.anyio
async def test_remote_model_event_stream_rejects_invalid_event_coordinates() -> None:
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
    stream = model_adapter.RemoteModelEventStream(raw)

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
