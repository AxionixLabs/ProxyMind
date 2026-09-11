from pathlib import Path

import pytest

from infrastructure.config.schema import (
    ConfigValidationError,
    config_override,
)
from infrastructure.config.session import ConfigSession
from infrastructure.config.store import ConfigStore


@pytest.mark.parametrize("mode", ["session", "current"])
def test_resume_directory_strategy_round_trips(tmp_path: Path, mode: str) -> None:
    session = ConfigSession(ConfigStore(tmp_path / "config.toml"))
    assert "resume_cwd" not in session.load()["tui"]
    session.update_user({("tui", "resume_cwd"): mode})
    assert session.load()["tui"]["resume_cwd"] == mode
    session.delete_user([("tui", "resume_cwd")])
    assert "resume_cwd" not in session.load()["tui"]


@pytest.mark.parametrize("value", ["ask", "", True, None, 3, {}])
def test_resume_directory_strategy_rejects_invalid_values(value) -> None:
    with pytest.raises(ConfigValidationError, match="resume_cwd"):
        config_override(("tui", "resume_cwd"), value)


def test_config_workspace_commit_keeps_launch_directory(tmp_path: Path) -> None:
    launch = tmp_path / "launch"
    target = tmp_path / "target"
    launch.mkdir()
    target.mkdir()
    session = ConfigSession(
        ConfigStore(tmp_path / "config.toml"), workspace=launch, directory_override=True,
    )
    target_resolution = session.resolve(workspace=target)
    assert target_resolution.project_trust.trust_root == target
    assert session.resolve().project_trust.trust_root == launch
    session.bind_workspace(target)
    assert session.resolve().project_trust.trust_root == target
    assert session.launch_directory == launch
    assert session.directory_override
