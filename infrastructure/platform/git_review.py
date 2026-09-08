# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import enum
import os
from dataclasses import dataclass
from pathlib import Path

from protocol.schema.review import (
    REVIEW_FILE_MAX_COUNT,
    REVIEW_WORKSPACE_MAX_BYTES,
    ClientReviewWorkspace,
    ReviewBaseBranchTarget,
    ReviewCommitTarget,
    ReviewCustomTarget,
    ReviewTarget,
    ReviewUncommittedTarget,
)

from .git_diff import (
    EXECUTABLE_FILTER_CONFIG_PATTERN,
    SAFE_BARE_REPOSITORY_CONFIG,
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
REVIEW_GIT_PATCH_BYTES_CAP = REVIEW_WORKSPACE_MAX_BYTES + 1
REVIEW_RECENT_COMMIT_LIMIT = 100


class ReviewGitErrorCode(enum.Enum):
    """描述 Review Git 操作的稳定失败类别。"""

    NOT_GIT_REPOSITORY = "not_git_repository"
    HEAD_MISSING = "head_missing"
    TARGET_MISSING = "target_missing"
    EMPTY_DIFF = "empty_diff"
    SNAPSHOT_TOO_LARGE = "snapshot_too_large"
    COMMAND_FAILED = "command_failed"
    INVALID_OUTPUT = "invalid_output"


class ReviewGitError(RuntimeError):
    """描述无法生成完整 Review catalog 或快照的失败。"""

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


class WorkspaceReviewGitService:
    """查询 Review Git 目录并冻结目标对应的客户端快照。"""

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
            normalized_sha = sha.strip().lower()
            try:
                timestamp = int(raw_timestamp.strip())
            except ValueError:
                continue
            if (
                len(normalized_sha) not in {40, 64}
                or any(char not in "0123456789abcdef" for char in normalized_sha)
                or timestamp < 0
            ):
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
    ) -> ClientReviewWorkspace:
        """冻结指定 Review target 的不可变客户端 patch。"""
        workdir = await self._repository(cwd)
        filter_overrides = await self._filter_overrides(workdir)
        if isinstance(target, ReviewCommitTarget):
            patch = await self._commit_patch(
                workdir,
                target,
                filter_overrides,
            )
        elif isinstance(target, ReviewBaseBranchTarget):
            patch = await self._base_branch_patch(
                workdir,
                target,
                filter_overrides,
            )
        elif isinstance(target, (ReviewUncommittedTarget, ReviewCustomTarget)):
            patch = await self._working_tree_patch(workdir, filter_overrides)
        else:
            raise TypeError("unsupported Review target")

        if not patch.strip() and not isinstance(target, ReviewCustomTarget):
            raise ReviewGitError(
                ReviewGitErrorCode.EMPTY_DIFF,
                "Review target does not contain any changes.",
            )
        try:
            return ClientReviewWorkspace.create(patch=patch)
        except ValueError as error:
            if "limit" in str(error) or "too many" in str(error):
                raise ReviewGitError(
                    ReviewGitErrorCode.SNAPSHOT_TOO_LARGE,
                    "Review workspace exceeds the service limits.",
                ) from error
            raise ReviewGitError(
                ReviewGitErrorCode.INVALID_OUTPUT,
                "Review workspace could not be normalized.",
            ) from error

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

    async def _working_tree_patch(
        self,
        cwd: Path,
        filter_overrides: tuple[tuple[str, str], ...],
    ) -> str:
        """冻结 staged、unstaged 和 untracked 工作区差异。"""
        head = await self._resolve_commit(cwd, "HEAD", required=False)
        if head is None:
            tracked_patch = await self._unborn_worktree_patch(
                cwd,
                filter_overrides,
            )
            return tracked_patch

        tracked_patch = await self._diff(
            cwd,
            (head,),
            filter_overrides,
            label="diff uncommitted changes",
        )
        untracked_patch = await self._untracked_patch(cwd, filter_overrides)
        return self._combine_patch_parts((tracked_patch, untracked_patch))

    async def _unborn_worktree_patch(
        self,
        cwd: Path,
        filter_overrides: tuple[tuple[str, str], ...],
    ) -> str:
        """冻结尚无 HEAD 的工作区全部非忽略文件。"""
        output = await self._run(
            cwd,
            ("ls-files", "-z", "--cached", "--others", "--exclude-standard"),
            label="list unborn worktree files",
        )
        paths = self._nul_paths(output.stdout)
        return await self._paths_against_empty(cwd, paths, filter_overrides)

    async def _untracked_patch(
        self,
        cwd: Path,
        filter_overrides: tuple[tuple[str, str], ...],
    ) -> str:
        """冻结所有非忽略 untracked 文件。"""
        output = await self._run(
            cwd,
            ("ls-files", "-z", "--others", "--exclude-standard"),
            label="list untracked files",
        )
        paths = self._nul_paths(output.stdout)
        return await self._paths_against_empty(cwd, paths, filter_overrides)

    async def _paths_against_empty(
        self,
        cwd: Path,
        paths: tuple[str, ...],
        filter_overrides: tuple[tuple[str, str], ...],
    ) -> str:
        """把路径列表冻结为对空文件的 binary patch。"""
        if len(paths) > REVIEW_FILE_MAX_COUNT:
            raise ReviewGitError(
                ReviewGitErrorCode.SNAPSHOT_TOO_LARGE,
                "Review workspace contains too many untracked files.",
            )
        null_device = "NUL" if os.name == "nt" else "/dev/null"
        parts: list[str] = []
        for path in paths:
            parts.append(await self._diff(
                cwd,
                ("--no-index", "--", null_device, path),
                filter_overrides,
                label="diff untracked file",
            ))
        return self._combine_patch_parts(parts)

    async def _base_branch_patch(
        self,
        cwd: Path,
        target: ReviewBaseBranchTarget,
        filter_overrides: tuple[tuple[str, str], ...],
    ) -> str:
        """相对 HEAD 与基础分支的 merge base 冻结完整工作区差异。"""
        head = await self._resolve_commit(cwd, "HEAD", required=True)
        branch = await self._resolve_commit(
            cwd,
            f"refs/heads/{target.branch}",
            required=True,
        )
        preferred = await self._preferred_branch_commit(
            cwd,
            target.branch,
            branch,
        )
        merge_base_output = await self._run(
            cwd,
            ("merge-base", head, preferred),
            allowed_exit_codes=frozenset({0, 1}),
            label="resolve review merge base",
        )
        merge_base = merge_base_output.stdout.strip().lower()
        if merge_base_output.exit_code != 0 or not merge_base:
            raise ReviewGitError(
                ReviewGitErrorCode.TARGET_MISSING,
                "Review base branch does not share history with HEAD.",
            )
        tracked_patch = await self._diff(
            cwd,
            (merge_base,),
            filter_overrides,
            label="diff base branch",
        )
        untracked_patch = await self._untracked_patch(cwd, filter_overrides)
        return self._combine_patch_parts((tracked_patch, untracked_patch))

    async def _preferred_branch_commit(
        self,
        cwd: Path,
        branch: str,
        local_commit: str,
    ) -> str:
        """在上游领先本地基础分支时使用上游提交。"""
        upstream_output = await self._run(
            cwd,
            (
                "rev-parse",
                "--abbrev-ref",
                "--symbolic-full-name",
                f"{branch}@{{upstream}}",
            ),
            allowed_exit_codes=frozenset({0, 128}),
            label="resolve base branch upstream",
        )
        if upstream_output.exit_code != 0:
            return local_commit
        upstream = upstream_output.stdout.strip()
        if not upstream:
            return local_commit
        counts_output = await self._run(
            cwd,
            ("rev-list", "--left-right", "--count", f"{branch}...{upstream}"),
            allowed_exit_codes=frozenset({0, 128}),
            label="compare base branch upstream",
        )
        if counts_output.exit_code != 0:
            return local_commit
        counts = counts_output.stdout.split()
        if len(counts) != 2:
            return local_commit
        try:
            remote_ahead = int(counts[1])
        except ValueError:
            return local_commit
        if remote_ahead <= 0:
            return local_commit
        upstream_commit = await self._resolve_commit(cwd, upstream, required=False)
        return upstream_commit or local_commit

    async def _commit_patch(
        self,
        cwd: Path,
        target: ReviewCommitTarget,
        filter_overrides: tuple[tuple[str, str], ...],
    ) -> str:
        """冻结指定提交自身的 binary patch，包含 root commit。"""
        commit = await self._resolve_commit(cwd, target.sha, required=True)
        return await self._diff(
            cwd,
            ("--root", "--format=", commit),
            filter_overrides,
            command="show",
            label="show review commit",
        )

    async def _resolve_commit(
        self,
        cwd: Path,
        revision: str,
        *,
        required: bool,
    ) -> str | None:
        """把 revision 解析为完整 commit SHA。"""
        output = await self._run(
            cwd,
            ("rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"),
            allowed_exit_codes=frozenset({0, 128}),
            label="resolve review commit",
        )
        resolved = output.stdout.strip().lower()
        if output.exit_code == 0 and resolved:
            return resolved
        if required:
            code = (
                ReviewGitErrorCode.HEAD_MISSING
                if revision == "HEAD"
                else ReviewGitErrorCode.TARGET_MISSING
            )
            raise ReviewGitError(code, f"Review revision is unavailable: {revision}")
        return None

    async def _filter_overrides(
        self,
        cwd: Path,
    ) -> tuple[tuple[str, str], ...]:
        """返回禁用仓库可执行 clean/process filter 的临时配置。"""
        output = await self._run(
            cwd,
            (
                "config",
                "--null",
                "--name-only",
                "--get-regexp",
                EXECUTABLE_FILTER_CONFIG_PATTERN,
            ),
            allowed_exit_codes=frozenset({0, 1}),
            label="inspect git filters",
        )
        drivers = sorted({
            key.removesuffix(".clean").removesuffix(".process")
            for key in output.stdout.split("\0")
            if key.endswith((".clean", ".process"))
        })
        return tuple(
            item
            for driver in drivers
            for item in (
                (f"{driver}.clean", ""),
                (f"{driver}.process", ""),
                (f"{driver}.required", "false"),
            )
        )

    async def _diff(
        self,
        cwd: Path,
        args: tuple[str, ...],
        filter_overrides: tuple[tuple[str, str], ...],
        *,
        command: str = "diff",
        label: str,
    ) -> str:
        """执行固定的 binary Git diff/show 并检查输出容量。"""
        output = await self._run(
            cwd,
            (
                command,
                "--binary",
                "--full-index",
                "--no-textconv",
                "--no-ext-diff",
                "--submodule=short",
                "--ignore-submodules=dirty",
                "--color=never",
                *args,
            ),
            allowed_exit_codes=frozenset({0, 1}),
            output_bytes_cap=REVIEW_GIT_PATCH_BYTES_CAP,
            filter_overrides=filter_overrides,
            label=label,
        )
        self._validate_patch_size(output.stdout)
        return output.stdout

    async def _run(
        self,
        cwd: Path,
        args: tuple[str, ...],
        *,
        allowed_exit_codes: frozenset[int] = frozenset({0}),
        timeout_sec: float = REVIEW_GIT_TIMEOUT_SEC,
        output_bytes_cap: int = REVIEW_GIT_METADATA_BYTES_CAP,
        filter_overrides: tuple[tuple[str, str], ...] = (),
        label: str,
    ) -> WorkspaceCommandOutput:
        """使用统一非交互 Git 前缀执行有界命令。"""
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
                _disable_hooks_config(),
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
            env=tuple(environment),
            timeout_sec=timeout_sec,
            output_bytes_cap=output_bytes_cap,
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

    @staticmethod
    def _nul_paths(value: str) -> tuple[str, ...]:
        """解析 Git `-z` 路径列表并拒绝空路径。"""
        paths = tuple(path for path in value.split("\0") if path)
        if any("\0" in path for path in paths):
            raise ReviewGitError(
                ReviewGitErrorCode.INVALID_OUTPUT,
                "Git returned an invalid path list.",
            )
        return paths

    @staticmethod
    def _validate_patch_size(patch: str) -> None:
        """拒绝达到捕获上限或服务端字节上限的 patch。"""
        if len(patch.encode("utf-8")) > REVIEW_WORKSPACE_MAX_BYTES:
            raise ReviewGitError(
                ReviewGitErrorCode.SNAPSHOT_TOO_LARGE,
                "Review patch exceeds the service byte limit.",
            )

    @classmethod
    def _combine_patch_parts(cls, parts: tuple[str, ...] | list[str]) -> str:
        """稳定连接多个完整 patch，并在连接后复核容量。"""
        patch = "".join(parts)
        cls._validate_patch_size(patch)
        return patch


def _disable_hooks_config() -> str:
    """返回当前平台禁用 Git hooks 的临时配置。"""
    return "core.hooksPath=NUL" if os.name == "nt" else "core.hooksPath=/dev/null"


if __name__ == '__main__':
    pass
