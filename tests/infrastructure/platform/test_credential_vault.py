# -*- coding: utf-8 -*-

import traceback

import pytest
from unittest.mock import (
    Mock,
    patch,
)

from keyring.errors import KeyringError

from agent.domain.mcp_oauth import McpOAuthStorageError
from infrastructure.platform.credential_vault import (
    SystemCredentialVault,
    _SystemBackend,
)


@pytest.mark.parametrize("operation", ["read", "write", "delete"])
def test_backend_errors_are_sanitized_without_exception_chaining(operation: str) -> None:
    backend = Mock(spec=("get_password", "set_password", "delete_password"))
    backend.get_password.side_effect = KeyringError("access-secret refresh-secret code=secret")
    with patch("infrastructure.platform.credential_vault._system_backend", return_value=_SystemBackend(backend, (KeyringError,))):
        vault = SystemCredentialVault()
        with pytest.raises(McpOAuthStorageError) as raised:
            if operation == "read":
                vault.read("key")
            elif operation == "write":
                vault.write_new("key", "secret")
            else:
                vault.delete("key")
    assert raised.value.code == "storage_unavailable"
    rendered = "".join(traceback.format_exception(raised.value))
    assert "access-secret" not in rendered and "refresh-secret" not in rendered
    assert "code=secret" not in rendered


def test_backend_missing_delete_is_idempotent_and_existing_write_is_rejected() -> None:
    backend = Mock(spec=("get_password", "set_password", "delete_password"))
    with patch("infrastructure.platform.credential_vault._system_backend", return_value=_SystemBackend(backend, (KeyringError,))):
        vault = SystemCredentialVault()
        backend.get_password.return_value = None
        vault.delete("key")
        backend.delete_password.assert_not_called()
        backend.get_password.return_value = "previous"
        with pytest.raises(McpOAuthStorageError) as raised:
            vault.write_new("key", "new")
        assert raised.value.code == "credential_conflict"
        backend.set_password.assert_not_called()


def test_backend_reports_write_that_did_not_persist() -> None:
    backend = Mock(spec=("get_password", "set_password", "delete_password"))
    backend.get_password.return_value = None
    with patch("infrastructure.platform.credential_vault._system_backend", return_value=_SystemBackend(backend, (KeyringError,))):
        with pytest.raises(McpOAuthStorageError):
            SystemCredentialVault().write_new("key", "secret")


def test_backend_delete_failure_is_not_mistaken_for_missing() -> None:
    backend = Mock(spec=("get_password", "set_password", "delete_password"))
    backend.get_password.return_value = "secret"
    backend.delete_password.side_effect = KeyringError("deletion secret failure")
    with patch("infrastructure.platform.credential_vault._system_backend", return_value=_SystemBackend(backend, (KeyringError,))):
        with pytest.raises(McpOAuthStorageError):
            SystemCredentialVault().delete("key")


def test_unsupported_platform_does_not_invoke_keyring_discovery() -> None:
    with patch("infrastructure.platform.credential_vault.sys.platform", "unsupported"), patch("keyring.get_keyring") as discovery:
        with pytest.raises(McpOAuthStorageError):
            SystemCredentialVault().read("key")
    discovery.assert_not_called()
