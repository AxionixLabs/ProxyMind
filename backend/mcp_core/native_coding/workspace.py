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
            truncated=len(items) >= max_items,
            recommended_next_steps=self._list_files_next_steps(
                path=self._rel(base),
                pattern=pattern,
                recursive=recursive,
                max_items=max_items,
                truncated=len(items) >= max_items
            )
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
        full_text = self._decode(full_raw)
        preview_text = self._decode(raw[:limit])
        full_lines = full_text.splitlines()
        total_lines = len(full_lines)
        if start_line is not None or max_lines is not None:
            start = max(1, int(start_line or 1))
            count = max(1, min(int(max_lines or 200), 2000))
            sliced = full_lines[start - 1:start - 1 + count]
            text = "\n".join(sliced)
        else:
            start = 1
            count = None
            text = preview_text

        content_raw = text.encode(const.CHARSET, const.IGNORE)
        output_truncated = len(content_raw) > limit
        if output_truncated:
            text = self._decode(content_raw[:limit])

        end_line = start + len(text.splitlines()) - 1 if text else start
        range_truncated = bool(count is not None and start - 1 + count < total_lines)
        byte_truncated = truncated or output_truncated

        return self._ok(
            f"workspace read ok path={self._rel(target)} bytes={min(size, limit)} truncated={byte_truncated}",
            path=self._rel(target),
            content=text,
            size=size,
            sha256=self._sha256(full_raw),
            start_line=start,
            end_line=end_line,
            total_lines=total_lines,
            truncated=byte_truncated or range_truncated,
            byte_truncated=byte_truncated,
            range_truncated=range_truncated,
            recommended_next_steps=self._read_file_next_steps(
                path=self._rel(target),
                end_line=end_line,
                total_lines=total_lines,
                limit=limit,
                byte_truncated=byte_truncated,
                range_truncated=range_truncated
            )
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
            truncated=len(matches) >= max_matches,
            recommended_next_steps=self._search_text_next_steps(
                query=needle,
                path=self._rel(base),
                glob=glob,
                case_sensitive=case_sensitive,
                max_matches=max_matches,
                truncated=len(matches) >= max_matches
            )
        )

    @staticmethod
    def _list_files_next_steps(
        *,
        path: str,
        pattern: str | None,
        recursive: bool,
        max_items: int,
        truncated: bool
    ) -> list[dict[str, typing.Any]]:
        if not truncated:
            return []
        return [
            {
                "tool": "workspace_list_files",
                "args": {
                    "path": path,
                    "pattern": pattern,
                    "recursive": recursive,
                    "max_items": min(max_items * 2, 2000)
                },
                "reason": "increase_limit"
            },
            {
                "tool": "workspace_list_files",
                "args": {
                    "path": path,
                    "pattern": pattern or "*",
                    "recursive": False,
                    "max_items": max_items
                },
                "reason": "narrow_scope"
            }
        ]

    @staticmethod
    def _read_file_next_steps(
        *,
        path: str,
        end_line: int,
        total_lines: int,
        limit: int,
        byte_truncated: bool,
        range_truncated: bool
    ) -> list[dict[str, typing.Any]]:
        steps: list[dict[str, typing.Any]] = []
        if (range_truncated or byte_truncated) and end_line < total_lines:
            steps.append({
                "tool": "workspace_read_file",
                "args": {"path": path, "start_line": end_line + 1, "max_lines": 200},
                "reason": "continue_from_next_line"
            })
        if byte_truncated:
            steps.append({
                "tool": "workspace_read_file",
                "args": {"path": path, "max_bytes": limit},
                "reason": "content_exceeds_max_bytes"
            })
        return steps

    @staticmethod
    def _search_text_next_steps(
        *,
        query: str,
        path: str,
        glob: str | None,
        case_sensitive: bool,
        max_matches: int,
        truncated: bool
    ) -> list[dict[str, typing.Any]]:
        if not truncated:
            return []
        return [
            {
                "tool": "workspace_search_text",
                "args": {
                    "query": query,
                    "path": path,
                    "glob": glob,
                    "case_sensitive": case_sensitive,
                    "max_matches": min(max_matches * 2, 1000)
                },
                "reason": "increase_limit"
            },
            {
                "tool": "workspace_list_files",
                "args": {"path": path, "pattern": glob or "*", "recursive": True, "max_items": 200},
                "reason": "narrow_search_scope"
            }
        ]

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
        target.write_text(payload, encoding=const.CHARSET, newline="")
        return self._ok(
            f"workspace write ok path={self._rel(target)} bytes={size}",
            path=self._rel(target),
            bytes=size,
            sha256=self._sha256(payload.encode(const.CHARSET, const.IGNORE))
        )

    def move_file(
        self,
        *,
        source_path: str,
        target_path: str,
        overwrite: bool = False,
        create_dirs: bool = True,
        expected_sha256: str | None = None,
        force: bool = False
    ) -> dict[str, typing.Any]:
        try:
            source = self._resolve(source_path)
            target = self._resolve(target_path)
        except ValueError as exc:
            return self._fail("path_outside_workspace", error=str(exc))

        if not source.is_file():
            return self._fail("source_file_not_found", source_path=source_path)
        if target.exists() and target.is_dir():
            return self._fail("target_is_directory", target_path=self._rel(target))
        if target.exists() and not overwrite:
            return self._fail("target_exists", target_path=self._rel(target))

        payload = source.read_bytes()
        sha = self._sha256(payload)
        if expected_sha256 and not force and expected_sha256 != sha:
            return self._fail(
                "file_changed_since_read",
                source_path=self._rel(source),
                expected_sha256=expected_sha256,
                actual_sha256=sha
            )

        if create_dirs:
            target.parent.mkdir(parents=True, exist_ok=True)
        elif not target.parent.exists():
            return self._fail("target_parent_not_found", target_path=self._rel(target))

        overwritten = target.exists()
        source.replace(target)
        return self._ok(
            f"workspace move ok source={self._rel(source)} target={self._rel(target)} bytes={len(payload)}",
            source_path=self._rel(source),
            target_path=self._rel(target),
            bytes=len(payload),
            sha256=sha,
            overwritten=overwritten
        )

    def copy_file(
        self,
        *,
        source_path: str,
        target_path: str,
        overwrite: bool = False,
        create_dirs: bool = True,
        expected_sha256: str | None = None,
        force: bool = False
    ) -> dict[str, typing.Any]:
        try:
            source = self._resolve(source_path)
            target = self._resolve(target_path)
        except ValueError as exc:
            return self._fail("path_outside_workspace", error=str(exc))

        if not source.is_file():
            return self._fail("source_file_not_found", source_path=source_path)
        if target.exists() and target.is_dir():
            return self._fail("target_is_directory", target_path=self._rel(target))
        if target.exists() and not overwrite:
            return self._fail("target_exists", target_path=self._rel(target))

        payload = source.read_bytes()
        sha = self._sha256(payload)
        if expected_sha256 and not force and expected_sha256 != sha:
            return self._fail(
                "file_changed_since_read",
                source_path=self._rel(source),
                expected_sha256=expected_sha256,
                actual_sha256=sha
            )

        if create_dirs:
            target.parent.mkdir(parents=True, exist_ok=True)
        elif not target.parent.exists():
            return self._fail("target_parent_not_found", target_path=self._rel(target))

        overwritten = target.exists()
        target.write_bytes(payload)
        return self._ok(
            f"workspace copy ok source={self._rel(source)} target={self._rel(target)} bytes={len(payload)}",
            source_path=self._rel(source),
            target_path=self._rel(target),
            bytes=len(payload),
            sha256=sha,
            overwritten=overwritten
        )

    def delete_file(
        self,
        *,
        path: str,
        expected_sha256: str | None = None,
        force: bool = False
    ) -> dict[str, typing.Any]:
        try:
            target = self._resolve(path)
        except ValueError as exc:
            return self._fail("path_outside_workspace", error=str(exc))

        if not target.exists():
            return self._fail("file_not_found", path=path)
        if not target.is_file():
            return self._fail("target_not_file", path=self._rel(target))

        payload = target.read_bytes()
        sha = self._sha256(payload)
        if expected_sha256 and not force and expected_sha256 != sha:
            return self._fail(
                "file_changed_since_read",
                path=self._rel(target),
                expected_sha256=expected_sha256,
                actual_sha256=sha
            )

        target.unlink()
        return self._ok(
            f"workspace delete ok path={self._rel(target)} bytes={len(payload)}",
            path=self._rel(target),
            bytes=len(payload),
            sha256=sha
        )


if __name__ == '__main__':
    pass
