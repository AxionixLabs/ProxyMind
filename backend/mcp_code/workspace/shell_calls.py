# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
import asyncio
from backend.mcp_code.base import NativeCodingComponent


class ShellCallCore(typing.Protocol):
    """描述并行 shell 调用依赖的接口。"""

    async def shell_command(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """执行受控 shell 命令。"""
        ...


class ShellCallTools(NativeCodingComponent):
    """并行执行多个 shell 调用。"""

    MAX_ITEMS       = 12
    MAX_CONCURRENCY = 4

    def _normalize_items(
        self,
        items: list[dict[str, typing.Any]]
    ) -> list[dict[str, typing.Any]]:
        """归一化批量 shell 请求，限制数量并保留原始顺序索引。"""
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

    async def shell_calls(
        self,
        items: list[dict[str, typing.Any]]
    ) -> dict[str, typing.Any]:
        """并发执行允许的 shell 调用，并返回有序结果。"""
        normalized = self._normalize_items(items)
        core       = typing.cast(ShellCallCore, typing.cast(object, self.core))
        semaphore  = asyncio.Semaphore(self.MAX_CONCURRENCY)

        async def run_item(item: dict[str, typing.Any]) -> dict[str, typing.Any]:
            """执行单个并行 shell 调用并保留原始索引。"""
            index = int(item.get("index") or 0)
            tool  = str(item.get("tool") or "").strip()
            args  = item.get("args") if isinstance(item.get("args"), dict) else {}

            try:
                async with semaphore:
                    if tool == "shell_command":
                        result = await core.shell_command(**args)
                    else:
                        result = {
                            "text"        : "shell call item not allowed",
                            "attachments" : [],
                            "data"        : {"ok": False, "tool": tool, "error": "tool_not_allowed"},
                            "logs"        : []
                        }
            except Exception as exc:
                result = {
                    "text": "shell call item error",
                    "attachments": [],
                    "data": {
                        "ok": False,
                        "tool": tool,
                        "error": f"{type(exc).__name__}: {exc}"
                    },
                    "logs": []
                }

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

        return self.ok_result(
            (
                f"shell calls ok total={len(results)} "
                f"ok={ok_count} fail={fail_count} truncated={truncated}"
            ),
            requested_count=requested_count,
            total=len(results),
            ok_count=ok_count,
            fail_count=fail_count,
            max_items=self.MAX_ITEMS,
            max_concurrency=self.MAX_CONCURRENCY,
            dropped_count=dropped_count,
            truncated=truncated,
            results=results
        )


if __name__ == '__main__':
    pass
