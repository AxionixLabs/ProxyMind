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
from mind_app.runtime.workspace_restore import WorkspaceCheckpointRestorer
from mind_app.runtime import workspace_artifacts
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
        scope="workspace",
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
async def test_effect_journal_inspection_does_not_claim_execution(tmp_path: Path) -> None:
    journal = LocalEffectJournal(tmp_path / "effects.db")
    effect = _effect()

    assert (await journal.inspect(effect)).action == "execute"
    assert (await journal.inspect(effect)).action == "execute"
    assert (await journal.begin(effect)).action == "execute"
    assert (await journal.inspect(effect)).action == "reconcile"


@pytest.mark.anyio
async def test_effect_journal_persists_unexecuted_result_without_dispatch(
    tmp_path: Path,
) -> None:
    journal = LocalEffectJournal(tmp_path / "effects.db")
    effect = _effect()
    payload = {
        "ok": False,
        "reconciliation_result_payload": {
            "execution": {"effect": {"effect_id": effect.effect_id}},
        },
    }

    await journal.commit_unexecuted(effect, payload)
    reused = await journal.inspect(effect)

    assert reused.action == "reuse"
    assert reused.result_payload == payload
    with sqlite3.connect(tmp_path / "effects.db") as connection:
        row = connection.execute(
            "SELECT status, dispatch_count FROM local_effects WHERE effect_id = ?",
            (effect.effect_id,),
        ).fetchone()
    assert row == ("committed", 0)


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

    await WorkspaceArtifactGate(artifact_dir).ensure(
        checkpoint,
        expected_workspace_root=str(workspace.resolve()),
    )

    artifact = post.await_args.kwargs["artifact"]
    assert artifact["kind"] == "directory_snapshot"
    assert artifact["size_bytes"] > 0
    archive_path = Path(artifact["local_ref"])
    with tarfile.open(archive_path, mode="r:gz") as archive:
        assert "workspace/note.txt" in archive.getnames()
    post.assert_awaited_once()


@pytest.mark.anyio
async def test_default_artifact_dir_inside_workspace_is_rejected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.txt").write_text("before", encoding="utf-8")
    post = AsyncMock(return_value={})
    monkeypatch.setattr(
        "mind_app.runtime.workspace_artifacts.workspace_artifacts_dir",
        lambda: workspace / ".app" / "workspace-artifacts",
    )
    monkeypatch.setattr(
        "mind_app.runtime.workspace_artifacts.post_checkpoint_artifact",
        post,
    )
    checkpoint = WorkspaceCheckpoint(
        checkpoint_id="checkpoint_fallback",
        required=True,
        workspace={"root": str(workspace)},
        artifact={},
    )

    with pytest.raises(ValueError, match="must be outside the workspace"):
        await WorkspaceArtifactGate().ensure(
            checkpoint,
            expected_workspace_root=str(workspace.resolve()),
        )

    post.assert_not_awaited()
    assert not (workspace / ".app").exists()


@pytest.mark.anyio
async def test_explicit_artifact_dir_inside_workspace_is_rejected(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    artifact_dir = workspace / "artifacts"
    checkpoint = WorkspaceCheckpoint(
        checkpoint_id="checkpoint_explicit_inside",
        required=True,
        workspace={"root": str(workspace)},
        artifact={},
    )

    with pytest.raises(ValueError, match="must be outside the workspace"):
        await WorkspaceArtifactGate(artifact_dir).ensure(
            checkpoint,
            expected_workspace_root=str(workspace.resolve()),
        )

    assert not artifact_dir.exists()


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

    await WorkspaceArtifactGate(tmp_path / "artifacts").ensure(
        checkpoint,
        expected_workspace_root=str(workspace.resolve()),
    )

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
async def test_git_subdirectory_uses_bounded_directory_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = tmp_path / "repository"
    workspace = repository / "nested"
    workspace.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Test"],
        check=True,
    )
    (repository / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "tracked.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-qm", "initial"],
        check=True,
    )
    (workspace / "nested.txt").write_text("nested\n", encoding="utf-8")
    post = AsyncMock(return_value={})
    monkeypatch.setattr(
        "mind_app.runtime.workspace_artifacts.post_checkpoint_artifact",
        post,
    )

    await WorkspaceArtifactGate(tmp_path / "artifacts").ensure(
        WorkspaceCheckpoint(
            checkpoint_id="checkpoint_git_subdirectory",
            required=True,
            workspace={"root": str(workspace)},
            artifact={},
        ),
        expected_workspace_root=str(workspace),
    )

    assert post.await_args.kwargs["artifact"]["kind"] == "directory_snapshot"


@pytest.mark.anyio
async def test_confirmed_checkpoint_blocks_execution_when_local_artifact_is_missing(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    missing = tmp_path / "artifacts" / "checkpoint_missing.tar.gz"
    checkpoint = WorkspaceCheckpoint(
        checkpoint_id="checkpoint_missing",
        required=True,
        workspace={"root": str(workspace.resolve())},
        artifact={
            "kind": "directory_snapshot",
            "local_ref": str(missing.resolve()),
            "sha256": "a" * 64,
            "size_bytes": 10,
            "workspace_root": str(workspace.resolve()),
        },
    )

    with pytest.raises(FileNotFoundError, match="artifact is unavailable"):
        await WorkspaceArtifactGate(tmp_path / "artifacts").ensure(
            checkpoint,
            expected_workspace_root=str(workspace.resolve()),
        )


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

    await gate.ensure(
        WorkspaceCheckpoint(
            checkpoint_id="checkpoint_shared",
            required=True,
            workspace={"root": str(first_workspace)},
            artifact={},
        ),
        expected_workspace_root=str(first_workspace.resolve()),
    )

    with pytest.raises(ValueError, match="root does not match checkpoint"):
        await gate.ensure(
            WorkspaceCheckpoint(
                checkpoint_id="checkpoint_shared",
                required=True,
                workspace={"root": str(second_workspace)},
                artifact={},
            ),
            expected_workspace_root=str(second_workspace.resolve()),
        )


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
    await gate.ensure(
        WorkspaceCheckpoint(
            checkpoint_id="checkpoint_descriptor",
            required=True,
            workspace={"root": str(first_workspace)},
            artifact={},
        ),
        expected_workspace_root=str(first_workspace.resolve()),
    )
    artifact = dict(post.await_args.kwargs["artifact"])
    artifact["workspace_root"] = str(second_workspace.resolve())

    with pytest.raises(ValueError, match="descriptor does not match"):
        await gate.ensure(
            WorkspaceCheckpoint(
                checkpoint_id="checkpoint_descriptor",
                required=True,
                workspace={"root": str(second_workspace.resolve())},
                artifact=artifact,
            ),
            expected_workspace_root=str(second_workspace.resolve()),
        )


@pytest.mark.anyio
async def test_checkpoint_rejects_home_workspace_before_scanning() -> None:
    home = Path.home().resolve()
    checkpoint = WorkspaceCheckpoint(
        checkpoint_id="checkpoint_home",
        required=True,
        workspace={"root": str(home)},
        artifact={},
    )

    with pytest.raises(ValueError, match="too broad"):
        await WorkspaceArtifactGate(home.parent / "artifacts").ensure(
            checkpoint,
            expected_workspace_root=str(home),
        )


@pytest.mark.anyio
async def test_checkpoint_binds_server_root_to_current_turn(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    other = tmp_path / "other"
    workspace.mkdir()
    other.mkdir()
    checkpoint = WorkspaceCheckpoint(
        checkpoint_id="checkpoint_mismatch",
        required=True,
        workspace={"root": str(workspace)},
        artifact={},
    )

    with pytest.raises(ValueError, match="current turn"):
        await WorkspaceArtifactGate(tmp_path / "artifacts").ensure(
            checkpoint,
            expected_workspace_root=str(other),
        )


@pytest.mark.anyio
async def test_directory_snapshot_enforces_file_and_byte_limits(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "one.txt").write_text("12", encoding="utf-8")
    (workspace / "two.txt").write_text("34", encoding="utf-8")
    checkpoint = WorkspaceCheckpoint(
        checkpoint_id="checkpoint_limits",
        required=True,
        workspace={"root": str(workspace)},
        artifact={},
    )
    gate = WorkspaceArtifactGate(tmp_path / "artifacts")

    monkeypatch.setattr(workspace_artifacts, "MAX_WORKSPACE_FILES", 1)
    with pytest.raises(ValueError, match="too many"):
        await gate.ensure(checkpoint, expected_workspace_root=str(workspace))

    monkeypatch.setattr(workspace_artifacts, "MAX_WORKSPACE_FILES", 10)
    monkeypatch.setattr(workspace_artifacts, "MAX_WORKSPACE_SOURCE_BYTES", 1)
    with pytest.raises(ValueError, match="source size"):
        await gate.ensure(checkpoint, expected_workspace_root=str(workspace))


@pytest.mark.anyio
async def test_directory_snapshot_never_skips_unreadable_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    blocked = workspace / "blocked.txt"
    blocked.write_text("secret", encoding="utf-8")
    original_open = Path.open

    def guarded_open(path: Path, *args, **kwargs):
        """模拟受保护文件拒绝读取。"""
        if path == blocked:
            raise PermissionError("protected")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    checkpoint = WorkspaceCheckpoint(
        checkpoint_id="checkpoint_unreadable",
        required=True,
        workspace={"root": str(workspace)},
        artifact={},
    )

    with pytest.raises(PermissionError, match="protected"):
        await WorkspaceArtifactGate(tmp_path / "artifacts").ensure(
            checkpoint,
            expected_workspace_root=str(workspace),
        )


@pytest.mark.anyio
async def test_directory_restore_replaces_workspace_from_verified_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.txt").write_text("before", encoding="utf-8")
    post = AsyncMock(return_value={})
    monkeypatch.setattr(
        "mind_app.runtime.workspace_artifacts.post_checkpoint_artifact",
        post,
    )
    checkpoint = WorkspaceCheckpoint(
        checkpoint_id="checkpoint_restore_directory",
        required=True,
        workspace={"root": str(workspace)},
        artifact={},
    )
    await WorkspaceArtifactGate(tmp_path / "artifacts").ensure(
        checkpoint,
        expected_workspace_root=str(workspace),
    )
    artifact = dict(post.await_args.kwargs["artifact"])
    (workspace / "note.txt").write_text("after", encoding="utf-8")
    (workspace / "new.txt").write_text("remove", encoding="utf-8")

    WorkspaceCheckpointRestorer(tmp_path / "artifacts")._restore_workspace(
        {"workspace": {"root": str(workspace)}, "artifact": artifact},
        artifact,
        str(workspace),
    )

    assert (workspace / "note.txt").read_text(encoding="utf-8") == "before"
    assert not (workspace / "new.txt").exists()


@pytest.mark.anyio
async def test_git_restore_recovers_verified_worktree_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    subprocess.run(["git", "-C", str(workspace), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(workspace), "config", "user.name", "Test"], check=True)
    (workspace / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(workspace), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(workspace), "commit", "-qm", "initial"], check=True)
    (workspace / "tracked.txt").write_text("checkpoint\n", encoding="utf-8")
    (workspace / "untracked.txt").write_text("checkpoint\n", encoding="utf-8")
    post = AsyncMock(return_value={})
    monkeypatch.setattr("mind_app.runtime.workspace_artifacts.post_checkpoint_artifact", post)
    checkpoint = WorkspaceCheckpoint(
        checkpoint_id="checkpoint_restore_git",
        required=True,
        workspace={"root": str(workspace)},
        artifact={},
    )
    await WorkspaceArtifactGate(tmp_path / "artifacts").ensure(
        checkpoint,
        expected_workspace_root=str(workspace),
    )
    artifact = dict(post.await_args.kwargs["artifact"])
    (workspace / "tracked.txt").write_text("later\n", encoding="utf-8")
    (workspace / "untracked.txt").unlink()
    (workspace / "later.txt").write_text("remove\n", encoding="utf-8")

    WorkspaceCheckpointRestorer(tmp_path / "artifacts")._restore_workspace(
        {"workspace": {"root": str(workspace)}, "artifact": artifact},
        artifact,
        str(workspace),
    )

    assert (workspace / "tracked.txt").read_text(encoding="utf-8") == "checkpoint\n"
    assert (workspace / "untracked.txt").read_text(encoding="utf-8") == "checkpoint\n"
    assert not (workspace / "later.txt").exists()


def test_restore_cleanup_never_deletes_outside_artifact_storage(tmp_path: Path) -> None:
    storage = tmp_path / "artifacts"
    storage.mkdir()
    outside = tmp_path / "keep.tar.gz"
    outside.write_bytes(b"keep")
    artifact = {
        "kind": "directory_snapshot",
        "local_ref": str(outside),
        "sha256": "a" * 64,
        "size_bytes": 4,
        "workspace_root": str(tmp_path / "workspace"),
    }

    WorkspaceCheckpointRestorer(storage)._remove_restored_artifact(artifact)

    assert outside.read_bytes() == b"keep"


@pytest.mark.anyio
async def test_restored_prepare_replays_commit_before_artifact_cleanup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    storage = tmp_path / "artifacts"
    storage.mkdir()
    local_ref = storage / "checkpoint_restored.tar.gz"
    local_ref.write_bytes(b"artifact")
    artifact = {
        "kind": "directory_snapshot",
        "local_ref": str(local_ref),
        "sha256": "a" * 64,
        "size_bytes": len(b"artifact"),
        "workspace_root": str(tmp_path / "workspace"),
    }
    prepare = AsyncMock(return_value={
        "checkpoint_id": "checkpoint_restored",
        "workspace": {"root": str(tmp_path / "workspace")},
        "artifact": artifact,
        "_restore_status": "restored",
    })
    commit = AsyncMock(return_value={
        "checkpoint": {"checkpoint_id": "checkpoint_restored"},
        "superseded_artifacts": [],
    })
    monkeypatch.setattr(
        "mind_app.runtime.workspace_restore.prepare_checkpoint_restore",
        prepare,
    )
    monkeypatch.setattr(
        "mind_app.runtime.workspace_restore.commit_checkpoint_restore",
        commit,
    )

    await WorkspaceCheckpointRestorer(storage).restore(
        checkpoint_id="checkpoint_restored",
        request_id="restore-request",
        expected_workspace_root=str(tmp_path / "workspace"),
    )

    commit.assert_awaited_once_with(
        checkpoint_id="checkpoint_restored",
        request_id="restore-request",
        artifact_sha256="a" * 64,
    )
    assert not local_ref.exists()
