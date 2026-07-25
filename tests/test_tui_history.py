# -*- coding: utf-8 -*-

import pytest

from mind_app.tui.features import history


@pytest.mark.anyio
async def test_history_menu_displays_date_then_query(monkeypatch) -> None:
    record = {
        "cid": "conversation-id",
        "title": "explain the current architecture",
        "workspace": r"D:\PycharmProjects\ProxyMind",
        "updated_at": 1,
    }
    requests = []

    class Runtime(object):
        async def select_menu(self, request):
            requests.append(request)
            return request.options[0].value

    monkeypatch.setattr(
        history,
        "_format_updated_at",
        lambda _value: "07-21 14:30",
    )

    selected = await history.choose_history_session(Runtime(), [record])

    assert selected is record
    option = requests[0].options[0]
    assert option.label == "07-21 14:30"
    assert option.detail == "explain the current architecture"


@pytest.mark.anyio
async def test_history_menu_can_include_workspace(monkeypatch) -> None:
    record = {
        "cid": "conversation-id",
        "title": "continue the task",
        "workspace": r"D:\PycharmProjects\ProxyMind",
        "updated_at": 1,
    }
    requests = []

    class Runtime(object):
        async def select_menu(self, request):
            requests.append(request)
            return request.options[0].value

    monkeypatch.setattr(
        history,
        "_format_updated_at",
        lambda _value: "07-21 14:30",
    )

    await history.choose_history_session(
        Runtime(),
        [record],
        show_workspace=True,
    )

    assert requests[0].options[0].detail == (
        r"continue the task · D:\PycharmProjects\ProxyMind"
    )
