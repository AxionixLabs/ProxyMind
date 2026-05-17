# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import re
import fnmatch
import typing
from backend.mcp_core.native_coding.base import NativeCodingComponent
from backend.utilities import const
from backend.utilities.trace import clip_text


class WorkspaceTools(NativeCodingComponent):

    def workspace_root(self) -> dict[str, typing.Any]:
        return {
            "text"        : f"workspace root={self.root}",
            "attachments" : [],
            "data"        : {"ok": True, "root": str(self.root)},
            "logs"        : []
        }

    def list_files(
        self,
        *,
        path: str = ".",
        pattern: str | None = None,
        recursive: bool = True,
        max_items: int = 200
    ) -> dict[str, typing.Any]:
        base = self._resolve(path)
        if not base.exists():
            return self._fail("path_not_found", path=path)

        max_items = max(1, min(int(max_items or 200), 2000))
        items: list[dict[str, typing.Any]] = []

        for item in self._walk(base, recursive=recursive):
            if self._is_excluded(item):
                continue
            rel = self._rel(item)
            if pattern and not fnmatch.fnmatch(rel, pattern) and not fnmatch.fnmatch(item.name, pattern):
                continue
            try:
                stat = item.stat()
            except OSError:
                continue
            items.append({
                "path"  : rel,
                "kind"  : "dir" if item.is_dir() else "file",
                "size"  : stat.st_size,
                "mtime" : int(stat.st_mtime)
            })
            if len(items) >= max_items:
                break

        return self._ok(
            f"workspace list ok count={len(items)} root={self._rel(base)}",
            path=self._rel(base),
            items=items,
            truncated=len(items) >= max_items
        )

    def read_file(
        self,
        *,
        path: str,
        start_line: int | None = None,
        max_lines: int | None = None,
        max_bytes: int | None = None
    ) -> dict[str, typing.Any]:
        target = self._resolve(path)
        if not target.is_file():
            return self._fail("file_not_found", path=path)

        limit = max(1, min(int(max_bytes or self.max_read_bytes), self.max_read_bytes))
        size = target.stat().st_size
        with target.open("rb") as fh:
            raw = fh.read(limit + 1)
        full_raw = target.read_bytes()
        truncated = len(raw) > limit or size > limit
        text = self._decode(raw[:limit])

        lines = text.splitlines()
        total_lines = len(lines)
        if start_line is not None or max_lines is not None:
            start = max(1, int(start_line or 1))
            count = max(1, min(int(max_lines or 200), 2000))
            sliced = lines[start - 1:start - 1 + count]
            text = "\n".join(sliced)
        else:
            start = 1

        return self._ok(
            f"workspace read ok path={self._rel(target)} bytes={min(size, limit)} truncated={truncated}",
            path=self._rel(target),
            content=text,
            size=size,
            sha256=self._sha256(full_raw),
            start_line=start,
            total_lines=total_lines,
            truncated=truncated
        )

    def search_text(
        self,
        *,
        query: str,
        path: str = ".",
        glob: str | None = None,
        case_sensitive: bool = False,
        max_matches: int = 100
    ) -> dict[str, typing.Any]:
        needle = str(query or "")
        if not needle:
            return self._fail("query_empty")

        base = self._resolve(path)
        if not base.exists():
            return self._fail("path_not_found", path=path)

        max_matches = max(1, min(int(max_matches or 100), 1000))
        flags = 0 if case_sensitive else re.IGNORECASE
        regex = re.compile(re.escape(needle), flags)
        matches: list[dict[str, typing.Any]] = []
        files = self._walk(base, recursive=True)

        for item in files:
            if len(matches) >= max_matches:
                break
            if not item.is_file() or self._is_excluded(item) or not self._looks_text(item):
                continue
            rel = self._rel(item)
            if glob and not fnmatch.fnmatch(rel, glob) and not fnmatch.fnmatch(item.name, glob):
                continue
            try:
                raw = item.read_bytes()[:self.max_read_bytes]
            except OSError:
                continue
            text = self._decode(raw)
            for lineno, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    matches.append({
                        "path": rel,
                        "line": lineno,
                        "text": clip_text(line.strip(), limit=500)
                    })
                    if len(matches) >= max_matches:
                        break

        return self._ok(
            f"workspace search ok matches={len(matches)}",
            query=needle,
            matches=matches,
            truncated=len(matches) >= max_matches
        )

    def write_file(
        self,
        *,
        path: str,
        content: str,
        create_dirs: bool = True,
        overwrite: bool = True,
        expected_sha256: str | None = None,
        force: bool = False
    ) -> dict[str, typing.Any]:
        target = self._resolve(path)
        payload = str(content or "")
        size = len(payload.encode(const.CHARSET, const.IGNORE))
        if size > self.max_write_bytes:
            return self._fail("content_too_large", size=size, max_bytes=self.max_write_bytes)
        if target.exists() and not overwrite:
            return self._fail("file_exists", path=self._rel(target))
        if conflict := self._conflict_guard(target, expected_sha256=expected_sha256, force=force):
            return conflict
        if create_dirs:
            target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload, encoding=const.CHARSET)
        return self._ok(
            f"workspace write ok path={self._rel(target)} bytes={size}",
            path=self._rel(target),
            bytes=size,
            sha256=self._sha256(payload.encode(const.CHARSET, const.IGNORE))
        )


if __name__ == '__main__':
    pass
