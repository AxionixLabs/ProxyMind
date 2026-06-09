# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import re
import typing
import fnmatch
from backend.mcp_core.coding_native.base import NativeCodingComponent
from backend.utilities.trace import clip_text
from backend.utilities import const


class WorkspaceTools(NativeCodingComponent):
    """提供工作区文件读取、写入、搜索和路径列表能力。"""

    def workspace_root(
        self
    ) -> dict[str, typing.Any]:
        """返回当前 native coding 工作区根目录。"""
        return {
            "text"        : f"workspace root={self.root}",
            "attachments" : [],
            "data"        : {"ok": True, "root": str(self.root)},
            "logs"        : []
        }

    def list_file(
        self,
        *,
        path: str = ".",
        glob: str | None = None,
        recursive: bool = True,
        max_matches: int = 100
    ) -> dict[str, typing.Any]:
        """列出工作区内的文件路径。"""
        base = self._resolve(path)
        if not base.exists():
            return self._fail("path_not_found", path=path)
        if not base.is_dir():
            return self._fail("path_not_directory", path=path)

        limit = max(1, min(int(max_matches or 100), 1000))

        files: list[dict[str, typing.Any]] = []

        for item in self._walk(base, recursive=bool(recursive)):
            if len(files) >= limit:
                break
            if self._is_excluded(item):
                continue

            rel = self._rel(item)
            if glob and not fnmatch.fnmatch(rel, glob) and not fnmatch.fnmatch(item.name, glob):
                continue

            files.append({
                "path"      : rel,
                "file_kind" : "dir" if item.is_dir() else "file",
                "size"      : item.stat().st_size if item.is_file() else None,
            })

        return self._ok(
            f"workspace list file ok path={self._rel(base)} files={len(files)}",
            path=self._rel(base),
            glob=glob,
            recursive=bool(recursive),
            files=files,
            file_count=len(files),
            truncated=len(files) >= limit,
            max_matches=limit
        )

    def read_file(
        self,
        *,
        path: str,
        start_line: int | None = None,
        max_lines: int | None = None,
        max_bytes: int | None = None
    ) -> dict[str, typing.Any]:
        """读取文本文件内容；大文件只读取预览或指定行窗口，避免全量进入内存。"""
        target = self._resolve(path)
        if not target.is_file():
            return self._fail("file_not_found", path=path)
        if not self._looks_text(target):
            return self._fail(
                "file_not_text",
                path=self._rel(target),
                size=target.stat().st_size,
                suggested_next_action="use_shell_or_specialized_binary_tool"
            )

        limit = max(1, min(int(max_bytes or self.max_read_bytes), self.max_read_bytes))
        size  = target.stat().st_size

        line_window = start_line is not None or max_lines is not None

        sha256: str | None     = None
        sha256_available: bool = False

        if size <= limit:

            full_raw         = target.read_bytes()
            sha256           = self._sha256(full_raw)
            sha256_available = True
            full_text        = self._decode(full_raw)
            full_lines       = full_text.splitlines()

            total_lines: int | None = len(full_lines)

            if line_window:
                start           = max(1, int(start_line or 1))
                count           = max(1, min(int(max_lines or 200), 2000))
                sliced          = full_lines[start - 1:start - 1 + count]
                text            = "\n".join(sliced)
                line_truncated  = start - 1 + count < total_lines
            else:
                start           = 1
                text            = full_text
                line_truncated  = False

            input_truncated = False

        elif line_window:
            start = max(1, int(start_line or 1))
            count = max(1, min(int(max_lines or 200), 2000))

            text, total_lines, line_truncated = self._read_line_window(
                target,
                start_line=start,
                max_lines=count
            )
            input_truncated = True

        else:
            with target.open("rb") as fh:
                raw = fh.read(limit + 1)

            start           = 1
            text            = self._decode(raw[:limit])
            total_lines     = None
            line_truncated  = False
            input_truncated = len(raw) > limit or size > limit

        content_raw = text.encode(const.CHARSET, const.IGNORE)

        output_truncated = len(content_raw) > limit
        if output_truncated:
            text = self._decode(content_raw[:limit])

        end_line = start + len(text.splitlines()) - 1 if text else start

        byte_truncated = input_truncated or output_truncated

        truncation_reasons = self._read_file_truncation_reasons(
            input_truncated=input_truncated,
            output_truncated=output_truncated,
            line_truncated=line_truncated,
            line_window=line_window,
            total_lines=total_lines
        )

        return self._ok(
            f"workspace read ok path={self._rel(target)} bytes={min(size, limit)} truncated={byte_truncated}",
            path=self._rel(target),
            content=text,
            size=size,
            sha256=sha256,
            sha256_available=sha256_available,
            window_start=start,
            window_end=end_line,
            total_lines_estimated=total_lines,
            truncated=bool(truncation_reasons),
            byte_truncated=byte_truncated,
            line_truncated=line_truncated,
            truncation_reasons=truncation_reasons,
            recommended_next_steps=self._read_file_next_steps(
                path=self._rel(target),
                end_line=end_line,
                total_lines=total_lines,
                byte_truncated=byte_truncated,
                line_truncated=line_truncated
            )
        )

    def search(
        self,
        *,
        query: str | list[str],
        path: str = ".",
        glob: str | None = None,
        mode: str = "auto",
        case_sensitive: bool = False,
        context_before: int = 0,
        context_after: int = 0,
        max_matches: int = 100
    ) -> dict[str, typing.Any]:
        """统一搜索工作区路径、文本、正则和符号，并返回同构匹配结果。"""
        queries = self._normalize_search_queries(query)

        normalized_mode = str(mode or "auto").strip().lower()
        if normalized_mode not in {"auto", "text", "literal", "regex", "file", "symbol"}:
            normalized_mode = "auto"

        if not queries:
            return self._ok(
                "workspace search skipped: query empty",
                query="",
                queries=[],
                mode=normalized_mode,
                matches=[],
                match_count=0,
                skipped=True,
                reason="query_empty",
                suggested_next_action="read_known_file_or_provide_search_query"
            )

        base = self._resolve(path)
        if not base.exists():
            return self._fail("path_not_found", path=path)

        max_matches    = max(1, min(int(max_matches or 100), 1000))
        context_before = max(0, min(int(context_before or 0), 20))
        context_after  = max(0, min(int(context_after or 0), 20))

        matches: list[dict[str, typing.Any]] = []

        for needle in queries:
            if len(matches) >= max_matches:
                break
            if normalized_mode in {"auto", "file"}:
                self._search_files(
                    base=base,
                    query=needle,
                    glob=glob,
                    case_sensitive=case_sensitive,
                    max_matches=max_matches,
                    matches=matches
                )

            if normalized_mode in {"auto", "text", "literal", "regex"} and len(matches) < max_matches:
                self._search_file_contents(
                    base=base,
                    query=needle,
                    glob=glob,
                    regex_mode=normalized_mode == "regex",
                    case_sensitive=case_sensitive,
                    context_before=context_before,
                    context_after=context_after,
                    max_matches=max_matches,
                    matches=matches
                )

            if normalized_mode in {"auto", "symbol"} and len(matches) < max_matches:
                self._search_symbols(
                    path=self._rel(base),
                    query=needle,
                    glob=glob,
                    case_sensitive=case_sensitive,
                    max_matches=max_matches,
                    matches=matches
                )

        matches = self._rank_search_matches(
            self._dedupe_search_matches(matches),
            queries=queries
        )[:max_matches]

        primary_query = queries[0]

        return self._ok(
            f"workspace search ok mode={normalized_mode} matches={len(matches)}",
            query=primary_query,
            queries=queries,
            mode=normalized_mode,
            matches=matches,
            match_count=len(matches),
            truncated=len(matches) >= max_matches,
            recommended_next_steps=self._search_next_steps(
                queries=queries,
                path=self._rel(base),
                glob=glob,
                mode=normalized_mode,
                case_sensitive=case_sensitive,
                max_matches=max_matches,
                matches=matches,
                truncated=len(matches) >= max_matches
            )
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
        """创建或整体覆盖文本文件，支持父目录创建和 sha256 冲突保护。"""
        target  = self._resolve(path)
        payload = str(content or "")
        size    = len(payload.encode(const.CHARSET, const.IGNORE))

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
        """移动工作区内文件，支持覆盖控制、父目录创建和源文件 sha256 校验。"""
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
        sha     = self._sha256(payload)

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
        """复制工作区内文件，支持覆盖控制、父目录创建和源文件 sha256 校验。"""
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
        sha     = self._sha256(payload)

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
        """删除工作区内单个文件，删除前可按 sha256 校验当前内容。"""
        try:
            target = self._resolve(path)
        except ValueError as exc:
            return self._fail("path_outside_workspace", error=str(exc))

        if not target.exists():
            return self._fail("file_not_found", path=path)
        if not target.is_file():
            return self._fail("target_not_file", path=self._rel(target))

        payload = target.read_bytes()
        sha     = self._sha256(payload)

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

    def _search_files(
        self,
        *,
        base: typing.Any,
        query: str,
        glob: str | None,
        case_sensitive: bool,
        max_matches: int,
        matches: list[dict[str, typing.Any]]
    ) -> None:
        """搜索工作区相对路径和文件名。"""
        needle = query if case_sensitive else query.lower()

        for item in self._walk(base, recursive=True):
            if len(matches) >= max_matches:
                break
            if self._is_excluded(item):
                continue

            rel = self._rel(item)
            if glob and not fnmatch.fnmatch(rel, glob) and not fnmatch.fnmatch(item.name, glob):
                continue

            haystack = rel if case_sensitive else rel.lower()
            name     = item.name if case_sensitive else item.name.lower()

            if needle not in haystack and needle not in name:
                continue
            matches.append({
                "kind"      : "file",
                "query"     : query,
                "path"      : rel,
                "line"      : None,
                "text"      : rel,
                "file_kind" : "dir" if item.is_dir() else "file"
            })

    def _search_file_contents(
        self,
        *,
        base: typing.Any,
        query: str,
        glob: str | None,
        regex_mode: bool,
        case_sensitive: bool,
        context_before: int,
        context_after: int,
        max_matches: int,
        matches: list[dict[str, typing.Any]]
    ) -> None:
        """搜索文本内容，支持字面量、正则和上下文行。"""
        flags = 0 if case_sensitive else re.IGNORECASE

        try:
            regex = re.compile(query if regex_mode else re.escape(query), flags)
        except re.error as exc:
            matches.append({
                "kind"   : "error",
                "path"   : None,
                "line"   : None,
                "text"   : f"invalid regex: {exc}",
                "reason" : "invalid_regex"
            })
            return

        for item in self._walk(base, recursive=True):
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

            lines = self._decode(raw).splitlines()
            for index, line in enumerate(lines):
                if regex.search(line):
                    matches.append({
                        "kind"    : "text",
                        "query"   : query,
                        "path"    : rel,
                        "line"    : index + 1,
                        "text"    : clip_text(line.strip(), limit=500),
                        "context" : self._line_context(
                            lines,
                            index=index,
                            before=context_before,
                            after=context_after
                        )
                    })
                    if len(matches) >= max_matches:
                        break

    def _search_symbols(
        self,
        *,
        path: str,
        query: str,
        glob: str | None,
        case_sensitive: bool,
        max_matches: int,
        matches: list[dict[str, typing.Any]]
    ) -> None:
        """通过 repo map 的符号索引补充符号搜索结果。"""
        result = self._find_symbol(
            query=query,
            path=path,
            glob=glob,
            max_matches=max(1, max_matches - len(matches))
        )

        data = result.get("data") if isinstance(result, dict) else {}
        if not isinstance(data, dict) or not data.get("ok"):
            return

        needle = query if case_sensitive else query.lower()

        for item in data.get("matches") or []:
            if len(matches) >= max_matches:
                break

            name = str(item.get("name") or item.get("qualified_name") or "")

            haystack = name if case_sensitive else name.lower()
            if needle not in haystack:
                continue
            matches.append({
                "kind"   : "symbol",
                "query"  : query,
                "path"   : item.get("path"),
                "line"   : item.get("line"),
                "text"   : item.get("signature") or item.get("qualified_name") or item.get("name"),
                "symbol" : item
            })

    @staticmethod
    def _read_line_window(
        target: typing.Any,
        *,
        start_line: int,
        max_lines: int
    ) -> tuple[str, int | None, bool]:
        """读取指定行窗口，避免大文件整文件进入内存。"""
        selected: list[str] = []

        last_line       = 0
        stop_after      = start_line + max_lines - 1
        line_truncated = False

        with target.open("r", encoding=const.CHARSET, errors=const.IGNORE, newline=None) as fh:
            for lineno, line in enumerate(fh, start=1):
                last_line = lineno
                if lineno < start_line:
                    continue
                if lineno > stop_after:
                    line_truncated = True
                    break
                selected.append(line.rstrip("\r\n"))

        total_lines = None if line_truncated else last_line
        return "\n".join(selected), total_lines, line_truncated

    @staticmethod
    def _normalize_search_queries(
        query: str | list[str]
    ) -> list[str]:
        """把单个查询或多查询列表归一化为非空字符串列表。"""
        raw_items = query if isinstance(query, list) else [query]

        values: list[str] = []
        for item in raw_items:
            value = str(item or "").strip()
            if value and value not in values:
                values.append(value)

        return values[:20]

    @staticmethod
    def _dedupe_search_matches(
        matches: list[dict[str, typing.Any]]
    ) -> list[dict[str, typing.Any]]:
        """按稳定定位信息去除重复搜索结果。"""
        deduped: list[dict[str, typing.Any]] = []
        seen: set[tuple[str, str, int, str, str]] = set()

        for item in matches:
            if not isinstance(item, dict):
                continue
            key = (
                str(item.get("kind") or ""),
                str(item.get("path") or ""),
                int(item.get("line") or 0),
                str(item.get("query") or ""),
                str(item.get("text") or "")
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)

        return deduped

    @staticmethod
    def _rank_search_matches(
        matches: list[dict[str, typing.Any]],
        *,
        queries: list[str]
    ) -> list[dict[str, typing.Any]]:
        """把更可能有用的文件名、符号和精确命中排到前面。"""
        lowered_queries = [item.lower() for item in queries if item]
        kind_rank = {
            "file"   : 0,
            "symbol" : 1,
            "text"   : 2,
            "error"  : 9
        }

        def rank(item: dict[str, typing.Any]) -> tuple[int, int, int, int, str, int]:
            kind = str(item.get("kind") or "")
            path = str(item.get("path") or "")
            name = path.replace("\\", "/").rsplit("/", 1)[-1].lower()

            exact_name  = 0 if any(query == name for query in lowered_queries) else 1
            starts_name = 0 if any(name.startswith(query) for query in lowered_queries) else 1

            line = int(item.get("line") or 0)

            return (
                kind_rank.get(kind, 8),
                exact_name,
                starts_name,
                len(path),
                path,
                line if line > 0 else 1_000_000
            )

        return sorted(matches, key=rank)

    @staticmethod
    def _line_context(
        lines: list[str],
        *,
        index: int,
        before: int,
        after: int
    ) -> list[dict[str, typing.Any]]:
        """生成命中行前后的简短上下文。"""
        if before <= 0 and after <= 0:
            return []

        start = max(0, index - before)
        stop  = min(len(lines), index + after + 1)

        return [
            {
                "line"  : line_index + 1,
                "text"  : clip_text(lines[line_index].strip(), limit=500),
                "match" : line_index == index
            }
            for line_index in range(start, stop)
        ]

    @staticmethod
    def _read_file_next_steps(
        *,
        path: str,
        end_line: int,
        total_lines: int | None,
        byte_truncated: bool,
        line_truncated: bool
    ) -> list[dict[str, typing.Any]]:
        """为被截断的文件读取结果生成继续读取下一段的建议。"""
        steps: list[dict[str, typing.Any]] = []

        has_known_next_line    = isinstance(total_lines, int) and end_line < total_lines
        has_possible_next_line = line_truncated or (byte_truncated and total_lines is None)

        if has_known_next_line or has_possible_next_line:
            steps.append({
                "tool"   : "workspace_read_file",
                "args"   : {"path": path, "start_line": end_line + 1, "max_lines": 200},
                "reason" : "continue_from_next_line"
            })

        return steps

    @staticmethod
    def _read_file_truncation_reasons(
        *,
        input_truncated: bool,
        output_truncated: bool,
        line_truncated: bool,
        line_window: bool,
        total_lines: int | None
    ) -> list[str]:
        """返回文件读取被截断的具体原因。"""
        reasons: list[str] = []
        if input_truncated and not line_window:
            reasons.append("byte_limit")
        elif input_truncated and line_window and total_lines is None:
            reasons.append("large_file_window")
        if output_truncated:
            reasons.append("output_byte_limit")
        if line_truncated:
            reasons.append("line_window")
        return reasons

    @staticmethod
    def _search_next_steps(
        *,
        queries: list[str],
        path: str,
        glob: str | None,
        mode: str,
        case_sensitive: bool,
        max_matches: int,
        matches: list[dict[str, typing.Any]],
        truncated: bool
    ) -> list[dict[str, typing.Any]]:
        """根据搜索结果生成继续定位、读取窗口和扩大/缩小范围建议。"""
        steps: list[dict[str, typing.Any]] = []

        read_items: list[dict[str, typing.Any]] = []
        seen_reads: set[tuple[str, int]] = set()

        ranked = WorkspaceTools._rank_search_matches(
            WorkspaceTools._dedupe_search_matches(matches),
            queries=queries
        )
        for item in ranked:
            if not isinstance(item, dict):
                continue
            item_path = str(item.get("path") or "").strip()
            line = item.get("line")
            if not item_path or not isinstance(line, int):
                continue
            start_line = max(1, line - 20)
            key = (item_path, start_line)
            if key in seen_reads:
                continue
            seen_reads.add(key)
            read_items.append({
                "tool"   : "workspace_read_file",
                "args"   : {"path": item_path, "start_line": start_line, "max_lines": 60},
                "reason" : "read_result_window"
            })
            if len(read_items) >= 8:
                break

        steps.extend(read_items[:5])
        if len(read_items) > 1:
            steps.append({
                "tool"   : "native_parallel_read",
                "args"   : {"items": read_items[:8]},
                "reason" : "read_multiple_candidate_windows"
            })

        if truncated:
            steps.append({
                "tool": "workspace_search",
                "args": {
                    "query": queries,
                    "path": path,
                    "glob": glob,
                    "mode": mode,
                    "case_sensitive": case_sensitive,
                    "max_matches": min(max_matches * 2, 1000)
                },
                "reason": "increase_limit"
            })

        if mode != "file":
            steps.append({
                "tool": "workspace_search",
                "args": {
                    "query": queries,
                    "path": path,
                    "glob": glob,
                    "mode": "file",
                    "case_sensitive": case_sensitive,
                    "max_matches": min(max_matches, 200)
                },
                "reason": "check_file_path_matches"
            })

        if mode != "symbol":
            steps.append({
                "tool": "workspace_search",
                "args": {
                    "query": queries,
                    "path": path,
                    "glob": glob,
                    "mode": "symbol",
                    "case_sensitive": case_sensitive,
                    "max_matches": min(max_matches, 200)
                },
                "reason": "check_symbol_matches"
            })

        if not glob:
            likely_glob = WorkspaceTools._suggest_search_glob(matches)
            if likely_glob:
                steps.append({
                    "tool": "workspace_search",
                    "args": {
                        "query": queries,
                        "path": path,
                        "glob": likely_glob,
                        "mode": mode,
                        "case_sensitive": case_sensitive,
                        "max_matches": max_matches
                    },
                    "reason": "narrow_search_scope"
                })

        return steps[:12]

    @staticmethod
    def _suggest_search_glob(
        matches: list[dict[str, typing.Any]]
    ) -> str | None:
        """根据命中文件扩展名建议一个更窄的 glob。"""
        suffix_counts: dict[str, int] = {}
        for item in matches:
            path = str(item.get("path") or "")
            if "." not in path:
                continue
            suffix = "." + path.rsplit(".", 1)[-1]
            suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1

        if not suffix_counts:
            return None

        suffix = max(suffix_counts, key=suffix_counts.get)
        return f"**/*{suffix}"


if __name__ == '__main__':
    pass
