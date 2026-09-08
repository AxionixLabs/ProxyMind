# -*- coding: utf-8 -*-

import subprocess
import sys
from pathlib import Path

import pytest

from agent.ports import ReviewWorkspaceReadError
from infrastructure.workspace.review_reader import ReviewWorkspaceReader


def _git(repo: Path, *args: str) -> str:
    """在隔离仓库执行准备命令。"""
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


def _repository(tmp_path: Path) -> tuple[Path, str]:
    """创建带提交和工作区变化的测试仓库。"""
    repo = tmp_path / "reader repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Review Reader")
    _git(repo, "config", "user.email", "reader@example.com")
    _git(repo, "branch", "-M", "main")
    (repo / "source.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    _git(repo, "add", "source.py")
    _git(repo, "commit", "-qm", "initial")
    sha = _git(repo, "rev-parse", "HEAD")
    (repo / "source.py").write_text("one\nchanged\nthree\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("new\n", encoding="utf-8")
    return repo, sha


@pytest.mark.anyio
async def test_review_reader_exposes_status_diff_show_and_file_ranges(
    tmp_path: Path,
) -> None:
    repo, sha = _repository(tmp_path)
    reader = ReviewWorkspaceReader(repo)

    status = await reader.read_repository(operation="status")
    diff = await reader.read_repository(operation="diff", revision=sha)
    show = await reader.read_repository(operation="show", revision=sha)
    source = await reader.read_file(path="source.py", start_line=2, max_lines=1)

    assert "source.py" in status.stdout
    assert "untracked.txt" in status.stdout
    assert status.command == "git status --short"
    assert "changed" in diff.stdout
    assert diff.command == f"git diff {sha}"
    assert "initial" in show.stdout
    assert show.command.endswith(sha)
    assert source.path == "source.py"
    assert source.content == "changed\n"
    assert (source.start_line, source.end_line, source.total_lines) == (2, 2, 3)
    assert source.truncated is True


@pytest.mark.anyio
async def test_review_reader_rejects_option_injection_and_outside_paths(
    tmp_path: Path,
) -> None:
    repo, _sha = _repository(tmp_path)
    reader = ReviewWorkspaceReader(repo)

    with pytest.raises(ReviewWorkspaceReadError, match="revision is invalid"):
        await reader.read_repository(operation="diff", revision="--output=outside")
    with pytest.raises(ReviewWorkspaceReadError, match="review path is invalid"):
        await reader.read_repository(operation="status", paths=("../outside",))
    with pytest.raises(ValueError, match="outside workspace"):
        await reader.read_file(path="../outside.txt")
    with pytest.raises(ReviewWorkspaceReadError, match="excluded"):
        await reader.read_file(path=".git/config")


@pytest.mark.anyio
async def test_review_reader_rejects_fields_outside_each_operation(
    tmp_path: Path,
) -> None:
    repo, sha = _repository(tmp_path)
    reader = ReviewWorkspaceReader(repo)

    with pytest.raises(ReviewWorkspaceReadError, match="unsupported fields"):
        await reader.read_repository(
            operation="show",
            revision=sha,
            staged=True,
        )
    with pytest.raises(ReviewWorkspaceReadError, match="requires revision"):
        await reader.read_repository(operation="show")


@pytest.mark.anyio
async def test_review_reader_disables_repository_clean_process_filters(
    tmp_path: Path,
) -> None:
    repo, _sha = _repository(tmp_path)
    marker = repo / "filter-ran.txt"
    script = repo / "filter.py"
    script.write_text(
        "import pathlib, sys\n"
        "pathlib.Path('filter-ran.txt').write_text('ran', encoding='utf-8')\n"
        "sys.stdout.buffer.write(sys.stdin.buffer.read())\n",
        encoding="utf-8",
    )
    (repo / ".gitattributes").write_text("*.py filter=evil\n", encoding="utf-8")
    filter_command = (
        f'"{Path(sys.executable).as_posix()}" "{script.as_posix()}"'
    )
    _git(repo, "config", "filter.evil.clean", filter_command)
    _git(repo, "config", "filter.evil.required", "true")
    _git(repo, "add", ".gitattributes", "filter.py")
    _git(repo, "commit", "-qm", "configure filter")
    marker.unlink(missing_ok=True)
    script.write_text(script.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")

    result = await ReviewWorkspaceReader(repo).read_repository(operation="diff")

    assert result.exit_code == 0
    assert "filter.py" in result.stdout
    assert marker.exists() is False


if __name__ == '__main__':
    pass
