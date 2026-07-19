# -*- coding: utf-8 -*-

import asyncio

from engine import manage


def test_check_update_reports_through_callback(monkeypatch) -> None:
    """服务管理器只通过显式回调报告可用更新。"""
    local = {"ok": True, "version": "1.0.0"}
    remote = {"version": "1.1.0", "notes": "fixes"}
    updates: list[tuple[dict, dict]] = []
    manager = manage.ServerManage.__new__(manage.ServerManage)
    manager.on_update = lambda current, latest: updates.append((current, latest))

    async def fake_probe(self):
        return local

    async def fake_manifest():
        return remote

    monkeypatch.setattr(manage.ServerManage, "probe_version", fake_probe)
    monkeypatch.setattr(manage.request, "fetch_manifest", fake_manifest)

    asyncio.run(manager.check_update())

    assert updates == [(local, remote)]
