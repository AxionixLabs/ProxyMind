import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent.stores.sessions import (
    ConversationHistoryStore,
    normalize_workspace,
)
from infrastructure.config.session import ConfigSession
from infrastructure.config.store import ConfigStore
from infrastructure.persistence.transcripts import ConversationTranscriptStore
from protocol.schema.identifiers import (
    new_cid,
    new_sid,
)
from tests.pty import (
    PtyKey,
    TerminalHarness,
    TerminalSize,
    spawn_terminal,
)


pytestmark = pytest.mark.pty_acceptance
DIRECTORY_TITLE = "Choose working directory to resume this session"


@dataclass
class ResumeFixture:
    root: Path
    launch: Path
    history: Path
    entry: Path
    config: ConfigSession
    store: ConversationHistoryStore
    history_sid: str
    launch_sid: str

    def start(self, *arguments: str) -> TerminalHarness:
        environment = dict(os.environ)
        environment["MIND_HOME"] = str(self.root / "config")
        environment["MIND_STATE_HOME"] = str(self.root / "state")
        return spawn_terminal(
            [sys.executable, str(self.entry), *arguments],
            cwd=self.launch, env=environment,
            size=TerminalSize(rows=32, columns=180),
            failure_artifact_directory=self.root / "artifacts",
        )


@pytest.fixture
def resume_fixture(tmp_path, repository_root):
    launch = tmp_path / "resume-launch"
    history = tmp_path / "resume-history"
    config = ConfigSession(ConfigStore(tmp_path / "config" / "config.toml"), workspace=launch)
    config.update_user({
        ("model_provider",): "marker",
        ("model_providers", "marker", "kind"): "openai",
        ("model_providers", "marker", "model"): "resume-check-model",
        ("model_providers", "marker", "base_url"): "http://127.0.0.1:9",
        ("model_providers", "marker", "api_key"): "local-test-unused",
    })
    store = ConversationHistoryStore(tmp_path / "state" / "history" / "history.db")
    transcripts = ConversationTranscriptStore(tmp_path / "state" / "sessions")
    mcp_script = tmp_path / "cwd_server.py"
    mcp_script.write_text(
        "from pathlib import Path\n"
        "from mcp.server.fastmcp import FastMCP\n"
        "Path('mcp-cwd.txt').write_text(str(Path.cwd()), encoding='utf-8')\n"
        "server = FastMCP('cwd-check')\n"
        "@server.tool()\n"
        "def cwd() -> str:\n"
        "    return str(Path.cwd())\n"
        "server.run(transport='stdio')\n",
        encoding="utf-8",
    )
    sessions = []
    for directory in (launch, history):
        directory.mkdir()
        project = ConfigStore(directory / ".mind" / "config.toml")
        project.update({
            ("sandbox_mode",): "danger-full-access" if directory == history else "workspace-write",
            ("approval_policy",): "never" if directory == history else "on-request",
            ("mcp_servers", "cwdcheck"): {
                "command": sys.executable, "args": [str(mcp_script)], "startup_timeout_sec": 10,
            },
        })
        skill = directory / ".agents" / "skills" / f"skill-{directory.name}" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            f"---\nname: skill-{directory.name}\ndescription: SKILL_{directory.name}\n---\n",
            encoding="utf-8",
        )
        decision = config.resolve(workspace=directory).project_trust
        config.set_project_trust(decision, "trusted", workspace=directory)
        cid = new_cid()
        sid = new_sid(cid)
        sessions.append(sid)
        store.touch_session(cid=cid, sid=sid, workspace=str(directory), title=directory.name, source="tui")
        writer = transcripts.writer(transcripts.path_for_session(sid), session_id=sid)
        writer.open()
        writer.append("message.created", actor="assistant", payload={"content": f"HISTORY_{directory.name}"})
        writer.close()
    return ResumeFixture(tmp_path, launch, history, repository_root / "mind.py", config, store, sessions[1], sessions[0])


def _verify_execution_directory(terminal: TerminalHarness, expected: Path, marker: str) -> None:
    terminal.wait_for_screen_text("resume-check-model medium", timeout=15)
    terminal.wait_for_screen_text(f"{os.sep}{expected.name}", timeout=15)
    terminal.wait_for_screen_text("Full Access" if expected.name == "resume-history" else "Ask for approval")
    terminal.write_user_text("$skill-")
    terminal.wait_for_screen_text(f"SKILL_{expected.name}", timeout=10)
    terminal.send_key(PtyKey.ESCAPE)
    terminal.write_user(b"\x15")
    terminal.write_user_text(
        f'!python -c "from pathlib import Path; Path(\'{marker}\').write_text(str(Path.cwd()), encoding=\'utf-8\')"'
    )
    terminal.send_key(PtyKey.ENTER)
    deadline = time.monotonic() + 15
    target = expected / marker
    while not target.is_file():
        if time.monotonic() >= deadline:
            raise AssertionError(f"Shell did not write in {expected}: {terminal.diagnostics().screen.visible_text}")
        time.sleep(0.05)
    assert Path(target.read_text(encoding="utf-8")) == expected
    assert Path((expected / "mcp-cwd.txt").read_text(encoding="utf-8")) == expected


def _quit(terminal: TerminalHarness) -> None:
    terminal.send_key(PtyKey.ESCAPE)
    terminal.write_user_text("/quit")
    terminal.send_key(PtyKey.ENTER)
    assert terminal.wait_for_exit(timeout=15) == 0


@pytest.mark.parametrize(("selection", "use_history", "remembered"), [
    ("1", True, None), ("2", False, None),
    ("3", True, "session"), ("4", False, "current"),
])
def test_real_cli_four_directory_choices(resume_fixture, selection, use_history, remembered):
    fixture = resume_fixture
    expected = fixture.history if use_history else fixture.launch
    with fixture.start("resume", fixture.history_sid) as terminal:
        terminal.wait_for_screen_text(DIRECTORY_TITLE, timeout=20)
        terminal.wait_for_screen_text("Always use current directory")
        terminal.write_user_text(selection)
        terminal.wait_for_screen_text("HISTORY_resume-history", timeout=15)
        _verify_execution_directory(terminal, expected, "actual-cwd.txt")
        _quit(terminal)
    assert fixture.store.find_session(fixture.history_sid)["workspace"] == normalize_workspace(expected)
    assert fixture.config.load()["tui"].get("resume_cwd") == remembered
    if remembered is not None:
        record = fixture.store.find_session(fixture.history_sid)
        fixture.store.touch_session(
            cid=record["cid"], sid=record["sid"], title=record["title"],
            workspace=str(fixture.history), source="tui",
        )
        with fixture.start("resume", fixture.history_sid) as terminal:
            terminal.wait_for_screen_text("HISTORY_resume-history", timeout=20)
            assert DIRECTORY_TITLE not in terminal.session.output_text()
            _verify_execution_directory(terminal, expected, "restarted-cwd.txt")
            _quit(terminal)


def test_real_cli_remembered_strategy_restart_and_explicit_directory(resume_fixture):
    fixture = resume_fixture
    fixture.config.update_user({("tui", "resume_cwd"): "session"})
    with fixture.start("resume", fixture.history_sid) as terminal:
        terminal.wait_for_screen_text("HISTORY_resume-history", timeout=20)
        assert DIRECTORY_TITLE not in terminal.session.output_text()
        _verify_execution_directory(terminal, fixture.history, "remembered-cwd.txt")
        _quit(terminal)
    with fixture.start("resume", fixture.history_sid, "--cd", str(fixture.launch)) as terminal:
        terminal.wait_for_screen_text("HISTORY_resume-history", timeout=20)
        assert DIRECTORY_TITLE not in terminal.session.output_text()
        _verify_execution_directory(terminal, fixture.launch, "explicit-cwd.txt")
        _quit(terminal)


def test_real_cli_all_picker_and_in_app_resume(resume_fixture):
    fixture = resume_fixture
    with fixture.start("resume", "--all") as terminal:
        terminal.wait_for_screen_text("resume-history", timeout=20)
        terminal.send_key(PtyKey.ENTER)
        terminal.wait_for_screen_text(DIRECTORY_TITLE)
        terminal.write_user_text("1")
        terminal.wait_for_screen_text("HISTORY_resume-history", timeout=15)
        _verify_execution_directory(terminal, fixture.history, "first-cwd.txt")
        terminal.send_key(PtyKey.ESCAPE)
        terminal.write_user_text("/resume")
        terminal.send_key(PtyKey.ENTER)
        terminal.wait_for_screen_text("Resume")
        terminal.write_user(b"\x1b[C")
        terminal.wait_for_screen_text("[All]")
        terminal.write_user_text("resume-launch")
        terminal.send_key(PtyKey.ENTER)
        terminal.wait_for_screen_text(DIRECTORY_TITLE)
        terminal.write_user_text("4")
        terminal.wait_for_screen_text("HISTORY_resume-launch", timeout=15)
        _verify_execution_directory(terminal, fixture.launch, "second-cwd.txt")
        _quit(terminal)
    assert fixture.config.load()["tui"]["resume_cwd"] == "current"


@pytest.mark.parametrize("key", [PtyKey.ESCAPE, PtyKey.CTRL_C, PtyKey.CTRL_D])
def test_real_directory_menu_escape_and_exit(resume_fixture, key):
    fixture = resume_fixture
    with fixture.start("resume", fixture.history_sid) as terminal:
        terminal.wait_for_screen_text(DIRECTORY_TITLE, timeout=20)
        terminal.write_user(key.value)
        if key is PtyKey.ESCAPE:
            terminal.wait_for_screen_text("HISTORY_resume-history", timeout=15)
            _verify_execution_directory(terminal, fixture.history, "escape-cwd.txt")
            _quit(terminal)
        else:
            assert terminal.wait_for_exit(timeout=15) == 0
    assert fixture.config.load()["tui"].get("resume_cwd") is None
