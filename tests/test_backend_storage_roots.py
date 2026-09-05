# -*- coding: utf-8 -*-

from backend.utilities.storage.roots import (
    CONFIG_HOME_ENV,
    HX_HOME_ENV,
    STATE_HOME_ENV,
    helix_home,
)


def test_helix_home_uses_state_home_before_config_home(tmp_path) -> None:
    config_root = tmp_path / "config"
    state_root = tmp_path / "state"
    environment = {
        CONFIG_HOME_ENV: str(config_root),
        STATE_HOME_ENV: str(state_root),
    }

    assert helix_home(
        environment=environment,
        user_home=tmp_path,
    ) == state_root / "helix"


def test_explicit_helix_home_overrides_state_home(tmp_path) -> None:
    state_root = tmp_path / "state"
    explicit_root = tmp_path / "helix-explicit"
    environment = {
        STATE_HOME_ENV: str(state_root),
        HX_HOME_ENV: str(explicit_root),
    }

    assert helix_home(
        environment=environment,
        user_home=tmp_path,
    ) == explicit_root
