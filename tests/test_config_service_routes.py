# -*- coding: utf-8 -*-

from mind_core.config_session import ConfigSession
from mind_core.config_store import ConfigStore
from server.app import create_app


def test_config_service_exposes_only_active_pages(tmp_path) -> None:
    session = ConfigSession(ConfigStore(tmp_path / "config.toml"))
    app = create_app(session)

    paths = {route.path for route in app.routes}

    assert {"/", "/pref", "/agent"} <= paths
    assert "/code" not in paths
