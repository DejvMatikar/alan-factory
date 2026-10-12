from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alan import default_profile, engine_config, server
from alan.engine_config import RepoIdentity
from alan.roots import STATE_ROOT_SUBDIRS
from tests.fakes.forge import FakeForge

REPO_ROOT = Path(__file__).resolve().parents[1]
_PLACEHOLDER = RepoIdentity(owner="unconfigured.invalid", repo="unconfigured")
_PROBE = (
    "import json, sys\n"
    "from pathlib import Path\n"
    "import alan.default_profile as profile\n"
    "profile.OWNER_FILE = Path(sys.argv[1])\n"
    "import alan.server\n"
    "import alan.engine_config as engine_config\n"
    "default = engine_config.get_registered()\n"
    "print(json.dumps({\n"
    "    'source': profile.resolved().source.value,\n"
    "    'default': default.repo.slug,\n"
    "    'forge_target': engine_config.DEFAULT_FORGE_TARGET.repo.slug,\n"
    "    'posture': default.posture.value,\n"
    "    'ceiling': engine_config.file_ceiling(default.repo).value,\n"
    "    'ceiling_source': engine_config.ceiling_source(default.repo),\n"
    "    'forge': default.forge is None,\n"
    "}))\n"
)


@pytest.fixture
def unconfigured_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RepoIdentity:
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "engines"))
    resolved = default_profile.resolve(tmp_path / "no-profile", tmp_path / "no-owner.yaml")
    monkeypatch.setattr(default_profile, "resolved", lambda: resolved)
    default = engine_config.default_engine_config()
    engine_config.register(default, default=True, validate_env=False)
    engine_config.set_default_ceiling({default.repo: default.posture})
    return default.repo


def test_an_unconfigured_install_boots_and_serves_the_placeholder_default(
    unconfigured_default: RepoIdentity,
) -> None:
    with TestClient(server.app) as client:
        health = client.get("/api/health")
        engines = client.get("/api/engines").json()["engines"]
    assert unconfigured_default == _PLACEHOLDER
    assert health.status_code == 200
    assert [engine["repo"] for engine in engines] == [
        {"owner": "unconfigured.invalid", "repo": "unconfigured"}
    ]
    assert engines[0]["posture"] == "observe"
    assert engines[0]["forge"] is None
    assert engines[0]["code_quality"] is None
    assert engines[0]["ci"] is None


def test_an_unconfigured_install_lists_the_placeholder_as_its_default_key(
    unconfigured_default: RepoIdentity,
) -> None:
    with TestClient(server.app) as client:
        listing = client.get("/api/engines").json()
    assert unconfigured_default == _PLACEHOLDER
    assert listing["default_engine_key"] == _PLACEHOLDER.slug


def test_an_unconfigured_install_reports_an_observe_ceiling_for_its_default(
    unconfigured_default: RepoIdentity,
) -> None:
    with TestClient(server.app) as client:
        sources = client.get("/api/engines/sources").json()["sources"]
    assert [(s["slug"], s["posture_ceiling"]) for s in sources] == [
        ("unconfigured.invalid/unconfigured", "observe")
    ]
    assert unconfigured_default == _PLACEHOLDER


def test_an_unconfigured_install_boot_makes_no_forge_call(
    unconfigured_default: RepoIdentity, _active_fake_forge: FakeForge
) -> None:
    with TestClient(server.app) as client:
        client.get("/api/health")
        client.get("/api/engines")
        client.get("/api/engines/sources")
    assert unconfigured_default == _PLACEHOLDER
    assert _active_fake_forge.calls == []


def _fresh_env(home: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ALAN_", "COV_CORE_")) and key != "PYTHONHASHSEED"
    }
    env["HOME"] = str(home)
    for token in STATE_ROOT_SUBDIRS:
        root = home / token.lower()
        root.mkdir()
        env[token] = str(root)
    return env


def test_a_fresh_process_with_no_profile_and_no_owner_file_imports_the_whole_app_unconfigured(
    tmp_path: Path,
) -> None:
    try:
        done = subprocess.run(
            [sys.executable, "-c", _PROBE, str(tmp_path / "no-owner.yaml")],
            cwd=REPO_ROOT,
            env=_fresh_env(tmp_path),
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        pytest.fail(exc.stderr)
    assert json.loads(done.stdout.splitlines()[-1]) == {
        "source": "unconfigured",
        "default": "unconfigured.invalid/unconfigured",
        "forge_target": "unconfigured.invalid/unconfigured",
        "posture": "observe",
        "ceiling": "observe",
        "ceiling_source": "default",
        "forge": True,
    }
