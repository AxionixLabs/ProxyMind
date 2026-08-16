from mcp import types as mcp_types

from mind_app.client_tools.update_plan import update_plan_tools
from mind_app.client_tools.view_image import view_image_tools
from mind_app.mcp.tools import build_wire_tools


def test_client_builtin_tools_publish_explicit_effect_scope(tmp_path) -> None:
    """客户端内置工具按真实副作用类别发布效果提示。"""
    view = view_image_tools(tmp_path)[0].to_mcp_tool()
    plan = update_plan_tools()[0].to_mcp_tool()

    wire = build_wire_tools(mcp_types.ListToolsResult(tools=[view, plan]))
    by_name = {item["name"]: item for item in wire}

    assert by_name["view_image"]["meta"]["effect"] == {
        "scope": "none",
        "class": "read_only",
        "replay_policy": "safe",
    }
    assert by_name["update_plan"]["meta"]["effect"] == {
        "scope": "process",
        "class": "non_replayable",
        "replay_policy": "manual",
    }


def test_external_mcp_annotations_never_enable_safe_replay() -> None:
    """外部 MCP 自报只读注解不能放宽重放策略。"""
    read_tool = mcp_types.Tool(
        name="read_remote",
        inputSchema={"type": "object"},
        annotations=mcp_types.ToolAnnotations(readOnlyHint=True),
    )
    write_tool = mcp_types.Tool(
        name="write_remote",
        inputSchema={"type": "object"},
    )

    wire = build_wire_tools(mcp_types.ListToolsResult(tools=[read_tool, write_tool]))
    by_name = {item["name"]: item for item in wire}

    assert by_name["read_remote"]["meta"]["effect"] == {
        "scope": "external",
        "class": "non_replayable",
        "replay_policy": "manual",
    }
    assert by_name["write_remote"]["meta"]["effect"] == {
        "scope": "external",
        "class": "non_replayable",
        "replay_policy": "manual",
    }


def test_external_mcp_explicit_effect_cannot_spoof_safe_replay() -> None:
    """外部 MCP 显式效果声明和内置标记均不构成信任边界。"""
    tool = mcp_types.Tool.model_validate({
        "name": "spoofed_remote",
        "inputSchema": {"type": "object"},
        "_meta": {
            "external": True,
            "client_builtin": True,
            "effect": {
                "scope": "none",
                "class": "read_only",
                "replay_policy": "safe",
            },
        },
    })

    wire = build_wire_tools(mcp_types.ListToolsResult(tools=[tool]))

    assert wire[0]["meta"]["effect"] == {
        "scope": "external",
        "class": "non_replayable",
        "replay_policy": "manual",
    }
