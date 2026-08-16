# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import httpx
import typing
from engine.channel import Channel
from mind_nova.services import service_endpoints

_ARTIFACT_REQUIRED_KEYS = frozenset({
    "kind",
    "local_ref",
    "sha256",
    "size_bytes",
    "workspace_root",
})
_ARTIFACT_OPTIONAL_KEYS = frozenset({"git_head", "manifest_hash"})


def validate_artifact_reference(
    artifact: dict[str, typing.Any],
) -> dict[str, typing.Any]:
    """严格校验 workspace artifact 引用并返回规范副本。"""
    if not isinstance(artifact, dict):
        raise TypeError("workspace artifact reference must be an object")
    normalized = dict(artifact)
    for key in _ARTIFACT_OPTIONAL_KEYS:
        if normalized.get(key) is None:
            normalized.pop(key, None)

    keys = set(normalized)
    if not _ARTIFACT_REQUIRED_KEYS.issubset(keys) or not keys.issubset(
        _ARTIFACT_REQUIRED_KEYS | _ARTIFACT_OPTIONAL_KEYS
    ):
        raise ValueError("workspace artifact reference fields are invalid")
    if normalized.get("kind") not in {
        "git_worktree_snapshot",
        "directory_snapshot",
    }:
        raise ValueError("workspace artifact kind is invalid")

    for key in ("local_ref", "workspace_root"):
        value = normalized.get(key)
        if not isinstance(value, str):
            raise ValueError("workspace artifact paths must be absolute")
        value = value.strip()
        if not 1 <= len(value) <= 4096 or not (
            value.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", value)
        ):
            raise ValueError("workspace artifact paths must be absolute")
        normalized[key] = value
    digest = normalized.get("sha256")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("workspace artifact sha256 is invalid")
    size = normalized.get("size_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 512 * 1024 * 1024:
        raise ValueError("workspace artifact size is invalid")

    git_head = normalized.get("git_head")
    if git_head is not None and (
        not isinstance(git_head, str)
        or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", git_head) is None
    ):
        raise ValueError("workspace artifact Git head is invalid")
    if normalized["kind"] == "git_worktree_snapshot" and git_head is None:
        raise ValueError("Git workspace artifact requires git_head")
    if normalized["kind"] == "directory_snapshot" and git_head is not None:
        raise ValueError("directory workspace artifact must not include git_head")
    manifest_hash = normalized.get("manifest_hash")
    if manifest_hash is not None and (
        not isinstance(manifest_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", manifest_hash) is None
    ):
        raise ValueError("workspace artifact manifest hash is invalid")
    return normalized


async def post_checkpoint_artifact(
    *,
    checkpoint_id: str,
    request_id: str,
    artifact: dict[str, typing.Any],
    timeout: float = 30.0
) -> dict[str, typing.Any]:
    """在本地副作用执行前关联 workspace 恢复 artifact。"""
    normalized_artifact = validate_artifact_reference(artifact)
    payload = {
        "checkpoint_id": str(checkpoint_id or "").strip(),
        "request_id": str(request_id or "").strip(),
        "artifact": normalized_artifact,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            service_endpoints.endpoint("/checkpoint/artifact"),
            headers=Channel.make_headers(),
            json=payload,
        )
        response.raise_for_status()
        body = response.json()

    if not isinstance(body, dict) or body.get("ok") is not True:
        raise RuntimeError("checkpoint artifact response is invalid")

    checkpoint = body.get("checkpoint")
    if not isinstance(checkpoint, dict):
        raise RuntimeError("checkpoint artifact response is missing checkpoint")

    attached = checkpoint.get("artifact")
    if not isinstance(attached, dict) or attached != normalized_artifact:
        raise RuntimeError("checkpoint artifact response does not match request")

    return checkpoint


async def prepare_checkpoint_restore(
    *,
    checkpoint_id: str,
    request_id: str,
    timeout: float = 30.0,
) -> dict[str, typing.Any]:
    """准备一次恢复并返回绑定的 checkpoint 与 artifact。"""
    payload = {
        "checkpoint_id": str(checkpoint_id or "").strip(),
        "request_id": str(request_id or "").strip(),
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            service_endpoints.endpoint("/checkpoint/restore/prepare"),
            headers=Channel.make_headers(),
            json=payload,
        )
        response.raise_for_status()
        body = response.json()

    data = body.get("data") if isinstance(body, dict) else None
    checkpoint = data.get("checkpoint") if isinstance(data, dict) else None
    if (
        not isinstance(data, dict)
        or data.get("status") not in {"prepared", "restored"}
        or data.get("request_id") != payload["request_id"]
        or not isinstance(checkpoint, dict)
        or checkpoint.get("checkpoint_id") != payload["checkpoint_id"]
    ):
        raise RuntimeError("checkpoint restore prepare response is invalid")
    artifact = checkpoint.get("artifact")
    if not isinstance(artifact, dict):
        raise RuntimeError("checkpoint restore artifact is missing")
    checkpoint = dict(checkpoint)
    checkpoint["artifact"] = validate_artifact_reference(artifact)
    checkpoint["_restore_status"] = data["status"]
    return checkpoint


async def commit_checkpoint_restore(
    *,
    checkpoint_id: str,
    request_id: str,
    artifact_sha256: str,
    timeout: float = 30.0,
) -> dict[str, typing.Any]:
    """在 workspace 恢复完成后提交服务端 Transcript 恢复。"""
    payload = {
        "checkpoint_id": str(checkpoint_id or "").strip(),
        "request_id": str(request_id or "").strip(),
        "artifact_sha256": str(artifact_sha256 or "").strip(),
    }
    if re.fullmatch(r"[0-9a-f]{64}", payload["artifact_sha256"]) is None:
        raise ValueError("checkpoint restore artifact sha256 is invalid")
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            service_endpoints.endpoint("/checkpoint/restore/commit"),
            headers=Channel.make_headers(),
            json=payload,
        )
        response.raise_for_status()
        body = response.json()

    data = body.get("data") if isinstance(body, dict) else None
    checkpoint = data.get("checkpoint") if isinstance(data, dict) else None
    if (
        not isinstance(data, dict)
        or data.get("status") != "restored"
        or data.get("request_id") != payload["request_id"]
        or not isinstance(checkpoint, dict)
        or checkpoint.get("checkpoint_id") != payload["checkpoint_id"]
    ):
        raise RuntimeError("checkpoint restore commit response is invalid")
    superseded = data.get("superseded_artifacts")
    if not isinstance(superseded, list) or any(
        not isinstance(item, dict) for item in superseded
    ):
        raise RuntimeError("checkpoint restore superseded artifacts are invalid")
    return {
        "checkpoint": dict(checkpoint),
        "superseded_artifacts": [
            validate_artifact_reference(item)
            for item in superseded
        ],
    }


if __name__ == "__main__":
    pass
