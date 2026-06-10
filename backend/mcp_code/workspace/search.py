# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import re
import typing
import fnmatch
from backend.mcp_code.base import NativeCodingComponent
from backend.utilities.trace import clip_text


class WorkspaceSearchTools(NativeCodingComponent):

    def __init__(self, core, *, diagnostics, symbols):
        super().__init__(core)
        self._diagnostics = diagnostics
        self._symbols     = symbols

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
        deduped: list[dict[str, typing.Any]]      = []
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
    def _attach_search_read_windows(
        matches: list[dict[str, typing.Any]]
    ) -> None:
        """给可定位的搜索命中补充直接可执行的读取窗口参数。"""
        for item in matches:
            if not isinstance(item, dict):
                continue

            item_path = str(item.get("path") or "").strip()
            line      = item.get("line")

            if not item_path or not isinstance(line, int):
                continue
            start_line = max(1, line - 20)
            item["read_window"] = {
                "tool": "workspace_read_file",
                "args": {"path": item_path, "start_line": start_line, "max_lines": 60},
                "reason": "read_result_window"
            }

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
    def _search_next_steps(
        *,
        queries: list[str],
        path: str,
        glob: str | None,
        mode: str,
        case_sensitive: bool,
        max_matches: int,
        matches: list[dict[str, typing.Any]],
        truncated: bool,
        coverage_diagnostics: dict[str, typing.Any] | None = None
    ) -> list[dict[str, typing.Any]]:
        """根据搜索结果生成继续定位、读取窗口和扩大/缩小范围建议。"""
        steps: list[dict[str, typing.Any]] = []

        read_items: list[dict[str, typing.Any]] = []
        seen_reads: set[tuple[str, int]]        = set()

        ranked = WorkspaceSearchTools._rank_search_matches(
            WorkspaceSearchTools._dedupe_search_matches(matches),
            queries=queries
        )
        for item in ranked:
            if not isinstance(item, dict):
                continue

            item_path = str(item.get("path") or "").strip()
            line      = item.get("line")

            if not item_path or not isinstance(line, int):
                continue
            start_line = max(1, line - 20)
            key = (item_path, start_line)
            if key in seen_reads:
                continue
            seen_reads.add(key)
            read_items.append({
                "tool": "workspace_read_file",
                "args": {"path": item_path, "start_line": start_line, "max_lines": 60},
                "reason": "read_result_window"
            })
            if len(read_items) >= 8:
                break

        steps.extend(read_items[:5])
        if len(read_items) > 1:
            steps.append({
                "tool": "native_parallel_read",
                "args": {"items": read_items[:8]},
                "reason": "read_multiple_candidate_windows"
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

        if isinstance(coverage_diagnostics, dict):
            for item in coverage_diagnostics.get("recommended_next_steps") or []:
                if isinstance(item, dict):
                    steps.append(item)

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
            likely_glob = WorkspaceSearchTools._suggest_search_glob(matches)
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
            return self.ok_result(
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

        base = self.resolve_path(path)
        if not base.exists():
            return self.fail_result("path_not_found", path=path)

        max_matches    = max(1, min(int(max_matches or 100), 1000))
        context_before = max(0, min(int(context_before or 0), 20))
        context_after  = max(0, min(int(context_after or 0), 20))

        matches: list[dict[str, typing.Any]] = []

        search_diagnostics: dict[str, typing.Any] = {
            "files_scanned": 0,
            "large_files_truncated" : [],
            "large_files_truncated_count": 0,
            "skipped_large_tail_count": 0,
            "search_byte_limit": self.max_read_bytes
        }

        symbol_metadata: dict[str, typing.Any] = {
            "indexed_files": [],
            "skipped_files": [],
            "truncated_files": [],
            "supported_languages": [],
            "parser_level": None,
            "parser_limitations": [],
            "references": [],
            "call_candidates": [],
            "reference_search_truncated_files": []
        }

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
                    matches=matches,
                    diagnostics=search_diagnostics
                )

            if normalized_mode in {"auto", "symbol"} and len(matches) < max_matches:
                self._search_symbols(
                    path=self.relative_path(base),
                    query=needle,
                    glob=glob,
                    case_sensitive=case_sensitive,
                    max_matches=max_matches,
                    matches=matches,
                    metadata=symbol_metadata
                )

        matches = self._rank_search_matches(
            self._dedupe_search_matches(matches),
            queries=queries
        )[:max_matches]
        self._attach_search_read_windows(matches)

        coverage_diagnostics = self._diagnostics.search_coverage_diagnostics(
            base=base,
            glob=glob,
            text_search_enabled=normalized_mode in {"auto", "text", "literal", "regex", "symbol"},
            content_diagnostics=search_diagnostics,
            symbol_metadata=symbol_metadata
        )

        search_diagnostics["large_files_truncated_count"] = len(search_diagnostics["large_files_truncated"])
        search_diagnostics["skipped_large_tail_count"]    = len(search_diagnostics["large_files_truncated"])
        search_diagnostics["coverage"]                    = coverage_diagnostics

        primary_query = queries[0]
        search_truncated = (
            len(matches) >= max_matches
            or bool(search_diagnostics["large_files_truncated"])
            or bool(symbol_metadata.get("truncated_files"))
            or bool(symbol_metadata.get("reference_search_truncated_files"))
            or bool(coverage_diagnostics.get("incomplete"))
        )

        return self.ok_result(
            f"workspace search ok mode={normalized_mode} matches={len(matches)}",
            query=primary_query,
            queries=queries,
            mode=normalized_mode,
            matches=matches,
            match_count=len(matches),
            truncated=search_truncated,
            search_diagnostics=search_diagnostics,
            coverage_diagnostics=coverage_diagnostics,
            large_files_truncated=search_diagnostics["large_files_truncated"],
            large_files_truncated_count=search_diagnostics["large_files_truncated_count"],
            skipped_large_tail_count=search_diagnostics["skipped_large_tail_count"],
            symbol_search=symbol_metadata if normalized_mode in {"auto", "symbol"} else None,
            parser_level=symbol_metadata.get("parser_level") if normalized_mode in {"auto", "symbol"} else None,
            parser_limitations=symbol_metadata.get("parser_limitations") if normalized_mode in {"auto", "symbol"} else [],
            indexed_files=symbol_metadata["indexed_files"] if normalized_mode in {"auto", "symbol"} else [],
            skipped_files=symbol_metadata["skipped_files"] if normalized_mode in {"auto", "symbol"} else [],
            truncated_files=symbol_metadata["truncated_files"] if normalized_mode in {"auto", "symbol"} else [],
            indexed_file_count=symbol_metadata.get("indexed_file_count", 0) if normalized_mode in {"auto", "symbol"} else 0,
            skipped_file_count=symbol_metadata.get("skipped_file_count", 0) if normalized_mode in {"auto", "symbol"} else 0,
            truncated_file_count=symbol_metadata.get("truncated_file_count", 0) if normalized_mode in {"auto", "symbol"} else 0,
            references=symbol_metadata["references"] if normalized_mode in {"auto", "symbol"} else [],
            call_candidates=symbol_metadata["call_candidates"] if normalized_mode in {"auto", "symbol"} else [],
            reference_count=symbol_metadata.get("reference_count", 0) if normalized_mode in {"auto", "symbol"} else 0,
            call_candidate_count=symbol_metadata.get("call_candidate_count", 0) if normalized_mode in {"auto", "symbol"} else 0,
            reference_search_truncated_files=symbol_metadata["reference_search_truncated_files"] if normalized_mode in {"auto", "symbol"} else [],
            reference_search_truncated_file_count=symbol_metadata.get("reference_search_truncated_file_count", 0) if normalized_mode in {"auto", "symbol"} else 0,
            recommended_next_steps=self._search_next_steps(
                queries=queries,
                path=self.relative_path(base),
                glob=glob,
                mode=normalized_mode,
                case_sensitive=case_sensitive,
                max_matches=max_matches,
                matches=matches,
                truncated=search_truncated,
                coverage_diagnostics=coverage_diagnostics
            )
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

        for item in self.walk_paths(base, recursive=True):
            if len(matches) >= max_matches:
                break
            if self.is_excluded_path(item):
                continue

            rel = self.relative_path(item)
            if glob and not fnmatch.fnmatch(rel, glob) and not fnmatch.fnmatch(item.name, glob):
                continue

            haystack = rel if case_sensitive else rel.lower()
            name     = item.name if case_sensitive else item.name.lower()

            if needle not in haystack and needle not in name:
                continue
            matches.append({
                "kind": "file",
                "query": query,
                "path": rel,
                "line": None,
                "text": rel,
                "file_kind": "dir" if item.is_dir() else "file"
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
        matches: list[dict[str, typing.Any]],
        diagnostics: dict[str, typing.Any] | None = None
    ) -> None:
        """搜索文本内容，支持字面量、正则和上下文行。"""
        flags = 0 if case_sensitive else re.IGNORECASE
        diag  = diagnostics if isinstance(diagnostics, dict) else {}

        try:
            regex = re.compile(query if regex_mode else re.escape(query), flags)
        except re.error as exc:
            matches.append({
                "kind": "error",
                "path": None,
                "line": None,
                "text": f"invalid regex: {exc}",
                "reason": "invalid_regex"
            })
            return

        for item in self.walk_paths(base, recursive=True):
            if len(matches) >= max_matches:
                break
            if not item.is_file() or self.is_excluded_path(item) or not self.looks_text(item):
                continue

            rel = self.relative_path(item)
            if glob and not fnmatch.fnmatch(rel, glob) and not fnmatch.fnmatch(item.name, glob):
                continue

            try:
                size = item.stat().st_size
                raw = item.read_bytes()[:self.max_read_bytes]
            except OSError:
                continue
            diag["files_scanned"] = int(diag.get("files_scanned") or 0) + 1
            if size > self.max_read_bytes:
                large_files = diag.setdefault("large_files_truncated", [])
                if isinstance(large_files, list) and not any(entry.get("path") == rel for entry in large_files if isinstance(entry, dict)):
                    large_files.append({
                        "path": rel,
                        "size": size,
                        "searched_bytes": min(size, self.max_read_bytes),
                        "unsearched_bytes": max(0, size - self.max_read_bytes)
                    })

            lines = self.decode_bytes(raw).splitlines()
            for index, line in enumerate(lines):
                if regex.search(line):
                    matches.append({
                        "kind": "text",
                        "query": query,
                        "path": rel,
                        "line": index + 1,
                        "text": clip_text(line.strip(), limit=500),
                        "searched_bytes": min(size, self.max_read_bytes),
                        "file_size": size,
                        "search_truncated": size > self.max_read_bytes,
                        "context": self._line_context(
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
        matches: list[dict[str, typing.Any]],
        metadata: dict[str, typing.Any]
    ) -> None:
        """通过内部符号索引补充符号搜索结果。"""
        if self._symbols is None:
            return

        result = self._symbols.find_symbol(
            query=query,
            path=path,
            glob=glob,
            max_matches=max(1, max_matches - len(matches))
        )

        data = result.get("data") if isinstance(result, dict) else {}
        if not isinstance(data, dict) or not data.get("ok"):
            return

        self._diagnostics.merge_symbol_search_metadata(metadata, data)

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


if __name__ == '__main__':
    pass
