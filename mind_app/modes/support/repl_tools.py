# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections import defaultdict
from mind_core.design import Design
from mind_nova.modes import RunMode

GROUP_DISPLAY_LIMIT = 12


def summarize_tool_groups(
    tools: list[dict[str, typing.Any]],
) -> list[dict[str, typing.Any]]:
    """按 external/server 或 domain/class 汇总工具列表。"""
    grouped: dict[tuple[str, str, str], list[str]] = defaultdict(list)

    for tool in tools:
        name = str(tool.get("name") or "").strip() if isinstance(tool, dict) else ""
        if not name:
            continue

        meta = tool.get("meta") if isinstance(tool.get("meta"), dict) else {}
        if bool(meta.get("external")):
            label     = str(meta.get("server") or "external").strip() or "external"
            transport = str(meta.get("transport") or "external").strip() or "external"
            key       = ("external", label, transport)
        else:
            domain = str(meta.get("domain") or "local").strip() or "local"
            cls    = str(meta.get("class") or "tool").strip() or "tool"
            key    = ("local", domain, cls)

        grouped[key].append(name)

    result: list[dict[str, typing.Any]] = []
    for (source, label, detail), names in grouped.items():
        result.append({
            "source" : source,
            "label"  : label,
            "detail" : detail,
            "tools"  : sorted(names)
        })

    return sorted(
        result,
        key=lambda item: (
            0 if item["source"] == "external" else 1,
            str(item["label"]),
            str(item["detail"])
        )
    )


def render_tools_summary(
    *,
    mode: RunMode,
    tools: list[dict[str, typing.Any]],
    limit: int = GROUP_DISPLAY_LIMIT
) -> None:
    """打印当前会话可见工具摘要。"""
    groups = summarize_tool_groups(tools)
    total  = sum(len(item["tools"]) for item in groups)

    external_total = sum(
        len(item["tools"]) for item in groups
        if item["source"] == "external"
    )

    Design.console.print(
        f"[bold #AFC7D8]Tools[/] "
        f"[dim #7F8C9A]· mode={mode} total={total} external={external_total}[/]"
    )

    if not groups:
        Design.console.print("[bold #7F8C9A]No visible tools.[/]")
        Design.console.print()
        return None

    for group in groups:
        names  = group["tools"]
        label  = group["label"]
        detail = group["detail"]
        source = group["source"]
        marker = "external" if source == "external" else "local"

        Design.console.print(
            f"[bold #F4F7FA]{label}[/] "
            f"[dim #7F8C9A]({marker} · {detail} · {len(names)})[/]"
        )
        for name in names[:limit]:
            Design.console.print(f"[bold #AFC7D8]  •[/] [#DDE7EF]{name}[/]")
        if len(names) > limit:
            Design.console.print(f"[#7F8C9A]  ... and {len(names) - limit} more[/]")

    Design.console.print()
    return None


if __name__ == '__main__':
    pass
