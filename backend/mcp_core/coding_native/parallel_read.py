# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
import asyncio
from backend.mcp_core.coding_native.base import NativeCodingComponent


class ParallelReadCore(typing.Protocol):

    def workspace_root(self) -> dict[str, typing.Any]:
        ...

    def list_file(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        ...

    def read_file(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        ...

    def search(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        ...


class ParallelReadTools(NativeCodingComponent):
    """并行读取工作区上下文的只读组合工具。"""

    ALLOWED_TOOLS = {
        "workspace_root",
        "workspace_list_file",
        "workspace_read_file",
        "workspace_search"
    }

    MAX_ITEMS       = 12
    MAX_CONCURRENCY = 4

    def _normalize_items(
        self,
        items: list[dict[str, typing.Any]]
    ) -> list[dict[str, typing.Any]]:
        if not isinstance(items, list):
            return []

        normalized: list[dict[str, typing.Any]] = []
        for index, item in enumerate(items[:self.MAX_ITEMS]):
            if not isinstance(item, dict):
                normalized.append({"index": index, "tool": "", "args": {}})
                continue

            tool = str(item.get("tool") or "").strip()
            args = item.get("args") if isinstance(item.get("args"), dict) else {}
            normalized.append({"index": index, "tool": tool, "args": args})

        return normalized

    async def parallel_read(
        self,
        items: list[dict[str, typing.Any]]
    ) -> dict[str, typing.Any]:
        normalized = self._normalize_items(items)
        core       = typing.cast(ParallelReadCore, typing.cast(object, self.core))
        semaphore  = asyncio.Semaphore(self.MAX_CONCURRENCY)

        async def run_item(item: dict[str, typing.Any]) -> dict[str, typing.Any]:
            index = int(item.get("index") or 0)
            tool  = str(item.get("tool") or "").strip()
            args  = item.get("args") if isinstance(item.get("args"), dict) else {}

            try:
                async with semaphore:
                    if tool == "workspace_root":
                        result = await asyncio.to_thread(core.workspace_root)
                    elif tool == "workspace_list_file":
                        result = await asyncio.to_thread(core.list_file, **args)
                    elif tool == "workspace_read_file":
                        result = await asyncio.to_thread(core.read_file, **args)
                    elif tool == "workspace_search":
                        result = await asyncio.to_thread(core.search, **args)
                    else:
                        result = self._fail("tool_not_allowed", tool=tool)
            except Exception as exc:
                result = self._fail(
                    "parallel_read_item_failed",
                    tool=tool,
                    error=f"{type(exc).__name__}: {exc}"
                )

            return {
                "index"  : index,
                "tool"   : tool,
                "args"   : args,
                "ok"     : bool((result.get("data") or {}).get("ok")) if isinstance(result, dict) else False,
                "result" : result
            }

        results: list[dict[str, typing.Any]] = list(await asyncio.gather(
            *(run_item(item) for item in normalized),
            return_exceptions=False
        ))
        results.sort(key=lambda item: int(item.get("index") or 0))

        ok_count        = sum(1 for item in results if item.get("ok"))
        fail_count      = len(results) - ok_count
        requested_count = len(items) if isinstance(items, list) else 0
        dropped_count   = max(0, requested_count - len(normalized))
        truncated       = dropped_count > 0
        failures        = self._parallel_read_failures(results)
        failure_reasons = self._parallel_read_failure_reasons(results)

        recommended_next_steps = self._parallel_read_next_steps(
            original_items=items or [],
            results=results,
            truncated=truncated,
            failures=failures
        )

        return self._ok(
            (
                f"native parallel read ok total={len(results)} "
                f"ok={ok_count} fail={fail_count} truncated={truncated}"
            ),
            requested_count=requested_count,
            total=len(results),
            ok_count=ok_count,
            fail_count=fail_count,
            failure_reasons=failure_reasons,
            failures=failures,
            max_items=self.MAX_ITEMS,
            max_concurrency=self.MAX_CONCURRENCY,
            dropped_count=dropped_count,
            truncated=truncated,
            recommended_next_steps=recommended_next_steps,
            results=results
        )

    @staticmethod
    def _parallel_read_item_reason(
        item: dict[str, typing.Any]
    ) -> str:
        result = item.get("result") if isinstance(item, dict) else None

        data = result.get("data") if isinstance(result, dict) else None
        if isinstance(data, dict):
            reason = str(data.get("reason") or "").strip()
            if reason:
                return reason

        return "item_failed"

    def _parallel_read_failure_reasons(
        self,
        results: list[dict[str, typing.Any]]
    ) -> dict[str, int]:
        reasons: dict[str, int] = {}

        for item in results:
            if item.get("ok"):
                continue
            reason = self._parallel_read_item_reason(item)
            reasons[reason] = reasons.get(reason, 0) + 1

        return reasons

    def _parallel_read_failures(
        self,
        results: list[dict[str, typing.Any]]
    ) -> list[dict[str, typing.Any]]:
        failures: list[dict[str, typing.Any]] = []
        for item in results:
            if item.get("ok"):
                continue
            failures.append({
                "index"  : item.get("index"),
                "tool"   : item.get("tool"),
                "args"   : item.get("args") if isinstance(item.get("args"), dict) else {},
                "reason" : self._parallel_read_item_reason(item)
            })
        return failures

    def _parallel_read_next_steps(
        self,
        *,
        original_items: list[dict[str, typing.Any]],
        results: list[dict[str, typing.Any]],
        truncated: bool,
        failures: list[dict[str, typing.Any]]
    ) -> list[dict[str, typing.Any]]:
        steps: list[dict[str, typing.Any]] = []
        if truncated:
            steps.append({
                "tool"   : "native_parallel_read",
                "args"   : {"items": original_items[self.MAX_ITEMS:self.MAX_ITEMS * 2]},
                "reason" : "continue_remaining_items"
            })

        if failures:
            steps.append({
                "tool"   : "native_parallel_read",
                "args"   : {"items": [
                    {"tool": item.get("tool"), "args": item.get("args") or {}}
                    for item in failures[:self.MAX_ITEMS]
                ]},
                "reason" : "retry_failed_items_after_fixing_inputs"
            })

        for item in results:
            result = item.get("result") if isinstance(item, dict) else None
            data = result.get("data") if isinstance(result, dict) else None
            if not isinstance(data, dict):
                continue
            if not data.get("truncated") and not data.get("recommended_next_steps"):
                continue
            for step in data.get("recommended_next_steps") or []:
                if isinstance(step, dict):
                    steps.append({
                        "source_index": item.get("index"),
                        **step
                    })
        return steps


if __name__ == '__main__':
    pass
