# -*- coding: utf-8 -*-

from types import SimpleNamespace

import pytest
from mcp.server import FastMCP
from pydantic import ValidationError

from backend.mcp_hub.hub_nexus.domain.merge import MergeService
from backend.mcp_tools.automator import ctl_info
from backend.mcp_tools.bench.schemas.schema_nexus import (
    FtpSharedEnv,
    GraphqlSharedEnv,
    HttpBatchItem,
    HttpSharedEnv,
    SseSharedEnv,
    TcpSharedEnv,
)
from backend.mcp_tools.media import screen
from backend.register import (
    register_bench_tools,
    register_media_tools,
)
from backend.utilities.runtime import AppContext


def test_removed_media_and_load_tools_are_not_registered() -> None:
    mcp = FastMCP("test")

    register_bench_tools(mcp, SimpleNamespace(), SimpleNamespace())
    register_media_tools(
        mcp,
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
    )

    names = {tool.name for tool in mcp._tool_manager.list_tools()}

    assert not any(name.startswith("ffmpeg_") for name in names)
    assert {"perf_run", "perf_run_file"}.isdisjoint(names)


def test_capture_tools_require_explicit_output_paths() -> None:
    mcp = FastMCP("test")

    ctl_info.bind(mcp, SimpleNamespace(), SimpleNamespace())
    screen.bind(mcp, SimpleNamespace(), SimpleNamespace())

    screenshot = mcp._tool_manager.get_tool("screenshot")
    recorder = mcp._tool_manager.get_tool("scrcpy_record")

    assert screenshot is not None
    assert recorder is not None
    assert screenshot.parameters["required"] == ["local"]
    assert recorder.parameters["required"] == ["directory"]
    assert screenshot.parameters["properties"]["local"]["minLength"] == 1
    assert recorder.parameters["properties"]["directory"]["minLength"] == 1


def test_runtime_context_omits_removed_execution_engines() -> None:
    context = AppContext()

    assert not hasattr(context, "ffmpeg")
    assert not hasattr(context, "k6")
    assert context.player.agent_id == "player"


def test_nexus_schema_exposes_only_canonical_request_fields() -> None:
    request = HttpSharedEnv.model_validate({
        "json": {"answer": 42},
        "body_text": "payload",
    })

    assert request.model_dump(exclude_none=True, by_alias=True) == {
        "json": {"answer": 42},
        "body_text": "payload",
    }
    properties = HttpSharedEnv.model_json_schema()["properties"]
    assert "json" in properties
    assert "json_body" not in properties
    assert GraphqlSharedEnv.model_validate({
        "operation_name": "CanonicalQuery",
    }).operation_name == "CanonicalQuery"
    assert FtpSharedEnv.model_validate({
        "payload_text": "payload",
    }).payload_text == "payload"
    sse = SseSharedEnv.model_validate({
        "json": {"event": "ready"},
        "max_events": 3,
    })
    assert sse.model_dump(exclude_none=True, by_alias=True) == {
        "json": {"event": "ready"},
        "max_events": 3,
    }


@pytest.mark.parametrize(("model", "payload"), [
    (HttpSharedEnv, {"json_body": {"legacy": True}}),
    (HttpSharedEnv, {"body": "legacy"}),
    (TcpSharedEnv, {"body": "legacy"}),
    (GraphqlSharedEnv, {"operationName": "LegacyQuery"}),
    (FtpSharedEnv, {"body_text": "legacy"}),
    (FtpSharedEnv, {"body": "legacy"}),
])
def test_nexus_schema_rejects_legacy_request_aliases(model, payload) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_nexus_batch_item_requires_request_wrapper() -> None:
    item = HttpBatchItem.model_validate({
        "name": "health",
        "request": {"method": "GET", "url": "/health"},
    })

    assert item.request.url == "/health"
    with pytest.raises(ValidationError):
        HttpBatchItem.model_validate({
            "name": "health",
            "method": "GET",
            "url": "/health",
        })


def test_nexus_materializes_explicit_env_and_request_values() -> None:
    merged = MergeService.materialize(
        env={
            "headers": {"shared": "yes"},
            "json": {"shared": True},
            "timeout": 10,
        },
        request={
            "headers": {"case": "yes"},
            "json": {"case": True},
        },
    )

    assert merged == {
        "headers": {"shared": "yes", "case": "yes"},
        "json": {"shared": True, "case": True},
        "timeout": 10,
    }
