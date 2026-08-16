import tarfile
import subprocess
import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from mind_app.runtime.durable_effects import (
    EffectJournalPersistenceError,
    LocalEffectJournal,
)
from mind_app.runtime.workspace_artifacts import WorkspaceArtifactGate
from mind_nova.stream_events import ExecutionEffect, WorkspaceCheckpoint


def _effect(
    *,
    fingerprint: str = "a" * 64,
    dispatch_required: bool = True,
) -> ExecutionEffect:
    return ExecutionEffect(
        effect_id="effect_test",
        fingerprint=fingerprint,
        effect_class="non_replayable",
        replay_policy="manual",
        provider_idempotency_key="",
        status="dispatching",
        dispatch_required=dispatch_required,
        dispatch_count=1,
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
async def test_effect_journal_exposes_only_identity_bound_reconciliation_result(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "effects.db"
    journal = LocalEffectJournal(db_path)
    effect = _effect()
    server_result = {
        "request_id": "effect-result-test",
        "execution": {
            "effect": {
                "effect_id": effect.effect_id,
                "fingerprint": effect.fingerprint,
            },
        },
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


@pytest.mark.anyio
async def test_non_git_checkpoint_uploads_reference_before_return(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.txt").write_text("before", encoding="utf-8")
    artifact_dir = tmp_path / "artifacts"
    post = AsyncMock(return_value={})
    monkeypatch.setattr(
        "mind_app.runtime.workspace_artifacts.post_checkpoint_artifact",
        post,
    )
    checkpoint = WorkspaceCheckpoint(
        checkpoint_id="checkpoint_test",
        required=True,
        workspace={"root": str(workspace)},
        artifact={},
    )

    await WorkspaceArtifactGate(artifact_dir).ensure(checkpoint)

    artifact = post.await_args.kwargs["artifact"]
    assert artifact["kind"] == "directory_snapshot"
    assert artifact["size_bytes"] > 0
    archive_path = Path(artifact["local_ref"])
    with tarfile.open(archive_path, mode="r:gz") as archive:
        assert "workspace/note.txt" in archive.getnames()
    post.assert_awaited_once()


@pytest.mark.anyio
async def test_git_checkpoint_captures_binary_diff_and_untracked_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    subprocess.run(
        ["git", "-C", str(workspace), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(workspace), "config", "user.name", "Test"],
        check=True,
    )
    (workspace / "tracked.txt").write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(workspace), "add", "tracked.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(workspace), "commit", "-qm", "initial"],
        check=True,
    )
    (workspace / "tracked.txt").write_text("after\n", encoding="utf-8")
    (workspace / "untracked.txt").write_text("recover me\n", encoding="utf-8")
    post = AsyncMock(return_value={})
    monkeypatch.setattr(
        "mind_app.runtime.workspace_artifacts.post_checkpoint_artifact",
        post,
    )
    checkpoint = WorkspaceCheckpoint(
        checkpoint_id="checkpoint_git",
        required=True,
        workspace={"root": str(workspace)},
        artifact={},
    )

    await WorkspaceArtifactGate(tmp_path / "artifacts").ensure(checkpoint)

    artifact = post.await_args.kwargs["artifact"]
    assert artifact["kind"] == "git_worktree_snapshot"
    assert artifact["git_head"]
    with tarfile.open(artifact["local_ref"], mode="r:gz") as archive:
        names = archive.getnames()
        patch = archive.extractfile("working-tree.patch")
        assert patch is not None
        assert b"+after" in patch.read()
    assert "untracked/untracked.txt" in names
    assert all("/.git/" not in name for name in names)


@pytest.mark.anyio
async def test_confirmed_checkpoint_blocks_execution_when_local_artifact_is_missing(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    missing = tmp_path / "missing.tar.gz"
    checkpoint = WorkspaceCheckpoint(
        checkpoint_id="checkpoint_missing",
        required=True,
        workspace={"root": str(workspace.resolve())},
        artifact={
            "schema_version": 1,
            "kind": "directory_snapshot",
            "local_ref": str(missing.resolve()),
            "sha256": "a" * 64,
            "size_bytes": 10,
            "workspace_root": str(workspace.resolve()),
        },
    )

    with pytest.raises(FileNotFoundError, match="artifact is unavailable"):
        await WorkspaceArtifactGate(tmp_path / "artifacts").ensure(checkpoint)


@pytest.mark.anyio
async def test_existing_checkpoint_artifact_cannot_be_rebound_to_another_workspace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"
    first_workspace.mkdir()
    second_workspace.mkdir()
    post = AsyncMock(return_value={})
    monkeypatch.setattr(
        "mind_app.runtime.workspace_artifacts.post_checkpoint_artifact",
        post,
    )
    gate = WorkspaceArtifactGate(tmp_path / "artifacts")

    await gate.ensure(WorkspaceCheckpoint(
        checkpoint_id="checkpoint_shared",
        required=True,
        workspace={"root": str(first_workspace)},
        artifact={},
    ))

    with pytest.raises(ValueError, match="root does not match checkpoint"):
        await gate.ensure(WorkspaceCheckpoint(
            checkpoint_id="checkpoint_shared",
            required=True,
            workspace={"root": str(second_workspace)},
            artifact={},
        ))


@pytest.mark.anyio
async def test_confirmed_artifact_validates_embedded_workspace_descriptor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"
    first_workspace.mkdir()
    second_workspace.mkdir()
    post = AsyncMock(return_value={})
    monkeypatch.setattr(
        "mind_app.runtime.workspace_artifacts.post_checkpoint_artifact",
        post,
    )
    gate = WorkspaceArtifactGate(tmp_path / "artifacts")
    await gate.ensure(WorkspaceCheckpoint(
        checkpoint_id="checkpoint_descriptor",
        required=True,
        workspace={"root": str(first_workspace)},
        artifact={},
    ))
    artifact = dict(post.await_args.kwargs["artifact"])
    artifact["workspace_root"] = str(second_workspace.resolve())

    with pytest.raises(ValueError, match="descriptor does not match"):
        await gate.ensure(WorkspaceCheckpoint(
            checkpoint_id="checkpoint_descriptor",
            required=True,
            workspace={"root": str(second_workspace.resolve())},
            artifact=artifact,
        ))
