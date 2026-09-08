# -*- coding: utf-8 -*-

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_npm_update_state_uses_state_home(
    tmp_path: Path,
    repository_root: Path,
) -> None:
    module_path = (
        repository_root
        / "npm"
        / "packages"
        / "mind"
        / "lib"
        / "paths.js"
    )
    config_root = tmp_path / "config"
    state_root = tmp_path / "state"
    environment = {
        **os.environ,
        "MIND_HOME": str(config_root),
        "MIND_STATE_HOME": str(state_root),
    }
    script = """
const paths = await import(process.argv[1]);
console.log(JSON.stringify([paths.mindStateHome(), paths.versionPath()]));
"""

    result = subprocess.run(
        ["node", "--input-type=module", "--eval", script, module_path.as_uri()],
        cwd=module_path.parents[4],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert json.loads(result.stdout) == [
        str(state_root),
        str(state_root / "version.json"),
    ]
