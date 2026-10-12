from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEPLOY_DIR = _REPO_ROOT / "deploy"
_SCRIPTS_DIR = _REPO_ROOT / "scripts"

_HOME_LITERAL_RE = re.compile("/" + "Users" + "/[A-Za-z0-9_.-]+")

_SCANNED_ASSETS: tuple[Path, ...] = (
    _DEPLOY_DIR / "ai.alan.plist",
    _DEPLOY_DIR / "render_plist.sh",
    _DEPLOY_DIR / "run_alan.sh",
    _DEPLOY_DIR / "ai.alan.update.plist",
    _DEPLOY_DIR / "render_update_plist.sh",
    _DEPLOY_DIR / "update_runner.sh",
    _SCRIPTS_DIR / "deploy_restart_decision.py",
    _SCRIPTS_DIR / "swap_web_dist.py",
    _SCRIPTS_DIR / "deploy_refresh.py",
    _REPO_ROOT / "setup.sh",
    _SCRIPTS_DIR / "setup_macos.sh",
)


def test_no_absolute_home_literal_in_deploy_assets() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _SCANNED_ASSETS:
        matches = _HOME_LITERAL_RE.findall(path.read_text(encoding="utf-8"))
        if matches:
            offenders[str(path.relative_to(_REPO_ROOT))] = sorted(set(matches))
    assert not offenders, f"absolute home literal(s) found: {offenders}"


def test_setup_scripts_are_in_the_scanned_assets() -> None:
    assert _REPO_ROOT / "setup.sh" in _SCANNED_ASSETS
    assert _SCRIPTS_DIR / "setup_macos.sh" in _SCANNED_ASSETS


def test_the_home_literal_detector_matches_an_absolute_home_path() -> None:
    sample = "ROOT=" + "/" + "Users" + "/someone/checkout"
    assert _HOME_LITERAL_RE.findall(sample) == ["/" + "Users" + "/someone"]
