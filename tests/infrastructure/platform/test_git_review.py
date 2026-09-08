# -*- coding: utf-8 -*-

import subprocess
from collections import deque
from pathlib import Path

import pytest

from infrastructure.platform.git_review import (
    REVIEW_GIT_PATCH_BYTES_CAP,
    ReviewGitError,
    ReviewGitErrorCode,
    WorkspaceReviewGitService,
)
from infrastructure.platform.workspace import (
    WorkspaceCommand,
    WorkspaceCommandOutput,
)
from protocol.schema.review import (
    REVIEW_EMPTY_WORKSPACE_REVISION,
    REVIEW_WORKSPACE_MAX_BYTES,
    ReviewBaseBranchTarget,
    ReviewCommitTarget,
    ReviewCustomTarget,
    ReviewUncommittedTarget,
)


class FakeRunner:
    """按顺序返回固定 Git 结果并记录命令。"""

    def __init__(self, outputs: list[WorkspaceCommandOutput]) -> None:
        self.outputs = deque(outputs)
        self.commands: list[WorkspaceCommand] = []

    async def run(self, command: WorkspaceCommand) -> WorkspaceCommandOutput:
        """记录命令并返回下一项结果。"""
        self.commands.append(command)
        return self.outputs.popleft()


def _output(exit_code: int = 0, stdout: str = "") -> WorkspaceCommandOutput:
    """构造工作区命令结果。"""
    return WorkspaceCommandOutput(
        exit_code=exit_code,
        stdout=stdout,
        stderr="",
    )


def _git(repo: Path, *args: str) -> str:
    """在临时仓库执行测试准备命令。"""
    result = subprocess.run(
        ("git", *args),
        cwd=repo,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> Path:
    """创建带 main 初始提交的隔离仓库。"""
    repo = tmp_path / "review repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Review Test")
    _git(repo, "config", "user.email", "review@example.com")
    _git(repo, "branch", "-M", "main")
    (repo / "initial.txt").write_text("initial\n", encoding="utf-8")
    _git(repo, "add", "initial.txt")
    _git(repo, "commit", "-qm", "initial commit")
    return repo


@pytest.mark.anyio
async def test_review_catalog_matches_codex_branch_and_commit_order(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    _git(repo, "branch", "zeta")
    _git(repo, "branch", "alpha")
    _git(repo, "checkout", "-q", "zeta")
    for index in range(3):
        (repo / "initial.txt").write_text(f"revision {index}\n", encoding="utf-8")
        _git(repo, "commit", "-qam", f"change {index}")

    service = WorkspaceReviewGitService()
    catalog = await service.branch_catalog(repo)
    commits = await service.recent_commits(repo, limit=2)

    assert catalog.current_branch == "zeta"
    assert catalog.branches == ("main", "alpha", "zeta")
    assert tuple(item.subject for item in commits) == ("change 2", "change 1")
    assert all(len(item.sha) == 40 for item in commits)

    _git(repo, "checkout", "--detach", "-q")
    detached = await service.branch_catalog(repo)
    assert detached.current_branch is None
    assert detached.branches == catalog.branches


@pytest.mark.anyio
async def test_uncommitted_snapshot_includes_staged_unstaged_untracked_and_binary(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    (repo / "unstaged.txt").write_text("before unstaged\n", encoding="utf-8")
    (repo / "staged.txt").write_text("before staged\n", encoding="utf-8")
    _git(repo, "add", "unstaged.txt", "staged.txt")
    _git(repo, "commit", "-qm", "add tracked files")

    (repo / "unstaged.txt").write_text("after unstaged\n", encoding="utf-8")
    (repo / "staged.txt").write_text("after staged\n", encoding="utf-8")
    _git(repo, "add", "staged.txt")
    (repo / "new file.txt").write_text("new untracked\n", encoding="utf-8")
    (repo / "binary.dat").write_bytes(b"\x00\x01\x02review\xff")

    service = WorkspaceReviewGitService()
    first = await service.freeze(repo, ReviewUncommittedTarget())
    second = await service.freeze(repo, ReviewUncommittedTarget())

    assert "after staged" in first.patch
    assert "after unstaged" in first.patch
    assert "new untracked" in first.patch
    assert "GIT binary patch" in first.patch
    assert first.files == ()
    assert second == first


@pytest.mark.anyio
async def test_base_branch_snapshot_uses_merge_base_and_keeps_untracked(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    _git(repo, "checkout", "-qb", "feature/review")
    (repo / "feature.txt").write_text("feature commit\n", encoding="utf-8")
    _git(repo, "add", "feature.txt")
    _git(repo, "commit", "-qm", "feature commit")
    (repo / "working.txt").write_text("untracked feature\n", encoding="utf-8")

    workspace = await WorkspaceReviewGitService().freeze(
        repo,
        ReviewBaseBranchTarget("main"),
    )

    assert "feature commit" in workspace.patch
    assert "untracked feature" in workspace.patch
    assert workspace.revision.startswith("sha256:")


@pytest.mark.anyio
async def test_commit_snapshot_supports_root_commit_and_rejects_empty_commit(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    root_sha = _git(repo, "rev-list", "--max-parents=0", "HEAD")
    service = WorkspaceReviewGitService()

    root = await service.freeze(repo, ReviewCommitTarget(root_sha, "initial commit"))

    assert "initial.txt" in root.patch
    assert "initial" in root.patch

    _git(repo, "commit", "--allow-empty", "-qm", "empty commit")
    empty_sha = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(ReviewGitError) as raised:
        await service.freeze(repo, ReviewCommitTarget(empty_sha, "empty commit"))
    assert raised.value.code is ReviewGitErrorCode.EMPTY_DIFF


@pytest.mark.anyio
async def test_clean_custom_is_canonical_empty_but_other_targets_fail(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    service = WorkspaceReviewGitService()

    custom = await service.freeze(repo, ReviewCustomTarget("Review architecture."))

    assert custom.patch == ""
    assert custom.revision == REVIEW_EMPTY_WORKSPACE_REVISION
    with pytest.raises(ReviewGitError) as raised:
        await service.freeze(repo, ReviewUncommittedTarget())
    assert raised.value.code is ReviewGitErrorCode.EMPTY_DIFF


@pytest.mark.anyio
async def test_review_git_reports_non_repository_and_missing_target(
    tmp_path: Path,
) -> None:
    service = WorkspaceReviewGitService()
    with pytest.raises(ReviewGitError) as not_git:
        await service.branch_catalog(tmp_path)
    assert not_git.value.code is ReviewGitErrorCode.NOT_GIT_REPOSITORY

    repo = _repository(tmp_path)
    with pytest.raises(ReviewGitError) as missing:
        await service.freeze(repo, ReviewBaseBranchTarget("missing"))
    assert missing.value.code is ReviewGitErrorCode.TARGET_MISSING


@pytest.mark.anyio
async def test_review_git_rejects_non_utf8_text_diff_as_named_invalid_output(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    path = repo / "encoding.txt"
    path.write_bytes(b"before\n")
    _git(repo, "add", "encoding.txt")
    _git(repo, "commit", "-qm", "add encoding fixture")
    path.write_bytes(b"\xffafter\n")

    with pytest.raises(ReviewGitError) as raised:
        await WorkspaceReviewGitService().freeze(
            repo,
            ReviewUncommittedTarget(),
        )

    assert raised.value.code is ReviewGitErrorCode.INVALID_OUTPUT


@pytest.mark.anyio
async def test_review_git_commands_are_noninteractive_bounded_and_filter_safe(
    tmp_path: Path,
) -> None:
    head = "a" * 40
    runner = FakeRunner([
        _output(stdout="true\n"),
        _output(stdout="filter.evil.clean\0filter.evil.process\0"),
        _output(stdout=head + "\n"),
        _output(stdout="diff --git a/a.py b/a.py\n"),
        _output(stdout=""),
    ])

    workspace = await WorkspaceReviewGitService(runner).freeze(
        tmp_path,
        ReviewUncommittedTarget(),
    )

    assert workspace.patch.startswith("diff --git")
    diff_command = runner.commands[3]
    assert diff_command.argv[:2] == ("git", "--no-pager")
    assert "core.fsmonitor=false" in diff_command.argv
    assert any(value.startswith("core.hooksPath=") for value in diff_command.argv)
    assert "--binary" in diff_command.argv
    assert "--no-textconv" in diff_command.argv
    assert "--no-ext-diff" in diff_command.argv
    assert diff_command.timeout_sec == 30.0
    assert diff_command.output_bytes_cap == REVIEW_GIT_PATCH_BYTES_CAP
    assert diff_command.require_utf8_output is True
    assert ("GIT_TERMINAL_PROMPT", "0") in diff_command.env
    assert ("GIT_EXTERNAL_DIFF", None) in diff_command.env
    assert ("GIT_CONFIG_COUNT", "3") in diff_command.env


@pytest.mark.anyio
async def test_base_branch_prefers_upstream_sha_when_remote_is_ahead(
    tmp_path: Path,
) -> None:
    head = "a" * 40
    local_base = "b" * 40
    remote_base = "c" * 40
    merge_base = "d" * 40
    runner = FakeRunner([
        _output(stdout="true\n"),
        _output(exit_code=1),
        _output(stdout=head + "\n"),
        _output(stdout=local_base + "\n"),
        _output(stdout="origin/main\n"),
        _output(stdout="0\t1\n"),
        _output(stdout=remote_base + "\n"),
        _output(stdout=merge_base + "\n"),
        _output(stdout="diff --git a/a.py b/a.py\n"),
        _output(stdout=""),
    ])

    await WorkspaceReviewGitService(runner).freeze(
        tmp_path,
        ReviewBaseBranchTarget("main"),
    )

    merge_base_command = runner.commands[7]
    assert merge_base_command.argv[-3:] == (
        "merge-base",
        head,
        remote_base,
    )


@pytest.mark.anyio
async def test_review_git_rejects_patch_beyond_service_byte_limit(
    tmp_path: Path,
) -> None:
    head = "a" * 40
    runner = FakeRunner([
        _output(stdout="true\n"),
        _output(exit_code=1),
        _output(stdout=head + "\n"),
        _output(stdout="x" * (REVIEW_WORKSPACE_MAX_BYTES + 1)),
    ])

    with pytest.raises(ReviewGitError) as raised:
        await WorkspaceReviewGitService(runner).freeze(
            tmp_path,
            ReviewUncommittedTarget(),
        )

    assert raised.value.code is ReviewGitErrorCode.SNAPSHOT_TOO_LARGE
