"""真实 ConPTY、TCP 故障服务及独立进程的删除恢复验收。"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import suppress
from dataclasses import asdict
from http.server import (
    BaseHTTPRequestHandler,
    ThreadingHTTPServer,
)
from pathlib import Path
from urllib.parse import (
    parse_qs,
    urlparse,
)

import pytest

from infrastructure.persistence.transcripts import ConversationTranscriptStore
from protocol.schema.session_deletion import (
    SessionDeletionRequest,
    SessionDeletionTarget,
)
from tests.agent.stores.sessions.deletion_fixture import (
    rows,
    seeded,
    store,
)
from tests.pty import (
    PtyKey,
    TerminalEnvironment,
    TerminalSize,
    spawn_terminal,
)


pytestmark = pytest.mark.pty_acceptance


class DeletionServer:
    def __init__(self):
        self.mode = "success"
        self.posts = []
        self.gets = []
        self.receipts = {}
        self.received = threading.Event()
        self.release = threading.Event()
        state = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def reply(self, status, body):
                data = json.dumps(body).encode()
                with suppress(BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                assert self.path == "/session/delete"
                assert set(payload) == {"cid", "sid", "request_id", "descendants"}
                command = SessionDeletionRequest(
                    payload["request_id"], SessionDeletionTarget(payload["cid"], payload["sid"]),
                    tuple(SessionDeletionTarget(**target) for target in payload["descendants"]),
                )
                state.posts.append(command)
                if state.mode == "reject":
                    self.reply(409, {"details": {"code": "session_busy", "retryable": True}})
                    return
                receipt = {
                    "cid": command.root.cid, "sid": command.root.sid, "request_id": command.request_id,
                    "status": "deleted", "targets": [target.payload() for target in command.targets],
                }
                state.receipts[command.request_id] = receipt
                state.received.set()
                if state.mode == "lost":
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                if state.mode == "delay":
                    state.release.wait(timeout=20)
                self.reply(200, receipt)

            def do_GET(self):
                request_id = parse_qs(urlparse(self.path).query)["request_id"][0]
                state.gets.append(request_id)
                if request_id not in state.receipts:
                    self.reply(404, {"details": {"code": "request_not_found"}})
                    return
                self.reply(200, state.receipts[request_id])

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        assert not self.thread.is_alive()


@pytest.fixture
def deletion_case(tmp_path):
    plan, control = seeded(tmp_path)
    (tmp_path / "fixture.json").write_text(json.dumps({
        "request_id": plan.request_id,
        "targets": [{"cid": target.cid, "sid": target.sid} for target in plan.targets],
        "control": {"cid": control.cid, "sid": control.sid}, "before": rows(tmp_path),
    }), encoding="utf-8")
    (tmp_path / "config.toml").write_text("shared_configuration = true\n", encoding="utf-8")
    server = DeletionServer()
    try:
        yield tmp_path, plan, server
    finally:
        (tmp_path / "http-facts.json").write_text(json.dumps({
            "posts": [command.payload() for command in server.posts], "gets": server.gets,
        }, indent=2), encoding="utf-8")
        server.close()


def launch(directory, server, *, mode="initial", columns=80, rows_count=28, no_color=False):
    return spawn_terminal(
        [sys.executable, "-m", "tests.pty.session_delete_scenario", str(directory), server.url, mode],
        cwd=Path.cwd(), env={**os.environ, "PYTHONUTF8": "1"},
        size=TerminalSize(rows=rows_count, columns=columns),
        terminal=TerminalEnvironment(no_color=no_color),
        failure_artifact_directory=directory / "artifacts",
    )


def command(terminal, text):
    terminal.write_user_text(text)
    terminal.send_key(PtyKey.ENTER)


def confirm(terminal):
    command(terminal, "/delete")
    terminal.wait_for_screen_text("No, keep this session")
    terminal.write_user(b"\x1b[B")
    terminal.send_key(PtyKey.ENTER)


def wait_for(predicate):
    deadline = time.monotonic() + 12
    while not predicate():
        if time.monotonic() > deadline:
            raise TimeoutError("deletion acceptance condition not reached")
        time.sleep(0.02)


def verify_new_process(directory):
    result = subprocess.run(
        [sys.executable, "-m", "tests.manual.session_delete_storage", "--child", "verify", "--directory", str(directory)],
        capture_output=True, text=True, encoding="utf-8", timeout=20,
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    (directory / "new-process-verification.json").write_text(result.stdout, encoding="utf-8")


@pytest.mark.parametrize("columns,rows_count,no_color", [(80, 28, False), (44, 22, False), (36, 18, False), (80, 28, True)])
def test_native_delete_menu_cancel_confirm_and_scope(deletion_case, columns, rows_count, no_color):
    directory, plan, server = deletion_case
    with launch(directory, server, columns=columns, rows_count=rows_count, no_color=no_color) as terminal:
        terminal.wait_for_screen_text("DELETE ACCEPTANCE READY", timeout=15)
        for index, key in enumerate((PtyKey.ENTER, PtyKey.ESCAPE, PtyKey.CTRL_C)):
            command(terminal, "/delete")
            terminal.wait_for_screen_text("No, keep this session")
            terminal.save_failure_artifacts(directory / f"cancel-{index}")
            terminal.send_key(key)
            command(terminal, f"继续验收{index}")
            terminal.wait_for_screen_text(f"Accepted: 继续验收{index}")
            assert server.posts == []
        command(terminal, "/delete")
        screen = terminal.wait_for_screen_text("No, keep this session")
        assert "› 1. No, keep this session" in screen.visible_text
        if columns == 80:
            # 固定 Codex slash_delete_confirmation_popup 快照，避免依赖未跟踪的参考源码目录。
            expected = (Path(__file__).with_name("fixtures") / "delete_confirmation.txt").read_text(encoding="utf-8").strip("\n")
            actual = "\n".join(line.rstrip() for line in screen.visible_lines).strip("\n")
            assert expected in actual
        terminal.save_failure_artifacts(directory / "default")
        title_row = next(i for i, line in enumerate(screen.visible_lines) if "Delete this session?" in line)
        assert terminal.screen.cell(title_row, 2).bold
        terminal.write_user(b"\x1b[B")
        selected = terminal.wait_for_screen_text("› 2. Yes, delete and exit")
        terminal.save_failure_artifacts(directory / "selected")
        (directory / "selected-screen.json").write_text(json.dumps(asdict(selected)), encoding="utf-8")
        selected_row = next(i for i, line in enumerate(selected.visible_lines) if "› 2." in line)
        label = terminal.screen.cell(selected_row, 5)
        cancel_row = next(i for i, line in enumerate(selected.visible_lines) if "1. No, keep" in line)
        cancel_label = terminal.screen.cell(cancel_row, 5)
        assert label.bold and not label.reverse
        assert label.background == cancel_label.background
        if no_color:
            assert label.foreground == "default"
        (directory / "style-facts.json").write_text(json.dumps({
            "selected_label": asdict(label), "cancel_label": asdict(cancel_label), "no_color": no_color,
        }, indent=2), encoding="utf-8")
        if columns == 80 and not no_color:
            terminal.resize(TerminalSize(rows=18, columns=36))
            terminal.write_user(b"\x1b[D")
            wait_for(lambda: "ads will also be deleted." in terminal.screen.snapshot().visible_text)
            terminal.wait_for_screen_text("› 2. Yes, delete and exit")
            terminal.save_failure_artifacts(directory / "resized-narrow")
            terminal.resize(TerminalSize(rows=28, columns=80))
            terminal.write_user(b"\x1b[D")
            wait_for(lambda: "Permanently delete this session now" in terminal.screen.snapshot().visible_text)
            terminal.save_failure_artifacts(directory / "resized-wide")
        terminal.write_user(b"\r\r")
        assert terminal.wait_for_exit(timeout=15) == 0
        assert len(server.posts) == 1
        assert len(server.posts[0].targets) == len(plan.targets)
        assert "mind resume" not in terminal.diagnostics().screen.visible_text
    verify_new_process(directory)


def test_rejection_then_lost_response_recovers_original_request(deletion_case):
    directory, plan, server = deletion_case
    backend = store(directory, plan.root)
    server.mode = "reject"
    with launch(directory, server) as terminal:
        terminal.wait_for_screen_text("DELETE ACCEPTANCE READY", timeout=15)
        confirm(terminal)
        terminal.wait_for_screen_text("The server rejected session deletion")
        assert backend.pending() == ()
        command(terminal, "after rejection")
        terminal.wait_for_screen_text("Accepted: after rejection")
        server.mode = "lost"
        confirm(terminal)
        terminal.wait_for_screen_text("Deletion outcome is unknown.")
        pending = backend.pending()[0]
        command(terminal, "after lost response")
        terminal.wait_for_screen_text("blocked pending deletion recovery")
        terminal.save_failure_artifacts(directory / "unknown")
        command(terminal, f"/delete recover {pending.request_id}")
        assert terminal.wait_for_exit(timeout=15) == 0
    assert len(server.posts) == 2
    assert server.gets == [pending.request_id]
    verify_new_process(directory)


@pytest.mark.parametrize("failure", ("cancel", "crash", "writer"))
def test_restart_recovers_without_resubmission(deletion_case, failure):
    directory, plan, server = deletion_case
    backend = store(directory, plan.root)
    writer = None
    if failure == "writer":
        transcripts = ConversationTranscriptStore(directory / "sessions")
        writer = transcripts.writer(transcripts.existing_path_for_session(plan.root.sid), session_id=plan.root.sid)
        writer.open()
    else:
        server.mode = "delay"
    try:
        with launch(directory, server) as terminal:
            terminal.wait_for_screen_text("DELETE ACCEPTANCE READY", timeout=15)
            confirm(terminal)
            assert server.received.wait(timeout=10)
            pending = backend.pending()[0]
            if failure == "cancel":
                terminal.send_key(PtyKey.CTRL_C)
                terminal.wait_for_screen_text("Deletion was interrupted")
            elif failure == "writer":
                terminal.wait_for_screen_text("local cleanup did not finish")
            terminal.save_failure_artifacts(directory / failure)
            if failure != "crash":
                command(terminal, "/quit")
                assert terminal.wait_for_exit(timeout=15) == 0
    finally:
        if writer is not None:
            writer.close()
        server.release.set()
    with launch(directory, server, mode="recovery") as terminal:
        terminal.wait_for_screen_text("DELETE ACCEPTANCE READY", timeout=15)
        command(terminal, f"/delete recover {pending.request_id}")
        terminal.wait_for_screen_text("Session deletion recovered.")
        assert backend.pending() == ()
        terminal.save_failure_artifacts(directory / "recovered")
        command(terminal, "/quit")
        assert terminal.wait_for_exit(timeout=15) == 0
    assert len(server.posts) == 1
    assert server.gets == [pending.request_id]
    verify_new_process(directory)
