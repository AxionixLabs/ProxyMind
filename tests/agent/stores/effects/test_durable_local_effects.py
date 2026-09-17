import json
import sqlite3
from pathlib import Path

import pytest

from agent.ports import (
    EffectJournalPersistenceError,
)
from agent.composition import open_effect_journal
from protocol.schema.stream_events import ExecutionEffect


def _effect(
    *,
    fingerprint: str = "a" * 64,
    replay: str = "manual",
) -> ExecutionEffect:
    return ExecutionEffect(
        effect_id="effect_test",
        fingerprint=fingerprint,
        replay=replay,
    )


@pytest.mark.anyio
async def test_tool_result_journal_preserves_raw_text_and_first_payload(tmp_path):
    path = tmp_path / "effects.db"
    journal = open_effect_journal(path, cid="cid", sid="sid")
    raw = {"text": "中文\x00😀"}
    payload = {"result": {"text": "中文␀😀"}}
    await journal.save_tool_result("cid", "sid", "call", payload, raw)
    await journal.save_tool_result("cid", "sid", "call", payload, {"text": "projected replay"})
    with pytest.raises(ValueError, match="conflicts"):
        await journal.save_tool_result("cid", "sid", "call", {"different": True}, raw)
    resumed = open_effect_journal(path, cid="cid", sid="sid")
    assert await resumed.load_tool_result("cid", "sid", "call") == payload
    assert await resumed.load_tool_result("cid", "other", "call") is None
    with sqlite3.connect(path) as connection:
        stored = connection.execute("SELECT original_result FROM local_tool_results").fetchone()[0]
    assert json.loads(stored) == raw


@pytest.mark.anyio
async def test_effect_journal_reuses_committed_result(tmp_path: Path) -> None:
    journal = open_effect_journal(tmp_path / "effects.db", cid="cid", sid="sid")
    effect = _effect()

    assert (await journal.begin(effect)).action == "execute"
    await journal.commit(effect, {"ok": True, "text": "done"})
    reused = await journal.begin(effect)

    assert reused.action == "reuse"
    assert reused.result_payload == {"ok": True, "text": "done"}


@pytest.mark.anyio
async def test_effect_journal_never_replays_uncertain_manual_effect(
    tmp_path: Path,
) -> None:
    journal = open_effect_journal(tmp_path / "effects.db", cid="cid", sid="sid")
    effect = _effect()

    assert (await journal.begin(effect)).action == "execute"
    assert (await journal.begin(effect)).action == "reconcile"


@pytest.mark.anyio
async def test_effect_journal_inspection_does_not_claim_execution(tmp_path: Path) -> None:
    journal = open_effect_journal(tmp_path / "effects.db", cid="cid", sid="sid")
    effect = _effect()

    assert (await journal.inspect(effect)).action == "execute"
    assert (await journal.inspect(effect)).action == "execute"
    assert (await journal.begin(effect)).action == "execute"
    assert (await journal.inspect(effect)).action == "reconcile"


@pytest.mark.anyio
async def test_effect_journal_rejects_fingerprint_conflict(tmp_path: Path) -> None:
    journal = open_effect_journal(tmp_path / "effects.db", cid="cid", sid="sid")
    await journal.begin(_effect())

    with pytest.raises(ValueError, match="fingerprint conflicts"):
        await journal.begin(_effect(fingerprint="b" * 64))


@pytest.mark.anyio
async def test_effect_journal_preserves_candidate_result_for_reconciliation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "effects.db"
    journal = open_effect_journal(db_path, cid="cid", sid="sid")
    effect = _effect()
    candidate = {"ok": True, "text": "applied"}
    await journal.begin(effect)

    await journal.mark_unknown(
        effect,
        OSError("commit unavailable"),
        result_payload=candidate,
    )

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT status, result_payload FROM local_effects WHERE effect_id = ?",
            (effect.effect_id,),
        ).fetchone()
    assert row is not None
    assert row[0] == "reconciliation_required"
    assert json.loads(row[1]) == candidate


@pytest.mark.anyio
async def test_effect_journal_exposes_stored_reconciliation_result(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "effects.db"
    journal = open_effect_journal(db_path, cid="cid", sid="sid")
    effect = _effect()
    server_result = {
        "request_id": "effect-result-test",
    }
    await journal.begin(effect)
    await journal.mark_unknown(
        effect,
        OSError("delivery unavailable"),
        result_payload={
            "ok": True,
            "reconciliation_result_payload": server_result,
        },
    )

    assert await journal.reconciliation_result(effect.effect_id) == server_result
    await journal.mark_reconciled(effect.effect_id)

    with sqlite3.connect(db_path) as connection:
        status = connection.execute(
            "SELECT status FROM local_effects WHERE effect_id = ?",
            (effect.effect_id,),
        ).fetchone()
    assert status == ("committed",)


@pytest.mark.anyio
async def test_mark_reconciled_wraps_storage_failures(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = open_effect_journal(tmp_path / "effects.db", cid="cid", sid="sid")

    def fail(_effect_id: str) -> None:
        raise sqlite3.OperationalError("database is unavailable")

    monkeypatch.setattr(journal, "_mark_reconciled", fail)

    with pytest.raises(EffectJournalPersistenceError):
        await journal.mark_reconciled("effect_test")
