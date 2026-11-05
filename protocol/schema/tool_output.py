# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import base64
import binascii
import json
import math
import re

from protocol.schema.json_value import (
    JsonObject,
    JsonValue,
)


TOOL_RESULT_MAX_BYTES = 16 * 1024 * 1024
TOOL_RESULT_METADATA_MAX_BYTES = 2 * 1024 * 1024
TOOL_RESULT_IMAGE_MAX_BYTES = 8 * 1024 * 1024


class ToolResultBudgetError(ValueError):
    """描述结果预算校验失败，不携带结果正文或图像内容。"""

    def __init__(self, code: str, path: str, actual_bytes: int, max_bytes: int) -> None:
        """保存机器可读的原因、位置和大小边界。"""
        self.code = code
        self.path = path
        self.actual_bytes = actual_bytes
        self.max_bytes = max_bytes
        super().__init__(
            f"{code} at {path}: {actual_bytes} bytes, limit {max_bytes} bytes"
        )


def _json_bytes(value: JsonValue) -> int:
    """按正式协议的紧凑 UTF-8 JSON 计算大小。"""
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))


def validate_tool_result_budget(result: JsonObject) -> None:
    """限制完整信封和普通内容，仅有效的内联图片使用独立预算。"""
    total = _json_bytes(result)
    if total > TOOL_RESULT_MAX_BYTES:
        raise ToolResultBudgetError("tool_result_too_large", "result", total, TOOL_RESULT_MAX_BYTES)
    attachments = result.get("attachments")
    if not isinstance(attachments, list):
        raise ValueError("tool result attachments must be a list")
    metadata_attachments: list[JsonValue] = []
    for index, attachment in enumerate(attachments):
        if not isinstance(attachment, dict) or attachment.get("kind") != "image":
            metadata_attachments.append(attachment)
            continue
        data_url = attachment.get("data_url")
        if data_url is None:
            metadata_attachments.append(attachment)
            continue
        mime = attachment.get("mime_type")
        path = f"result.attachments[{index}].data_url"
        if (
            mime not in ("image/png", "image/jpeg", "image/gif", "image/webp")
            or not isinstance(data_url, str)
            or not data_url.startswith(f"data:{mime};base64,")
        ):
            raise ToolResultBudgetError("tool_result_image_invalid", path, 0, TOOL_RESULT_IMAGE_MAX_BYTES)
        encoded = data_url.partition(",")[2]
        if len(encoded) > ((TOOL_RESULT_IMAGE_MAX_BYTES + 2) // 3) * 4:
            raise ToolResultBudgetError("tool_result_image_too_large", path, len(encoded) * 3 // 4, TOOL_RESULT_IMAGE_MAX_BYTES)
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ToolResultBudgetError("tool_result_image_invalid", path, 0, TOOL_RESULT_IMAGE_MAX_BYTES) from error
        if not content:
            raise ToolResultBudgetError("tool_result_image_invalid", path, 0, TOOL_RESULT_IMAGE_MAX_BYTES)
        if len(content) > TOOL_RESULT_IMAGE_MAX_BYTES:
            raise ToolResultBudgetError("tool_result_image_too_large", path, len(content), TOOL_RESULT_IMAGE_MAX_BYTES)
        metadata_attachments.append({**attachment, "data_url": ""})
    metadata_bytes = _json_bytes({**result, "attachments": metadata_attachments})
    if metadata_bytes > TOOL_RESULT_METADATA_MAX_BYTES:
        raise ToolResultBudgetError(
            "tool_result_metadata_too_large", "result", metadata_bytes, TOOL_RESULT_METADATA_MAX_BYTES
        )


def validate_persistable_json(value: JsonValue, path: str = "$", depth: int = 0) -> None:
    """校验线上 JSON 的字符、数值和深度，不改写参数或身份。"""
    if depth > 64:
        raise ValueError(f"JSON nesting exceeds 64 levels at {path}")
    if isinstance(value, str):
        if "\x00" in value or re.search(r"[\ud800-\udfff]", value):
            raise ValueError(f"unsupported text character at {path}")
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite JSON number at {path}")
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"JSON object key must be text at {path}")
            validate_persistable_json(key, f"{path}.<key>", depth + 1)
            validate_persistable_json(item, f"{path}.{key}", depth + 1)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            validate_persistable_json(item, f"{path}[{index}]", depth + 1)
    elif value is not None and not isinstance(value, (bool, int)):
        raise ValueError(f"unsupported JSON value at {path}")


def project_tool_output_text(value: str) -> str:
    """为展示和模型消费显式表示 NUL，保留本地原始结果。"""
    return value.replace("\x00", "␀")


def project_command_output(data: JsonObject) -> JsonObject:
    """仅投影命令输出字段，参数、路径、状态和其他业务数据保持原义。"""
    projected = dict(data)
    for field in ("stdout", "stderr", "output", "text"):
        text = data.get(field)
        if isinstance(text, str):
            projected[field] = project_tool_output_text(text)
    lines = data.get("output_lines")
    if isinstance(lines, list):
        projected_lines: list[JsonValue] = []
        for line in lines:
            if isinstance(line, str):
                projected_lines.append(project_tool_output_text(line))
            elif isinstance(line, dict):
                projected_lines.append(project_command_output(line))
            else:
                projected_lines.append(line)
        projected["output_lines"] = projected_lines
    return projected


def project_tool_output(tool: str, text: str, data: JsonObject) -> tuple[str, JsonObject]:
    """投影已命名的输出字段，并在摘要中声明发生的字符转换。"""
    projected_text = project_tool_output_text(text)
    projected_data = (
        project_command_output(data)
        if tool in {"shell_command", "exec_command", "write_stdin"}
        else dict(data)
    )
    if projected_text != text or projected_data != data:
        projected_text = "[Tool output: NUL represented as ␀]\n" + projected_text
    return projected_text, projected_data


if __name__ == '__main__':
    pass
