# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import io
import os
import json
import typing
import asyncio
import hashlib
import tarfile
import tempfile
import subprocess
from pathlib import Path
from mind_app.paths import workspace_artifacts_dir
from mind_nova.requests.checkpoints import post_checkpoint_artifact
from mind_nova.stream_events import WorkspaceCheckpoint
from mind_nova import const


class WorkspaceArtifactGate:
    """在本地副作用前生成并上传可恢复 workspace artifact 引用。"""

    def __init__(self, artifact_dir: str | Path | None = None) -> None:
        """绑定不会落入工作区的本地 artifact 目录。"""
        self.artifact_dir = Path(
            artifact_dir or workspace_artifacts_dir()
        ).expanduser()

    async def ensure(self, checkpoint: WorkspaceCheckpoint | None) -> None:
        """确认 artifact 已由服务端接受后才返回。"""
        if checkpoint is None or not checkpoint.required:
            raise ValueError("workspace checkpoint is required before local execution")
        if checkpoint.artifact:
            await asyncio.to_thread(
                self._validate_confirmed_artifact,
                checkpoint,
            )
            return None

        artifact = await asyncio.to_thread(self._create, checkpoint)
        request_id = f"checkpoint-artifact:{checkpoint.checkpoint_id}"
        await post_checkpoint_artifact(
            checkpoint_id=checkpoint.checkpoint_id,
            request_id=request_id,
            artifact=artifact,
        )

    @staticmethod
    def _validate_confirmed_artifact(checkpoint: WorkspaceCheckpoint) -> None:
        """确认服务端已关联的本地恢复包仍完整可用。"""
        artifact       = checkpoint.artifact
        local_ref      = artifact.get("local_ref")
        digest         = artifact.get("sha256")
        workspace_root = artifact.get("workspace_root")

        if not isinstance(local_ref, str) or not Path(local_ref).is_absolute():
            raise ValueError("confirmed workspace artifact local_ref is invalid")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("confirmed workspace artifact sha256 is invalid")

        root = Path(str(checkpoint.workspace.get("root") or "")).expanduser()
        if not root.is_absolute() or str(root.resolve()) != str(workspace_root or ""):
            raise ValueError("confirmed workspace artifact root does not match checkpoint")

        path = Path(local_ref).resolve()
        if not path.is_file():
            raise FileNotFoundError("confirmed workspace artifact is unavailable")

        descriptor = WorkspaceArtifactGate._archive_descriptor(path)
        if (
            descriptor.get("workspace_root") != str(root.resolve())
            or descriptor.get("kind") != artifact.get("kind")
        ):
            raise ValueError("confirmed workspace artifact descriptor does not match")

        actual = hashlib.sha256()
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                actual.update(chunk)

        if actual.hexdigest() != digest.lower():
            raise ValueError("confirmed workspace artifact checksum does not match")

    def _create(self, checkpoint: WorkspaceCheckpoint) -> dict[str, typing.Any]:
        """生成 Git 差异包或完整目录快照并返回小型引用。"""
        root = Path(str(checkpoint.workspace.get("root") or "")).expanduser()
        if not root.is_absolute():
            raise ValueError("workspace checkpoint root must be absolute")

        root = root.resolve()
        if not root.is_dir():
            raise ValueError("workspace checkpoint root must be an existing directory")

        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_root = self.artifact_dir.resolve()
        if artifact_root == root or artifact_root.is_relative_to(root):
            raise ValueError("workspace artifact directory must be outside the workspace")

        target = self.artifact_dir / f"{checkpoint.checkpoint_id}.tar.gz"
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

    def _write_atomic(
        self,
        target: Path,
        root: Path,
        *,
        kind: str,
    ) -> dict[str, typing.Any]:
        """通过同目录临时文件原子写入恢复包。"""
        descriptor: dict[str, typing.Any] = {
            "schema_version": 1,
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
                    descriptor.update(self._write_git_snapshot(archive, root))
                else:
                    archive.add(
                        root,
                        arcname="workspace",
                        recursive=True,
                        filter=self._directory_filter(root),
                    )
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
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
        return descriptor

    def _write_git_snapshot(
        self,
        archive: tarfile.TarFile,
        root: Path
    ) -> dict[str, typing.Any]:
        """保存 HEAD 差异和未跟踪文件，避免复制 Git 对象库。"""
        head = self._git_head(root)
        if not head:
            raise RuntimeError("git workspace snapshot requires a HEAD commit")

        patch = self._git(root, "diff", "--binary", "HEAD", "--")
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
        for value in raw_untracked.split(b"\0"):
            if not value:
                continue

            relative = value.decode(const.CHARSET, errors="strict")
            source   = (root / relative).resolve()

            if not source.is_relative_to(root) or not source.exists():
                continue
            archive.add(source, arcname=f"untracked/{relative}", recursive=True)
            untracked.append(relative)

        return {
            "git_head": head,
            "untracked_paths": untracked,
        }

    @staticmethod
    def _add_bytes(archive: tarfile.TarFile, name: str, payload: bytes) -> None:
        """向恢复包追加一项内存数据。"""
        info = tarfile.TarInfo(name=name)
        info.size = len(payload)
        info.mode = 0o600
        archive.addfile(info, io.BytesIO(payload))

    @staticmethod
    def _directory_filter(root: Path) -> typing.Callable[[tarfile.TarInfo], tarfile.TarInfo | None]:
        """返回排除 Git 对象库的目录快照过滤器。"""
        git_dir = root / ".git"

        def filter_entry(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
            """排除工作区内的 Git 元数据目录。"""
            relative = Path(info.name)
            relative = Path(*relative.parts[1:]) if relative.parts else relative
            source   = root / relative

            try:
                if source.resolve().is_relative_to(git_dir.resolve()):
                    return None
            except (OSError, RuntimeError):
                return None

            return info

        return filter_entry

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
        """返回工作区 HEAD；非 Git 或空仓库返回空文本。"""
        try:
            return self._git(root, "rev-parse", "--verify", "HEAD").decode(
                "ascii"
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
            if (
                payload.get("schema_version") == 1
                and kind in {"git_worktree_snapshot", "directory_snapshot"}
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
            "schema_version": 1,
            "kind": kind,
            "local_ref": str(path.resolve()),
            "sha256": digest.hexdigest(),
            "size_bytes": path.stat().st_size,
            "workspace_root": str(root),
        }
        if descriptor and descriptor.get("git_head"):
            reference["git_head"] = descriptor["git_head"]
        return reference


if __name__ == "__main__":
    pass
