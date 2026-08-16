# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import json
import shutil
import typing
import asyncio
import hashlib
import tarfile
import tempfile
import subprocess
from pathlib import Path
from mind_app.paths import mind_home, workspace_artifacts_dir
from mind_app.runtime.workspace_artifacts import (
    MAX_WORKSPACE_FILES,
    MAX_WORKSPACE_SOURCE_BYTES,
)
from mind_nova.requests.checkpoints import (
    commit_checkpoint_restore,
    prepare_checkpoint_restore,
    validate_artifact_reference
)


class WorkspaceCheckpointRestorer:
    """按两阶段协议恢复本地 workspace 与服务端 Transcript。"""

    def __init__(self, artifact_dir: str | Path | None = None) -> None:
        """绑定只允许读取和清理恢复包的专用目录。"""
        self.artifact_dir = Path(
            workspace_artifacts_dir() if artifact_dir is None else artifact_dir
        ).expanduser()

    async def restore(
        self,
        *,
        checkpoint_id: str,
        request_id: str,
        expected_workspace_root: str,
    ) -> dict[str, typing.Any]:
        """先恢复并校验本地 workspace，再提交服务端恢复命令。"""
        checkpoint = await prepare_checkpoint_restore(
            checkpoint_id=checkpoint_id,
            request_id=request_id,
        )
        artifact = validate_artifact_reference(dict(checkpoint["artifact"]))
        already_restored = checkpoint.pop("_restore_status", "prepared") == "restored"
        if not already_restored:
            await asyncio.to_thread(
                self._restore_workspace,
                checkpoint,
                artifact,
                expected_workspace_root,
            )
        restored = await commit_checkpoint_restore(
            checkpoint_id=checkpoint_id,
            request_id=request_id,
            artifact_sha256=str(artifact["sha256"]),
        )
        self._remove_restored_artifact(artifact)
        for superseded in restored.get("superseded_artifacts") or []:
            if isinstance(superseded, dict):
                self._remove_restored_artifact(superseded)
        return restored

    def _restore_workspace(
        self,
        checkpoint: dict[str, typing.Any],
        artifact: dict[str, typing.Any],
        expected_workspace_root: str
    ) -> None:
        """校验恢复包身份，并按类型执行本地恢复。"""
        workspace = checkpoint.get("workspace")
        if not isinstance(workspace, dict):
            raise ValueError("checkpoint workspace is invalid")

        root     = Path(str(workspace.get("root") or "")).expanduser()
        expected = Path(str(expected_workspace_root or "")).expanduser()

        if not root.is_absolute() or not expected.is_absolute():
            raise ValueError("workspace restore roots must be absolute")

        root     = root.resolve()
        expected = expected.resolve()

        if root != expected or str(root) != artifact["workspace_root"]:
            raise ValueError("checkpoint workspace does not match the current turn")
        if not root.is_dir():
            raise ValueError("workspace restore root must be an existing directory")

        filesystem_root  = Path(root.anchor).resolve()
        user_home        = Path.home().resolve()
        application_home = mind_home().expanduser().resolve()

        if root == filesystem_root or root == user_home or user_home.is_relative_to(root):
            raise ValueError("workspace restore root is too broad")
        if root.is_relative_to(application_home) or application_home.is_relative_to(root):
            raise ValueError("workspace restore overlaps application data")

        archive_path = self._artifact_path(artifact)
        if not archive_path.is_file():
            raise FileNotFoundError("workspace restore artifact is unavailable")
        if archive_path.stat().st_size != artifact["size_bytes"]:
            raise ValueError("workspace restore artifact size does not match")
        if self._sha256(archive_path) != artifact["sha256"]:
            raise ValueError("workspace restore artifact checksum does not match")

        with tarfile.open(archive_path, mode="r:gz") as archive:
            self._validate_archive_layout(archive, kind=str(artifact["kind"]))
            descriptor = self._descriptor(archive)
            if (
                descriptor.get("kind") != artifact["kind"]
                or descriptor.get("workspace_root") != str(root)
                or (
                    artifact.get("manifest_hash") is not None
                    and descriptor.get("manifest_hash") != artifact["manifest_hash"]
                )
            ):
                raise ValueError("workspace restore artifact descriptor does not match")
            if artifact["kind"] == "directory_snapshot":
                self._restore_directory(archive, root)
            else:
                self._restore_git(archive, root, descriptor)

    def _restore_directory(self, archive: tarfile.TarFile, root: Path) -> None:
        """在同一文件系统内构造目录快照并原子替换工作区。"""
        staging = Path(tempfile.mkdtemp(prefix=f".{root.name}.restore-", dir=root.parent))

        backup = Path(tempfile.mkdtemp(prefix=f".{root.name}.backup-", dir=root.parent))
        backup.rmdir()

        try:
            self._extract_prefix(archive, prefix="workspace", target=staging)
            os.replace(root, backup)
            try:
                os.replace(staging, root)
            except BaseException:
                os.replace(backup, root)
                raise
            shutil.rmtree(backup, ignore_errors=True)
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    def _restore_git(
        self,
        archive: tarfile.TarFile,
        root: Path,
        descriptor: dict[str, typing.Any]
    ) -> None:
        """在临时 worktree 验证 Git 快照后替换工作区内容。"""
        head         = str(descriptor.get("git_head") or "")
        current_head = self._git(root, "rev-parse", "--verify", "HEAD").decode().strip()

        if not head or current_head != head:
            raise ValueError("workspace Git HEAD does not match the checkpoint")
        top_level = Path(self._git(
            root,
            "rev-parse",
            "--show-toplevel",
        ).decode().strip()).resolve()
        if top_level != root:
            raise ValueError("workspace Git restore root must be the worktree root")

        staging = Path(tempfile.mkdtemp(prefix=f".{root.name}.worktree-", dir=root.parent))
        staging.rmdir()
        backup = Path(tempfile.mkdtemp(prefix=f".{root.name}.backup-", dir=root.parent))
        worktree_added = False
        backup_disposable = False
        index_contents: bytes | None = None
        index_path = self._git_path(root, "index")

        try:
            self._git(root, "worktree", "add", "--detach", str(staging), head)
            worktree_added = True
            patch_member = archive.extractfile("working-tree.patch")
            if patch_member is None:
                raise ValueError("workspace Git patch is missing")
            patch = patch_member.read()
            if len(patch) > MAX_WORKSPACE_SOURCE_BYTES:
                raise ValueError("workspace Git patch exceeds source size limit")
            self._git(
                staging,
                "apply",
                "--binary",
                "--whitespace=nowarn",
                "-",
                stdin=patch,
            )
            self._extract_prefix(
                archive,
                prefix="untracked",
                target=staging,
                initial_bytes=len(patch),
            )

            if index_path.is_file():
                index_contents = index_path.read_bytes()
            self._move_workspace_entries(root, backup)
            try:
                self._copy_workspace_entries(staging, root)
                self._git(root, "reset", "--mixed", head)
            except BaseException:
                self._clear_workspace_entries(root)
                self._move_workspace_entries(backup, root)
                if index_contents is not None:
                    index_path.write_bytes(index_contents)
                elif index_path.exists():
                    index_path.unlink()
                backup_disposable = True
                raise
            backup_disposable = True
        finally:
            if worktree_added:
                subprocess.run(
                    ["git", "-C", str(root), "worktree", "remove", "--force", str(staging)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
            if staging.exists():
                shutil.rmtree(staging)
            if backup.exists() and (
                backup_disposable or not any(backup.iterdir())
            ):
                shutil.rmtree(backup, ignore_errors=True)

    @staticmethod
    def _extract_prefix(
        archive: tarfile.TarFile,
        *,
        prefix: str,
        target: Path,
        initial_bytes: int = 0,
    ) -> None:
        """安全提取指定前缀下的普通目录、文件和相对符号链接。"""
        prefix_path = Path(prefix)
        seen: set[Path] = set()
        source_bytes = initial_bytes
        for member in archive.getmembers():
            member_path = Path(member.name)
            if member_path == prefix_path:
                continue
            try:
                relative = member_path.relative_to(prefix_path)
            except ValueError:
                continue
            if relative.is_absolute() or ".." in relative.parts or relative in seen:
                raise ValueError("workspace restore archive path is invalid")
            if len(seen) >= MAX_WORKSPACE_FILES:
                raise ValueError("workspace restore archive contains too many entries")
            seen.add(relative)
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if member.isdir():
                destination.mkdir(exist_ok=True)
            elif member.isfile():
                source_bytes += member.size
                if source_bytes > MAX_WORKSPACE_SOURCE_BYTES:
                    raise ValueError("workspace restore archive exceeds source size limit")
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError("workspace restore archive file is invalid")
                with destination.open("xb") as output:
                    shutil.copyfileobj(source, output)
            elif member.issym():
                link_target = Path(member.linkname)
                resolved = (destination.parent / link_target).resolve()
                if link_target.is_absolute() or not resolved.is_relative_to(target.resolve()):
                    raise ValueError("workspace restore symlink escapes the workspace")
                destination.symlink_to(member.linkname)
            else:
                raise ValueError("workspace restore archive contains a special file")

    @staticmethod
    def _move_workspace_entries(source: Path, target: Path) -> None:
        """原子批量移动工作区内容，失败时撤销已移动条目。"""
        moved: list[str] = []
        try:
            for entry in source.iterdir():
                if entry.name == ".git":
                    continue
                os.replace(entry, target / entry.name)
                moved.append(entry.name)
        except BaseException:
            for name in reversed(moved):
                os.replace(target / name, source / name)
            raise

    @staticmethod
    def _copy_workspace_entries(source: Path, target: Path) -> None:
        """复制已验证 worktree 内容但不替换目标 Git 元数据。"""
        for entry in source.iterdir():
            if entry.name == ".git":
                continue
            destination = target / entry.name
            if entry.is_symlink():
                destination.symlink_to(os.readlink(entry))
            elif entry.is_dir():
                shutil.copytree(entry, destination, symlinks=True)
            else:
                shutil.copy2(entry, destination, follow_symlinks=False)

    @staticmethod
    def _clear_workspace_entries(root: Path) -> None:
        """清理失败恢复写入的内容但保留 Git 元数据。"""
        for entry in root.iterdir():
            if entry.name == ".git":
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()

    @staticmethod
    def _descriptor(archive: tarfile.TarFile) -> dict[str, typing.Any]:
        """读取恢复包内的严格描述对象。"""
        member = archive.extractfile("artifact.json")
        if member is None:
            raise ValueError("workspace restore artifact descriptor is missing")
        value = json.loads(member.read())
        if not isinstance(value, dict):
            raise ValueError("workspace restore artifact descriptor is invalid")
        return dict(value)

    @staticmethod
    def _validate_archive_layout(archive: tarfile.TarFile, *, kind: str) -> None:
        """拒绝重复、越界或不属于快照类型的 archive 条目。"""
        members = archive.getmembers()
        names = [member.name for member in members]
        if len(names) > MAX_WORKSPACE_FILES + 3 or len(names) != len(set(names)):
            raise ValueError("workspace restore archive layout is invalid")

        allowed_files = {"artifact.json"}
        prefix = "workspace"
        if kind == "git_worktree_snapshot":
            allowed_files.add("working-tree.patch")
            prefix = "untracked"
        elif kind != "directory_snapshot":
            raise ValueError("workspace restore artifact kind is invalid")

        if "artifact.json" not in names or (
            kind == "git_worktree_snapshot" and "working-tree.patch" not in names
        ):
            raise ValueError("workspace restore archive layout is invalid")
        for member in members:
            path = Path(member.name)
            if member.name in allowed_files:
                if not member.isfile():
                    raise ValueError("workspace restore archive layout is invalid")
                continue
            if path != Path(prefix) and not path.is_relative_to(Path(prefix)):
                raise ValueError("workspace restore archive layout is invalid")

    def _artifact_path(self, artifact: dict[str, typing.Any]) -> Path:
        """把 artifact 引用约束到当前客户端的专用恢复包目录。"""
        normalized = validate_artifact_reference(artifact)
        storage_root = self.artifact_dir.resolve()
        path = Path(str(normalized["local_ref"])).expanduser().resolve()
        if path.parent != storage_root or path.suffixes[-2:] != [".tar", ".gz"]:
            raise ValueError("workspace restore artifact is outside local storage")
        return path

    @classmethod
    def _git_path(cls, root: Path, name: str) -> Path:
        """解析主仓库或 linked worktree 的 Git 管理文件路径。"""
        raw_path = cls._git(root, "rev-parse", "--git-path", name).decode().strip()
        path = Path(raw_path)
        return path.resolve() if path.is_absolute() else (root / path).resolve()

    @staticmethod
    def _sha256(path: Path) -> str:
        """计算本地恢复包的 SHA-256。"""
        digest = hashlib.sha256()
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _git(root: Path, *arguments: str, stdin: bytes | None = None) -> bytes:
        """执行恢复所需的 Git 命令并返回标准输出。"""
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            input=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        return completed.stdout

    def _remove_restored_artifact(self, artifact: dict[str, typing.Any]) -> None:
        """在服务端提交成功后删除当前已恢复的本地 artifact。"""
        try:
            path = self._artifact_path(artifact)
            path.unlink(missing_ok=True)
        except (OSError, TypeError, ValueError):
            return


if __name__ == '__main__':
    pass
