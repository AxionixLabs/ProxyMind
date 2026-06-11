# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from backend.mcp_code.base import (
    NativeCodingBase, NativeCodingComponent
)


class WorkspaceSearchTools(NativeCodingComponent):
    """提供结构化符号搜索能力。"""

    def __init__(
        self,
        core: NativeCodingBase,
        *,
        diagnostics: typing.Any,
        symbols: typing.Any
    ) -> None:
        """保存共享运行时上下文和搜索诊断依赖。"""
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
        """把更可能有用的符号精确命中排到前面。"""
        lowered_queries = [item.lower() for item in queries if item]

        def rank(item: dict[str, typing.Any]) -> tuple[int, int, int, int, str, int]:
            """返回搜索结果排序键。"""
            path   = str(item.get("path") or "")
            symbol = item.get("symbol") if isinstance(item.get("symbol"), dict) else {}

            name = str(
                item.get("text")
                or symbol.get("qualified_name")
                or symbol.get("name")
                or ""
            ).lower()

            exact_name  = 0 if any(query == name for query in lowered_queries) else 1
            starts_name = 0 if any(name.startswith(query) for query in lowered_queries) else 1

            line = int(item.get("line") or 0)

            return (
                0,
                exact_name,
                starts_name,
                len(path),
                path,
                line if line > 0 else 1_000_000
            )

        return sorted(matches, key=rank)

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

    def search(
        self,
        *,
        query: str | list[str],
        path: str = ".",
        glob: str | None = None,
        case_sensitive: bool = False,
        max_matches: int = 100
    ) -> dict[str, typing.Any]:
        """搜索工作区符号，并返回结构化匹配、引用和调用候选。"""
        queries = self._normalize_search_queries(query)

        if not queries:
            return self.ok_result(
                "workspace search skipped: query empty",
                query="",
                queries=[],
                matches=[],
                match_count=0,
                skipped=True,
                reason="query_empty"
            )

        base = self.resolve_path(path)
        if not base.exists():
            return self.fail_result("path_not_found", path=path)

        max_matches    = max(1, min(int(max_matches or 100), 1000))

        matches: list[dict[str, typing.Any]] = []

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

        coverage_diagnostics = self._diagnostics.search_coverage_diagnostics(
            base=base,
            glob=glob,
            symbol_metadata=symbol_metadata
        )

        primary_query = queries[0]
        search_truncated = (
            len(matches) >= max_matches
            or bool(symbol_metadata.get("truncated_files"))
            or bool(symbol_metadata.get("reference_search_truncated_files"))
            or bool(coverage_diagnostics.get("incomplete"))
        )

        return self.ok_result(
            f"workspace search ok matches={len(matches)}",
            query=primary_query,
            queries=queries,
            matches=matches,
            match_count=len(matches),
            truncated=search_truncated,
            coverage_diagnostics=coverage_diagnostics,
            symbol_search=symbol_metadata,
            parser_level=symbol_metadata.get("parser_level"),
            parser_limitations=symbol_metadata.get("parser_limitations") or [],
            indexed_files=symbol_metadata["indexed_files"],
            skipped_files=symbol_metadata["skipped_files"],
            truncated_files=symbol_metadata["truncated_files"],
            indexed_file_count=symbol_metadata.get("indexed_file_count", 0),
            skipped_file_count=symbol_metadata.get("skipped_file_count", 0),
            truncated_file_count=symbol_metadata.get("truncated_file_count", 0),
            references=symbol_metadata["references"],
            call_candidates=symbol_metadata["call_candidates"],
            reference_count=symbol_metadata.get("reference_count", 0),
            call_candidate_count=symbol_metadata.get("call_candidate_count", 0),
            reference_search_truncated_files=symbol_metadata["reference_search_truncated_files"],
            reference_search_truncated_file_count=symbol_metadata.get("reference_search_truncated_file_count", 0)
        )


if __name__ == '__main__':
    pass
