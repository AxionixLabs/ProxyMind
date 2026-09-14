# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import math

from protocol.schema.json_value import (
    JsonObject,
    JsonValue,
)


def validate_persistable_json(value: JsonValue, path: str = "$", depth: int = 0) -> None:
    """校验线上 JSON 的字符、数值和深度，不改写参数或身份。"""
    if depth > 64:
        raise ValueError(f"JSON nesting exceeds 64 levels at {path}")
    if isinstance(value, str):
        if "\x00" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
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
