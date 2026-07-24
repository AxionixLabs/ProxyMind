# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
from ..core.models import (
    MenuOption,
    MenuRequest
)

if typing.TYPE_CHECKING:
    from ..core.runtime import TuiRuntime


async def choose_history_session(
    runtime: "TuiRuntime",
    records: list[dict[str, typing.Any]],
) -> dict[str, typing.Any] | None:
    """在主 TUI 中选择一项历史会话。"""
    if not records:
        return None

    return await runtime.select_menu(MenuRequest(
        title="Resume conversation",
        status=f"items={len(records)}",
        help_text="Up/Down select · PgUp/PgDn jump · Enter resume · Esc/q cancel",
        options=tuple(
            MenuOption(
                value=record,
                label=_record_prefix(record),
                detail=_record_title(record),
            )
            for record in records
        ),
    ))


def _record_title(record: dict[str, typing.Any]) -> str:
    """返回历史会话的展示标题。"""
    title = str(record.get("title") or "").strip()
    if not title:
        title = str(record.get("cid") or "-").strip()
    return title


def _record_prefix(record: dict[str, typing.Any]) -> str:
    """返回历史会话的更新时间。"""
    return _format_updated_at(record.get("updated_at"))


def _format_updated_at(value: typing.Any) -> str:
    """格式化毫秒时间戳。"""
    try:
        timestamp = int(value) / 1000
    except (TypeError, ValueError):
        return "-"
    return time.strftime("%m-%d %H:%M", time.localtime(timestamp))


if __name__ == '__main__':
    pass
