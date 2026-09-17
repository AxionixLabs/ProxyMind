# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import math
import re
from urllib.parse import urlsplit

from mcp import types as mcp_types

from agent.domain.mcp_elicitation import (
    ElicitationField,
    ElicitationRequest,
    FieldKind,
    FormValue,
    McpInvocation,
)
from agent.protocol.json_value import (
    ThawedJsonValue,
    freeze_json,
    thaw_object,
)

_FIELD_KEYS = frozenset({
    "type",
    "title",
    "description",
    "default",
    "enum",
    "oneOf",
    "items",
    "minimum",
    "maximum",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "format",
})
_SECRET_NAMES = frozenset({
    "password",
    "passwd",
    "secret",
    "apikey",
    "accesstoken",
    "refreshtoken",
    "token",
    "privatekey",
    "creditcard",
    "cardnumber",
    "cvv",
    "pin",
})


def _mapping(value: ThawedJsonValue) -> dict[str, ThawedJsonValue]:
    """收窄 JSON 对象，拒绝以其他类型代替 schema。"""
    if not isinstance(value, dict):
        raise ValueError("Expected an elicitation schema object")
    return value


def _text(value: ThawedJsonValue, *, limit: int = 4096) -> str:
    """限制展示字符串长度，拒绝协议控制字符。"""
    if not isinstance(value, str) or len(value) > limit or any(
        (ord(char) < 32 and char not in "\n\t") or char in "\x7f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
        for char in value
    ):
        raise ValueError("Invalid elicitation text")
    return value


def _number(value: ThawedJsonValue) -> float | None:
    """读取有限数值，不把布尔值作为整数。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Invalid numeric constraint")
    try:
        result = float(value)
    except OverflowError:
        raise ValueError("Invalid numeric constraint") from None
    if not math.isfinite(result):
        raise ValueError("Invalid numeric constraint")
    return result


def _count(value: ThawedJsonValue, default: int, limit: int) -> int:
    """读取有界非负条目数，拒绝不可能在本地表面呈现的大小。"""
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= limit:
        raise ValueError("Invalid elicitation size constraint")
    return value


def _choices(schema: dict[str, ThawedJsonValue], titled_key: str) -> tuple[tuple[str, str], ...]:
    """把普通枚举及有标题的枚举统一为不可变值与标签。"""
    enum = schema.get("enum")
    titled = schema.get(titled_key)
    if enum is None and titled is None:
        return ()
    if enum is not None and titled is not None:
        raise ValueError("Conflicting enum schemas")
    values = enum if enum is not None else titled
    if not isinstance(values, list) or not 1 <= len(values) <= 64:
        raise ValueError("Invalid enum choices")
    result: list[tuple[str, str]] = []
    for item in values:
        if enum is not None:
            value = _text(item)
            result.append((value, value))
        else:
            entry = _mapping(item)
            if entry.keys() - {"const", "title"}:
                raise ValueError("Unsupported titled enum schema")
            value = _text(entry.get("const"))
            result.append((value, _text(entry.get("title", value))))
    if len(dict(result)) != len(result):
        raise ValueError("Duplicate enum choices")
    return tuple(result)


def _default(value: ThawedJsonValue) -> FormValue | None:
    """将有限的默认值转换为具名字段接受的不可变值。"""
    if isinstance(value, str):
        return _text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, list) and len(value) <= 64:
        return tuple(_text(item) for item in value)
    raise ValueError("Unsupported elicitation default")


def _field(name: str, schema: dict[str, ThawedJsonValue], required: bool) -> ElicitationField:
    """仅接收可完整验证和编辑的普通字段，敏感字段及未知约束显式拒绝。"""
    _text(name, limit=128)
    title = _text(schema.get("title", name))
    if not name or any(re.sub(r"[^a-z0-9]", "", text.lower()) in _SECRET_NAMES for text in (name, title)):
        raise ValueError("Sensitive fields require URL elicitation")
    if schema.keys() - _FIELD_KEYS:
        raise ValueError("Unsupported elicitation field constraint")
    if any(value is None for value in schema.values()):
        raise ValueError("Null field constraints are not supported")
    kind = schema.get("type")
    field_kind: FieldKind
    if kind == "string":
        field_kind = "string"
    elif kind == "integer":
        field_kind = "integer"
    elif kind == "number":
        field_kind = "number"
    elif kind == "boolean":
        field_kind = "boolean"
    elif kind == "array":
        field_kind = "array"
    else:
        raise ValueError("Unsupported elicitation field type")
    if kind == "array":
        items = _mapping(schema.get("items"))
        if items.keys() - {"type", "enum", "anyOf"} or items.get("type", "string") != "string":
            raise ValueError("Only string enum arrays are supported")
        choices = _choices(items, "anyOf")
        if not choices:
            raise ValueError("Array fields require enum choices")
    else:
        choices = _choices(schema, "oneOf")
    format_name = schema.get("format")
    if format_name is not None and not isinstance(format_name, str):
        raise ValueError("Invalid field format")
    if format_name is not None and (kind != "string" or format_name not in ("email", "uri", "date", "date-time")):
        raise ValueError("Unsupported or sensitive field format")
    if kind != "array" and schema.keys() & {"items", "minItems", "maxItems"}:
        raise ValueError("Array constraints require an array")
    if kind != "string" and schema.keys() & {"enum", "oneOf", "minLength", "maxLength", "format"}:
        raise ValueError("String constraints require a string")
    if kind not in ("number", "integer") and schema.keys() & {"minimum", "maximum"}:
        raise ValueError("Numeric constraints require a number")
    field = ElicitationField(
        name=name, title=title, description=_text(schema.get("description", "")),
        kind=field_kind, required=required, choices=choices, default=_default(schema.get("default")),
        minimum=_number(schema.get("minimum")), maximum=_number(schema.get("maximum")),
        min_length=_count(schema.get("minLength"), 0, 4096), max_length=_count(schema.get("maxLength"), 4096, 4096),
        min_items=_count(schema.get("minItems"), 0, 64), max_items=_count(schema.get("maxItems"), 64, 64),
        format=format_name,
    )
    if field.min_length > field.max_length or field.min_items > field.max_items:
        raise ValueError("Conflicting size constraints")
    if field.minimum is not None and field.maximum is not None and field.minimum > field.maximum:
        raise ValueError("Conflicting numeric constraints")
    if field.default is not None:
        field.validate(field.default)
    return field


def elicitation_request(params: mcp_types.ElicitRequestParams, *, request_id: str, server: str, invocation: McpInvocation) -> ElicitationRequest:
    """把 SDK 载荷收窄为普通字段或安全 URL；不保留外部字典、任务或扩展元数据。"""
    if params.task is not None:
        raise ValueError("Task elicitation is not supported")
    message = _text(params.message)
    if isinstance(params, mcp_types.ElicitRequestURLParams):
        url = _text(params.url, limit=8192)
        if any(char.isspace() or ord(char) < 32 for char in url) or "\\" in url:
            raise ValueError("Invalid elicitation URL")
        parsed = urlsplit(url)
        host = parsed.hostname
        if not host or parsed.username is not None or parsed.password is not None or parsed.port == 0:
            raise ValueError("Invalid elicitation URL authority")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and host in {"localhost", "127.0.0.1", "::1"}):
            raise ValueError("Elicitation URLs require HTTPS or loopback HTTP")
        identity = _text(params.elicitationId, limit=256)
        if not identity:
            raise ValueError("Missing URL elicitation identity")
        return ElicitationRequest(request_id, server, invocation, message, url=url,
            url_host=host.encode("idna").decode("ascii"), elicitation_id=identity)
    schema = thaw_object(freeze_json(params.requestedSchema, field_name="elicitation schema"), field_name="elicitation schema")
    if schema.get("type") != "object" or schema.keys() - {"type", "properties", "required", "additionalProperties", "$schema"}:
        raise ValueError("Only flat elicitation object schemas are supported")
    if "$schema" in schema and schema["$schema"] not in ("https://json-schema.org/draft/2020-12/schema", "http://json-schema.org/draft-07/schema#"):
        raise ValueError("Unsupported elicitation schema version")
    if schema.get("additionalProperties", False) is not False:
        raise ValueError("Additional elicitation fields are not supported")
    properties = _mapping(schema.get("properties"))
    if len(properties) > 32:
        raise ValueError("Too many elicitation fields")
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(isinstance(name, str) and name in properties for name in required):
        raise ValueError("Invalid required fields")
    if len(required) != len(set(required)):
        raise ValueError("Duplicate required fields")
    fields = tuple(_field(name, _mapping(value), name in required) for name, value in properties.items())
    return ElicitationRequest(request_id, server, invocation, message, fields=fields)


if __name__ == '__main__':
    pass
