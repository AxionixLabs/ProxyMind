# -*- coding: utf-8 -*-

import subprocess
from collections import deque
from pathlib import Path

import pytest

from infrastructure.platform.git_review import (
    ReviewGitError,
    ReviewGitErrorCode,
    WorkspaceReviewGitService,
)
from infrastructure.platform.workspace import (
    WorkspaceCommand,
    WorkspaceCommandOutput,
)
from protocol.schema.review import (
    ClientReviewWorkspace,
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
async def test_freeze_resolves_base_and_uses_empty_workspace_for_all_targets(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    initial_sha = _git(repo, "rev-parse", "HEAD")
    targets = (
        ReviewBaseBranchTarget("main"),
        ReviewUncommittedTarget(),
        ReviewCommitTarget(initial_sha, "initial commit"),
        ReviewCustomTarget("Review architecture."),
    )

    results = tuple([
        await WorkspaceReviewGitService().freeze(repo, target)
        for target in targets
    ])

    assert results[0].target == ReviewBaseBranchTarget("main", initial_sha)
    assert tuple(item.target for item in results[1:]) == targets[1:]
    assert all(item.workspace == ClientReviewWorkspace.create() for item in results)


@pytest.mark.anyio
async def test_clean_main_and_empty_commit_remain_valid_review_targets(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    _git(repo, "commit", "--allow-empty", "-qm", "empty commit")
    empty_sha = _git(repo, "rev-parse", "HEAD")
    service = WorkspaceReviewGitService()

    base = await service.freeze(repo, ReviewBaseBranchTarget("main"))
    commit = await service.freeze(repo, ReviewCommitTarget(empty_sha, "empty commit"))
    uncommitted = await service.freeze(repo, ReviewUncommittedTarget())

    assert base.target == ReviewBaseBranchTarget("main", empty_sha)
    assert commit.target == ReviewCommitTarget(empty_sha, "empty commit")
    assert uncommitted.workspace == ClientReviewWorkspace.create()


@pytest.mark.anyio
async def test_missing_branch_and_unborn_head_freeze_null_merge_base(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    missing = await WorkspaceReviewGitService().freeze(
        repo,
        ReviewBaseBranchTarget("missing"),
    )
    assert missing.target == ReviewBaseBranchTarget("missing", None)

    unborn = tmp_path / "unborn"
    unborn.mkdir()
    _git(unborn, "init", "-q")
    result = await WorkspaceReviewGitService().freeze(
        unborn,
        ReviewBaseBranchTarget("main"),
    )
    assert result.target == ReviewBaseBranchTarget("main", None)


@pytest.mark.anyio
async def test_review_git_reports_non_repository(tmp_path: Path) -> None:
    with pytest.raises(ReviewGitError) as raised:
        await WorkspaceReviewGitService().branch_catalog(tmp_path)
    assert raised.value.code is ReviewGitErrorCode.NOT_GIT_REPOSITORY


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
        _output(stdout=head + "\n"),
        _output(stdout=local_base + "\n"),
        _output(stdout="origin/main\n"),
        _output(stdout="0\t1\n"),
        _output(stdout=remote_base + "\n"),
        _output(stdout=merge_base + "\n"),
    ])

    result = await WorkspaceReviewGitService(runner).freeze(
        tmp_path,
        ReviewBaseBranchTarget("main"),
    )

    assert result.target == ReviewBaseBranchTarget("main", merge_base)
    merge_base_command = runner.commands[6]
    assert merge_base_command.argv[-3:] == (
        "merge-base",
        head,
        remote_base,
    )
    assert merge_base_command.timeout_sec == 30.0
    assert merge_base_command.require_utf8_output is True
    assert ("GIT_TERMINAL_PROMPT", "0") in merge_base_command.env
    assert ("GIT_EXTERNAL_DIFF", None) in merge_base_command.env


if __name__ == '__main__':
    pass
