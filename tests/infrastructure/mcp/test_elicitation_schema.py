# -*- coding: utf-8 -*-

import pytest
from mcp import types as mcp_types

from agent.domain.mcp_elicitation import (
    ElicitationResponse,
    McpInvocation,
)
from infrastructure.mcp.elicitation_schema import elicitation_request


def request(properties, **schema):
    return elicitation_request(mcp_types.ElicitRequestFormParams(message="Ordinary information", requestedSchema={
        "type": "object", "properties": properties, **schema,
    }), request_id="request", server="server", invocation=McpInvocation("session", "turn", "call", "root"))


def test_typed_form_validates_defaults_all_primitives_and_enum_arrays():
    form = request({
        "name": {"type": "string", "minLength": 2, "maxLength": 20},
        "count": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
        "ratio": {"type": "number", "minimum": 0, "maximum": 1},
        "enabled": {"type": "boolean", "default": False},
        "color": {"type": "string", "oneOf": [{"const": "blue", "title": "Blue"}]},
        "tags": {"type": "array", "items": {"anyOf": [{"const": "a", "title": "A"}]}, "minItems": 1},
    }, required=["name", "tags"])
    form.validate_response(ElicitationResponse("accept", (("name", "Test"), ("count", 2), ("ratio", .5),
        ("enabled", True), ("color", "blue"), ("tags", ("a",)))))
    assert form.fields[1].default == 3 and form.fields[3].default is False
    for content in ((("name", "x"),), (("unknown", True),), (("name", "Test"), ("tags", ("a", "a"))),
                    (("name", "Test"), ("tags", ("a",)), ("count", True))):
        with pytest.raises(ValueError):
            form.validate_response(ElicitationResponse("accept", content))
    with pytest.raises(ValueError):
        form.validate_response(ElicitationResponse("decline", (("name", "Test"),)))


@pytest.mark.parametrize("field", [
    {"type": "object"}, {"type": "array", "items": {"type": "object"}},
    {"type": "string", "format": "password"}, {"type": "string", "writeOnly": True},
    {"type": "string", "pattern": "(a+)+"}, {"type": "string", "maxLength": 100000},
    {"type": "integer", "minimum": 10, "maximum": 1}, {"type": "number", "default": float("nan")},
    {"type": "string", "enum": ["a", "a"]}, {"type": "string", "minLength": 3, "default": "x"},
    {"type": "string", "default": None}, {"type": "string", "title": "Password"},
    {"type": "string", "default": "hidden\x1b[2J"}, {"type": "string", "default": "spoof\u202e"},
])
def test_unsupported_or_invalid_schema_fails_without_echoing_values(field):
    with pytest.raises(ValueError):
        request({"field": field})


@pytest.mark.parametrize("name", ["password", "api_key", "accessToken", "cvv"])
def test_sensitive_fields_are_not_presented(name):
    with pytest.raises(ValueError, match="Sensitive"):
        request({name: {"type": "string", "default": "do-not-expose-this"}})


@pytest.mark.parametrize("url", ["file:///secret", "javascript:alert(1)", "https://user:secret@example.com/", "http://remote.test/", "https://example.test\\evil", "https://example.test/\x1b"])
def test_unsafe_urls_are_rejected(url):
    with pytest.raises(ValueError):
        elicitation_request(mcp_types.ElicitRequestURLParams(mode="url", message="Continue", url=url, elicitationId="opaque"),
            request_id="request", server="server", invocation=McpInvocation("s", "t", "c", "a"))


def test_url_preserves_exact_target_and_displays_ascii_host():
    form = elicitation_request(mcp_types.ElicitRequestURLParams(mode="url", message="Continue", url="https://例子.test/path?opaque=1", elicitationId="opaque"),
        request_id="request", server="server", invocation=McpInvocation("s", "t", "c", "a"))
    assert form.url_host == "xn--fsqu00a.test" and form.url.endswith("?opaque=1")
    form.validate_response(ElicitationResponse("accept"))


@pytest.mark.parametrize("format,valid,invalid", [
    ("email", "test@example.org", "test@"),
    ("uri", "https://example.org/path", "no scheme"),
    ("date", "2026-09-17", "2026-02-30"),
    ("date-time", "2026-09-17T10:00:00Z", "2026-09-17T10:00:00"),
])
def test_string_format_is_checked_before_submission(format, valid, invalid):
    form = request({"value": {"type": "string", "format": format}})
    form.validate_response(ElicitationResponse("accept", (("value", valid),)))
    with pytest.raises(ValueError):
        form.validate_response(ElicitationResponse("accept", (("value", invalid),)))
