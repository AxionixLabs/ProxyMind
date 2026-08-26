import json
import sqlite3
from pathlib import Path

import pytest

from mind_app.runtime.durable_effects import (
    EffectJournalPersistenceError,
    LocalEffectJournal,
)
from mind_nova.stream_events import ExecutionEffect


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
async def test_effect_journal_reuses_committed_result(tmp_path: Path) -> None:
    journal = LocalEffectJournal(tmp_path / "effects.db")
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
    journal = LocalEffectJournal(tmp_path / "effects.db")
    effect = _effect()

    assert (await journal.begin(effect)).action == "execute"
    assert (await journal.begin(effect)).action == "reconcile"


@pytest.mark.anyio
async def test_effect_journal_inspection_does_not_claim_execution(tmp_path: Path) -> None:
    journal = LocalEffectJournal(tmp_path / "effects.db")
    effect = _effect()

    assert (await journal.inspect(effect)).action == "execute"
    assert (await journal.inspect(effect)).action == "execute"
    assert (await journal.begin(effect)).action == "execute"
    assert (await journal.inspect(effect)).action == "reconcile"


@pytest.mark.anyio
async def test_effect_journal_rejects_fingerprint_conflict(tmp_path: Path) -> None:
    journal = LocalEffectJournal(tmp_path / "effects.db")
    await journal.begin(_effect())

    with pytest.raises(ValueError, match="fingerprint conflicts"):
        await journal.begin(_effect(fingerprint="b" * 64))


@pytest.mark.anyio
async def test_effect_journal_preserves_candidate_result_for_reconciliation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "effects.db"
    journal = LocalEffectJournal(db_path)
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
    journal = LocalEffectJournal(db_path)
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
    journal = LocalEffectJournal(tmp_path / "effects.db")

    def fail(_effect_id: str) -> None:
        raise sqlite3.OperationalError("database is unavailable")

    monkeypatch.setattr(journal, "_mark_reconciled", fail)

    with pytest.raises(EffectJournalPersistenceError):
        await journal.mark_reconciled("effect_test")
