# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from dataclasses import replace
from ...contracts.menu import MenuRequest


def request_for_tab(
    request: MenuRequest,
    preferred_tab_id: str | None = None
) -> MenuRequest:
    """返回应用指定或默认页签内容后的菜单请求。"""
    if not request.tabs:
        return replace(request, active_tab_id=None)
    target_id = preferred_tab_id or request.active_tab_id
    tab = next(
        (tab for tab in request.tabs if tab.tab_id == target_id),
        request.tabs[0],
    )
    footer_hint = (
        tab.footer_hint
        if tab.footer_hint is not None
        else request.footer_hint
    )
    return replace(
        request,
        options=tab.options,
        active_tab_id=tab.tab_id,
        footer_hint=footer_hint,
    )


def switched_tab_request(
    request: MenuRequest,
    *,
    step: int,
    base_footer_hint: str,
) -> MenuRequest | None:
    """返回按方向循环切换页签后的请求。"""
    tabs = request.tabs
    if len(tabs) < 2:
        return None
    current = next(
        (
            index
            for index, tab in enumerate(tabs)
            if tab.tab_id == request.active_tab_id
        ),
        0,
    )
    target = tabs[(current + (1 if step >= 0 else -1)) % len(tabs)]
    return request_for_tab(
        replace(request, footer_hint=base_footer_hint),
        target.tab_id,
    )


if __name__ == '__main__':
    pass
