# -*- coding: utf-8 -*-

import asyncio
from pathlib import Path

from mind_app.native_coding import NativeCoding


def approved_execution(**overrides: object) -> dict[str, object]:
    """生成允许本地执行的测试授权元数据。"""
    data: dict[str, object] = {
        "grantId": "test-grant",
        "state": "approved",
        "target": "local",
        "risk": "test",
        "category": "test",
        "reasons": [],
    }
    data.update(overrides)
    return data


def run_async(value: object) -> object:
    """同步测试中运行异步工具调用。"""
    return asyncio.run(value)


def write_script(root: Path, name: str, content: str) -> str:
    """写入测试脚本并返回可由 shell 执行的命令。"""
    script = root / name
    script.write_text(content, encoding="utf-8")
    return f"python {script.name}"


def test_shell_command_rejects_missing_execution_metadata(tmp_path: Path) -> None:
    """缺少 execution 元数据时拒绝执行命令。"""
    result = run_async(
        NativeCoding(root=tmp_path).shell_command(
            command="python --version",
            cwd=".",
            timeout_sec=5,
            execution=None,
        )
    )

    assert result["ok"] is False
    assert result["data"]["error"] == "execution_policy_blocked"
    assert result["data"]["risk_signals"] == ["missing_execution_metadata"]


def test_shell_command_rejects_empty_command(tmp_path: Path) -> None:
    """空命令返回稳定失败原因。"""
    result = run_async(
        NativeCoding(root=tmp_path).shell_command(
            command="",
            execution=approved_execution(),
        )
    )

    assert result["ok"] is False
    assert result["data"]["reason"] == "command_empty"


def test_shell_command_rejects_missing_cwd(tmp_path: Path) -> None:
    """不存在的工作目录不会启动进程。"""
    result = run_async(
        NativeCoding(root=tmp_path).shell_command(
            command="python --version",
            cwd="missing",
            timeout_sec=5,
            execution=approved_execution(),
        )
    )

    assert result["ok"] is False
    assert result["data"]["error"] == "cwd_not_directory"
    assert result["data"]["cwd"] == "missing"


def test_shell_command_runs_successfully_and_records_history(tmp_path: Path) -> None:
    """成功命令返回 stdout 并记录最近一次 shell 结果。"""
    command = write_script(tmp_path, "ok.py", "print('native-ok')\n")
    coding = NativeCoding(root=tmp_path)

    result = run_async(
        coding.shell_command(
            command=command,
            cwd=".",
            timeout_sec=5,
            execution=approved_execution(),
        )
    )

    assert result["ok"] is True
    assert result["data"]["exit_code"] == 0
    assert result["data"]["stdout"].strip() == "native-ok"
    assert result["data"]["cwd"] == "."
    assert coding.last_shell_result == result["data"]
    assert coding.validation_history[-1] == result["data"]


def test_shell_command_reports_nonzero_exit(tmp_path: Path) -> None:
    """非零退出码返回 command_failed。"""
    command = write_script(
        tmp_path,
        "fail.py",
        "import sys\nprint('before-fail')\nsys.exit(7)\n",
    )

    result = run_async(
        NativeCoding(root=tmp_path).shell_command(
            command=command,
            cwd=".",
            timeout_sec=5,
            execution=approved_execution(),
        )
    )

    assert result["ok"] is False
    assert result["data"]["exit_code"] != 0
    assert result["data"]["reason"] == "command_failed"
    assert "before-fail" in result["data"]["stdout"]


def test_shell_command_reports_timeout(tmp_path: Path) -> None:
    """超时命令会被终止并返回 command_timed_out。"""
    command = write_script(
        tmp_path,
        "sleep.py",
        "import time\ntime.sleep(5)\n",
    )

    result = run_async(
        NativeCoding(root=tmp_path).shell_command(
            command=command,
            cwd=".",
            timeout_sec=1,
            execution=approved_execution(),
        )
    )

    assert result["ok"] is False
    assert result["data"]["timed_out"] is True
    assert result["data"]["reason"] == "command_timed_out"


def test_shell_command_audits_created_files(tmp_path: Path) -> None:
    """写文件命令会在文件审计中标记新增文件。"""
    command = write_script(
        tmp_path,
        "write_file.py",
        "from pathlib import Path\nPath('created.txt').write_text('created', encoding='utf-8')\n",
    )

    result = run_async(
        NativeCoding(root=tmp_path).shell_command(
            command=command,
            cwd=".",
            timeout_sec=5,
            execution=approved_execution(),
        )
    )

    assert result["ok"] is True
    assert (tmp_path / "created.txt").read_text(encoding="utf-8") == "created"
    assert result["data"]["shell_write_detected"] is True
    assert "created.txt" in result["data"]["shell_file_changes"]["created"]


def test_shell_command_returns_cloud_sandbox_handoff(tmp_path: Path) -> None:
    """云沙盒目标返回交接结果，不启动本地命令。"""
    result = run_async(
        NativeCoding(root=tmp_path).shell_command(
            command="python --version",
            cwd=".",
            timeout_sec=5,
            execution=approved_execution(target="cloud_sandbox"),
        )
    )

    assert result["ok"] is False
    assert result["data"]["execution_target"] == "cloud_sandbox"
    assert result["data"]["requires_cloud_sandbox"] is True


def test_shell_calls_requires_execution_metadata(tmp_path: Path) -> None:
    """批量 shell 调用也必须携带 execution 元数据。"""
    result = run_async(
        NativeCoding(root=tmp_path).shell_calls(
            items=[{"command": "python --version", "cwd": ".", "timeout_sec": 5}],
            execution=None,
        )
    )

    assert result["ok"] is False
    assert result["data"]["reason"] == "execution_metadata_required"
    assert result["data"]["error"] == "execution_policy_blocked"


def test_shell_calls_runs_batch_and_preserves_order(tmp_path: Path) -> None:
    """批量 shell 调用并发执行后按原始顺序返回结果。"""
    first = write_script(tmp_path, "first.py", "print('first')\n")
    second = write_script(tmp_path, "second.py", "print('second')\n")

    result = run_async(
        NativeCoding(root=tmp_path).shell_calls(
            items=[
                {"command": first, "cwd": ".", "timeout_sec": 5},
                {"command": second, "cwd": ".", "timeout_sec": 5},
            ],
            execution=approved_execution(),
        )
    )

    results = result["data"]["results"]

    assert result["ok"] is True
    assert result["data"]["ok_count"] == 2
    assert [item["index"] for item in results] == [0, 1]
    assert results[0]["result"]["data"]["stdout"].strip() == "first"
    assert results[1]["result"]["data"]["stdout"].strip() == "second"


def test_shell_calls_reports_failed_item(tmp_path: Path) -> None:
    """批量中任一命令失败时汇总为批次失败。"""
    ok = write_script(tmp_path, "ok.py", "print('ok')\n")
    fail = write_script(tmp_path, "fail.py", "import sys\nsys.exit(4)\n")

    result = run_async(
        NativeCoding(root=tmp_path).shell_calls(
            items=[
                {"command": ok, "cwd": ".", "timeout_sec": 5},
                {"command": fail, "cwd": ".", "timeout_sec": 5},
            ],
            execution=approved_execution(),
        )
    )

    assert result["ok"] is False
    assert result["data"]["reason"] == "shell_calls_batch_failed"
    assert result["data"]["ok_count"] == 1
    assert result["data"]["fail_count"] == 1
    assert result["data"]["results"][1]["result"]["data"]["exit_code"] != 0
