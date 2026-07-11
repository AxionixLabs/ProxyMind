# -*- coding: utf-8 -*-

import asyncio
import base64
from pathlib import Path

from mind_app.client_tools.types import ClientTool, ClientToolRuntime
from mind_app.client_tools.registry import default_registry
from mind_app.client_tools.view_image import (
    MAX_IMAGE_BYTES,
    VIEW_IMAGE_TOOL,
    view_image_tools,
)


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/"
    "5ncLrgAAAABJRU5ErkJggg=="
)


def run_async(value: object) -> object:
    """同步测试中运行异步工具调用。"""
    return asyncio.run(value)


def tool_by_name(tools: list[ClientTool], name: str) -> ClientTool:
    """按名称取得客户端工具。"""
    for tool in tools:
        if tool.name == name:
            return tool
    raise AssertionError(f"tool not found: {name}")


def test_view_image_returns_image_attachment(tmp_path: Path, monkeypatch) -> None:
    """可访问图片以 data URL 附件回传。"""
    image = tmp_path / "pixel.png"
    image.write_bytes(PNG_BYTES)
    monkeypatch.chdir(tmp_path)
    tool = tool_by_name(view_image_tools(), VIEW_IMAGE_TOOL)

    result = run_async(tool.handler({"path": "pixel.png"}, ClientToolRuntime(session=None)))
    structured = result.structuredContent or {}
    attachment = structured["attachments"][0]

    assert result.isError is False
    assert structured["data"] == {
        "path": "pixel.png",
        "mime_type": "image/png",
        "size": len(PNG_BYTES),
    }
    assert attachment["kind"] == "image"
    assert attachment["filename"] == "pixel.png"
    assert attachment["mime_type"] == "image/png"
    assert attachment["data_url"] == f"data:image/png;base64,{base64.b64encode(PNG_BYTES).decode('ascii')}"


def test_view_image_is_in_default_client_registry() -> None:
    """默认客户端工具注册表会对外上报图片查看工具。"""
    registry = default_registry()

    assert registry.has_tool(VIEW_IMAGE_TOOL) is True
    tool = tool_by_name(view_image_tools(), VIEW_IMAGE_TOOL)
    assert tool.meta["domain"] == "client"
    assert tool.meta["class"] == "view"


def test_view_image_reads_absolute_path(tmp_path: Path, monkeypatch) -> None:
    """绝对路径图片可在系统允许读取时回传。"""
    image = tmp_path / "outside.png"
    image.write_bytes(PNG_BYTES)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    tool = tool_by_name(view_image_tools(), VIEW_IMAGE_TOOL)

    result = run_async(tool.handler({"path": str(image)}, ClientToolRuntime(session=None)))
    structured = result.structuredContent or {}

    assert result.isError is False
    assert structured["data"]["path"] == str(image.resolve())


def test_view_image_reads_relative_path_outside_workspace(tmp_path: Path, monkeypatch) -> None:
    """相对路径基于执行环境目录解析，并按读取权限尝试访问。"""
    image = tmp_path / "outside.png"
    image.write_bytes(PNG_BYTES)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    monkeypatch.chdir(workspace)
    tool = tool_by_name(view_image_tools(), VIEW_IMAGE_TOOL)
    result = run_async(tool.handler({"path": "../outside.png"}, ClientToolRuntime(session=None)))
    structured = result.structuredContent or {}

    assert result.isError is False
    assert structured["data"]["path"] == str(image.resolve())


def test_view_image_rejects_unsupported_content(tmp_path: Path, monkeypatch) -> None:
    """非图片内容不会仅依据文件扩展名接受。"""
    (tmp_path / "not-image.png").write_text("not an image", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    tool = tool_by_name(view_image_tools(), VIEW_IMAGE_TOOL)

    result = run_async(tool.handler({"path": "not-image.png"}, ClientToolRuntime(session=None)))
    structured = result.structuredContent or {}

    assert result.isError is True
    assert structured["data"]["error"] == "unsupported_image"


def test_view_image_rejects_oversized_file(tmp_path: Path, monkeypatch) -> None:
    """超过上限的图片不会被编码进请求载荷。"""
    image = tmp_path / "large.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * (MAX_IMAGE_BYTES - 7))
    monkeypatch.chdir(tmp_path)
    tool = tool_by_name(view_image_tools(), VIEW_IMAGE_TOOL)

    result = run_async(tool.handler({"path": "large.png"}, ClientToolRuntime(session=None)))
    structured = result.structuredContent or {}

    assert result.isError is True
    assert structured["data"]["error"] == "image_too_large"
    assert structured["data"]["size"] == MAX_IMAGE_BYTES + 1
