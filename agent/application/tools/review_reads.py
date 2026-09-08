# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.application.tools.context import ToolHandlerContext
from agent.application.tools.definitions import ClientTool
from agent.application.tools.results import (
    LocalToolResult,
    client_tool_result,
)
from agent.ports.review_workspace import (
    ReviewRepositoryOperation,
    ReviewWorkspaceReadError,
    WorkspaceReviewReadPort,
)

READ_REPOSITORY_TOOL: typing.Final[str] = "read_repository"
READ_FILE_TOOL: typing.Final[str] = "read_file"

_REPOSITORY_OPERATIONS = frozenset({
    "status",
    "diff",
    "show",
    "merge_base",
    "log",
    "list_files",
})

READ_REPOSITORY_INPUT_SCHEMA: typing.Final[dict[str, typing.Any]] = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": sorted(_REPOSITORY_OPERATIONS),
            "description": (
                "Read-only Git operation. Use status first for uncommitted changes, "
                "diff for working/staged/base changes, show for a commit, merge_base "
                "for two revisions, log for history, and list_files for repository paths."
            ),
        },
        "revision": {
            "type": "string",
            "minLength": 1,
            "maxLength": 255,
            "description": "Optional Git revision; required by show.",
        },
        "other_revision": {
            "type": "string",
            "minLength": 1,
            "maxLength": 255,
            "description": "Second Git revision required only by merge_base.",
        },
        "staged": {
            "type": "boolean",
            "default": False,
            "description": "For diff only, inspect staged changes with --cached.",
        },
        "paths": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 1024},
            "maxItems": 64,
            "default": [],
            "description": "Optional normalized repository-relative path filters.",
        },
        "max_count": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "default": 20,
            "description": "Maximum commits returned by log.",
        },
    },
    "required": ["operation"],
    "additionalProperties": False,
}

READ_FILE_INPUT_SCHEMA: typing.Final[dict[str, typing.Any]] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "minLength": 1,
            "maxLength": 1024,
            "description": "Normalized repository-relative UTF-8 text file path.",
        },
        "start_line": {
            "type": "integer",
            "minimum": 1,
            "default": 1,
        },
        "max_lines": {
            "type": "integer",
            "minimum": 1,
            "maximum": 400,
            "default": 200,
        },
    },
    "required": ["path"],
    "additionalProperties": False,
}


def review_read_tools(reader: WorkspaceReviewReadPort) -> list[ClientTool]:
    """构造由工作区策略证明只读的 Review 仓库检查工具。"""

    async def read_repository_handler(
        arguments: dict[str, typing.Any],
        _runtime: ToolHandlerContext,
    ) -> LocalToolResult:
        """校验并执行一次结构化只读 Git 查询。"""
        try:
            values = _repository_arguments(arguments)
            result = await reader.read_repository(**values)
        except (ReviewWorkspaceReadError, TypeError, ValueError) as error:
            return client_tool_result(
                tool=READ_REPOSITORY_TOOL,
                ok=False,
                text=str(error).strip() or type(error).__name__,
                args=arguments,
                data={"error": str(error).strip() or type(error).__name__},
            )
        return client_tool_result(
            tool=READ_REPOSITORY_TOOL,
            ok=result.exit_code == 0,
            text=(
                "repository read completed"
                if result.exit_code == 0
                else f"git exited with status {result.exit_code}"
            ),
            args=arguments,
            data={
                "command": result.command,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "status": "exited",
            },
        )

    async def read_file_handler(
        arguments: dict[str, typing.Any],
        _runtime: ToolHandlerContext,
    ) -> LocalToolResult:
        """校验并执行一次有界 UTF-8 文件读取。"""
        try:
            values = _file_arguments(arguments)
            result = await reader.read_file(**values)
        except (ReviewWorkspaceReadError, TypeError, ValueError) as error:
            return client_tool_result(
                tool=READ_FILE_TOOL,
                ok=False,
                text=str(error).strip() or type(error).__name__,
                args=arguments,
                data={"error": str(error).strip() or type(error).__name__},
            )
        return client_tool_result(
            tool=READ_FILE_TOOL,
            ok=True,
            text=f"read {result.path}:{result.start_line}-{result.end_line}",
            args=arguments,
            data={
                "path": result.path,
                "content": result.content,
                "start_line": result.start_line,
                "end_line": result.end_line,
                "total_lines": result.total_lines,
                "truncated": result.truncated,
            },
        )

    read_only_meta = {
        "hidden": False,
        "domain": "coding",
        "class": "review_read",
        "review_read_only": True,
    }
    return [
        ClientTool(
            name=READ_REPOSITORY_TOOL,
            description=(
                "Inspect the current Git repository through a fixed read-only operation "
                "allowlist. Commands are executed without a shell, prompts, hooks, external "
                "diffs, writes, or permission escalation."
            ),
            input_schema=READ_REPOSITORY_INPUT_SCHEMA,
            meta=read_only_meta,
            handler=read_repository_handler,
        ),
        ClientTool(
            name=READ_FILE_TOOL,
            description=(
                "Read a bounded line range from a UTF-8 text file inside the repository. "
                "Paths outside the workspace and excluded metadata directories are rejected."
            ),
            input_schema=READ_FILE_INPUT_SCHEMA,
            meta=read_only_meta,
            handler=read_file_handler,
        ),
    ]


def _repository_arguments(
    arguments: dict[str, typing.Any],
) -> dict[str, typing.Any]:
    """严格解析 read_repository 模型参数。"""
    _reject_unknown(
        arguments,
        frozenset({
            "operation",
            "revision",
            "other_revision",
            "staged",
            "paths",
            "max_count",
        }),
    )
    operation_value = arguments.get("operation")
    if operation_value not in _REPOSITORY_OPERATIONS:
        raise ValueError("repository operation is not supported")
    operation: ReviewRepositoryOperation = operation_value
    revision = _optional_text(arguments.get("revision"), "revision")
    other_revision = _optional_text(
        arguments.get("other_revision"),
        "other_revision",
    )
    staged = arguments.get("staged", False)
    if not isinstance(staged, bool):
        raise TypeError("staged must be a boolean")
    raw_paths = arguments.get("paths", [])
    if not isinstance(raw_paths, list) or any(
        not isinstance(item, str) for item in raw_paths
    ):
        raise TypeError("paths must be an array of strings")
    max_count = arguments.get("max_count", 20)
    if isinstance(max_count, bool) or not isinstance(max_count, int):
        raise TypeError("max_count must be an integer")
    return {
        "operation": operation,
        "revision": revision,
        "other_revision": other_revision,
        "staged": staged,
        "paths": tuple(raw_paths),
        "max_count": max_count,
    }


def _file_arguments(arguments: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """严格解析 read_file 模型参数。"""
    _reject_unknown(arguments, frozenset({"path", "start_line", "max_lines"}))
    path = arguments.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path is required")
    start_line = arguments.get("start_line", 1)
    max_lines = arguments.get("max_lines", 200)
    if isinstance(start_line, bool) or not isinstance(start_line, int):
        raise TypeError("start_line must be an integer")
    if isinstance(max_lines, bool) or not isinstance(max_lines, int):
        raise TypeError("max_lines must be an integer")
    return {
        "path": path,
        "start_line": start_line,
        "max_lines": max_lines,
    }


def _optional_text(value: typing.Any, field_name: str) -> str | None:
    """读取可省略的非空文本字段。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be text")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _reject_unknown(
    arguments: dict[str, typing.Any],
    allowed: frozenset[str],
) -> None:
    """拒绝模型工具调用中的未声明字段。"""
    unknown = sorted(set(arguments).difference(allowed))
    if unknown:
        raise ValueError("unsupported arguments: " + ", ".join(unknown))


if __name__ == '__main__':
    pass
