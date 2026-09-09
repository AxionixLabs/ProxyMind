import httpx
import pytest

from infrastructure.config.preferences import config_to_preferences
from infrastructure.config.schema import (
    ConfigValidationError,
    normalize_config,
)
from infrastructure.config.session import ConfigSession
from infrastructure.config.store import ConfigStore
from infrastructure.services.configuration_host.app import create_app
from infrastructure.services.configuration_host.storage import (
    create_provider,
    set_active_provider,
    update_provider,
)
from protocol.client.compact import build_compact_payload
from protocol.client.payload import build_chat_payload


@pytest.mark.anyio
async def test_profile_context_settings_reach_both_chat_and_compact_requests(tmp_path):
    session = ConfigSession(ConfigStore(tmp_path / "config.toml"))
    create_provider(session, {
        "id": "model-a", "name": "Model A", "kind": "openai", "model": "model-a",
        "model_context_window": 128000, "model_auto_compact_token_limit": 90000,
    })
    set_active_provider(session, "model-a")
    pref = config_to_preferences(session.load())
    chat = await build_chat_payload(pref, "hello", [], session_mode="create")
    compact = build_compact_payload({"cid": "cid-1", "sid": "sid-1", "llm_conf": pref})
    assert chat["llm_conf"] == compact["llm_conf"]
    primary = chat["llm_conf"]["primary"]
    assert primary["model_context_window"] == 128000
    assert primary["model_auto_compact_token_limit"] == 90000
    result = update_provider(session, "model-a", {
        "model_context_window": 64000,
        "model_auto_compact_token_limit": None,
    })
    profile = next(item for item in result["providers"] if item["id"] == "model-a")
    assert "model_auto_compact_token_limit" not in profile
    assert profile["model_context_window"] == 64000
    assert "model_auto_compact_token_limit" not in session.store.read_raw()["model_providers"]["model-a"]


@pytest.mark.anyio
@pytest.mark.parametrize("window", [None, 1050000])
async def test_preferences_html_payload_can_save_with_unset_optional_context_fields(tmp_path, window):
    session = ConfigSession(ConfigStore(tmp_path / "config.toml"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(session)), base_url="http://test",
    ) as client:
        profile = {
            "id": "OpenAI", "name": "OpenAI", "kind": "openai", "model": "gpt-test",
            "route": "responses", "reasoning_effort": "medium", "api_key": "",
            "clear_api_key": False, "base_url": "",
        }
        response = await client.post("/api/pref/providers", json=profile)
        assert response.status_code == 200
        payload = {
            **profile, "model_context_window": window, "model_auto_compact_token_limit": None,
        }
        for _ in range(2):
            response = await client.patch("/api/pref/providers/OpenAI", json=payload)
            assert response.status_code == 200, response.text
        response = await client.get("/api/pref")
        saved_profile = next(item for item in response.json()["data"]["providers"] if item["id"] == "OpenAI")
        stored = session.store.read_raw()["model_providers"]["OpenAI"]
        for result in (saved_profile, stored):
            assert "model_auto_compact_token_limit" not in result
            if window is None:
                assert "model_context_window" not in result
            else:
                assert result["model_context_window"] == window


@pytest.mark.anyio
async def test_preferences_http_rejects_invalid_policy_without_mutating_profile(tmp_path):
    session = ConfigSession(ConfigStore(tmp_path / "config.toml"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(session)), base_url="http://test",
    ) as client:
        response = await client.post("/api/pref/providers", json={
            "id": "model-a", "name": "Model A", "kind": "openai", "model": "model-a",
            "model_context_window": 128000, "model_auto_compact_token_limit": 90000,
        })
        assert response.status_code == 200
        saved = session.store.read_raw()
        response = await client.patch("/api/pref/providers/model-a", json={
            "model_context_window": 64000,
        })
        assert response.status_code == 400
        assert session.store.read_raw() == saved
        response = await client.patch("/api/pref/providers/model-a", json={
            "model_context_window": 64000, "model_auto_compact_token_limit": None,
        })
        assert response.status_code == 200
        profile = next(item for item in response.json()["data"]["providers"] if item["id"] == "model-a")
        assert profile["model_context_window"] == 64000
        assert "model_auto_compact_token_limit" not in profile


@pytest.mark.parametrize("field", ["model_context_window", "model_auto_compact_token_limit"])
@pytest.mark.parametrize("value", [0, -1, True, 12.5, "128000"])
def test_invalid_profile_context_setting_is_rejected(field, value):
    with pytest.raises(ConfigValidationError, match=field):
        normalize_config({"model_providers": {"test": {field: value}}})


def test_threshold_cannot_exceed_configured_window():
    with pytest.raises(ConfigValidationError, match="smaller"):
        normalize_config({"model_providers": {"test": {
            "model_context_window": 64000, "model_auto_compact_token_limit": 90000,
        }}})
