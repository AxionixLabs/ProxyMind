import pytest

from mind_app.client_tools.registry import default_registry
from mind_core.config import (
    ConfigValidationError,
    normalize_config,
    parse_config_override,
)
from mind_core.config_session import ConfigSession
from mind_core.config_store import ConfigStore
from mind_core.feature_config import FeatureSettings


def test_feature_settings_use_normalized_defaults() -> None:
    config = normalize_config({})

    assert config["features"] == {
        "js_repl": True,
        "subagents": True,
    }
    assert FeatureSettings.from_config(config) == FeatureSettings()


def test_feature_settings_read_explicit_config() -> None:
    config = normalize_config({
        "features": {
            "js_repl": False,
            "subagents": False,
        },
    })

    assert FeatureSettings.from_config(config) == FeatureSettings(
        js_repl=False,
        subagents=False,
    )


@pytest.mark.parametrize(
    ("features", "message"),
    [
        ({"js_repl": 1}, "features.js_repl must be a boolean"),
        ({"subagents": "yes"}, "features.subagents must be a boolean"),
        ({"unknown": True}, "unknown features key"),
    ],
)
def test_invalid_feature_settings_are_rejected(features, message) -> None:
    with pytest.raises(ConfigValidationError, match=message):
        normalize_config({"features": features})


def test_js_repl_feature_removes_both_repl_tools(tmp_path) -> None:
    tools = default_registry(
        execution_root=tmp_path,
        features=FeatureSettings(js_repl=False),
    ).list_tools().tools
    names = {tool.name for tool in tools}

    assert "js_repl" not in names
    assert "js_repl_reset" not in names
    assert "shell_command" in names


def test_feature_switch_supports_temporary_cli_override(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    session = ConfigSession(
        store,
        (parse_config_override("features.js_repl=false"),),
    )

    resolution = session.resolve()

    assert resolution.config["features"]["js_repl"] is False
    assert store.read_raw()["features"]["js_repl"] is True
