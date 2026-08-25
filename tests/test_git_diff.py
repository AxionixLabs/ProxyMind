# -*- coding: utf-8 -*-

import os
import subprocess
import asyncio
from collections import deque
from pathlib import Path

import pytest

from mind_app.native_coding.git_diff import (
    DIFF_COMMAND_TIMEOUT_SEC,
    SAFE_BARE_REPOSITORY_CONFIG,
    WorkspaceDiffError,
    WorkspaceDiffService,
    WorkspaceDiffState,
)
from mind_app.native_coding.workspace_command import (
    WorkspaceCommand,
    WorkspaceCommandOutput,
)


class FakeRunner(object):
    def __init__(self, outputs: list[WorkspaceCommandOutput]) -> None:
        self.outputs = deque(outputs)
        self.commands: list[WorkspaceCommand] = []

    async def run(self, command: WorkspaceCommand) -> WorkspaceCommandOutput:
        self.commands.append(command)
        return self.outputs.popleft()


def _output(exit_code: int = 0, stdout: str = "") -> WorkspaceCommandOutput:
    return WorkspaceCommandOutput(exit_code=exit_code, stdout=stdout, stderr="")


@pytest.mark.anyio
async def test_workspace_diff_returns_not_git_without_followup_commands(
    tmp_path: Path,
) -> None:
    runner = FakeRunner([_output(128)])

    result = await WorkspaceDiffService(runner).compute(tmp_path)

    assert result.state is WorkspaceDiffState.NOT_GIT_REPOSITORY
    assert result.text == ""
    assert len(runner.commands) == 1
    assert runner.commands[0].cwd == tmp_path.resolve()
    assert runner.commands[0].argv[-2:] == (
        "rev-parse",
        "--is-inside-work-tree",
    )


@pytest.mark.anyio
async def test_workspace_diff_combines_tracked_and_untracked_with_safe_metadata(
    tmp_path: Path,
) -> None:
    runner = FakeRunner([
        _output(stdout="true\n"),
        _output(stdout="/tmp/helper\0"),
        _output(1),
        _output(stdout="filter.evil.clean\0filter.evil.process\0"),
        _output(1, "tracked\n"),
        _output(stdout="new file.txt\n"),
        _output(1, "untracked\n"),
    ])

    result = await WorkspaceDiffService(runner).compute(tmp_path)

    assert result.state is WorkspaceDiffState.READY
    assert result.text == "tracked\nuntracked\n"
    tracked = runner.commands[4]
    untracked = runner.commands[6]
    assert tracked.argv[:3] == ("git", "-c", SAFE_BARE_REPOSITORY_CONFIG)
    assert tracked.argv[-6:] == (
        "diff",
        "--no-textconv",
        "--no-ext-diff",
        "--submodule=short",
        "--ignore-submodules=dirty",
        "--color",
    )
    assert untracked.argv[-3:] == (
        "--",
        "NUL" if os.name == "nt" else "/dev/null",
        "new file.txt",
    )
    expected_env = (
        ("GIT_CONFIG_COUNT", "3"),
        ("GIT_CONFIG_KEY_0", "filter.evil.clean"),
        ("GIT_CONFIG_VALUE_0", ""),
        ("GIT_CONFIG_KEY_1", "filter.evil.process"),
        ("GIT_CONFIG_VALUE_1", ""),
        ("GIT_CONFIG_KEY_2", "filter.evil.required"),
        ("GIT_CONFIG_VALUE_2", "false"),
    )
    assert tracked.env == expected_env
    assert untracked.env == expected_env
    assert tracked.timeout_sec == DIFF_COMMAND_TIMEOUT_SEC
    assert tracked.output_bytes_cap is None


@pytest.mark.anyio
async def test_workspace_diff_preserves_supported_builtin_fsmonitor(
    tmp_path: Path,
) -> None:
    runner = FakeRunner([
        _output(stdout="true\n"),
        _output(stdout="true\0"),
        _output(stdout="feature: fsmonitor--daemon\n"),
        _output(1),
        _output(1, "tracked\n"),
        _output(stdout=""),
    ])

    result = await WorkspaceDiffService(runner).compute(tmp_path)

    assert result.text == "tracked\n"
    assert "core.fsmonitor=true" in runner.commands[4].argv


@pytest.mark.anyio
async def test_workspace_diff_accepts_one_and_rejects_other_diff_statuses(
    tmp_path: Path,
) -> None:
    accepted = FakeRunner([
        _output(),
        _output(1),
        _output(1),
        _output(1, "tracked\n"),
        _output(),
    ])
    assert (await WorkspaceDiffService(accepted).compute(tmp_path)).text == "tracked\n"

    rejected = FakeRunner([
        _output(),
        _output(1),
        _output(1),
        _output(2),
        _output(),
    ])
    with pytest.raises(WorkspaceDiffError, match="failed with status 2"):
        await WorkspaceDiffService(rejected).compute(tmp_path)


@pytest.mark.anyio
async def test_workspace_diff_cancels_parallel_command_after_sibling_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = WorkspaceDiffService()
    cancelled = asyncio.Event()

    async def inside_git_repository(_cwd: Path) -> bool:
        return True

    async def detect_fsmonitor(_cwd: Path):
        return None

    async def filter_overrides(_cwd: Path, _fsmonitor):
        return ()

    async def tracked_diff(*_args, **_kwargs):
        await asyncio.sleep(0)
        raise WorkspaceDiffError("tracked diff failed")

    async def untracked_files(*_args, **_kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(service, "_inside_git_repository", inside_git_repository)
    monkeypatch.setattr(service, "_detect_fsmonitor_override", detect_fsmonitor)
    monkeypatch.setattr(service, "_diff_filter_config_overrides", filter_overrides)
    monkeypatch.setattr(service, "_run_diff", tracked_diff)
    monkeypatch.setattr(service, "_run_stdout", untracked_files)

    with pytest.raises(WorkspaceDiffError, match="tracked diff failed"):
        await service.compute(tmp_path)

    assert cancelled.is_set()


@pytest.mark.anyio
async def test_workspace_diff_real_repository_matches_codex_scope(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo with spaces"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "test")
    _git(repo, "config", "user.email", "test@example.com")

    (repo / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (repo / ".gitattributes").write_text(
        "*.txt filter=evil diff=evil\n",
        encoding="utf-8",
    )
    (repo / "tracked.txt").write_text("before\n", encoding="utf-8")
    (repo / "staged.txt").write_text("before\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "initial")

    _git(repo, "config", "filter.evil.clean", "missing-filter-helper")
    _git(repo, "config", "filter.evil.process", "missing-filter-helper")
    _git(repo, "config", "filter.evil.required", "true")
    _git(repo, "config", "diff.evil.command", "missing-diff-helper")
    _git(repo, "config", "core.fsmonitor", "missing-fsmonitor-helper")

    (repo / "tracked.txt").write_text("after tracked\n", encoding="utf-8")
    (repo / "staged.txt").write_text("after staged\n", encoding="utf-8")
    _git(
        repo,
        "-c",
        "filter.evil.clean=cat",
        "-c",
        "filter.evil.process=",
        "-c",
        "filter.evil.required=false",
        "add",
        "staged.txt",
    )
    (repo / "new file.txt").write_text("new untracked\n", encoding="utf-8")
    (repo / "ignored.txt").write_text("ignored\n", encoding="utf-8")

    result = await WorkspaceDiffService().compute(repo)

    assert result.state is WorkspaceDiffState.READY
    assert "after tracked" in result.text
    assert "new untracked" in result.text
    assert "after staged" not in result.text
    assert "ignored.txt" not in result.text


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ("git", *args),
        cwd=repo,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


if __name__ == '__main__':
    pass
