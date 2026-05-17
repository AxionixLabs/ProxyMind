# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from backend.mcp_core.native_coding.base import NativeCodingComponent


class FileAudit(NativeCodingComponent):
    """用于 shell 命令的轻量级工作区文件变更审计。"""

    MAX_AUDIT_FILES = 3000
    MAX_HASH_BYTES  = 2_000_000

    def capture_file_fingerprints(self, *, max_files: int | None = None) -> dict[str, typing.Any]:
        limit = max(1, int(max_files or self.MAX_AUDIT_FILES))
        files: dict[str, dict[str, typing.Any]] = {}
        truncated = False
        count = 0
        for item in self._walk(self.root, recursive=True):
            if not item.is_file() or self._is_excluded(item):
                continue
            count += 1
            if len(files) >= limit:
                truncated = True
                continue
            try:
                stat = item.stat()
                rel = self._rel(item)
                fingerprint: dict[str, typing.Any] = {
                    "path": rel,
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns
                }
                if stat.st_size <= self.MAX_HASH_BYTES:
                    fingerprint["sha256"] = self._sha256(item.read_bytes())
                files[rel] = fingerprint
            except OSError:
                continue
        return {
            "files": files,
            "file_count": count,
            "captured_count": len(files),
            "truncated": truncated,
            "max_files": limit
        }

    @staticmethod
    def diff_file_fingerprints(
        before: dict[str, typing.Any] | None,
        after: dict[str, typing.Any] | None,
        *,
        max_items: int = 100
    ) -> dict[str, typing.Any]:
        before_files = (before or {}).get("files") or {}
        after_files = (after or {}).get("files") or {}
        before_paths = set(before_files.keys())
        after_paths = set(after_files.keys())

        created = sorted(after_paths - before_paths)
        deleted = sorted(before_paths - after_paths)
        modified: list[str] = []
        for path in sorted(before_paths & after_paths):
            if FileAudit._fingerprint_changed(before_files.get(path) or {}, after_files.get(path) or {}):
                modified.append(path)

        changed = created + modified + deleted
        return {
            "changed": bool(changed),
            "change_count": len(changed),
            "created": created[:max_items],
            "modified": modified[:max_items],
            "deleted": deleted[:max_items],
            "created_count": len(created),
            "modified_count": len(modified),
            "deleted_count": len(deleted),
            "truncated": bool((before or {}).get("truncated")) or bool((after or {}).get("truncated")) or len(changed) > max_items,
            "before_file_count": (before or {}).get("file_count", 0),
            "after_file_count": (after or {}).get("file_count", 0)
        }

    @staticmethod
    def _fingerprint_changed(before: dict[str, typing.Any], after: dict[str, typing.Any]) -> bool:
        before_hash = before.get("sha256")
        after_hash = after.get("sha256")
        if before_hash is not None and after_hash is not None:
            return before_hash != after_hash
        return before.get("size") != after.get("size") or before.get("mtime_ns") != after.get("mtime_ns")


if __name__ == '__main__':
    pass
