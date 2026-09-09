# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import enum
import os
from dataclasses import dataclass
from pathlib import Path

from protocol.schema.review import (
    ClientReviewWorkspace,
    ReviewBaseBranchTarget,
    ReviewTarget,
)

from .git_safety import (
    SAFE_BARE_REPOSITORY_CONFIG,
    disabled_git_hooks_config,
)
from .workspace import (
    LocalWorkspaceCommandRunner,
    WorkspaceCommand,
    WorkspaceCommandEncodingError,
    WorkspaceCommandError,
    WorkspaceCommandOutput,
    WorkspaceCommandRunner,
)

REVIEW_GIT_TIMEOUT_SEC = 30.0
REVIEW_GIT_PROBE_TIMEOUT_SEC = 5.0
REVIEW_GIT_METADATA_BYTES_CAP = 512 * 1024
REVIEW_RECENT_COMMIT_LIMIT = 100


class ReviewGitErrorCode(enum.Enum):
    """描述 Review Git 目标解析的稳定失败类别。"""

    NOT_GIT_REPOSITORY = "not_git_repository"
    COMMAND_FAILED = "command_failed"
    INVALID_OUTPUT = "invalid_output"


class ReviewGitError(RuntimeError):
    """描述无法查询 Review catalog 或冻结目标派生事实。"""

    def __init__(self, code: ReviewGitErrorCode, message: str) -> None:
        """保存稳定错误类别和可展示说明。"""
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ReviewBranchCatalog:
    """保存当前分支及默认分支置顶的本地分支列表。"""

    current_branch: str | None
    branches: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReviewCommitEntry:
    """保存提交选择器使用的稳定提交摘要。"""

    sha: str
    timestamp: int
    subject: str


@dataclass(frozen=True, slots=True)
class ResolvedReviewInput:
    """保存一次 Review 的冻结目标派生事实和规范空工作区。"""

    target: ReviewTarget
    workspace: ClientReviewWorkspace


class WorkspaceReviewGitService:
    """查询 Review Git 目录并冻结客户端拥有的目标派生事实。"""

    def __init__(self, runner: WorkspaceCommandRunner | None = None) -> None:
        """绑定非交互工作区命令执行方。"""
        self._runner = runner or LocalWorkspaceCommandRunner()

    async def branch_catalog(
        self,
        cwd: str | os.PathLike[str],
    ) -> ReviewBranchCatalog:
        """返回当前分支和按 Codex 规则排序的本地分支。"""
        workdir = await self._repository(cwd)
        branch_output = await self._run(
            workdir,
            (
                "for-each-ref",
                "--format=%(refname:short)",
                "refs/heads",
            ),
            label="list local branches",
        )
        branches = sorted({
            branch.strip()
            for branch in branch_output.stdout.splitlines()
            if branch.strip()
        })
        default_branch = next(
            (candidate for candidate in ("main", "master") if candidate in branches),
            None,
        )
        if default_branch is not None:
            branches.remove(default_branch)
            branches.insert(0, default_branch)

        current_output = await self._run(
            workdir,
            ("branch", "--show-current"),
            label="read current branch",
        )
        current_branch = current_output.stdout.strip() or None
        return ReviewBranchCatalog(
            current_branch=current_branch,
            branches=tuple(branches),
        )

    async def recent_commits(
        self,
        cwd: str | os.PathLike[str],
        *,
        limit: int = REVIEW_RECENT_COMMIT_LIMIT,
    ) -> tuple[ReviewCommitEntry, ...]:
        """返回从 HEAD 可达的最近提交摘要。"""
        workdir = await self._repository(cwd)
        bounded_limit = min(REVIEW_RECENT_COMMIT_LIMIT, max(1, int(limit)))
        output = await self._run(
            workdir,
            (
                "log",
                "-n",
                str(bounded_limit),
                "--encoding=UTF-8",
                "--pretty=format:%H%x1f%ct%x1f%s",
            ),
            allowed_exit_codes=frozenset({0, 128}),
            label="list recent commits",
        )
        if output.exit_code == 128:
            return ()

        entries: list[ReviewCommitEntry] = []
        for line in output.stdout.splitlines():
            parts = line.split("\x1f", 2)
            if len(parts) != 3:
                continue
            sha, raw_timestamp, subject = parts
            normalized_sha = _git_object_id(sha)
            try:
                timestamp = int(raw_timestamp.strip())
            except ValueError:
                continue
            if normalized_sha is None or timestamp < 0:
                continue
            entries.append(ReviewCommitEntry(
                sha=normalized_sha,
                timestamp=timestamp,
                subject=subject.strip(),
            ))
        return tuple(entries)

    async def freeze(
        self,
        cwd: str | os.PathLike[str],
        target: ReviewTarget,
    ) -> ResolvedReviewInput:
        """冻结 target 派生事实；仓库内容由 Review 命令工具按需观察。"""
        workdir = await self._repository(cwd)
        resolved_target = target
        if isinstance(target, ReviewBaseBranchTarget):
            resolved_target = ReviewBaseBranchTarget(
                branch=target.branch,
                merge_base_sha=await self._merge_base_with_head(
                    workdir,
                    target.branch,
                ),
            )
        return ResolvedReviewInput(
            target=resolved_target,
            workspace=ClientReviewWorkspace.create(),
        )

    async def _merge_base_with_head(
        self,
        cwd: Path,
        branch: str,
    ) -> str | None:
        """按 Codex 规则解析 HEAD 与本地或较新 upstream 的 merge base。"""
        head = await self._resolve_commit(cwd, "HEAD")
        if head is None:
            return None
        branch_ref = await self._resolve_commit(cwd, branch)
        if branch_ref is None:
            return None
        preferred_ref = branch_ref
        upstream = await self._upstream_if_remote_ahead(cwd, branch)
        if upstream is not None:
            upstream_ref = await self._resolve_commit(cwd, upstream)
            if upstream_ref is not None:
                preferred_ref = upstream_ref
        output = await self._run(
            cwd,
            ("merge-base", head, preferred_ref),
            allowed_exit_codes=frozenset({0, 1, 128}),
            label="resolve merge base",
        )
        if output.exit_code != 0:
            return None
        merge_base = _git_object_id(output.stdout)
        if merge_base is None:
            raise ReviewGitError(
                ReviewGitErrorCode.INVALID_OUTPUT,
                "Git returned an invalid merge base.",
            )
        return merge_base

    async def _resolve_commit(self, cwd: Path, reference: str) -> str | None:
        """把可解析 ref 转换为完整 commit 标识，缺失时返回空。"""
        output = await self._run(
            cwd,
            ("rev-parse", "--verify", f"{reference}^{{commit}}"),
            allowed_exit_codes=frozenset({0, 128}),
            label="resolve git reference",
        )
        if output.exit_code != 0:
            return None
        resolved = _git_object_id(output.stdout)
        if resolved is None:
            raise ReviewGitError(
                ReviewGitErrorCode.INVALID_OUTPUT,
                "Git returned an invalid commit identifier.",
            )
        return resolved

    async def _upstream_if_remote_ahead(
        self,
        cwd: Path,
        branch: str,
    ) -> str | None:
        """仅在 upstream 比本地 branch 多提交时返回 upstream 名称。"""
        upstream_output = await self._run(
            cwd,
            (
                "rev-parse",
                "--abbrev-ref",
                "--symbolic-full-name",
                f"{branch}@{{upstream}}",
            ),
            allowed_exit_codes=frozenset({0, 128}),
            label="resolve branch upstream",
        )
        if upstream_output.exit_code != 0:
            return None
        upstream = upstream_output.stdout.strip()
        if not upstream:
            return None
        counts = await self._run(
            cwd,
            (
                "rev-list",
                "--left-right",
                "--count",
                f"{branch}...{upstream}",
            ),
            allowed_exit_codes=frozenset({0, 128}),
            label="compare branch upstream",
        )
        if counts.exit_code != 0:
            return None
        parts = counts.stdout.split()
        try:
            remote_ahead = int(parts[1]) if len(parts) >= 2 else 0
        except ValueError:
            return None
        return upstream if remote_ahead > 0 else None

    async def _repository(self, cwd: str | os.PathLike[str]) -> Path:
        """解析工作目录并确认其位于 Git work tree。"""
        workdir = Path(cwd).resolve()
        output = await self._run(
            workdir,
            ("rev-parse", "--is-inside-work-tree"),
            allowed_exit_codes=frozenset({0, 128}),
            timeout_sec=REVIEW_GIT_PROBE_TIMEOUT_SEC,
            label="locate git repository",
        )
        if output.exit_code != 0 or output.stdout.strip() != "true":
            raise ReviewGitError(
                ReviewGitErrorCode.NOT_GIT_REPOSITORY,
                "Review requires a Git working tree.",
            )
        return workdir

    async def _run(
        self,
        cwd: Path,
        args: tuple[str, ...],
        *,
        allowed_exit_codes: frozenset[int] = frozenset({0}),
        timeout_sec: float = REVIEW_GIT_TIMEOUT_SEC,
        label: str,
    ) -> WorkspaceCommandOutput:
        """使用统一非交互 Git 前缀执行有界元数据命令。"""
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
            cwd=cwd,
            env=(
                ("GIT_OPTIONAL_LOCKS", "0"),
                ("GIT_TERMINAL_PROMPT", "0"),
                ("GIT_PAGER", "cat"),
                ("GIT_EXTERNAL_DIFF", None),
                ("GIT_DIFF_OPTS", None),
            ),
            timeout_sec=timeout_sec,
            output_bytes_cap=REVIEW_GIT_METADATA_BYTES_CAP,
            require_utf8_output=True,
        )
        try:
            output = await self._runner.run(command)
        except WorkspaceCommandEncodingError as error:
            raise ReviewGitError(
                ReviewGitErrorCode.INVALID_OUTPUT,
                f"Could not {label}: Git output is not valid UTF-8.",
            ) from error
        except WorkspaceCommandError as error:
            raise ReviewGitError(
                ReviewGitErrorCode.COMMAND_FAILED,
                f"Could not {label}: {error}",
            ) from error
        if output.exit_code not in allowed_exit_codes:
            raise ReviewGitError(
                ReviewGitErrorCode.COMMAND_FAILED,
                f"Could not {label}: git exited with status {output.exit_code}.",
            )
        return output


def _git_object_id(value: str) -> str | None:
    """规范化 Git 返回的完整 SHA-1 或 SHA-256 对象标识。"""
    normalized = value.strip().lower()
    if len(normalized) not in {40, 64}:
        return None
    if any(char not in "0123456789abcdef" for char in normalized):
        return None
    return normalized


if __name__ == '__main__':
    pass
