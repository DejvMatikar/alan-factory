from __future__ import annotations

import sys
from pathlib import Path

from alan.deploy_refresh import CLEAN_EXCLUDES

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEPLOY_DIR = _REPO_ROOT / "deploy"
_SCRIPTS_DIR = _REPO_ROOT / "scripts"

sys.path.insert(0, str(_SCRIPTS_DIR))
import deploy_restart_decision  # type: ignore[import-not-found]  # noqa: E402


def test_run_alan_preserves_fd_limit() -> None:
    run_alan = (_DEPLOY_DIR / "run_alan.sh").read_text(encoding="utf-8")
    assert "ulimit -S -n 4096" in run_alan


def test_classifier_diff_disables_rename_detection() -> None:
    source = (_REPO_ROOT / "alan" / "deploy_restart.py").read_text(encoding="utf-8")
    assert "--no-renames" in source


def test_deploy_doc_safe_set_matches_classifier_literal() -> None:
    doc_text = (_REPO_ROOT / "docs" / "architecture" / "deploy.md").read_text(encoding="utf-8")
    for prefix in deploy_restart_decision.SAFE_DIR_PREFIXES:
        assert prefix in doc_text, f"deploy.md is missing safe-set prefix {prefix!r}"
    for suffix in deploy_restart_decision.SAFE_SUFFIXES:
        assert suffix in doc_text, f"deploy.md is missing safe-set suffix {suffix!r}"


def test_pull_latest_clean_line_excludes_dist_slots() -> None:
    assert "alan/web/dist*" in CLEAN_EXCLUDES
