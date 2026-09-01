# -*- coding: utf-8 -*-
# Notes: ==== Mind(TM) ====

import typing
from agent.application.tools.context import ToolHandlerContext
from agent.application.tools.definitions import ClientTool
from agent.application.tools.results import (
    LocalToolResult,
    client_tool_result,
)
from agent.ports.media import (
    ImageReadError,
    ImageReaderPort,
)

VIEW_IMAGE_TOOL = "view_image"

VIEW_IMAGE_INPUT_SCHEMA: dict[str, typing.Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "需要查看的图片路径。相对路径基于当前执行环境目录解析，绝对路径按原样读取。",
        },
    },
    "required": ["path"],
    "additionalProperties": False,
}

_IMAGE_ERROR_TEXT = {
    "path_invalid": "Image path could not be resolved.",
    "path_not_file": "Image file was not found.",
    "path_unreadable": "Image file could not be read.",
    "image_too_large": "Image exceeds the configured byte limit.",
    "unsupported_image": "Unsupported image format.",
}


def _image_result(
    *,
    ok: bool,
    text: str,
    path: str,
    error: str = "",
    data: dict[str, typing.Any] | None = None,
    attachments: list[dict[str, typing.Any]] | None = None,
) -> LocalToolResult:
    """构造图片查看工具的稳定结果。"""
    payload = {"path": path, **dict(data or {})}
    if error:
        payload["error"] = error
    return client_tool_result(
        tool=VIEW_IMAGE_TOOL,
        ok=ok,
        text=text,
        args={"path": path},
        data=payload,
        attachments=attachments,
    )


def media_tools(image_reader: ImageReaderPort) -> list[ClientTool]:
    """构造使用已注入图片读取端口的媒体工具。"""

    async def view_image_handler(
        arguments: dict[str, typing.Any],
        runtime: ToolHandlerContext,
    ) -> LocalToolResult:
        """读取图片并以稳定附件结果回传。"""
        _ = runtime
        raw_path = str(arguments.get("path") or "").strip()
        if not raw_path:
            return _image_result(
                ok=False,
                text="Image path is required.",
                path="",
                error="path_required",
            )

        try:
            asset = await image_reader.read(raw_path)
        except ImageReadError as error:
            return _image_result(
                ok=False,
                text=_IMAGE_ERROR_TEXT.get(error.code, "Image file could not be read."),
                path=error.path,
                error=error.code,
                data=dict(error.details),
            )

        return _image_result(
            ok=True,
            text=f"Loaded image: {asset.path}",
            path=asset.path,
            data={"mime_type": asset.mime_type, "size": asset.size},
            attachments=[{
                "kind": "image",
                "filename": asset.filename,
                "mime_type": asset.mime_type,
                "data_url": asset.data_url,
            }],
        )

    return [ClientTool(
        name=VIEW_IMAGE_TOOL,
        description="读取当前执行环境中可访问的图片，并把图片作为附件提供给后续模型推理。",
        input_schema=VIEW_IMAGE_INPUT_SCHEMA,
        meta={"hidden": False, "domain": "client", "class": "view"},
        handler=view_image_handler,
    )]


__all__ = (
    "VIEW_IMAGE_INPUT_SCHEMA",
    "VIEW_IMAGE_TOOL",
    "media_tools",
)
