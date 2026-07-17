# -*- coding: utf-8 -*-

import asyncio
import time
from pathlib import Path

import mind_app.native_coding.encoding as output_encoding
from mind_app.native_coding import NativeCoding
from mind_app.native_coding.exec.process_capture import (
    CapturedOutputLine,
    CapturedProcessResult,
    ProcessCapture
)
from mind_app.native_coding.exec.file_audit import FileAudit
from mind_app.native_coding.exec.shell_exec import ShellCommandTools


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


def test_shell_command_treats_powershell_read_commands_as_metadata_audit() -> None:
    """PowerShell 只读命令不触发完整文件哈希审计。"""
    assert ShellCommandTools.audit_mode_for_command(
        "Get-Content -LiteralPath '.\\SKILL.md'",
        audit_files=True,
    ) == "metadata"
    assert ShellCommandTools.audit_mode_for_command(
        "Select-String -Path '.\\SKILL.md' -Pattern 'Mobile'",
        audit_files=True,
    ) == "metadata"


def test_file_audit_stops_after_capture_limit(tmp_path: Path) -> None:
    """文件审计达到采集上限后立即截断，避免继续遍历大目录。"""
    for index in range(5):
        (tmp_path / f"item_{index}.txt").write_text(str(index), encoding="utf-8")

    audit = FileAudit(NativeCoding(root=tmp_path))
    result = audit.capture_file_fingerprints(max_files=3, hash_files=False)

    assert result["captured_count"] == 3
    assert result["truncated"] is True
    assert result["file_count"] == 4


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


def test_shell_command_records_output_lines_in_receive_order(tmp_path: Path) -> None:
    """stdout/stderr 分离字段之外保留接收顺序输出。"""
    command = write_script(
        tmp_path,
        "ordered_output.py",
        "\n".join([
            "import sys",
            "sys.stderr.write('stderr-first\\n')",
            "sys.stderr.flush()",
            "sys.stdout.write('stdout-second\\n')",
            "sys.stdout.flush()",
            "",
        ]),
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
    assert result["data"]["stdout"].strip() == "stdout-second"
    assert result["data"]["stderr"].strip() == "stderr-first"
    assert result["data"]["output_lines"] == ["stderr-first", "stdout-second"]


def test_shell_command_decodes_mixed_output_lines(
    tmp_path: Path,
    monkeypatch: object
) -> None:
    """stdout 和有序输出行使用同一套逐行编码策略。"""
    stdout = "一\r\n".encode("utf-8") + bytes.fromhex("d6d0cec4b2e2cad40d0a")

    async def fake_run_shell(*args: object, **kwargs: object) -> CapturedProcessResult:
        _ = args, kwargs
        return CapturedProcessResult(
            exit_code=0,
            stdout=stdout,
            stderr=b"",
            output_records=(
                CapturedOutputLine("stdout", "一".encode("utf-8")),
                CapturedOutputLine("stdout", bytes.fromhex("d6d0cec4b2e2cad4")),
            ),
            stdout_dropped=0,
            stderr_dropped=0,
            timed_out=False,
            elapsed_ms=1
        )

    monkeypatch.setattr(
        output_encoding,
        "process_output_encodings",
        lambda: ["utf-8", "gbk"]
    )
    monkeypatch.setattr(ProcessCapture, "run_shell", fake_run_shell)

    result = run_async(
        NativeCoding(root=tmp_path).shell_command(
            command="Write-Output test",
            cwd=".",
            timeout_sec=5,
            execution=approved_execution(),
        )
    )

    assert result["ok"] is True
    assert result["data"]["stdout"] == "一\r\n中文测试\r\n"
    assert result["data"]["output_lines"] == ["一", "中文测试"]
    assert result["data"]["detected_output_encodings"] == ["utf-8", "gbk"]
    assert result["data"]["output_encoding_ambiguous"] is False


def test_shell_command_respects_explicit_output_encoding(
    tmp_path: Path,
    monkeypatch: object
) -> None:
    """显式输出编码同时应用于 stdout 和有序行。"""
    raw = bytes.fromhex("d2bb0a")

    async def fake_run_shell(*args: object, **kwargs: object) -> CapturedProcessResult:
        _ = args, kwargs
        return CapturedProcessResult(
            exit_code=0,
            stdout=raw,
            stderr=b"",
            output_records=(CapturedOutputLine("stdout", raw[:-1]),),
            stdout_dropped=0,
            stderr_dropped=0,
            timed_out=False,
            elapsed_ms=1
        )

    monkeypatch.setattr(ProcessCapture, "run_shell", fake_run_shell)

    result = run_async(
        NativeCoding(root=tmp_path).shell_command(
            command="Write-Output test",
            cwd=".",
            timeout_sec=5,
            output_encoding="utf-8",
            execution=approved_execution(),
        )
    )

    assert result["ok"] is True
    assert result["data"]["stdout"] == "һ\n"
    assert result["data"]["output_lines"] == ["һ"]
    assert result["data"]["output_encoding"] == "utf-8"
    assert result["data"]["output_encoding_ambiguous"] is False


def test_shell_command_rejects_unknown_output_encoding(tmp_path: Path) -> None:
    """未知输出编码在启动进程前返回稳定错误。"""
    result = run_async(
        NativeCoding(root=tmp_path).shell_command(
            command="Write-Output test",
            output_encoding="not-a-codec",
            execution=approved_execution(),
        )
    )

    assert result["ok"] is False
    assert result["data"]["reason"] == "output_encoding_invalid"
    assert result["data"]["output_encoding"] == "not-a-codec"


def test_shell_command_rejects_non_byte_line_output_encoding(tmp_path: Path) -> None:
    """非单字节换行编码不会进入进程捕获阶段。"""
    result = run_async(
        NativeCoding(root=tmp_path).shell_command(
            command="Write-Output test",
            output_encoding="utf-16",
            execution=approved_execution(),
        )
    )

    assert result["ok"] is False
    assert result["data"]["reason"] == "output_encoding_invalid"
    assert result["data"]["output_encoding"] == "utf-16"


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


def test_shell_command_timeout_does_not_wait_for_inherited_output_pipe(tmp_path: Path) -> None:
    """超时后不会等待持有输出管道的子进程自然退出。"""
    command = write_script(
        tmp_path,
        "spawn_pipe_holder.py",
        "\n".join([
            "import subprocess",
            "import sys",
            "import time",
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(8)'], stdout=sys.stdout, stderr=sys.stderr)",
            "print('parent-finished', flush=True)",
            "time.sleep(8)",
            "",
        ]),
    )

    started = time.monotonic()
    result = run_async(
        NativeCoding(root=tmp_path).shell_command(
            command=command,
            cwd=".",
            timeout_sec=1,
            execution=approved_execution(),
        )
    )
    elapsed = time.monotonic() - started

    assert result["ok"] is False
    assert result["data"]["timed_out"] is True
    assert result["data"]["reason"] == "command_timed_out"
    assert "parent-finished" in result["data"]["stdout"]
    assert elapsed < 6.0


def test_shell_command_truncates_high_volume_output(tmp_path: Path) -> None:
    """高频输出会按上限截断后返回。"""
    command = write_script(
        tmp_path,
        "high_output.py",
        "print('x' * 120000)\n",
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
    assert result["data"]["stdout_truncated"] is True
    assert result["data"]["truncated"] is True
    assert "...[truncated " in result["data"]["stdout"]


def test_shell_command_does_not_audit_created_files_by_default(tmp_path: Path) -> None:
    """普通 shell 命令默认不做工作区文件变更审计。"""
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
    assert result["data"]["file_audit_enabled"] is False
    assert result["data"]["file_audit_mode"] == "off"
    assert result["data"]["shell_write_detected"] is False
    assert result["data"]["shell_file_changes"]["changed"] is False


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
