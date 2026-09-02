# -*- coding: utf-8 -*-
# Notes: ==== Mind(TM) ====

import json
import typing
from dataclasses import dataclass

from agent.ports.javascript import (
    JavaScriptExecutionError,
    JavaScriptFailureKind,
)
from agent.protocol.json_value import ThawedJsonValue
from metadata import const

FRAME_MAX_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ExecResultMessage:
    """描述 Kernel 返回的一次 Cell 执行结果。"""

    request_id: str
    ok: bool
    output: str
    error: str | None


@dataclass(frozen=True, slots=True)
class RunToolMessage:
    """描述 Kernel 发起的一次嵌套工具调用。"""

    request_id: str
    execution_id: str
    tool_name: str
    arguments_json: str


@dataclass(frozen=True, slots=True)
class EmitImageMessage:
    """描述 Kernel 发起的一次图片附加请求。"""

    request_id: str
    execution_id: str
    image_url: str
    detail: str | None


KernelMessage: typing.TypeAlias = (
    ExecResultMessage
    | RunToolMessage
    | EmitImageMessage
)


def encode_exec(
    *,
    request_id: str,
    code: str,
    timeout_ms: int,
) -> bytes:
    """构造不可变 Kernel 已定义的 Cell 执行消息。"""
    return _encode({
        "type": "exec",
        "id": request_id,
        "code": code,
        "timeout_ms": timeout_ms,
    })


def encode_tool_result(
    *,
    request_id: str,
    ok: bool,
    response: dict[str, ThawedJsonValue] | None,
    error: str | None,
) -> bytes:
    """构造不可变 Kernel 已定义的嵌套工具结果消息。"""
    return _encode({
        "type": "run_tool_result",
        "id": request_id,
        "ok": ok,
        "response": response,
        "error": error,
    })


def encode_image_result(
    *,
    request_id: str,
    ok: bool,
    error: str | None,
) -> bytes:
    """构造不可变 Kernel 已定义的图片接收结果消息。"""
    return _encode({
        "type": "emit_image_result",
        "id": request_id,
        "ok": ok,
        "error": error,
    })


def parse_kernel_frame(frame: bytes) -> KernelMessage:
    """严格解析一条完整 Kernel 输出帧。"""
    if len(frame) > FRAME_MAX_BYTES:
        raise _protocol_error(
            f"JavaScript sidecar frame exceeded {FRAME_MAX_BYTES} bytes"
        )
    try:
        text = frame.decode(const.CHARSET)
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise _protocol_error("JavaScript sidecar returned invalid JSON") from error
    if not isinstance(value, dict):
        raise _protocol_error("JavaScript sidecar frame must be an object")

    message_type = _required_identity(value, "type")
    if message_type == "exec_result":
        _require_fields(value, {"type", "id", "ok", "output", "error"})
        return ExecResultMessage(
            request_id=_required_identity(value, "id"),
            ok=_required_bool(value, "ok"),
            output=_required_string(value, "output"),
            error=_optional_string(value, "error"),
        )
    if message_type == "run_tool":
        _require_fields(
            value,
            {"type", "id", "exec_id", "tool_name", "arguments"},
        )
        return RunToolMessage(
            request_id=_required_identity(value, "id"),
            execution_id=_required_identity(value, "exec_id"),
            tool_name=_required_string(value, "tool_name"),
            arguments_json=_required_string(value, "arguments"),
        )
    if message_type == "emit_image":
        _require_fields(
            value,
            {"type", "id", "exec_id", "image_url", "detail"},
        )
        return EmitImageMessage(
            request_id=_required_identity(value, "id"),
            execution_id=_required_identity(value, "exec_id"),
            image_url=_required_string(value, "image_url"),
            detail=_optional_string(value, "detail"),
        )
    raise _protocol_error(
        f"JavaScript sidecar returned unknown message type: {message_type}"
    )


def parse_arguments(value: str) -> dict[str, ThawedJsonValue]:
    """校验 Kernel 嵌套工具参数是严格 JSON 对象。"""
    try:
        parsed = json.loads(
            value,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise _protocol_error("host.tool arguments must be valid JSON") from error
    if not isinstance(parsed, dict):
        raise _protocol_error("host.tool arguments must be an object")
    return _json_object(parsed, field_name="host.tool arguments")


def _encode(message: dict[str, ThawedJsonValue]) -> bytes:
    """编码一条由 Python 构造的 Kernel 输入帧。"""
    try:
        payload = json.dumps(
            message,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode(const.CHARSET)
    except (TypeError, ValueError) as error:
        raise _protocol_error(
            "JavaScript sidecar request contains a non-JSON value"
        ) from error
    if len(payload) > FRAME_MAX_BYTES:
        raise _protocol_error(
            f"JavaScript sidecar frame exceeded {FRAME_MAX_BYTES} bytes"
        )
    return payload + b"\n"


def _unique_object(pairs: list[tuple[str, typing.Any]]) -> dict[str, typing.Any]:
    """拒绝会导致字段含义不唯一的重复 JSON key。"""
    value: dict[str, typing.Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON field: {key}")
        value[key] = item
    return value


def _reject_constant(value: str) -> typing.NoReturn:
    """拒绝 Python JSON decoder 默认接受的非标准数值。"""
    raise ValueError(f"non-standard JSON value: {value}")


def _require_fields(value: dict[str, typing.Any], expected: set[str]) -> None:
    """要求消息字段集合与既有 Kernel 契约精确一致。"""
    actual = set(value)
    if actual != expected:
        missing = sorted(expected.difference(actual))
        extra = sorted(actual.difference(expected))
        raise _protocol_error(
            f"JavaScript sidecar fields mismatch; missing={missing}, extra={extra}"
        )


def _required_string(value: dict[str, typing.Any], field: str) -> str:
    """读取必需字符串字段，并保留协议允许的空字符串。"""
    item = value.get(field)
    if not isinstance(item, str):
        raise _protocol_error(f"JavaScript sidecar field {field} must be a string")
    return item


def _required_identity(value: dict[str, typing.Any], field: str) -> str:
    """读取必须非空的消息判别或关联标识。"""
    item = _required_string(value, field)
    if not item:
        raise _protocol_error(
            f"JavaScript sidecar field {field} must not be empty"
        )
    return item


def _optional_string(value: dict[str, typing.Any], field: str) -> str | None:
    """读取可空字符串字段。"""
    item = value.get(field)
    if item is None:
        return None
    if not isinstance(item, str):
        raise _protocol_error(
            f"JavaScript sidecar field {field} must be a string or null"
        )
    return item


def _required_bool(value: dict[str, typing.Any], field: str) -> bool:
    """读取必需布尔字段。"""
    item = value.get(field)
    if not isinstance(item, bool):
        raise _protocol_error(f"JavaScript sidecar field {field} must be a boolean")
    return item


def _json_object(
    value: dict[str, typing.Any],
    *,
    field_name: str,
) -> dict[str, ThawedJsonValue]:
    """在 adapter 边界递归验证可变 JSON 对象。"""
    return {
        key: _json_value(item, field_name=f"{field_name}.{key}")
        for key, item in value.items()
    }


def _json_value(value: typing.Any, *, field_name: str) -> ThawedJsonValue:
    """把无类型 JSON 值收窄为已校验的可变 JSON 值。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [
            _json_value(item, field_name=field_name)
            for item in value
        ]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise _protocol_error(f"{field_name} contains a non-string key")
        return _json_object(value, field_name=field_name)
    raise _protocol_error(f"{field_name} contains a non-JSON value")


def _protocol_error(detail: str) -> JavaScriptExecutionError:
    """构造稳定的私有协议失败。"""
    return JavaScriptExecutionError(
        JavaScriptFailureKind.PROTOCOL_ERROR,
        detail,
    )


if __name__ == "__main__":
    pass
