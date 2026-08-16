# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import io
import os
import json
import time
import typing
import asyncio
import hashlib
import tarfile
import tempfile
import subprocess
from pathlib import Path
from mind_app.paths import mind_home, workspace_artifacts_dir
from mind_nova.requests.checkpoints import (
    post_checkpoint_artifact,
    validate_artifact_reference,
)
from mind_nova.stream_events import WorkspaceCheckpoint
from mind_nova import const

MAX_WORKSPACE_FILES = 10_000
MAX_WORKSPACE_SOURCE_BYTES = 256 * 1024 * 1024
MAX_WORKSPACE_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_WORKSPACE_ARTIFACT_SECONDS = 30.0


class WorkspaceArtifactGate:
    """在本地副作用前生成并上传可恢复 workspace artifact 引用。"""

    def __init__(self, artifact_dir: str | Path | None = None) -> None:
        """绑定必须位于工作区外的本地 artifact 目录。"""
        self.artifact_dir = Path(
            workspace_artifacts_dir() if artifact_dir is None else artifact_dir
        ).expanduser()

    async def ensure(
        self,
        checkpoint: WorkspaceCheckpoint | None,
        *,
        expected_workspace_root: str
    ) -> None:
        """确认 artifact 已由服务端接受后才返回。"""
        if checkpoint is None or not checkpoint.required:
            raise ValueError("workspace checkpoint is required before local execution")
        if checkpoint.artifact:
            await asyncio.to_thread(
                self._validate_confirmed_artifact,
                checkpoint,
                expected_workspace_root,
            )
            return None

        artifact = await asyncio.to_thread(
            self._create,
            checkpoint,
            expected_workspace_root,
        )
        request_id = f"checkpoint-artifact:{checkpoint.checkpoint_id}"
        await post_checkpoint_artifact(
            checkpoint_id=checkpoint.checkpoint_id,
            request_id=request_id,
            artifact=artifact,
        )

    def _validate_confirmed_artifact(
        self,
        checkpoint: WorkspaceCheckpoint,
        expected_workspace_root: str
    ) -> None:
        """确认服务端已关联的本地恢复包仍完整可用。"""
        artifact       = validate_artifact_reference(checkpoint.artifact)
        local_ref      = artifact.get("local_ref")
        digest         = artifact.get("sha256")
        workspace_root = artifact.get("workspace_root")

        if not isinstance(local_ref, str) or not Path(local_ref).is_absolute():
            raise ValueError("confirmed workspace artifact local_ref is invalid")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("confirmed workspace artifact sha256 is invalid")

        root = self._validated_workspace_root(checkpoint, expected_workspace_root)
        if str(root) != str(workspace_root or ""):
            raise ValueError("confirmed workspace artifact root does not match checkpoint")

        artifact_root = self._artifact_root(root)

        path = Path(local_ref).expanduser().resolve()

        if (
            path.parent != artifact_root
            or path.name != f"{checkpoint.checkpoint_id}.tar.gz"
        ):
            raise ValueError("confirmed workspace artifact path is outside local storage")
        if not path.is_file():
            raise FileNotFoundError("confirmed workspace artifact is unavailable")

        descriptor = self._archive_descriptor(path)
        if (
            descriptor.get("workspace_root") != str(root.resolve())
            or descriptor.get("kind") != artifact.get("kind")
            or (
                artifact.get("manifest_hash") is not None
                and descriptor.get("manifest_hash") != artifact["manifest_hash"]
            )
        ):
            raise ValueError("confirmed workspace artifact descriptor does not match")
        if path.stat().st_size != artifact["size_bytes"]:
            raise ValueError("confirmed workspace artifact size does not match")

        actual = hashlib.sha256()
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                actual.update(chunk)

        if actual.hexdigest() != digest.lower():
            raise ValueError("confirmed workspace artifact checksum does not match")

    def _create(
        self,
        checkpoint: WorkspaceCheckpoint,
        expected_workspace_root: str
    ) -> dict[str, typing.Any]:
        """生成 Git 差异包或完整目录快照并返回小型引用。"""
        root = self._validated_workspace_root(checkpoint, expected_workspace_root)

        artifact_root = self._artifact_root(root)
        target = artifact_root / f"{checkpoint.checkpoint_id}.tar.gz"
        if target.exists():
            descriptor = self._archive_descriptor(target)
            if descriptor.get("workspace_root") != str(root):
                raise ValueError("existing workspace artifact root does not match checkpoint")
            return self._reference(
                target,
                root,
                kind=str(descriptor["kind"]),
                descriptor=descriptor,
            )

        kind = "git_worktree_snapshot" if self._git_head(root) else "directory_snapshot"

        descriptor = self._write_atomic(target, root, kind=kind)

        return self._reference(
            target,
            root,
            kind=kind,
            descriptor=descriptor,
        )

    def _artifact_root(self, workspace_root: Path) -> Path:
        """解析并创建工作区外的 artifact 目录。"""
        configured_root = self.artifact_dir.resolve()
        if self._is_within(configured_root, workspace_root):
            raise ValueError(
                "workspace artifact directory must be outside the workspace"
            )
        configured_root.mkdir(parents=True, exist_ok=True)
        return configured_root

    @staticmethod
    def _validated_workspace_root(
        checkpoint: WorkspaceCheckpoint,
        expected_workspace_root: str,
    ) -> Path:
        """校验服务端工作区与当前轮次目录一致且范围足够收敛。"""
        root = Path(str(checkpoint.workspace.get("root") or "")).expanduser()
        expected = Path(str(expected_workspace_root or "")).expanduser()
        if not root.is_absolute() or not expected.is_absolute():
            raise ValueError("workspace roots must be absolute")

        root = root.resolve()
        expected = expected.resolve()
        if root != expected:
            raise ValueError("workspace checkpoint root does not match the current turn")
        if not root.is_dir():
            raise ValueError("workspace checkpoint root must be an existing directory")

        filesystem_root = Path(root.anchor).resolve()
        user_home = Path.home().resolve()
        application_home = mind_home().expanduser().resolve()
        if root == filesystem_root or root == user_home or user_home.is_relative_to(root):
            raise ValueError("workspace checkpoint root is too broad")
        if root.is_relative_to(application_home) or application_home.is_relative_to(root):
            raise ValueError("workspace checkpoint overlaps application data")
        return root

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        """判断路径是否等于或位于指定根目录内。"""
        return path == root or path.is_relative_to(root)

    def _write_atomic(
        self,
        target: Path,
        root: Path,
        *,
        kind: str,
    ) -> dict[str, typing.Any]:
        """通过同目录临时文件原子写入恢复包。"""
        started_at = time.monotonic()
        descriptor: dict[str, typing.Any] = {
            "kind": kind,
            "workspace_root": str(root),
        }
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
        os.close(file_descriptor)
        temporary = Path(temporary_name)

        try:
            with tarfile.open(temporary, mode="w:gz") as archive:
                if kind == "git_worktree_snapshot":
                    descriptor.update(self._write_git_snapshot(
                        archive,
                        root,
                        started_at=started_at,
                    ))
                else:
                    entries, manifest_hash = self._directory_entries(
                        root,
                        started_at=started_at,
                    )
                    archive.add(root, arcname="workspace", recursive=False)
                    for source, relative in entries:
                        self._check_deadline(started_at)
                        archive.add(
                            source,
                            arcname=f"workspace/{relative}",
                            recursive=False,
                        )
                    descriptor["manifest_hash"] = manifest_hash
                self._add_bytes(
                    archive,
                    "artifact.json",
                    json.dumps(
                        descriptor,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8"),
                )
            if temporary.stat().st_size > MAX_WORKSPACE_ARCHIVE_BYTES:
                raise ValueError("workspace artifact exceeds archive size limit")
            self._check_deadline(started_at)
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
        return descriptor

    def _write_git_snapshot(
        self,
        archive: tarfile.TarFile,
        root: Path,
        *,
        started_at: float,
    ) -> dict[str, typing.Any]:
        """保存 HEAD 差异和未跟踪文件，避免复制 Git 对象库。"""
        head = self._git_head(root)
        if not head:
            raise RuntimeError("git workspace snapshot requires a HEAD commit")

        patch = self._git(root, "diff", "--binary", "HEAD", "--")
        if len(patch) > MAX_WORKSPACE_SOURCE_BYTES:
            raise ValueError("workspace Git diff exceeds source size limit")
        self._add_bytes(archive, "working-tree.patch", patch)

        raw_untracked = self._git(
            root,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
        )
        untracked: list[str] = []
        source_bytes = len(patch)
        manifest = hashlib.sha256()
        manifest.update(b"working-tree.patch\0")
        manifest.update(hashlib.sha256(patch).digest())
        for value in raw_untracked.split(b"\0"):
            if not value:
                continue

            self._check_deadline(started_at)
            if len(untracked) >= MAX_WORKSPACE_FILES:
                raise ValueError("workspace contains too many untracked files")

            relative = value.decode(const.CHARSET, errors="strict")
            relative_path = Path(relative)
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise ValueError("workspace contains an invalid untracked path")
            candidate = root / relative_path
            if candidate.is_symlink():
                raise ValueError("workspace Git snapshot does not support untracked symlinks")
            source = candidate.resolve()

            if not source.is_relative_to(root) or not source.is_file():
                raise ValueError("workspace contains an invalid untracked path")
            size = source.stat().st_size
            source_bytes += size
            if source_bytes > MAX_WORKSPACE_SOURCE_BYTES:
                raise ValueError("workspace snapshot exceeds source size limit")
            with source.open("rb") as file:
                digest = self._file_digest(file)
            archive.add(source, arcname=f"untracked/{relative}", recursive=False)
            untracked.append(relative)
            manifest.update(relative.encode(const.CHARSET))
            manifest.update(b"\0")
            manifest.update(digest)

        return {
            "git_head": head,
            "untracked_paths": untracked,
            "manifest_hash": manifest.hexdigest(),
        }

    def _directory_entries(
        self,
        root: Path,
        *,
        started_at: float,
    ) -> tuple[list[tuple[Path, str]], str]:
        """预检非 Git 工作区并返回有界、可读的稳定条目列表。"""
        entries: list[tuple[Path, str]] = []
        source_bytes = [0]
        manifest = hashlib.sha256()

        def scan(directory: Path) -> None:
            """递归扫描目录，但不跟随符号链接或跳过读取错误。"""
            self._check_deadline(started_at)
            with os.scandir(directory) as iterator:
                children = sorted(iterator, key=lambda item: item.name)
            for child in children:
                self._check_deadline(started_at)
                source = Path(child.path)
                if source == root / ".git":
                    continue
                relative = source.relative_to(root).as_posix()
                if len(entries) >= MAX_WORKSPACE_FILES:
                    raise ValueError("workspace contains too many files")

                stat = child.stat(follow_symlinks=False)
                entries.append((source, relative))
                manifest.update(relative.encode(const.CHARSET))
                manifest.update(b"\0")
                if child.is_symlink():
                    raw_link = os.readlink(source)
                    link_target = Path(raw_link)
                    resolved_link = (source.parent / link_target).resolve()
                    if link_target.is_absolute() or not resolved_link.is_relative_to(root):
                        raise ValueError("workspace symlink escapes the workspace")
                    link = raw_link.encode(const.CHARSET)
                    manifest.update(hashlib.sha256(link).digest())
                elif child.is_dir(follow_symlinks=False):
                    manifest.update(b"directory")
                    scan(source)
                elif child.is_file(follow_symlinks=False):
                    source_bytes[0] += stat.st_size
                    if source_bytes[0] > MAX_WORKSPACE_SOURCE_BYTES:
                        raise ValueError("workspace snapshot exceeds source size limit")
                    with source.open("rb") as file:
                        manifest.update(self._file_digest(file))
                else:
                    raise ValueError("workspace contains an unsupported special file")

        scan(root)
        return entries, manifest.hexdigest()

    @staticmethod
    def _file_digest(file: typing.BinaryIO) -> bytes:
        """读取文件并返回二进制 SHA-256 摘要。"""
        digest = hashlib.sha256()
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.digest()

    @staticmethod
    def _check_deadline(started_at: float) -> None:
        """在 artifact 生成超过时限时终止当前操作。"""
        if time.monotonic() - started_at > MAX_WORKSPACE_ARTIFACT_SECONDS:
            raise TimeoutError("workspace artifact generation timed out")

    @staticmethod
    def _add_bytes(archive: tarfile.TarFile, name: str, payload: bytes) -> None:
        """向恢复包追加一项内存数据。"""
        info = tarfile.TarInfo(name=name)
        info.size = len(payload)
        info.mode = 0o600
        archive.addfile(info, io.BytesIO(payload))

    @staticmethod
    def _git(root: Path, *arguments: str) -> bytes:
        """执行只读 Git 命令并返回原始标准输出。"""
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return completed.stdout

    def _git_head(self, root: Path) -> str:
        """仅在目录为 Git worktree 根且存在 HEAD 时返回提交标识。"""
        try:
            top_level = Path(self._git(
                root,
                "rev-parse",
                "--show-toplevel",
            ).decode(const.CHARSET, errors="strict").strip()).resolve()
            if top_level != root:
                return ""
            return self._git(root, "rev-parse", "--verify", "HEAD").decode(
                "ascii",
                errors="strict",
            ).strip()
        except (OSError, subprocess.SubprocessError, UnicodeError):
            return ""

    @staticmethod
    def _archive_descriptor(path: Path) -> dict[str, typing.Any]:
        """从既有恢复包读取并校验 artifact 描述。"""
        try:
            with tarfile.open(path, mode="r:gz") as archive:
                member  = archive.extractfile("artifact.json")
                payload = json.loads(member.read()) if member is not None else {}

            if not isinstance(payload, dict):
                raise ValueError("workspace artifact descriptor must be an object")
            kind = payload.get("kind")
            workspace_root = payload.get("workspace_root")
            allowed_keys = {
                "kind",
                "workspace_root",
                "manifest_hash",
            }
            if kind == "git_worktree_snapshot":
                allowed_keys.update({"git_head", "untracked_paths"})
            if (
                kind in {"git_worktree_snapshot", "directory_snapshot"}
                and set(payload).issubset(allowed_keys)
                and isinstance(workspace_root, str)
                and Path(workspace_root).is_absolute()
            ):
                return dict(payload)
        except (OSError, tarfile.TarError, ValueError, json.JSONDecodeError):
            pass

        raise ValueError("existing workspace artifact is invalid")

    @staticmethod
    def _reference(
        path: Path,
        root: Path,
        *,
        kind: str,
        descriptor: dict[str, typing.Any] | None = None
    ) -> dict[str, typing.Any]:
        """构造不携带恢复包正文的控制面引用。"""
        digest = hashlib.sha256()
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        reference: dict[str, typing.Any] = {
            "kind": kind,
            "local_ref": str(path.resolve()),
            "sha256": digest.hexdigest(),
            "size_bytes": path.stat().st_size,
            "workspace_root": str(root),
        }
        if descriptor and descriptor.get("git_head"):
            reference["git_head"] = descriptor["git_head"]
        if descriptor and descriptor.get("manifest_hash"):
            reference["manifest_hash"] = descriptor["manifest_hash"]
        return reference


if __name__ == "__main__":
    pass
