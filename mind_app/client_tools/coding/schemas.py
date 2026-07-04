# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

SHELL_COMMAND_INPUT_SCHEMA: dict[str, typing.Any] = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": "要执行的 shell 命令字符串。",
        },
        "cwd": {
            "type": "string",
            "default": ".",
            "description": "命令工作目录。",
        },
        "timeout_sec": {
            "type": "integer",
            "minimum": 1,
            "maximum": 600,
            "default": 60,
            "description": "命令超时秒数。",
        },
        "execution": {
            "type": ["object", "null"],
            "additionalProperties": True,
            "description": "执行元数据。",
        },
    },
    "required": ["command"],
    "additionalProperties": False,
}

SHELL_CALLS_INPUT_SCHEMA: dict[str, typing.Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "minItems": 1,
            "maxItems": 12,
            "items": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的 shell 命令字符串。"},
                    "cwd": {"type": "string", "default": ".", "description": "命令工作目录。"},
                    "timeout_sec": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 600,
                        "default": 60,
                        "description": "命令超时秒数。",
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        },
        "execution": {
            "type": ["object", "null"],
            "additionalProperties": True,
            "description": "执行元数据。",
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}

APPLY_PATCH_INPUT_SCHEMA: dict[str, typing.Any] = {
    "type": "object",
    "properties": {
        "patch": {
            "type": "string",
            "description": "严格 apply_patch 补丁文本。",
        },
        "expected_sha256": {
            "type": ["object", "null"],
            "additionalProperties": {"type": "string"},
            "description": "文件路径到 SHA256 的基线映射。",
        },
        "force": {
            "type": "boolean",
            "default": False,
            "description": "是否跳过 SHA256 基线检查。",
        },
    },
    "required": ["patch"],
    "additionalProperties": False,
}


def shell_command_payload(
    *,
    command: typing.Any,
    cwd: typing.Any = ".",
    timeout_sec: typing.Any = 60
) -> dict[str, typing.Any]:
    """把单条命令参数转换为执行参数。"""
    return {
        "command": str(command or ""),
        "cwd": str(cwd or "."),
        "timeout_sec": normalize_timeout(timeout_sec),
    }


def shell_command_items_payload(
    items: typing.Iterable[dict[str, typing.Any]] | None
) -> list[dict[str, typing.Any]]:
    """把批量命令参数转换为执行参数。"""
    payload: list[dict[str, typing.Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        payload.append(
            shell_command_payload(
                command=item.get("command"),
                cwd=item.get("cwd", "."),
                timeout_sec=item.get("timeout_sec", 60),
            )
        )
    return payload


def normalize_timeout(value: typing.Any) -> int:
    """把超时时间限制到有效范围。"""
    try:
        timeout = int(value or 60)
    except (TypeError, ValueError):
        timeout = 60
    return max(1, min(600, timeout))


if __name__ == '__main__':
    pass
