import pytest

from mind import create_native_coding
from infrastructure.mcp.local_tool_registry import ToolRegistry
from mind_app.builtin_tools.permissions import permission_tools
from mind_app.client_tools.factory import default_registry
from infrastructure.config.schema import (
    ConfigValidationError,
    normalize_config,
    parse_config_override,
)
from infrastructure.config.session import ConfigSession
from infrastructure.config.store import ConfigStore
from infrastructure.platform.images import FileImageReader
from agent.application.config.settings import FeatureSettings


def test_feature_settings_use_normalized_defaults() -> None:
    config = normalize_config({})

    assert config["features"] == {
        "js_repl": False,
        "subagents": False,
        "exec_permission_approvals": False,
        "request_permissions_tool": False,
    }
    assert FeatureSettings.from_config(config) == FeatureSettings()


def test_feature_settings_read_explicit_config() -> None:
    config = normalize_config({
        "features": {
            "js_repl": False,
            "subagents": False,
            "exec_permission_approvals": False,
            "request_permissions_tool": False,
        },
    })

    assert FeatureSettings.from_config(config) == FeatureSettings(
        js_repl=False,
        subagents=False,
        exec_permission_approvals=False,
        request_permissions_tool=False,
    )


def test_optional_tool_features_can_be_enabled_explicitly() -> None:
    config = normalize_config({
        "features": {
            "js_repl": True,
            "subagents": True,
        },
    })

    assert FeatureSettings.from_config(config).js_repl is True
    assert FeatureSettings.from_config(config).subagents is True


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
        create_native_coding(root=tmp_path, application_layout=None),
        image_reader=FileImageReader(tmp_path),
        features=FeatureSettings(js_repl=False),
    ).list_tools().tools
    names = {tool.name for tool in tools}

    assert "js_repl" not in names
    assert "js_repl_reset" not in names
    assert "shell_command" in names


def test_permission_features_control_tool_surface(tmp_path) -> None:
    tools = default_registry(
        create_native_coding(root=tmp_path, application_layout=None),
        image_reader=FileImageReader(tmp_path),
        features=FeatureSettings(
            request_permissions_tool=False,
            exec_permission_approvals=False,
        ),
    ).list_tools().tools
    by_name = {tool.name: tool for tool in tools}

    assert "request_permissions" not in by_name
    assert "with_additional_permissions" not in by_name["shell_command"].inputSchema[
        "properties"
    ]["sandbox_permissions"]["enum"]
    assert "additional_permissions" not in by_name["exec_command"].inputSchema[
        "properties"
    ]


def test_permission_features_are_disabled_by_default(tmp_path) -> None:
    names = {
        tool.name
        for tool in default_registry(
            create_native_coding(root=tmp_path, application_layout=None),
            image_reader=FileImageReader(tmp_path),
        ).list_tools().tools
    }
    assert "request_permissions" not in names


def test_permission_features_can_be_enabled_explicitly(tmp_path) -> None:
    tools = default_registry(
        create_native_coding(root=tmp_path, application_layout=None),
        image_reader=FileImageReader(tmp_path),
        features=FeatureSettings(
            request_permissions_tool=True,
            exec_permission_approvals=True,
        ),
    ).list_tools().tools
    by_name = {tool.name: tool for tool in tools}

    assert "request_permissions" not in by_name
    assert "with_additional_permissions" in by_name["shell_command"].inputSchema[
        "properties"
    ]["sandbox_permissions"]["enum"]

    builtin_tools = ToolRegistry(permission_tools(None)).list_tools().tools
    assert [tool.name for tool in builtin_tools] == ["request_permissions"]


def test_feature_switch_supports_temporary_cli_override(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    session = ConfigSession(
        store,
        (parse_config_override("features.js_repl=false"),),
    )

    resolution = session.resolve()

    assert resolution.config["features"]["js_repl"] is False
    assert store.read_raw()["features"]["js_repl"] is False
