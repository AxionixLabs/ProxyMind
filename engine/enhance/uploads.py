# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from pathlib import Path
from mcp.types import CallToolResult
from mind_nova import request
from mind_nova.attachments import upload_response_attachment
from .fields import (
    fields_map,
    normalize_element,
    tool_payload,
    tool_target
)


async def upload_local(
    local: str,
    agent_id: str,
    bucket: str,
    filename: typing.Optional[str] = None,
    mime_type: typing.Optional[str] = None
) -> tuple[typing.Optional[dict[str, typing.Any]], dict[str, typing.Any]]:
    """上传本地文件并返回服务端标准附件和内部上传记录。"""
    up = await request.upload_file_stream(local, agent_id, bucket)

    attachment = upload_response_attachment(up, context=f"upload {Path(local).name}")

    uploaded = {
        "ok"        : True,
        "local"     : local,
        "url"       : attachment.get("url"),
        "r2_key"    : up.get("key"),
        "filename"  : attachment.get("filename", filename),
        "mime_type" : attachment.get("mime_type", mime_type)
    }

    return attachment, uploaded


async def upload_attachments(
    local_attachments: list[dict[str, typing.Any]],
    agent_id: str,
    bucket: str
) -> tuple[list[dict[str, typing.Any]], list[dict[str, typing.Any]]]:
    """批量上传本地附件并返回服务端标准附件列表。"""
    attachments: list[dict[str, typing.Any]] = []
    uploads: list[dict[str, typing.Any]]     = []

    for attachment_item in local_attachments:
        if not isinstance(attachment_item, dict):
            continue

        local = attachment_item.get("local")
        if not local:
            continue

        attachment, uploaded = await upload_local(
            local=local,
            agent_id=agent_id,
            bucket=bucket,
            filename=attachment_item.get("filename"),
            mime_type=attachment_item.get("mime_type")
        )
        uploads.append(uploaded)
        if attachment:
            attachments.append(attachment)

    return attachments, uploads


async def upload_tool_element(
    element: dict[str, typing.Any],
    agent_id: str,
    *,
    bucket: str
) -> tuple[list[dict[str, typing.Any]], dict[str, typing.Any]]:
    """处理单个 agent 结果中的本地附件上传。"""
    normalized = normalize_element(element)

    if not normalized["ok"]:
        return [], {
            "ok"      : False,
            "uploads" : [{"ok": False, "error": normalized["text"]}]
        }

    uploaded_attachments, uploads = await upload_attachments(
        normalized["attachments"], agent_id, bucket=bucket
    )

    if not uploads:
        uploads = [{"ok": False, "error": "missing uploadable attachments"}]

    return uploaded_attachments, {
        "ok"      : all(item.get("ok") for item in uploads),
        "uploads" : uploads
    }


async def upload_tool_result(
    result: CallToolResult,
    *,
    bucket: str,
    missing_text: str,
    success_text: str,
    partial_text: str
) -> dict[str, typing.Any]:
    """处理带附件产物的工具结果并汇总上传状态。"""
    attachments: list[dict[str, typing.Any]] = []

    result_fields = fields_map(result)
    payload = tool_payload(result_fields)

    if not payload and not result_fields.get("attachments"):
        return {
            "ok"          : False,
            "text"        : missing_text,
            "attachments" : attachments,
            "data"        : {"upload_ok": False, "fields": result_fields}
        }

    target = tool_target(result_fields, payload)
    element = dict(result_fields)
    element["data"] = payload

    uploaded_attachments, upload_payload = await upload_tool_element(
        element, target, bucket=bucket
    )
    attachments.extend(uploaded_attachments)
    ok = bool(upload_payload.get("ok"))

    return {
        "ok"          : ok,
        "text"        : success_text if ok else partial_text,
        "target"      : target,
        "attachments" : attachments,
        "data": {
            "upload_ok" : ok,
            "uploads"   : upload_payload.get("uploads", [])
        }
    }


if __name__ == '__main__':
    pass
