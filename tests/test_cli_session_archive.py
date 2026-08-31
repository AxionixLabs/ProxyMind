# -*- coding: utf-8 -*-

from frontends.cli.commands import SessionArchiveCommand
from frontends.cli import session_archive
from mind_app.history import ConversationHistoryStore


def test_cli_archive_and_unarchive_resolve_global_title(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    store = ConversationHistoryStore(tmp_path / "history.db", ttl_ms=10_000)
    session = store.touch_session(
        cid="cid_archive_12345678",
        sid="sid_archive_1_abcdef",
        title="Review archive flow",
        workspace=str(tmp_path / "other-workspace"),
    )
    monkeypatch.setattr(session_archive, "ConversationHistoryStore", lambda: store)

    assert session_archive.run_session_archive_command(
        SessionArchiveCommand(action="archive", target="Review archive flow")
    ) == 0
    assert store.find_session(session["sid"], status="archived", now_ms=200)
    assert "Archived session sid_archive_1_abcdef." in capsys.readouterr().out

    assert session_archive.run_session_archive_command(
        SessionArchiveCommand(action="unarchive", target=session["sid"])
    ) == 0
    assert store.find_session(session["sid"], status="active", now_ms=300)
    assert "Unarchived session sid_archive_1_abcdef." in capsys.readouterr().out
