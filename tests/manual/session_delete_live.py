"""以完整源码 CLI、原生终端和已部署 AppServer 验收隔离会话删除。"""

import argparse
import hashlib
import httpx
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from http.server import (
    BaseHTTPRequestHandler,
    ThreadingHTTPServer,
)
from pathlib import Path

from agent.stores.agents.graph import AgentGraphStore
from agent.stores.sessions.history import ConversationHistoryStore
from infrastructure.config.paths import default_config_home
from infrastructure.config.session import ConfigSession
from infrastructure.config.store import ConfigStore
from infrastructure.persistence.transcripts import ConversationTranscriptStore
from protocol.schema.identifiers import (
    new_cid,
    new_request_id,
    new_sid,
)
from protocol.transport.auth import build_service_headers
from tests.pty import (
    PtyKey,
    TerminalEnvironment,
    TerminalSize,
    spawn_terminal,
)


REPOSITORY = Path.cwd().resolve()


class ForwardingServer:
    """转发真实服务字节流，仅记录删除身份与状态，不保存凭据或对话正文。"""

    def __init__(self, upstream: str):
        """绑定回环端口，所有路径都交由指定的已部署服务处理。"""
        self.facts = []
        self.drop_next_delete_response = False
        self.upstream = upstream.rstrip("/")
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                """关闭可能包含请求参数的默认访问日志。"""

            def forward(self):
                """保持正式 HTTP 响应，取消观察时仅关闭对应上游连接。"""
                payload = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                headers = {key: value for key, value in self.headers.items() if key.lower() not in {"host", "connection", "accept-encoding"}}
                fact = None
                if self.path.startswith("/session/delete"):
                    fact = {"method": self.command, "path": self.path}
                    if self.command == "POST":
                        fact["request"] = json.loads(payload)
                    owner.facts.append(fact)
                try:
                    with httpx.Client(timeout=180, trust_env=False) as client:
                        with client.stream(self.command, owner.upstream + self.path, headers=headers, content=payload) as response:
                            if fact is not None:
                                fact["status"] = response.status_code
                                if self.command == "POST" and response.status_code == 200 and owner.drop_next_delete_response:
                                    owner.drop_next_delete_response = False
                                    response.read()
                                    fact["response_dropped"] = True
                                    self.close_connection = True
                                    return
                            self.send_response(response.status_code)
                            for key in ("content-type", "content-length", "content-encoding", "cache-control"):
                                if key in response.headers:
                                    self.send_header(key, response.headers[key])
                            self.end_headers()
                            for chunk in response.iter_raw():
                                self.wfile.write(chunk)
                                self.wfile.flush()
                except (OSError, httpx.HTTPError):
                    self.close_connection = True

            do_GET = forward
            do_POST = forward

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        """返回仅供本轮客户端使用的透明转发地址。"""
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        """停止接入并释放本轮监听端口。"""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        assert not self.thread.is_alive()


def save(path, value):
    """仅保存明确选取的验收事实。"""
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2), encoding="utf-8")


def stage(value):
    """向运行终端提交不含凭据的阶段标识。"""
    print(json.dumps({"stage": value}), flush=True)


def wait_for(predicate, *, timeout=150):
    """等待实际状态满足条件，超时保留失败证据。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.1)
    raise TimeoutError("live acceptance condition timed out")


def runs(directory):
    """只读取本轮客户端正式 Run 身份与状态，不采集请求正文。"""
    path = directory / "state/history/runtime.db"
    if not path.exists():
        return []
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "run_remote_requests" not in tables:
            return []
        return [dict(row) for row in connection.execute(
            "SELECT r.run_id, r.cid, r.sid, r.turn_id, s.status FROM run_remote_requests r "
            "JOIN run_snapshots s ON s.run_id=r.run_id ORDER BY r.created_at"
        )]


def command(terminal, text):
    """通过原生终端发送真实输入。"""
    terminal.write_user_text(text)
    terminal.send_key(PtyKey.ENTER)


def wait_turn(directory, previous):
    """等待新根轮次的持久终态，不把回显中的提示词当作模型完成。"""
    def completed():
        """读取已经进入终态的新根轮次。"""
        candidates = [row for row in runs(directory) if row["run_id"] not in previous]
        if not candidates or candidates[-1]["status"] not in {"completed", "failed", "cancelled", "interrupted"}:
            return None
        return candidates[-1]
    result = wait_for(completed)
    save(directory / "last-turn.json", result)
    assert result["status"] == "completed", result
    time.sleep(0.4)
    return result


def prepare(directory, proxy, source, *, route=None):
    """以现有 Provider 建立隔离配置，启用本轮所需的子代理能力。"""
    workspace = directory / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    provider = source["model_provider"]
    config = ConfigSession(ConfigStore(directory / "config/config.toml"), workspace=workspace)
    config.update_user({
        ("model_provider",): provider,
        **{("model_providers", provider, key): value for key, value in source["model_providers"][provider].items()},
        ("service", "domain"): proxy.url,
        ("sandbox_mode",): "danger-full-access",
        ("approval_policy",): "never",
        ("network_access",): "enabled",
        ("features", "subagents"): True,
        ("skills", "enabled"): ["__acceptance_none__"],
    })
    if route is not None:
        config.update_user({("model_providers", provider, "route"): route})
    decision = config.resolve(workspace=workspace).project_trust
    config.set_project_trust(decision, "trusted", workspace=workspace)
    return workspace


def launch(directory, workspace, *arguments, columns=100, no_color=False):
    """从稳定 mind.py 入口启动完整客户端，隔离配置与所有本地状态。"""
    return spawn_terminal(
        [sys.executable, str(REPOSITORY / "mind.py"), *arguments], cwd=workspace,
        env={**os.environ, "PYTHONUTF8": "1", "MIND_HOME": str(directory / "config"), "MIND_STATE_HOME": str(directory / "state")},
        size=TerminalSize(rows=32, columns=columns), terminal=TerminalEnvironment(no_color=no_color),
        failure_artifact_directory=directory / "failure-terminal",
    )


def remote_status(client, identity):
    """使用正式查询端点核对远端轮次是否仍存在。"""
    return client.get("/turn/status", params={key: identity[key] for key in ("cid", "sid", "turn_id")})


def target_turns(directory, targets):
    """从实际 Transcript 读取根与子会话的轮次坐标，不保存聊天载荷。"""
    transcripts = ConversationTranscriptStore(directory / "state/sessions")
    identities = []
    for target in targets:
        path = transcripts.existing_path_for_session(target["sid"])
        assert path
        turn_ids = {entry.turn_id for entry in transcripts.reader(path).read() if entry.turn_id}
        assert turn_ids, target
        identities.extend({**target, "turn_id": turn_id} for turn_id in sorted(turn_ids))
    return identities


def capture_menu(terminal, directory, *, selected=False):
    """等待完整菜单帧后核验冻结文案和实际单元格样式。"""
    snapshot = REPOSITORY / "tests/frontends/tui/acceptance/fixtures/delete_confirmation.txt"
    expected = snapshot.read_text(encoding="utf-8").strip("\n")
    if selected:
        expected = expected.replace("› 1.", "  1.").replace("  2.", "› 2.")
    def complete_frame():
        """只接受已经渲染完整底部提示的菜单帧。"""
        current = terminal.screen.snapshot()
        rendered = "\n".join(line.rstrip() for line in current.visible_lines)
        return current if expected in rendered else None

    screen = wait_for(complete_frame, timeout=10)
    row = next(index for index, line in enumerate(screen.visible_lines) if ("› 2." if selected else "› 1.") in line)
    cell = terminal.screen.cell(row, 5)
    assert cell.bold and not cell.reverse
    terminal.save_failure_artifacts(directory)
    save(directory / "style.json", asdict(cell))


def verify(directory):
    """在新解释器中核对本轮真实数据清理及无关会话保留。"""
    facts = json.loads((directory / "facts.json").read_text(encoding="utf-8"))
    history = ConversationHistoryStore(directory / "state/history/history.db")
    graphs = AgentGraphStore(directory / "state/history/agents.db")
    transcripts = ConversationTranscriptStore(directory / "state/sessions")
    for target in facts["targets"]:
        assert history.find_session(target["sid"]) is None
        assert graphs.load(target["sid"]) is None
        assert transcripts.existing_path_for_session(target["sid"]) == ""
        with sqlite3.connect(directory / "state/history/effects.db") as connection:
            for table in ("local_effects", "local_tool_results"):
                assert connection.execute(f"SELECT count(*) FROM {table} WHERE cid=? AND sid=?", (target["cid"], target["sid"])).fetchone() == (0,)
        with sqlite3.connect(directory / "state/history/approvals.db") as connection:
            assert connection.execute("SELECT count(*) FROM approval_facts WHERE session_id=?", (target["sid"],)).fetchone() == (0,)
    control = facts["control"]
    assert history.find_session(control["sid"]) is not None
    assert transcripts.existing_path_for_session(control["sid"])
    with sqlite3.connect(directory / "state/history/history.db") as connection:
        assert connection.execute("SELECT completed FROM session_deletions WHERE request_id=?", (facts["receipt"]["request_id"],)).fetchone() == (1,)
    assert all((row["cid"], row["sid"]) not in {(target["cid"], target["sid"]) for target in facts["targets"]} for row in runs(directory))
    for path in (directory / "state/history").glob("*.db"):
        with sqlite3.connect(path) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    print(json.dumps({"verified": True, "pid": os.getpid(), "target_count": len(facts["targets"]), "control_survived": True}), flush=True)


def accept(directory, *, exit_summary=False, route=None, pause_before_delete=False):
    """执行实际对话、子代理、菜单取消确认、正式回执与重启核验。"""
    source = ConfigStore(default_config_home() / "config.toml").read_raw(create=False)
    source_config = default_config_home() / "config.toml"
    source_digest = hashlib.sha256(source_config.read_bytes()).hexdigest()
    domain = source["service"]["domain"]
    directory.mkdir(parents=True, exist_ok=False)
    proxy = ForwardingServer(domain)
    facts = {"service": domain, "client_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPOSITORY, text=True).strip()}
    provider = source["model_providers"][source["model_provider"]]
    facts["provider"] = {"model": provider["model"], "route": route or provider["route"]}
    try:
        workspace = prepare(directory, proxy, source, route=route)
        with httpx.Client(base_url=domain, headers=build_service_headers(), timeout=30, trust_env=False) as client:
            spec_response = client.get("/openapi.json")
            spec_response.raise_for_status()
            spec = spec_response.json()
            assert set(spec["paths"]["/session/delete"]) == {"get", "post"}
            facts["service_version"] = spec["info"]["version"]
            facts["openapi_sha256"] = hashlib.sha256(spec_response.content).hexdigest()
            stage("source_cli_starting")
            with launch(directory, workspace) as terminal:
                terminal.wait_for_screen_text(source["model_providers"][source["model_provider"]]["model"], timeout=30)
                time.sleep(0.5)
                stage("control_conversation")
                previous = {row["run_id"] for row in runs(directory)}
                command(terminal, "This is an isolated deletion acceptance control. Do not use tools. Reply exactly CONTROL_OK.")
                control = wait_turn(directory, previous)
                facts["control"] = control
                save(directory / "facts.json", facts)
                assert remote_status(client, control).status_code == 200
                command(terminal, "/new")
                time.sleep(0.5)
                previous = {row["run_id"] for row in runs(directory)}
                stage("root_and_child_conversation")
                command(terminal, "This is an isolated session deletion acceptance. I explicitly authorize one subagent for this test. Use spawn_agent exactly once with task_name acceptance_child and fork_turns none. Its task: reply CHILD_OK, use no tools and touch no files. Wait for its completion using wait_agent, then reply ROOT_OK. Do not use other tools or read files.")
                root = wait_for(lambda: next((row for row in runs(directory) if row["run_id"] not in previous), None))
                facts["root"] = root
                save(directory / "facts.json", facts)
                accepted = wait_for(lambda: response if (response := remote_status(client, root)).status_code == 200 else None)
                assert accepted.json()["terminal"] is None
                busy_command = {"cid": root["cid"], "sid": root["sid"], "request_id": new_request_id("delete_busy"), "descendants": []}
                busy = client.post("/session/delete", json=busy_command)
                facts["busy"] = {"status": busy.status_code, "code": busy.json().get("details", {}).get("code")}
                assert facts["busy"] == {"status": 409, "code": "session_busy"}, facts["busy"]
                command(terminal, "/delete")
                terminal.wait_for_screen_text("disabled while a task is in progress", timeout=15)
                root = wait_turn(directory, previous)
                facts["root"] = root
                checkpoint = AgentGraphStore(directory / "state/history/agents.db").load(root["sid"])
                assert checkpoint is not None and len(checkpoint.records) == 1
                targets = [{"cid": root["cid"], "sid": root["sid"]}, *[{"cid": record.thread.cid, "sid": record.thread.sid} for record in checkpoint.records]]
                facts["targets"] = targets
                facts["target_turns"] = target_turns(directory, targets)
                for identity in facts["target_turns"]:
                    response = remote_status(client, identity)
                    assert response.status_code == 200 and response.json()["terminal"] is not None
                save(directory / "facts.json", facts)
                stage("cancel_then_continue")
                command(terminal, "/delete")
                terminal.wait_for_screen_text("› 1. No, keep this session")
                capture_menu(terminal, directory / "default-menu")
                terminal.send_key(PtyKey.ENTER)
                assert proxy.facts == []
                previous = {row["run_id"] for row in runs(directory)}
                command(terminal, "Do not use tools. Reply exactly CONTINUED_AFTER_CANCEL.")
                facts["root"] = wait_turn(directory, previous)
                facts["target_turns"] = target_turns(directory, targets)
                if exit_summary:
                    from tests.manual.exit_summary_live import capture_usage

                    facts["usage_before_delete"] = [
                        capture_usage(directory, domain, target, root=index == 0)
                        for index, target in enumerate(targets)
                    ]
                    for index in range(3):
                        cached = facts["usage_before_delete"][0]["usage"]["cached_input_tokens"]
                        if cached is None or cached > 0:
                            break
                        previous = {row["run_id"] for row in runs(directory)}
                        command(terminal, f"Continue this isolated cache acceptance. Do not use tools. Reply exactly CACHE_OK_{index}.")
                        facts["root"] = wait_turn(directory, previous)
                        facts["usage_before_delete"][0] = capture_usage(directory, domain, targets[0])
                    facts["target_turns"] = target_turns(directory, targets)
                    save(directory / "facts.json", facts)
                    if pause_before_delete:
                        stage("ready_for_database_verification")
                        wait_for(lambda: (directory / "continue-delete").exists(), timeout=1800)
                stage("confirm_delete")
                command(terminal, "/delete")
                terminal.wait_for_screen_text("› 1. No, keep this session")
                terminal.write_user(b"\x1b[B")
                selected = terminal.wait_for_screen_text("› 2. Yes, delete and exit")
                capture_menu(terminal, directory / "confirm-menu", selected=True)
                save(directory / "selected-screen.json", asdict(selected))
                terminal.send_key(PtyKey.ENTER)
                facts["delete_exit_code"] = terminal.wait_for_exit(timeout=30)
                assert facts["delete_exit_code"] == 0
                assert "mind resume" not in terminal.screen.snapshot().visible_text
                if exit_summary:
                    from tests.manual.exit_summary_live import check_summary

                    check_summary(terminal, directory / "deleted-exit", facts["usage_before_delete"][0], resume=False)
            assert len(proxy.facts) == 1 and proxy.facts[0]["status"] == 200, proxy.facts
            frozen = proxy.facts[0]["request"]
            assert {(row["cid"], row["sid"]) for row in [frozen, *frozen["descendants"]]} == {(row["cid"], row["sid"]) for row in targets}
            stage("formal_receipt_and_restart")
            receipt = client.get("/session/delete", params={"request_id": frozen["request_id"]})
            assert receipt.status_code == 200
            assert client.post("/session/delete", json=frozen).json() == receipt.json()
            assert remote_status(client, root).status_code == 404
            facts["remote_target_statuses"] = [remote_status(client, identity).status_code for identity in facts["target_turns"]]
            assert set(facts["remote_target_statuses"]) == {404}
            assert remote_status(client, control).status_code == 200
            missing_cid = new_cid()
            missing = client.post("/session/delete", json={"cid": missing_cid, "sid": new_sid(missing_cid), "request_id": new_request_id("delete_missing"), "descendants": []})
            facts["missing"] = {"status": missing.status_code, "code": missing.json().get("details", {}).get("code")}
            assert facts["missing"] == {"status": 404, "code": "session_missing"}
            facts["receipt"] = receipt.json()
            save(directory / "facts.json", facts)
            result = subprocess.run([sys.executable, "-m", "tests.manual.session_delete_live", "--directory", str(directory), "--verify"], cwd=REPOSITORY, capture_output=True, text=True, encoding="utf-8", timeout=20)
            assert result.returncode == 0, result.stderr
            save(directory / "new-process.json", json.loads(result.stdout))
            with launch(directory, workspace, "resume", root["sid"]) as terminal:
                terminal.wait_for_screen_text("Session is unavailable.", timeout=30)
                facts["deleted_resume_exit_code"] = terminal.wait_for_exit(timeout=20)
                assert facts["deleted_resume_exit_code"] != 0
                terminal.save_failure_artifacts(directory / "deleted-resume-rejected")
            with launch(directory, workspace, "resume", control["sid"]) as terminal:
                terminal.wait_for_screen_text("CONTROL_OK", timeout=30)
                terminal.save_failure_artifacts(directory / "control-resumed")
                command(terminal, "/quit")
                assert terminal.wait_for_exit(timeout=20) == 0
            if exit_summary:
                from tests.manual.exit_summary_live import verify_exits

                facts["exit_acceptance"] = verify_exits(directory, workspace, domain, control, proxy)
                save(directory / "facts.json", facts)
            stage("passed")
    finally:
        facts["user_configuration_unchanged"] = hashlib.sha256(source_config.read_bytes()).hexdigest() == source_digest
        try:
            save(directory / "facts.json", facts)
            save(directory / "deletion-http.json", proxy.facts)
        finally:
            proxy.close()
            isolated_config = ConfigStore(directory / "config/config.toml")
            if isolated_config.path.exists():
                isolated_config.update({}, delete_paths=[("model_providers", source["model_provider"], "api_key")])
        assert facts["user_configuration_unchanged"]


def cleanup(directory):
    """删除清单内已终止的测试会话和隔离状态，保留脱敏完成证据。"""
    facts = json.loads((directory / "facts.json").read_text(encoding="utf-8"))
    cleanup_path = directory / "test-session-cleanup.json"
    journal = json.loads(cleanup_path.read_text(encoding="utf-8")) if cleanup_path.exists() else {"receipts": [], "requests": []}
    with httpx.Client(base_url=facts["service"], headers=build_service_headers(), timeout=30, trust_env=False) as client:
        for label in ("root", "control"):
            identity = facts.get(label)
            if identity is None:
                continue
            response = remote_status(client, identity)
            if response.status_code == 404:
                continue
            assert response.status_code == 200 and response.json()["terminal"] is not None
            targets = facts.get("targets", []) if label == "root" else []
            frozen = next((row for row in journal["requests"] if (row["cid"], row["sid"]) == (identity["cid"], identity["sid"])), None)
            if frozen is None:
                frozen = {
                    "cid": identity["cid"], "sid": identity["sid"], "request_id": new_request_id("delete_acceptance_cleanup"),
                    "descendants": [row for row in targets if row["sid"] != identity["sid"]],
                }
                journal["requests"].append(frozen)
                save(cleanup_path, journal)
            deleted = client.post("/session/delete", json=frozen)
            assert deleted.status_code == 200
            journal["receipts"].append(deleted.json())
            save(cleanup_path, journal)
            assert remote_status(client, identity).status_code == 404
    config = ConfigStore(directory / "config/config.toml")
    if config.path.exists():
        raw = config.read_raw(create=False)
        config.update({}, delete_paths=[
            ("model_providers", key, "api_key")
            for key, profile in raw.get("model_providers", {}).items() if "api_key" in profile
        ])
    state_directory = (directory / "state").resolve()
    assert state_directory.parent == directory.resolve()
    if state_directory.exists():
        assert (directory / "workspace").is_dir() and config.path.is_file()
        shutil.rmtree(state_directory)
    journal["isolated_state_removed"] = True
    save(cleanup_path, journal)
    stage("test_sessions_cleaned")


def main():
    """要求显式隔离目录；新进程验证不访问用户配置或发送模型请求。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--verify", action="store_true")
    mode.add_argument("--cleanup", action="store_true")
    parser.add_argument("--exit-summary", action="store_true")
    parser.add_argument("--route", choices=("responses", "chat_completions", "messages"))
    parser.add_argument("--pause-before-delete", action="store_true")
    args = parser.parse_args()
    if not (REPOSITORY / "mind.py").is_file():
        parser.error("Run this command from the repository root")
    directory = args.directory.resolve()
    if args.cleanup:
        cleanup(directory)
    elif args.verify:
        verify(directory)
    else:
        accept(directory, exit_summary=args.exit_summary, route=args.route, pause_before_delete=args.pause_before_delete)


if __name__ == "__main__":
    main()
