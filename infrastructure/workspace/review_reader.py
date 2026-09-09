# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import os
import shlex
from pathlib import Path

from agent.ports.review_workspace import (
    ReviewFileRead,
    ReviewRepositoryOperation,
    ReviewRepositoryRead,
    ReviewWorkspaceReadError,
)
from infrastructure.platform.git_safety import (
    EXECUTABLE_FILTER_CONFIG_PATTERN,
    SAFE_BARE_REPOSITORY_CONFIG,
    disabled_git_hooks_config,
)
from infrastructure.platform.workspace import (
    LocalWorkspaceCommandRunner,
    WorkspaceCommand,
    WorkspaceCommandEncodingError,
    WorkspaceCommandError,
    WorkspaceCommandOutput,
    WorkspaceCommandRunner,
)
from infrastructure.workspace.context import WorkspaceContext

REVIEW_READ_TIMEOUT_SEC = 30.0
REVIEW_READ_OUTPUT_BYTES = 512 * 1024
REVIEW_READ_SOURCE_BYTES = 4 * 1024 * 1024
REVIEW_READ_MAX_LINES = 400
REVIEW_READ_MAX_PATHS = 64


class ReviewWorkspaceReader(WorkspaceContext):
    """执行绑定工作区且不允许权限扩张的仓库与文件读取。"""

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        runner: WorkspaceCommandRunner | None = None,
    ) -> None:
        """绑定工作区根和有界非交互命令执行方。"""
        super().__init__(root=root)
        self._runner = runner or LocalWorkspaceCommandRunner()

    async def read_repository(
        self,
        *,
        operation: ReviewRepositoryOperation,
        revision: str | None = None,
        other_revision: str | None = None,
        staged: bool = False,
        paths: tuple[str, ...] = (),
        max_count: int = 20,
    ) -> ReviewRepositoryRead:
        """执行结构化 allowlist 中的单次只读 Git 查询。"""
        normalized_paths = _review_paths(paths)
        args = _repository_arguments(
            operation,
            revision=revision,
            other_revision=other_revision,
            staged=staged,
            paths=normalized_paths,
            max_count=max_count,
        )
        display_command = _display_repository_command(
            operation,
            revision=revision,
            other_revision=other_revision,
            staged=staged,
            paths=normalized_paths,
            max_count=max_count,
        )
        filter_overrides = await self._filter_overrides()
        output = await self._run_git(args, filter_overrides=filter_overrides)
        return ReviewRepositoryRead(
            command=display_command,
            exit_code=output.exit_code,
            stdout=_bounded_output(output.stdout),
            stderr=_bounded_output(output.stderr),
        )

    async def _filter_overrides(self) -> tuple[tuple[str, str], ...]:
        """返回禁用仓库可执行 clean/process filter 的临时配置。"""
        output = await self._run_git((
            "config",
            "--null",
            "--name-only",
            "--get-regexp",
            EXECUTABLE_FILTER_CONFIG_PATTERN,
        ))
        if output.exit_code not in {0, 1}:
            raise ReviewWorkspaceReadError(
                f"could not inspect Git filters: exit code {output.exit_code}"
            )
        if len(output.stdout.encode("utf-8")) > REVIEW_READ_OUTPUT_BYTES:
            raise ReviewWorkspaceReadError("Git filter catalog exceeds the read limit")
        drivers = sorted({
            key.removesuffix(".clean").removesuffix(".process")
            for key in output.stdout.split("\0")
            if key.endswith((".clean", ".process"))
        })
        if len(drivers) > 128:
            raise ReviewWorkspaceReadError("Git filter catalog contains too many drivers")
        return tuple(
            item
            for driver in drivers
            for item in (
                (f"{driver}.clean", ""),
                (f"{driver}.process", ""),
                (f"{driver}.required", "false"),
            )
        )

    async def _run_git(
        self,
        args: tuple[str, ...],
        *,
        filter_overrides: tuple[tuple[str, str], ...] = (),
    ) -> WorkspaceCommandOutput:
        """执行一条带统一只读防护的有界 Git 命令。"""
        environment: list[tuple[str, str | None]] = [
            ("GIT_OPTIONAL_LOCKS", "0"),
            ("GIT_TERMINAL_PROMPT", "0"),
            ("GIT_PAGER", "cat"),
            ("GIT_EXTERNAL_DIFF", None),
            ("GIT_DIFF_OPTS", None),
        ]
        if filter_overrides:
            environment.append(("GIT_CONFIG_COUNT", str(len(filter_overrides))))
            for index, (key, value) in enumerate(filter_overrides):
                environment.extend((
                    (f"GIT_CONFIG_KEY_{index}", key),
                    (f"GIT_CONFIG_VALUE_{index}", value),
                ))
        command = WorkspaceCommand(
            argv=(
                "git",
                "--no-pager",
                "-c",
                SAFE_BARE_REPOSITORY_CONFIG,
                "-c",
                "core.fsmonitor=false",
                "-c",
                disabled_git_hooks_config(),
                "-c",
                "core.pager=cat",
                "-c",
                "color.ui=false",
                "-c",
                "core.quotepath=false",
                "-c",
                "i18n.logOutputEncoding=utf-8",
                *args,
            ),
            cwd=self.root,
            env=tuple(environment),
            timeout_sec=REVIEW_READ_TIMEOUT_SEC,
            output_bytes_cap=REVIEW_READ_OUTPUT_BYTES + 1,
            require_utf8_output=True,
        )
        try:
            output = await self._runner.run(command)
        except WorkspaceCommandEncodingError as error:
            raise ReviewWorkspaceReadError(
                "Git output is not valid UTF-8."
            ) from error
        except WorkspaceCommandError as error:
            raise ReviewWorkspaceReadError(str(error)) from error
        return output

    async def read_file(
        self,
        *,
        path: str,
        start_line: int = 1,
        max_lines: int = 200,
    ) -> ReviewFileRead:
        """读取工作区内一个 UTF-8 文本文件的有界行范围。"""
        return await asyncio.to_thread(
            self._read_file,
            path,
            start_line,
            max_lines,
        )

    def _read_file(
        self,
        path: str,
        start_line: int,
        max_lines: int,
    ) -> ReviewFileRead:
        """在线程中完成受路径与容量约束的文件读取。"""
        target = self.resolve_path(path)
        if target == self.root or self.is_excluded_path(target):
            raise ReviewWorkspaceReadError("path is excluded from review reads")
        if not target.is_file():
            raise ReviewWorkspaceReadError("path is not a file")
        try:
            source_bytes = target.stat().st_size
        except OSError as error:
            raise ReviewWorkspaceReadError(str(error)) from error
        if source_bytes > REVIEW_READ_SOURCE_BYTES:
            raise ReviewWorkspaceReadError("file exceeds the review read limit")
        if not self.looks_text(target):
            raise ReviewWorkspaceReadError("file is not recognized as text")
        if isinstance(start_line, bool) or start_line < 1:
            raise ReviewWorkspaceReadError("start_line must be a positive integer")
        if (
            isinstance(max_lines, bool)
            or not 1 <= max_lines <= REVIEW_READ_MAX_LINES
        ):
            raise ReviewWorkspaceReadError(
                f"max_lines must be between 1 and {REVIEW_READ_MAX_LINES}"
            )

        try:
            with target.open("rb") as stream:
                raw_content = stream.read(REVIEW_READ_SOURCE_BYTES + 1)
            if len(raw_content) > REVIEW_READ_SOURCE_BYTES:
                raise ReviewWorkspaceReadError("file exceeds the review read limit")
            normalized_content = raw_content.decode("utf-8", errors="strict")
            normalized_content = normalized_content.replace("\r\n", "\n").replace(
                "\r",
                "\n",
            )
            lines = normalized_content.splitlines(keepends=True)
        except (OSError, UnicodeError) as error:
            raise ReviewWorkspaceReadError(
                "file could not be read as UTF-8 text"
            ) from error
        total_lines = len(lines)
        start_index = min(start_line - 1, total_lines)
        selected = lines[start_index:start_index + max_lines]
        content = "".join(selected)
        end_line = start_index + len(selected)
        return ReviewFileRead(
            path=self.relative_path(target),
            content=content,
            start_line=start_index + 1,
            end_line=end_line,
            total_lines=total_lines,
            truncated=end_line < total_lines,
        )


def _repository_arguments(
    operation: ReviewRepositoryOperation,
    *,
    revision: str | None,
    other_revision: str | None,
    staged: bool,
    paths: tuple[str, ...],
    max_count: int,
) -> tuple[str, ...]:
    """把结构化读取请求映射为唯一受支持的 Git argv。"""
    normalized_revision = _review_revision(revision, field_name="revision")
    normalized_other = _review_revision(
        other_revision,
        field_name="other_revision",
    )
    path_args = ("--", *paths) if paths else ()
    if operation == "status":
        _reject_fields(normalized_revision, normalized_other, staged, max_count != 20)
        return ("status", "--short", *path_args)
    if operation == "diff":
        if normalized_other is not None or max_count != 20:
            raise ReviewWorkspaceReadError("diff received unsupported fields")
        return (
            "diff",
            "--binary",
            "--full-index",
            "--no-textconv",
            "--no-ext-diff",
            "--submodule=short",
            "--ignore-submodules=dirty",
            "--color=never",
            *(("--cached",) if staged else ()),
            *((normalized_revision,) if normalized_revision is not None else ()),
            *path_args,
        )
    if operation == "show":
        if normalized_revision is None:
            raise ReviewWorkspaceReadError("show requires revision")
        if normalized_other is not None or staged or max_count != 20:
            raise ReviewWorkspaceReadError("show received unsupported fields")
        return (
            "show",
            "--binary",
            "--full-index",
            "--no-textconv",
            "--no-ext-diff",
            "--submodule=short",
            "--color=never",
            normalized_revision,
            *path_args,
        )
    if operation == "merge_base":
        if normalized_revision is None or normalized_other is None:
            raise ReviewWorkspaceReadError(
                "merge_base requires revision and other_revision"
            )
        if staged or paths or max_count != 20:
            raise ReviewWorkspaceReadError("merge_base received unsupported fields")
        return ("merge-base", normalized_revision, normalized_other)
    if operation == "log":
        if normalized_other is not None or staged or paths:
            raise ReviewWorkspaceReadError("log received unsupported fields")
        if isinstance(max_count, bool) or not 1 <= max_count <= 100:
            raise ReviewWorkspaceReadError("max_count must be between 1 and 100")
        return (
            "log",
            "-n",
            str(max_count),
            "--encoding=UTF-8",
            "--pretty=format:%H%x09%s",
            *((normalized_revision,) if normalized_revision is not None else ()),
        )
    if operation == "list_files":
        if normalized_other is not None or staged or max_count != 20:
            raise ReviewWorkspaceReadError("list_files received unsupported fields")
        if normalized_revision is not None:
            return (
                "ls-tree",
                "-r",
                "--name-only",
                normalized_revision,
                *path_args,
            )
        return (
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            *path_args,
        )
    raise ReviewWorkspaceReadError("repository operation is not supported")


def _reject_fields(
    revision: str | None,
    other_revision: str | None,
    staged: bool,
    changed_max_count: bool,
) -> None:
    """拒绝指定操作没有声明的参数组合。"""
    if revision is not None or other_revision is not None or staged or changed_max_count:
        raise ReviewWorkspaceReadError("operation received unsupported fields")


def _review_revision(value: str | None, *, field_name: str) -> str | None:
    """校验作为单个 argv 传递的 Git revision，拒绝选项注入。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ReviewWorkspaceReadError(f"{field_name} must be text")
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 255
        or normalized.startswith("-")
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in normalized)
    ):
        raise ReviewWorkspaceReadError(f"{field_name} is invalid")
    return normalized


def _review_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    """规范化 Git pathspec，并拒绝越权路径和选项注入。"""
    if not isinstance(paths, tuple):
        raise ReviewWorkspaceReadError("paths must be an immutable tuple")
    if len(paths) > REVIEW_READ_MAX_PATHS:
        raise ReviewWorkspaceReadError("too many review paths")
    normalized_paths: list[str] = []
    for value in paths:
        if not isinstance(value, str):
            raise ReviewWorkspaceReadError("review path must be text")
        normalized = value.replace("\\", "/").strip()
        parts = normalized.split("/")
        if (
            not normalized
            or len(normalized) > 1024
            or normalized.startswith(("/", "-"))
            or (len(normalized) >= 2 and normalized[1] == ":")
            or any(part in {"", ".", ".."} for part in parts)
            or any(ord(char) < 32 or ord(char) == 127 for char in normalized)
        ):
            raise ReviewWorkspaceReadError("review path is invalid")
        normalized_paths.append(normalized)
    return tuple(normalized_paths)


def _bounded_output(value: str) -> str:
    """把命令输出收敛到协议上限并显式标注截断。"""
    encoded = value.encode("utf-8")
    if len(encoded) <= REVIEW_READ_OUTPUT_BYTES:
        return value
    bounded = encoded[:REVIEW_READ_OUTPUT_BYTES]
    text = bounded.decode("utf-8", errors="ignore")
    return f"{text}\n...[review output truncated]"


def _display_command(argv: tuple[str, ...]) -> str:
    """生成跨平台稳定且不参与执行的 Git 命令摘要。"""
    return shlex.join(argv)


def _display_repository_command(
    operation: ReviewRepositoryOperation,
    *,
    revision: str | None,
    other_revision: str | None,
    staged: bool,
    paths: tuple[str, ...],
    max_count: int,
) -> str:
    """生成与 Codex 轨迹一致且不暴露执行防护参数的逻辑命令。"""
    if operation == "status":
        command = ["git", "status", "--short"]
    elif operation == "diff":
        command = ["git", "diff"]
        if staged:
            command.append("--cached")
        if revision:
            command.append(revision)
    elif operation == "show":
        command = ["git", "show"]
        if revision:
            command.append(revision)
    elif operation == "merge_base":
        command = ["git", "merge-base"]
        command.extend(value for value in (revision, other_revision) if value)
    elif operation == "log":
        command = ["git", "log", "-n", str(max_count)]
        if revision:
            command.append(revision)
    elif revision:
        command = ["git", "ls-tree", "-r", "--name-only", revision]
    else:
        command = ["git", "ls-files"]
    if paths:
        command.extend(("--", *paths))
    return _display_command(tuple(command))


if __name__ == '__main__':
    pass
