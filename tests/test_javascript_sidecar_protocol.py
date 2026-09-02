import json

import pytest

from agent.ports.javascript import (
    JavaScriptExecutionError,
    JavaScriptFailureKind,
)
from infrastructure.sidecars.javascript.protocol import (
    EmitImageMessage,
    ExecResultMessage,
    RunToolMessage,
    encode_exec,
    encode_image_result,
    encode_tool_result,
    parse_arguments,
    parse_kernel_frame,
)


@pytest.mark.parametrize(
    ("payload", "expected"),
    (
        (
            b'{"type":"exec_result","id":"exec-1","ok":true,'
            b'"output":"","error":null}',
            ExecResultMessage("exec-1", True, "", None),
        ),
        (
            b'{"type":"run_tool","id":"tool-1","exec_id":"exec-1",'
            b'"tool_name":"probe","arguments":"{\\"value\\":1}"}',
            RunToolMessage("tool-1", "exec-1", "probe", '{"value":1}'),
        ),
        (
            b'{"type":"emit_image","id":"image-1","exec_id":"exec-1",'
            b'"image_url":"data:image/png;base64,AA==","detail":null}',
            EmitImageMessage(
                "image-1",
                "exec-1",
                "data:image/png;base64,AA==",
                None,
            ),
        ),
    ),
)
def test_parse_kernel_frame_accepts_existing_message_catalog(
    payload: bytes,
    expected: ExecResultMessage | RunToolMessage | EmitImageMessage,
) -> None:
    """Python 适配器必须原样理解不可变 Kernel 的三类输出。"""
    assert parse_kernel_frame(payload) == expected


def test_protocol_encoders_preserve_existing_kernel_inputs() -> None:
    """Python 只能生成 Kernel 已定义的三类输入消息。"""
    assert json.loads(encode_exec(
        request_id="exec-1",
        code="console.log(1)",
        timeout_ms=5000,
    )) == {
        "type": "exec",
        "id": "exec-1",
        "code": "console.log(1)",
        "timeout_ms": 5000,
    }
    assert json.loads(encode_tool_result(
        request_id="tool-1",
        ok=True,
        response={"output": "ok"},
        error=None,
    )) == {
        "type": "run_tool_result",
        "id": "tool-1",
        "ok": True,
        "response": {"output": "ok"},
        "error": None,
    }
    assert json.loads(encode_image_result(
        request_id="image-1",
        ok=False,
        error="invalid",
    )) == {
        "type": "emit_image_result",
        "id": "image-1",
        "ok": False,
        "error": "invalid",
    }


@pytest.mark.parametrize(
    "payload",
    (
        b"not-json",
        b"[]",
        b'{"type":"unknown"}',
        b'{"type":"exec_result","id":"exec-1","ok":true,'
        b'"output":"","error":null,"extra":1}',
        b'{"type":"exec_result","type":"run_tool"}',
        b'{"type":"exec_result","id":"","ok":true,'
        b'"output":"","error":null}',
        b'{"type":"exec_result","id":"exec-1","ok":true,'
        b'"output":"","error":NaN}',
    ),
)
def test_parse_kernel_frame_rejects_ambiguous_or_unknown_payload(
    payload: bytes,
) -> None:
    """未知、重复、越界或类型错误的 Kernel 输出必须失败收敛。"""
    with pytest.raises(JavaScriptExecutionError) as captured:
        parse_kernel_frame(payload)
    assert captured.value.kind == JavaScriptFailureKind.PROTOCOL_ERROR


def test_parse_arguments_requires_unique_json_object() -> None:
    """nested tool 参数不得用重复字段或非对象绕过边界校验。"""
    assert parse_arguments('{"value":1}') == {"value": 1}
    with pytest.raises(JavaScriptExecutionError):
        parse_arguments("[]")
    with pytest.raises(JavaScriptExecutionError):
        parse_arguments('{"value":1,"value":2}')
    with pytest.raises(JavaScriptExecutionError):
        parse_arguments('{"value":Infinity}')
