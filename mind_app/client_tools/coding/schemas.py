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
        "output_encoding": {
            "type": "string",
            "default": "auto",
            "description": "输出文本编码；auto 自动判断，system 使用系统编码。",
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
                    "output_encoding": {
                        "type": "string",
                        "default": "auto",
                        "description": "输出文本编码。",
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

EXEC_COMMAND_INPUT_SCHEMA: dict[str, typing.Any] = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": "要启动的 shell 命令字符串。",
        },
        "cwd": {
            "type": "string",
            "default": ".",
            "description": "命令工作目录。",
        },
        "yield_time_ms": {
            "type": "integer",
            "minimum": 0,
            "maximum": 30000,
            "default": 1000,
            "description": "启动后等待首批输出的毫秒数；命令未结束时返回 session_id。",
        },
        "max_output_chars": {
            "type": "integer",
            "minimum": 1024,
            "maximum": 120000,
            "default": 24000,
            "description": "本次返回输出的最大字符数。",
        },
        "timeout_sec": {
            "type": "integer",
            "minimum": 1,
            "maximum": 7200,
            "default": 1800,
            "description": "会话最大存活秒数。",
        },
        "idle_timeout_sec": {
            "type": "integer",
            "minimum": 1,
            "maximum": 1800,
            "default": 300,
            "description": "无读写活动后的自动清理秒数。",
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

WRITE_STDIN_INPUT_SCHEMA: dict[str, typing.Any] = {
    "type": "object",
    "properties": {
        "session_id": {
            "type": "string",
            "description": "exec_command 返回的会话 ID。",
        },
        "stdin": {
            "type": "string",
            "default": "",
            "description": "写入进程标准输入的文本；为空时仅轮询输出。",
        },
        "wait_ms": {
            "type": "integer",
            "minimum": 0,
            "maximum": 30000,
            "default": 1000,
            "description": "写入后等待新输出的毫秒数。",
        },
        "max_output_chars": {
            "type": "integer",
            "minimum": 1024,
            "maximum": 120000,
            "default": 12000,
            "description": "本次返回输出的最大字符数。",
        },
        "control": {
            "type": "string",
            "enum": ["none", "interrupt", "eof", "terminate", "kill"],
            "default": "none",
            "description": "可选会话控制。",
        },
    },
    "required": ["session_id"],
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
    timeout_sec: typing.Any = 60,
    output_encoding: typing.Any = "auto"
) -> dict[str, typing.Any]:
    """把单条命令参数转换为执行参数。"""
    return {
        "command"         : str(command or ""),
        "cwd"             : str(cwd or "."),
        "timeout_sec"     : normalize_timeout(timeout_sec),
        "output_encoding" : str(output_encoding or "auto").strip() or "auto"
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
                output_encoding=item.get("output_encoding", "auto"),
            )
        )
    return payload


def exec_command_payload(
    *,
    command: typing.Any,
    cwd: typing.Any = ".",
    yield_time_ms: typing.Any = 1000,
    max_output_chars: typing.Any = 24000,
    timeout_sec: typing.Any = 1800,
    idle_timeout_sec: typing.Any = 300
) -> dict[str, typing.Any]:
    """把 exec_command 参数转换为执行参数。"""
    return {
        "command"          : str(command or ""),
        "cwd"              : str(cwd or "."),
        "yield_time_ms"    : normalize_int(yield_time_ms, default=1000, minimum=0, maximum=30000),
        "max_output_chars" : normalize_int(max_output_chars, default=24000, minimum=1024, maximum=120000),
        "timeout_sec"      : normalize_int(timeout_sec, default=1800, minimum=1, maximum=7200),
        "idle_timeout_sec" : normalize_int(idle_timeout_sec, default=300, minimum=1, maximum=1800)
    }


def write_stdin_payload(
    *,
    session_id: typing.Any,
    stdin: typing.Any = "",
    wait_ms: typing.Any = 1000,
    max_output_chars: typing.Any = 12000,
    control: typing.Any = "none"
) -> dict[str, typing.Any]:
    """把 write_stdin 参数转换为执行参数。"""
    normalized_control = str(control or "none").strip().lower() or "none"
    if normalized_control not in {"none", "interrupt", "eof", "terminate", "kill"}:
        normalized_control = "none"

    return {
        "session_id"       : str(session_id or ""),
        "stdin"            : str(stdin or ""),
        "wait_ms"          : normalize_int(wait_ms, default=1000, minimum=0, maximum=30000),
        "max_output_chars" : normalize_int(max_output_chars, default=12000, minimum=1024, maximum=120000),
        "control"          : normalized_control
    }


def normalize_timeout(value: typing.Any) -> int:
    """把超时时间限制到有效范围。"""
    return normalize_int(value, default=60, minimum=1, maximum=600)


def normalize_int(
    value: typing.Any,
    *,
    default: int,
    minimum: int,
    maximum: int
) -> int:
    """把整数值限制到有效范围。"""
    try:
        number = int(value if value is not None else default)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


if __name__ == '__main__':
    pass
