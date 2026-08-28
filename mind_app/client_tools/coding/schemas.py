# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import typing


PERMISSION_PROFILE_SCHEMA: dict[str, typing.Any] = {
    "type": "object",
    "properties": {
        "network": {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "description": "是否请求网络访问。",
                },
            },
            "additionalProperties": False,
        },
        "file_system": {
            "type": "object",
            "properties": {
                "read": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "请求读取的路径列表。",
                },
                "write": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "请求写入的路径列表。",
                },
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
    "description": "要申请的文件系统或网络权限。",
}


REQUEST_PERMISSIONS_INPUT_SCHEMA: dict[str, typing.Any] = {
    "type": "object",
    "properties": {
        "environment_id": {
            "type": "string",
            "description": "目标执行环境标识；省略时使用当前环境。",
        },
        "reason": {
            "type": "string",
            "description": "向用户展示的权限申请理由。",
        },
        "permissions": PERMISSION_PROFILE_SCHEMA,
    },
    "required": ["permissions"],
    "additionalProperties": False,
}

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
        "sandbox_permissions": {
            "type": "string",
            "enum": [
                "use_default",
                "with_additional_permissions",
                "require_escalated",
            ],
            "default": "use_default",
            "description": (
                "单条命令的沙箱覆盖；with_additional_permissions 必须同时提供 "
                "additional_permissions，require_escalated 需用户审批后使用宿主 shell。"
            ),
        },
        "additional_permissions": {
            **PERMISSION_PROFILE_SCHEMA,
            "description": (
                "仅在 sandbox_permissions 为 with_additional_permissions 时提供的 "
                "文件系统或网络权限申请。"
            ),
        },
        "justification": {
            "type": "string",
            "description": "请求 require_escalated 时展示给用户的审批理由。",
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
        "shell": {
            "type": "string",
            "description": "可选 shell 可执行文件路径；省略时使用系统默认 shell。",
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
        "sandbox_permissions": {
            "type": "string",
            "enum": [
                "use_default",
                "with_additional_permissions",
                "require_escalated",
            ],
            "default": "use_default",
            "description": (
                "单条命令的沙箱覆盖；with_additional_permissions 必须同时提供 "
                "additional_permissions，require_escalated 需用户审批后使用宿主 shell。"
            ),
        },
        "additional_permissions": {
            **PERMISSION_PROFILE_SCHEMA,
            "description": (
                "仅在 sandbox_permissions 为 with_additional_permissions 时提供的 "
                "文件系统或网络权限申请。"
            ),
        },
        "justification": {
            "type": "string",
            "description": "请求 require_escalated 时展示给用户的审批理由。",
        },
    },
    "required": ["command"],
    "additionalProperties": False,
}


def shell_command_input_schema(
    *,
    exec_permission_approvals_enabled: bool = True,
) -> dict[str, typing.Any]:
    """按 inline 权限能力生成单条命令参数协议。"""
    return _with_exec_permission_schema(
        SHELL_COMMAND_INPUT_SCHEMA,
        exec_permission_approvals_enabled=exec_permission_approvals_enabled,
    )


def exec_command_input_schema(
    *,
    exec_permission_approvals_enabled: bool = True,
) -> dict[str, typing.Any]:
    """按 inline 权限能力生成持续命令参数协议。"""
    return _with_exec_permission_schema(
        EXEC_COMMAND_INPUT_SCHEMA,
        exec_permission_approvals_enabled=exec_permission_approvals_enabled,
    )


def _with_exec_permission_schema(
    schema: dict[str, typing.Any],
    *,
    exec_permission_approvals_enabled: bool,
) -> dict[str, typing.Any]:
    """根据能力开关收窄命令沙箱覆盖参数。"""
    result = copy.deepcopy(schema)
    properties = result.get("properties")
    if not isinstance(properties, dict):
        return result
    sandbox = properties.get("sandbox_permissions")
    if isinstance(sandbox, dict):
        sandbox["enum"] = (
            [
                "use_default",
                "with_additional_permissions",
                "require_escalated",
            ]
            if exec_permission_approvals_enabled
            else ["use_default", "require_escalated"]
        )
    if not exec_permission_approvals_enabled:
        properties.pop("additional_permissions", None)
    return result

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
