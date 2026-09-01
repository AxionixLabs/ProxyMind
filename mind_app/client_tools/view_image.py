# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import base64
import typing
from pathlib import Path
from mcp import types as mcp_types
from agent.application.tools.context import ToolHandlerContext
from agent.application.tools.definitions import ClientTool
from mind_app.client_tools.result import client_tool_result

VIEW_IMAGE_TOOL = "view_image"
MAX_IMAGE_BYTES = 8 * 1024 * 1024

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


def _image_mime_type(content: bytes) -> str:
    """根据文件头识别受支持的图片类型。"""
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"

    return ""


def _image_result(
    *,
    ok: bool,
    text: str,
    path: str,
    error: str = "",
    data: dict[str, typing.Any] | None = None,
    attachments: list[dict[str, typing.Any]] | None = None,
) -> mcp_types.CallToolResult:
    """构造图片查看工具的标准结果。"""
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


def _resolve_image_path(root: Path, raw_path: str) -> Path:
    """按执行环境目录解析图片路径。"""
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.resolve()


def _display_image_path(root: Path, image_path: Path) -> str:
    """生成相对执行目录的图片展示路径。"""
    try:
        return image_path.relative_to(root).as_posix()
    except ValueError:
        return str(image_path)


def view_image_tools(execution_root: str | Path) -> list[ClientTool]:
    """返回图片查看工具列表。"""
    root = Path(execution_root).resolve()

    async def view_image_handler(
        arguments: dict[str, typing.Any],
        runtime: ToolHandlerContext,
    ) -> mcp_types.CallToolResult:
        """读取图片并以工具附件回传。"""
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
            image_path = _resolve_image_path(root, raw_path)
        except (OSError, RuntimeError, ValueError):
            return _image_result(
                ok=False,
                text="Image path could not be resolved.",
                path=raw_path,
                error="path_invalid",
            )

        if not image_path.is_file():
            return _image_result(
                ok=False,
                text="Image file was not found.",
                path=raw_path,
                error="path_not_file",
            )

        try:
            size = image_path.stat().st_size
        except OSError:
            return _image_result(
                ok=False,
                text="Image file could not be inspected.",
                path=raw_path,
                error="path_unreadable",
            )

        if size > MAX_IMAGE_BYTES:
            return _image_result(
                ok=False,
                text=f"Image exceeds the {MAX_IMAGE_BYTES} byte limit.",
                path=raw_path,
                error="image_too_large",
                data={"size": size, "max_bytes": MAX_IMAGE_BYTES},
            )

        try:
            content = image_path.read_bytes()
        except OSError:
            return _image_result(
                ok=False,
                text="Image file could not be read.",
                path=raw_path,
                error="path_unreadable",
            )

        mime_type = _image_mime_type(content)
        if not mime_type:
            return _image_result(
                ok=False,
                text="Unsupported image format.",
                path=raw_path,
                error="unsupported_image",
                data={"size": size},
            )

        relative_path = _display_image_path(root, image_path)

        data_url = f"data:{mime_type};base64,{base64.b64encode(content).decode('ascii')}"

        attachment = {
            "kind"      : "image",
            "filename"  : Path(relative_path).name,
            "mime_type" : mime_type,
            "data_url"  : data_url
        }

        return _image_result(
            ok=True,
            text=f"Loaded image: {relative_path}",
            path=relative_path,
            data={"mime_type": mime_type, "size": size},
            attachments=[attachment],
        )

    return [
        ClientTool(
            name=VIEW_IMAGE_TOOL,
            description="读取当前执行环境中可访问的图片，并把图片作为附件提供给后续模型推理。",
            input_schema=VIEW_IMAGE_INPUT_SCHEMA,
            meta={"hidden": False, "domain": "client", "class": "view"},
            handler=view_image_handler,
        ),
    ]


if __name__ == '__main__':
    pass
