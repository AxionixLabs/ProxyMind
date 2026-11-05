import json

import pytest

from agent.ports.desktop import DesktopError
from agent.protocol.json_value import ThawedJsonValue
from infrastructure.sidecars.desktop.protocol import (
    decode_capabilities,
    decode_input_result,
    decode_observation_validated,
    decode_response,
    decode_windows,
    encode_request,
)


@pytest.mark.parametrize("frame", [
    b"{}", b"[]\n", b"{\n", b"\xff\n",
    b'{"v":1,"v":1,"id":"a","ok":true,"result":{}}\n',
    b'{"v":true,"id":"a","ok":true,"result":{}}\n',
    b'{"v":1,"id":"b","ok":true,"result":{}}\n',
    b'{"v":1,"id":"a","ok":1,"result":{}}\n',
    b'{"v":1,"id":"a","ok":true,"result":{},"error":null}\n',
])
def test_rejects_malformed_or_ambiguous_frames(frame: bytes) -> None:
    with pytest.raises(DesktopError) as failure:
        decode_response(frame, "a")
    assert failure.value.code == "protocol_error"


def test_request_preserves_unicode_and_is_bounded() -> None:
    assert json.loads(encode_request("a", "observe", {"window": "中文"}))["parameters"] == {"window": "中文"}
    with pytest.raises(DesktopError) as failure:
        encode_request("a", "observe", {"window": "x" * 65536})
    assert failure.value.code == "request_limit"


def test_native_failure_remains_structured() -> None:
    frame = b'{"v":1,"id":"a","ok":false,"error":{"code":"stale_window","message":"Observe again"}}\n'
    with pytest.raises(DesktopError) as failure:
        decode_response(frame, "a")
    assert failure.value.code == "stale_window"


def test_handshake_requires_observation_validation() -> None:
    capabilities: dict[str, ThawedJsonValue] = {
        "protocol": 1, "platform": "windows", "actions": ["list_windows", "observe"],
        "coordinateSpace": "physical_screen_pixels", "captureSupported": True, "inputSupported": False,
    }
    with pytest.raises(DesktopError):
        decode_capabilities(capabilities)
    capabilities["actions"] = ["list_windows", "observe", "validate_observation"]
    assert "validate_observation" in decode_capabilities(capabilities).actions


def test_observation_validation_requires_empty_success() -> None:
    assert decode_observation_validated({}) is None
    with pytest.raises(DesktopError):
        decode_observation_validated({"valid": True})


@pytest.mark.parametrize("payload", [
    {"eventsSubmitted": True, "requiresObservation": True},
    {"eventsSubmitted": -1, "requiresObservation": True},
    {"eventsSubmitted": 1, "requiresObservation": False},
    {"eventsSubmitted": 1, "requiresObservation": True, "success": True},
])
def test_input_result_cannot_claim_application_success(payload: dict[str, ThawedJsonValue]) -> None:
    with pytest.raises(DesktopError):
        decode_input_result(payload)


def test_input_handshake_requires_both_native_actions() -> None:
    payload: dict[str, ThawedJsonValue] = {
        "protocol": 1, "platform": "windows", "actions": ["list_windows", "observe", "validate_observation", "activate"],
        "coordinateSpace": "physical_screen_pixels", "captureSupported": True, "inputSupported": True,
    }
    with pytest.raises(DesktopError):
        decode_capabilities(payload)
    payload["actions"] = ["list_windows", "observe", "validate_observation", "activate", "input"]
    assert decode_capabilities(payload).input_supported


@pytest.mark.parametrize("invalid", [True, -1, float("nan"), float("inf"), 10 ** 400])
def test_windows_rejects_invalid_dimensions(invalid: ThawedJsonValue) -> None:
    payload: dict[str, ThawedJsonValue] = {"windows": [{
        "window": "reference", "title": "Fixture", "pid": 123, "displayScale": 1,
        "minimized": False, "bounds": {"x": -100, "y": 0, "width": invalid, "height": 100},
    }], "truncated": False}
    with pytest.raises(DesktopError):
        decode_windows(payload)
