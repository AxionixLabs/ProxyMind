# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import stat
from collections.abc import Iterator
from contextlib import (
    ExitStack,
    contextmanager,
)
from pathlib import Path

from agent.ports.session_deletion import TranscriptDeletionLease
from infrastructure.platform.file_lock import FileLease


def validate_transcript_path(path: Path) -> None:
    """拒绝符号链接、Windows 重解析点、硬链接及非普通文件。"""
    absolute = path.absolute()
    for component in (*reversed(absolute.parents), absolute):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("transcript path contains a link or reparse point")
        if component == absolute and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
            raise ValueError("transcript path is not an unshared regular file")


def transcript_lease(path: Path, *, exclusive: bool) -> FileLease:
    """校验目标及侧文件并取得稳定文件锁。"""
    validate_transcript_path(path)
    lock_path = path.with_suffix(path.suffix + ".lock")
    validate_transcript_path(lock_path)
    validate_transcript_path(_marker(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    lease = FileLease(lock_path, exclusive=exclusive)
    try:
        validate_transcript_path(path)
        validate_transcript_path(lock_path)
        validate_transcript_path(_marker(path))
    except BaseException:
        lease.close()
        raise
    return lease


def require_live_transcript(path: Path) -> None:
    """在持有共享写锁时拒绝已退役的文件。"""
    if _marker(path).exists():
        raise PermissionError("session transcript has been deleted")


def _marker(path: Path) -> Path:
    """返回不含正文且不自动过期的停写标记路径。"""
    return path.with_suffix(path.suffix + ".deleted")


class _TranscriptDeletion:
    """在独占锁保护下写入停写标记并清理一组已校验文件。"""

    def __init__(self, paths: tuple[Path, ...]) -> None:
        """保存冻结的文件集合。"""
        self.paths = paths

    def retire(self) -> None:
        """刷新标记到磁盘，保留空标记作为后续进程的永久写入屏障。"""
        for path in self.paths:
            validate_transcript_path(path)
            marker = _marker(path)
            validate_transcript_path(marker)
            with marker.open("ab") as file:
                file.flush()
                os.fsync(file.fileno())

    def delete(self) -> None:
        """删除目标正文文件，任何占用或权限错误均向上传播。"""
        for path in self.paths:
            validate_transcript_path(path)
            path.unlink(missing_ok=True)


@contextmanager
def lock_transcripts(paths: tuple[Path, ...]) -> Iterator[TranscriptDeletionLease]:
    """先取得完整集合的独占锁，再允许调用方改变任何会话资源。"""
    with ExitStack() as stack:
        for path in sorted(paths):
            lease = transcript_lease(path, exclusive=True)
            stack.callback(lease.close)
        yield _TranscriptDeletion(paths)


if __name__ == '__main__':
    pass
