# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

JS_REPL_INPUT_SCHEMA: dict[str, typing.Any] = {
    "type": "object",
    "properties": {
        "code": {
            "type": "string",
            "description": "要在持久 JavaScript 内核中执行的代码。使用 console.log 输出结果。",
        },
        "timeout_ms": {
            "type": "integer",
            "minimum": 0,
            "default": 30000,
            "description": "本次执行的超时毫秒数；超时会重置内核。",
        },
    },
    "required": ["code"],
    "additionalProperties": False,
}

JS_REPL_RESET_INPUT_SCHEMA: dict[str, typing.Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

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
    },
    "required": ["command"],
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
            "description": (
                "完整补丁文本。支持 *** Begin Patch 严格格式，以及标准 unified diff / "
                "git diff。git diff 新建文件示例：\n"
                "diff --git a/hello.txt b/hello.txt\n"
                "new file mode 100644\n"
                "--- /dev/null\n"
                "+++ b/hello.txt\n"
                "@@ -0,0 +1 @@\n"
                "+hello"
            ),
            "examples": [
                "--- /dev/null\n+++ hello.txt\n@@ -0,0 +1 @@\n+hello\n"
            ],
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


if __name__ == '__main__':
    pass
