from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI

from alan.routes.infra import mount_ui
from tests.env_var_classification import EXCEPTED_CONFIG, ISOLATION_VARS

REPO_ROOT = Path(__file__).resolve().parents[1]
DUMP_SCHEMA = "import sys; from alan.server import openapi_json; sys.stdout.write(openapi_json())"


def _schema_under(env: dict[str, str]) -> str:
    result = subprocess.run(  # noqa: S603  # nosec B603
        [sys.executable, "-c", DUMP_SCHEMA],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _clean_env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ALAN_")}
    env["HOME"] = str(home)
    return env


def _every_alan_var_set(home: Path) -> dict[str, str]:
    env = _clean_env(home)
    for name in sorted(ISOLATION_VARS):
        env[name] = str(home / name.lower())
    for name in sorted(EXCEPTED_CONFIG):
        env[name] = "1"
    return env


def test_ui_mount_leaves_the_openapi_schema_unchanged(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    bare = FastAPI(title="alan")
    mounted = FastAPI(title="alan")
    assert mount_ui(mounted, tmp_path) is True
    assert mounted.openapi() == bare.openapi()


def test_schema_is_identical_with_every_alan_variable_unset_and_set(tmp_path: Path) -> None:
    unset_home = tmp_path / "unset"
    set_home = tmp_path / "set"
    unset_home.mkdir()
    set_home.mkdir()
    unset_schema = _schema_under(_clean_env(unset_home))
    set_schema = _schema_under(_every_alan_var_set(set_home))
    assert '"paths"' in unset_schema
    assert set_schema == unset_schema
