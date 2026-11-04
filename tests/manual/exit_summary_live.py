"""用已部署服务实报核对完整源码客户端的原生终端退出。"""

import asyncio
import re
import sqlite3
import time
from dataclasses import asdict

import httpx

from agent.stores.sessions.history import ConversationHistoryStore
from infrastructure.config.store import ConfigStore
from protocol.client.context_usage import recover_context_usage
from protocol.transport.auth import build_service_headers
from protocol.transport.endpoints import service_endpoints
from tests.manual.session_delete_live import (
    command,
    launch,
    remote_status,
    runs,
    save,
    stage,
    wait_for,
    wait_turn,
)
from tests.pty import PtyKey


def wait_ready(terminal, directory):
    """等待隔离配置的实际模型标签，不限定 Provider 或模型名称。"""
    config = ConfigStore(directory / "config/config.toml").read_raw(create=False)
    model = config["model_providers"][config["model_provider"]]["model"]
    terminal.wait_for_screen_text(model, timeout=40)


def capture_usage(directory, domain, identity, *, root=True):
    """通过正式回放读取实报，并与当前客户端已确认的持久快照比较。"""
    service_endpoints.configure(domain)
    event = asyncio.run(recover_context_usage(identity["cid"], identity["sid"]))
    assert event is not None and event.snapshot.total_token_usage is not None
    remote = asdict(event.snapshot.total_token_usage)
    history = ConversationHistoryStore(directory / "state/history/history.db")

    def synchronized():
        record = history.load_context_usage(identity["cid"], identity["sid"])
        if record is not None and record.total_token_usage is not None:
            return record if asdict(record.total_token_usage) == remote else None
        return None

    record = wait_for(synchronized, timeout=20) if root else None
    return {"cid": identity["cid"], "sid": identity["sid"], "event_seq": event.event_seq,
            "usage": remote, "local_event_seq": record.event_seq if record is not None else None}


def expected_usage(fact):
    """根据实报核心计数独立计算应展示的用量文本。"""
    usage = fact["usage"]
    if usage["unreported_calls"] or any(usage[key] is None for key in ("input_tokens", "cached_input_tokens", "output_tokens")):
        return None
    if not usage["total_tokens"]:
        return None
    uncached = usage["input_tokens"] - usage["cached_input_tokens"]
    value = f"Token usage: total={uncached + usage['output_tokens']:,} input={uncached:,}"
    if usage["cached_input_tokens"]:
        value += f" (+ {usage['cached_input_tokens']:,} cached)"
    value += f" output={usage['output_tokens']:,}"
    if usage["reasoning_output_tokens"]:
        value += f" (reasoning {usage['reasoning_output_tokens']:,})"
    return value


def check_summary(terminal, artifact, fact, *, resume, archived=False, offset=0, stopped=True, no_color=False):
    """保存完整终端事实，核对实报数字、换行、颜色、一次输出和光标恢复。"""
    terminal.save_failure_artifacts(artifact)
    raw = terminal.session.output()
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", raw.decode("utf-8", errors="replace")).replace("\r", "")
    expected = expected_usage(fact)
    if expected is not None and not stopped:
        expected = expected.replace("Token usage:", "Token usage so far:")
    if expected is not None:
        assert text.count("Token usage") == 1, text[-1200:]
        assert "■ " + expected in text.replace("\n", ""), text[-1200:]
    else:
        assert "Token usage" not in text
    if resume:
        assert f"■ To continue this session, run:\n  mind resume {fact['sid']}" in text, text[-1200:]
        lines = terminal.screen.snapshot().visible_lines
        row = next(index for index, line in enumerate(lines) if line.startswith("  mind resume "))
        cell = terminal.screen.cell(row, 2)
        assert not cell.bold
        assert (cell.foreground == "default") is no_color
    else:
        assert "mind resume" not in text, text[-1200:]
    if archived:
        assert f"Session archived: {fact['sid']}" in text
    if offset:
        assert b"\x1b[2J" not in raw[offset:] and b"\x1b[3J" not in raw[offset:]
    assert not terminal.screen.snapshot().cursor.hidden
    return {"expected": expected, "sid": fact["sid"], "resume": resume, "archived": archived}


def verify_exits(directory, workspace, domain, control, proxy):
    """逐次启动完整客户端，验证冷恢复、全部普通出口、归档及真实删除响应丢失。"""
    fact = capture_usage(directory, domain, control)
    results = []
    for index, entry in enumerate(("/quit", "/q", "quit", "exit", "/exit", "ctrl_c", "ctrl_d", "/shutdown")):
        stage("live_exit_" + entry.replace("/", ""))
        columns = 44 if entry == "/q" else 100
        with launch(directory, workspace, "resume", control["sid"], columns=columns, no_color=entry == "exit") as terminal:
            terminal.wait_for_screen_text("CONTROL_OK", timeout=40)
            time.sleep(1)
            offset = len(terminal.session.output())
            if entry == "ctrl_c":
                terminal.send_key(PtyKey.CTRL_C)
                terminal.wait_for_screen_text("again to exit")
                terminal.send_key(PtyKey.CTRL_C)
            elif entry == "ctrl_d":
                terminal.send_key(PtyKey.CTRL_D)
            else:
                command(terminal, entry)
            code = terminal.wait_for_exit(timeout=30)
            assert code == (130 if entry == "ctrl_c" else 0), (entry, code)
            result = check_summary(terminal, directory / f"exit-{index}", fact, resume=True,
                                   offset=offset, stopped=False, no_color=entry == "exit")
            results.append({"entry": entry, "exit_code": code, **result})
        current = capture_usage(directory, domain, control)
        assert current["usage"] == fact["usage"]
        save(directory / "exit-results.json", results)
    stage("live_archive")
    with launch(directory, workspace, "resume", control["sid"]) as terminal:
        terminal.wait_for_screen_text("CONTROL_OK", timeout=40)
        time.sleep(1)
        command(terminal, "/archive")
        terminal.wait_for_screen_text("Yes, archive and exit")
        terminal.write_user(b"\x1b[B")
        terminal.send_key(PtyKey.ENTER)
        assert terminal.wait_for_exit(timeout=30) == 0
        results.append({"entry": "/archive", **check_summary(terminal, directory / "archived-exit", fact, resume=False, archived=True, stopped=False)})
    for entry in ("/exit", "ctrl_d"):
        with launch(directory, workspace) as terminal:
            wait_ready(terminal, directory)
            time.sleep(1)
            if entry == "ctrl_d":
                terminal.send_key(PtyKey.CTRL_D)
            else:
                command(terminal, entry)
            assert terminal.wait_for_exit(timeout=30) == 0
            assert "Token usage" not in terminal.session.output_text()
            assert "mind resume" not in terminal.session.output_text()
            terminal.save_failure_artifacts(directory / ("empty-" + entry.replace("/", "")))
    verify_lost_delete(directory, workspace, domain, proxy)
    save(directory / "exit-results.json", results)
    return results


def verify_lost_delete(directory, workspace, domain, proxy):
    """仅丢弃真实服务已成功删除的响应，跨客户端进程恢复原请求。"""
    stage("live_lost_delete")
    with launch(directory, workspace) as terminal:
        wait_ready(terminal, directory)
        previous = {row["run_id"] for row in runs(directory)}
        command(terminal, "This is an isolated exit acceptance. Do not use tools. Reply exactly LOST_DELETE_OK.")
        identity = wait_turn(directory, previous)
        save(directory / "lost-delete-identity.json", identity)
        fact = capture_usage(directory, domain, identity)
        save(directory / "lost-delete-usage.json", fact)
        proxy.drop_next_delete_response = True
        command(terminal, "/delete")
        terminal.wait_for_screen_text("Yes, delete and exit")
        terminal.write_user(b"\x1b[B")
        terminal.send_key(PtyKey.ENTER)
        terminal.wait_for_screen_text("Deletion outcome is unknown.", timeout=40)
        request = proxy.facts[-1]["request"]
        save(directory / "lost-delete-request.json", request)
        command(terminal, "/exit")
        assert terminal.wait_for_exit(timeout=30) == 0
        terminal.save_failure_artifacts(directory / "pending-delete-exit")
        text = terminal.session.output_text()
        assert "Session deletion is pending." in text
        assert request["request_id"] in text and "mind resume" not in text
    posted = len([item for item in proxy.facts if item["method"] == "POST"])
    with launch(directory, workspace) as terminal:
        wait_ready(terminal, directory)
        command(terminal, "/delete recover " + request["request_id"])
        terminal.wait_for_screen_text("Session deletion recovered.", timeout=40)
        command(terminal, "/exit")
        assert terminal.wait_for_exit(timeout=30) == 0
        terminal.save_failure_artifacts(directory / "recovered-delete-exit")
    assert len([item for item in proxy.facts if item["method"] == "POST"]) == posted
    with httpx.Client(base_url=domain, headers=build_service_headers(), timeout=20, trust_env=False) as client:
        assert remote_status(client, identity).status_code == 404
    history = ConversationHistoryStore(directory / "state/history/history.db")
    assert history.find_session(identity["sid"]) is None
    assert history.load_context_usage(identity["cid"], identity["sid"]) is None


def verify_worker_recovery(directory, workspace, domain):
    """在真实工具等待检查点暂停，供运维终端重启 Worker 后继续同一轮次。"""
    with launch(directory, workspace) as terminal:
        wait_ready(terminal, directory)
        previous = {row["run_id"] for row in runs(directory)}
        command(terminal,
            "This is an isolated Worker restart acceptance. Use exec_command to run exactly: "
            "python -c \"import pathlib,time; pathlib.Path('tool-ready').touch(); "
            "p=pathlib.Path('release-tool'); [time.sleep(1) for _ in range(180) if not p.exists()]; "
            "print('TOOL_RELEASED')\". Wait for that tool using write_stdin if needed. "
            "Do not read other files or run other commands. After TOOL_RELEASED reply exactly WORKER_RECOVERED.")
        identity = wait_for(lambda: next((row for row in runs(directory) if row["run_id"] not in previous), None))
        save(directory / "worker-identity.json", identity)
        wait_for(lambda: (workspace / "tool-ready").exists(), timeout=80)
        terminal.save_failure_artifacts(directory / "worker-waiting")
        stage("ready_for_worker_restart")
        wait_for(lambda: (workspace / "release-tool").exists(), timeout=160)
        identity = wait_turn(directory, previous)
        fact = capture_usage(directory, domain, identity)
        save(directory / "worker-usage.json", fact)
        stage("worker_completed_ready_for_database_verification")
        wait_for(lambda: (directory / "continue-worker").exists(), timeout=1800)
        command(terminal, "/quit")
        assert terminal.wait_for_exit(timeout=30) == 0
        check_summary(terminal, directory / "worker-exit", fact, resume=True)
    return identity


def verify_access_denied(directory, workspace, domain, identity):
    """核对真实服务拒绝读取时，完整客户端不再给出误导的恢复命令。"""
    with httpx.Client(base_url=domain, headers=build_service_headers(), timeout=20, trust_env=False) as client:
        response = client.post("/reports/open", json={"cid": identity["cid"], "sid": identity["sid"], "proto": "mind.chat"})
        assert response.status_code == 403
        assert response.json()["details"]["code"] == "owner_mismatch"
    with launch(directory, workspace, "resume", identity["sid"]) as terminal:
        terminal.wait_for_screen_text("WORKER_RECOVERED", timeout=40)
        command(terminal, "/exit")
        assert terminal.wait_for_exit(timeout=30) == 0
        terminal.save_failure_artifacts(directory / "denied-resume-fixed")
        assert "mind resume" not in terminal.session.output_text()
        assert "Token usage" not in terminal.session.output_text()


def verify_close_failure(directory, workspace, identity):
    """用独立 SQLite 独占锁制造真实收尾失败，验证退出不打印正常摘要。"""
    with launch(directory, workspace, "resume", identity["sid"]) as terminal:
        terminal.wait_for_screen_text("WORKER_RECOVERED", timeout=40)
        time.sleep(1)
        connection = sqlite3.connect(directory / "state/history/history.db")
        try:
            connection.execute("BEGIN EXCLUSIVE")
            command(terminal, "/exit")
            code = terminal.wait_for_exit(timeout=50)
            terminal.save_failure_artifacts(directory / "close-failed")
            assert code != 0
            assert "Token usage" not in terminal.session.output_text()
            assert "mind resume" not in terminal.session.output_text()
        finally:
            connection.rollback()
            connection.close()
