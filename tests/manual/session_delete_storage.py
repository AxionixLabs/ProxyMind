"""从源码验收本地会话删除：独立进程、真实 SQLite、文件锁及强制退出恢复。"""

import argparse
import asyncio
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from agent.ports.persistence import RunPersistenceConflict
from agent.ports.session_deletion import (
    LocalDeletionPlan,
    LocalDeletionTarget,
    SessionDeletionConflict,
)
from agent.protocol import RunEvent
from infrastructure.persistence.transcripts import ConversationTranscriptStore
from tests.agent.stores.sessions.deletion_fixture import (
    approval,
    command,
    effect,
    rows,
    seeded,
    store,
)


def emit(event: str, **facts) -> None:
    """输出带独立进程身份的验收事实。"""
    print(json.dumps({"event": event, "pid": os.getpid(), **facts}), flush=True)


def load_fixture(directory: Path):
    """读取本轮独立目录中的冻结目标和对照身份。"""
    data = json.loads((directory / "fixture.json").read_text(encoding="utf-8"))
    targets = tuple(LocalDeletionTarget(row["cid"], row["sid"], ()) for row in data["targets"])
    return LocalDeletionPlan(data["request_id"], targets, targets[0]), data


def child(mode: str, directory: Path) -> None:
    """在独立解释器中驱动真实持久化入口，不访问用户运行目录。"""
    plan, data = load_fixture(directory)
    owner = plan.targets[0]
    backend = store(directory, owner)
    transcripts = ConversationTranscriptStore(directory / "sessions")
    if mode == "claim":
        emit("claim-ready")
        request_id = sys.stdin.readline().strip()
        candidate = replace(plan, request_id=request_id)
        try:
            backend.prepare(candidate)
        except SessionDeletionConflict:
            emit("claim-rejected", request_id=request_id)
        else:
            emit("claim-accepted", request_id=request_id)
    elif mode == "concurrent-retry":
        saved = backend.pending()[0]
        emit("recovery-ready")
        assert sys.stdin.readline().strip() == "recover"
        try:
            backend.delete(saved)
        except OSError:
            emit("recovery-contended", request_id=saved.request_id)
        else:
            emit("recovery-completed", request_id=saved.request_id)
    elif mode == "hold":
        path = transcripts.existing_path_for_session(owner.sid)
        writer = transcripts.writer(path, session_id=owner.sid)
        writer.open()
        writer.append("held-by-child")
        assert transcripts.reader(path).read()[-1].event == "held-by-child"
        emit("writer-open")
        try:
            assert sys.stdin.readline().strip() == "release"
        finally:
            writer.close()
        emit("writer-closed")
    elif mode == "try-delete":
        try:
            backend.delete(plan)
        except OSError as error:
            assert backend.pending()
            emit("delete-blocked", error_type=type(error).__name__, winerror=getattr(error, "winerror", None))
        else:
            raise AssertionError("open transcript did not block deletion")
    elif mode == "pause":
        def pause(_targets):
            emit("partial-cleanup", counts=rows(directory))
            sys.stdin.readline()
            raise AssertionError("parent must terminate this process")

        with patch.object(backend.approvals, "delete_sessions", side_effect=pause):
            backend.delete(plan)
    elif mode == "retry":
        pending = backend.pending()
        assert len(pending) == 1
        backend.delete(pending[0])
        assert backend.pending() == ()
        emit("recovered", counts=rows(directory))
    elif mode == "delete":
        backend.delete(plan)
        emit("deleted", counts=rows(directory))
    elif mode == "race":
        backend.history.touch_session(cid=owner.cid, sid=owner.sid, title="competing writer")
        emit("race-ready")
        assert sys.stdin.readline().strip() == "race"
        deadline = time.monotonic() + 40
        written = 0
        while time.monotonic() < deadline:
            try:
                backend.history.touch_session(cid=owner.cid, sid=owner.sid, title="competing writer")
                written += 1
            except sqlite3.IntegrityError:
                emit("race-fenced", writes_before_fence=written)
                return
        raise AssertionError("competing writer never observed the deletion fence")
    elif mode == "late":
        checkpoint = backend.graphs.load(owner.sid)
        assert checkpoint is not None
        old = sqlite3.connect(directory / "history.db")
        row = old.execute("SELECT * FROM conversation_session_cursors WHERE cid = ? AND sid = ?",
                          (owner.cid, owner.sid)).fetchone()
        assert row is not None
        path = transcripts.existing_path_for_session(owner.sid)
        writer = transcripts.writer(path, session_id=owner.sid)
        request = command(owner, suffix="_late")
        event = RunEvent.create(sequence=1, session_id=request.session_id, run_id=request.run_id,
                                kind="run_queued", payload={"status": "queued"}, causation_id=request.command_id)
        emit("late-writer-ready")
        assert sys.stdin.readline().strip() == "write"
        rejected = []
        try:
            try:
                old.execute("INSERT INTO conversation_session_cursors VALUES (?,?,?,?,?,?,?,?,?,?)", row)
            except sqlite3.IntegrityError:
                rejected.append("existing-sqlite-connection")
            finally:
                old.rollback()
        finally:
            old.close()
        operations = {
            "queued-agent-snapshot": lambda: backend.graphs.save(checkpoint),
            "pending-fork": lambda: backend.history.get_or_create_fork_request(
                cid=owner.cid, sid=owner.sid, request_id="fork_late_process",
            ),
            "effect": lambda: asyncio.run(backend.effects.begin(effect(owner, suffix="_late"))),
            "tool-result": lambda: asyncio.run(backend.effects.save_tool_result(owner.cid, owner.sid, "late", {}, {})),
            "approval": lambda: asyncio.run(backend.approvals.record_requested(approval(owner))),
            "run": lambda: asyncio.run(backend.runs.append_event(request, event)),
        }
        for name, operation in operations.items():
            try:
                operation()
            except (sqlite3.IntegrityError, SessionDeletionConflict, RunPersistenceConflict):
                rejected.append(name)
            else:
                raise AssertionError(f"{name} resurrected a deleted session")
        writer.open()
        writer.append("hook.completed", payload={"late": True})
        writer.append("session.ended", payload={"reason": "exit"})
        writer.close()
        assert not Path(path).exists()
        assert len(rejected) == 7
        emit("late-writes-rejected", rejected=rejected, transcript_absent=True)
    elif mode == "verify":
        actual = rows(directory)
        assert actual == {name: value // 3 for name, value in data["before"].items()}
        for target in plan.targets:
            assert backend.history.find_session(target.sid) is None
            assert backend.graphs.load(target.sid) is None
            assert transcripts.existing_path_for_session(target.sid) == ""
            assert asyncio.run(backend.runs.load_events(command(target).run_id)) == ()
        control_sid = data["control"]["sid"]
        assert backend.history.find_session(control_sid) is not None
        assert transcripts.reader(transcripts.existing_path_for_session(control_sid)).read()
        assert (directory / "config.toml").read_text(encoding="utf-8") == "shared_configuration = true\n"
        assert backend.pending() == ()
        emit("verified-from-new-process", counts=actual, control_survived=True, shared_configuration_unchanged=True)
    else:
        raise ValueError(mode)


def start(mode: str, directory: Path) -> subprocess.Popen[str]:
    """使用当前虚拟环境启动子进程，环境只传派生副本。"""
    return subprocess.Popen(
        [sys.executable, "-m", "tests.manual.session_delete_storage", "--child", mode, "--directory", str(directory)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1"},
    )


def ready(process: subprocess.Popen[str]):
    """等待子进程明确握手，超时结束该进程避免挂起验收。"""
    assert process.stdout is not None
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(process.stdout.readline)
        try:
            line = future.result(timeout=15)
        except TimeoutError:
            process.kill()
            process.wait(timeout=5)
            raise
    assert line, process.communicate(timeout=5)
    return json.loads(line)


def finish(process: subprocess.Popen[str], input_text: str | None = None):
    """核对子进程正常退出并保留结构化事实。"""
    stdout, stderr = process.communicate(input=input_text, timeout=50)
    assert process.returncode == 0, (process.returncode, stdout, stderr)
    return [{"exit_code": process.returncode, **json.loads(line)} for line in stdout.splitlines() if line.strip()]


def scenario(directory: Path, *, interrupted: bool):
    """创建目标、子会话和对照会话，执行跨进程竞争或中断恢复。"""
    directory.mkdir(parents=True, exist_ok=False)
    plan, control = seeded(directory)
    manifest = {"request_id": plan.request_id, "targets": [{"cid": t.cid, "sid": t.sid} for t in plan.targets],
                "control": {"cid": control.cid, "sid": control.sid}, "before": rows(directory)}
    (directory / "fixture.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (directory / "config.toml").write_text("shared_configuration = true\n", encoding="utf-8")
    evidence = []
    processes = []
    try:
        if interrupted:
            deleting = start("pause", directory)
            processes.append(deleting)
            evidence.append(ready(deleting))
            assert evidence[-1]["counts"]["local_effects"] == 1
            assert evidence[-1]["counts"]["approval_facts"] == 3
            actual_pid = evidence[-1]["pid"]
            os.kill(actual_pid, signal.SIGTERM)
            stdout, stderr = deleting.communicate(timeout=10)
            assert deleting.returncode != 0
            evidence.append({"event": "terminated-externally", "pid": actual_pid,
                             "launcher_pid": deleting.pid, "exit_code": deleting.returncode})
            evidence.extend(finish(start("retry", directory)))
        else:
            holder = start("hold", directory)
            processes.append(holder)
            evidence.append(ready(holder))
            evidence.extend(finish(start("try-delete", directory)))
            assert rows(directory) == manifest["before"]
            evidence.extend(finish(holder, "release\n"))
            late = start("late", directory)
            race = start("race", directory)
            processes.extend((late, race))
            evidence.extend((ready(late), ready(race)))
            assert race.stdin is not None
            race.stdin.write("race\n")
            race.stdin.flush()
            evidence.extend(finish(start("delete", directory)))
            evidence.extend(finish(race))
            evidence.extend(finish(late, "write\n"))
        evidence.extend(finish(start("verify", directory)))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
    return evidence


def claims_scenario(directory: Path):
    """验证两个独立进程只有一个删除身份获准，并可并发恢复同一计划。"""
    directory.mkdir(parents=True, exist_ok=False)
    plan, control = seeded(directory)
    (directory / "fixture.json").write_text(json.dumps({
        "request_id": plan.request_id,
        "targets": [{"cid": target.cid, "sid": target.sid} for target in plan.targets],
        "control": {"cid": control.cid, "sid": control.sid}, "before": rows(directory),
    }), encoding="utf-8")
    (directory / "config.toml").write_text("shared_configuration = true\n", encoding="utf-8")
    evidence = []
    processes = []
    try:
        for _ in range(2):
            process = start("claim", directory)
            processes.append(process)
            evidence.append(ready(process))
        for index, process in enumerate(processes):
            assert process.stdin is not None
            process.stdin.write(f"delete_claim_{index}\n")
            process.stdin.flush()
        for process in processes:
            evidence.extend(finish(process))
        assert [item["event"] for item in evidence].count("claim-accepted") == 1
        assert [item["event"] for item in evidence].count("claim-rejected") == 1
        assert len(store(directory, plan.root).pending()) == 1
        recoveries = [start("concurrent-retry", directory) for _ in range(2)]
        processes.extend(recoveries)
        for process in recoveries:
            evidence.append(ready(process))
        for process in recoveries:
            assert process.stdin is not None
            process.stdin.write("recover\n")
            process.stdin.flush()
        for process in recoveries:
            evidence.extend(finish(process))
        assert any(item["event"] == "recovery-completed" for item in evidence)
        evidence.extend(finish(start("verify", directory)))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
    return evidence


def main() -> None:
    """保存本轮独立进程、退出码与真实存储断言的验收报告。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--child")
    args = parser.parse_args()
    if args.child:
        child(args.child, args.directory.resolve())
        return
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    report = {"platform": sys.platform, "python": sys.version, "source": True,
              "concurrency": scenario(directory / "concurrency", interrupted=False),
              "crash_recovery": scenario(directory / "crash-recovery", interrupted=True),
              "claims_recovery": claims_scenario(directory / "claims-recovery")}
    (directory / "acceptance.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"passed": True, "report": str(directory / "acceptance.json")}))


if __name__ == "__main__":
    main()
