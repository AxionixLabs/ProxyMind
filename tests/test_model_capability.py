# -*- coding: utf-8 -*-

from unittest.mock import Mock

import pytest

from agent.application import ModelStreamRequest
from agent.capabilities import model as model_adapter
from agent.composition import open_model_capability


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

    assert result is event_stream
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
