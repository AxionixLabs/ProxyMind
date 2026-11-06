"""通过真实 Mind TUI 和模型操作专用窗口，保存输入结果与原生覆盖层采样。"""

import argparse
import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from pydantic import (
    JsonValue,
    TypeAdapter,
)

from tests.infrastructure.sidecars.desktop.test_native_reliability import ControlledWindow
from tests.pty import (
    PtyKey,
    TerminalSize,
    spawn_terminal,
)


def run_states(state_directory: Path) -> dict[str, str]:
    """只读取本轮独立状态目录的持久终态，不根据终端回显判断完成。"""
    database = state_directory / "history/runtime.db"
    if not database.is_file():
        return {}
    with sqlite3.connect(database) as connection:
        if not connection.execute("SELECT name FROM sqlite_master WHERE name='run_snapshots'").fetchone():
            return {}
        rows = connection.execute("SELECT run_id, status FROM run_snapshots").fetchall()
    states: dict[str, str] = {}
    for run_id, status in rows:
        if not isinstance(run_id, str) or not isinstance(status, str):
            raise ValueError("Unexpected run identity")
        states[run_id] = status
    return states


def completed_run(state_directory: Path, previous: set[str]) -> tuple[str, str] | None:
    """只接受此次新启动的终态，不把既有状态误报为本轮成功。"""
    finished = [(run_id, status) for run_id, status in run_states(state_directory).items()
                if run_id not in previous and status in {"completed", "failed", "cancelled", "interrupted"}]
    if len(finished) > 1:
        raise ValueError("Concurrent runs make this acceptance ambiguous")
    return finished[0] if finished else None


def tool_outcomes(state_directory: Path, previous: set[Path]) -> tuple[int, int]:
    """从真实工具事件核对移动和失败次数，不接受模型自报结果。"""
    payload = TypeAdapter(dict[str, JsonValue])
    count = 0
    failures = 0
    for path in (state_directory / "sessions").rglob("*.jsonl"):
        if path in previous:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            entry = payload.validate_json(line)
            result = entry.get("payload")
            if not isinstance(result, dict):
                continue
            if entry.get("event") == "tool.failed":
                failures += 1
            if entry.get("event") != "tool.completed":
                continue
            arguments = result.get("arguments")
            if (result.get("name") == "computer" and result.get("ok") is True
                    and isinstance(arguments, dict) and arguments.get("action") == "move"):
                count += 1
    return count, failures


async def run(directory: Path, repository: Path, state_directory: Path | None = None) -> None:
    """启动完整交互入口；只有模型可以提交 computer 输入，采样器只读夹具状态。"""
    directory.mkdir(parents=True, exist_ok=False)
    state_directory = state_directory or directory / "state"
    previous_runs = set(run_states(state_directory))
    previous_sessions = set((state_directory / "sessions").rglob("*.jsonl"))
    executable = repository / "tests/fixtures/cua_windows/bin/Release/net10.0-windows/Fixture.exe"
    process = await asyncio.create_subprocess_exec(
        str(executable), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    fixture = ControlledWindow(process)
    try:
        assert process.stdout is not None
        assert (await asyncio.wait_for(process.stdout.readline(), 10)).strip() == b"ready"
        await fixture.command("activate")
        text = "CUA 视觉验收：平滑移动与输入正常"
        prompt = (
            f"进行非破坏性 computer 真实视觉验收。只操作标题 Desktop sidecar verification fixture、PID {process.pid} 的专用窗口。"
            "禁止使用 shell、exec、其他应用或发送消息。先 list_windows、activate、observe(screenshot=true)，展示截图确认界面。"
            "然后通过 js_repl 的 host.tool('computer', ...) 在窗口客户区空白处做 12 次来回鼠标移动，每次 duration_ms=1200。"
            "每次输入前重新 observe(screenshot=true)，坐标从实际截图确定，不要碰窗口边框。结果元数据读取 result.data；"
            "不要打印图片 Base64。之后单击 Increment counter 一次，观察确认 Count: 1。再点击 Text input，Ctrl+A，"
            f"输入完整文本『{text}』，观察并展示最终截图确认。只能使用 computer 或 js_repl 嵌套 computer。"
            "如果发生外部输入介入则停止，禁止自动 reset 或重试失败输入。最后报告实际完成的移动次数、计数和文字。"
        )
        argv = [sys.executable, str(repository / "mind.py"), "-a", "never", "-s", "danger-full-access",
                "-c", "features.computer=true", "-c", "features.js_repl=true"]
        with spawn_terminal(
            argv, cwd=repository,
            env={**os.environ, "PYTHONUTF8": "1", "MIND_STATE_HOME": str(state_directory)},
            size=TerminalSize(rows=38, columns=140), failure_artifact_directory=directory / "terminal",
        ) as terminal:
            await asyncio.to_thread(terminal.wait_for_screen_text, "Mind", 30)
            terminal.write_user_text(prompt)
            terminal.send_key(PtyKey.ENTER)
            print(json.dumps({"stage": "submitted", "fixture_pid": process.pid, "mind_pid": terminal.session.pid}), flush=True)
            started = time.monotonic()
            next_report = started
            result = None
            sample_count = 0
            maximum_visible = 0
            payload = TypeAdapter(dict[str, JsonValue])
            with (directory / "overlay-samples.jsonl").open("x", encoding="utf-8") as samples:
                while time.monotonic() - started < 1200:
                    state = await fixture.exchange("overlays 0")
                    snapshot = payload.validate_json(state)
                    overlays = snapshot.get("overlays")
                    assert isinstance(overlays, list)
                    maximum_visible = max(maximum_visible, len(overlays))
                    samples.write(json.dumps({"elapsed": round(time.monotonic() - started, 3),
                                              "state": snapshot}) + "\n")
                    sample_count += 1
                    if time.monotonic() >= next_report:
                        result = completed_run(state_directory, previous_runs)
                        terminal.save_failure_artifacts(directory / "terminal")
                        print(json.dumps({"stage": "running", "elapsed": round(time.monotonic() - started),
                                          "samples": sample_count, "run": result}), flush=True)
                        next_report = time.monotonic() + 5
                        if result is not None:
                            break
                    await asyncio.sleep(0.03)
            final_state = await fixture.exchange("state")
            (directory / "fixture-final.json").write_bytes(final_state)
            terminal.save_failure_artifacts(directory / "terminal")
            terminal.write_user_text("/exit")
            terminal.send_key(PtyKey.ENTER)
            exit_code = await asyncio.to_thread(terminal.wait_for_exit, 30)
            overlay_final = await fixture.exchange("overlays 0")
            (directory / "overlay-final.json").write_bytes(overlay_final)
            remaining = payload.validate_json(overlay_final)
            expected = payload.validate_json(final_state)
            moves, failures = tool_outcomes(state_directory, previous_sessions)
            passed = (result is not None and result[1] == "completed" and exit_code == 0
                      and moves == 12 and failures == 0 and maximum_visible >= 2
                      and remaining["allocated"] == [] and remaining["overlays"] == []
                      and expected["count"] == 1 and expected["text"] == text and not expected["held"])
            summary = {"entry": argv, "state_home": str(state_directory), "run": result, "exit_code": exit_code, "samples": sample_count,
                       "completed_moves": moves, "tool_failures": failures,
                       "maximum_visible_overlays": maximum_visible, "passed": passed,
                       "scope": "Real model + Mind TUI; overlay sampling does not measure presented frame rate"}
            (directory / "result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(summary, ensure_ascii=True), flush=True)
            assert passed, summary
    finally:
        if process.stdin is not None:
            process.stdin.close()
        try:
            await asyncio.wait_for(process.communicate(), 5)
        except TimeoutError:
            process.kill()
            await process.communicate()


def main() -> None:
    """要求全新证据目录，保留当前用户配置并隔离运行状态。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--state-home", type=Path, help="复用指定状态根，以验收真实用户缓存和再次启动")
    arguments = parser.parse_args()
    asyncio.run(run(arguments.directory.resolve(), Path.cwd().resolve(),
                    arguments.state_home.resolve() if arguments.state_home else None))


if __name__ == "__main__":
    main()
