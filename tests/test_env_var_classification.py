from __future__ import annotations

import re
from pathlib import Path

from alan.roots import ALL_KNOWN_ROOTS
from tests.env_var_classification import _VALID_REASONS, EXCEPTED_CONFIG, ISOLATION_VARS

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ALAN_SRC_ROOT = _REPO_ROOT / "alan"
_ENV_VAR_RE = re.compile(r"(?<![A-Z0-9_])ALAN_[A-Z0-9_]+")


def _discover_env_vars(root: Path = _ALAN_SRC_ROOT) -> frozenset[str]:
    found: set[str] = set()
    for path in root.rglob("*.py"):
        found.update(_ENV_VAR_RE.findall(path.read_text(encoding="utf-8")))
    return frozenset(found)


def _unclassified(discovered: frozenset[str]) -> frozenset[str]:
    return discovered - (ISOLATION_VARS | EXCEPTED_CONFIG.keys())


def _stale(discovered: frozenset[str]) -> frozenset[str]:
    return (ISOLATION_VARS | EXCEPTED_CONFIG.keys()) - discovered


def test_every_discovered_env_var_is_classified() -> None:
    unclassified = _unclassified(_discover_env_vars())
    assert not unclassified, (
        f"classify this var as isolation or excepted-config: {sorted(unclassified)}"
    )


def test_no_stale_classified_vars() -> None:
    stale = _stale(_discover_env_vars())
    assert not stale, (
        f"var(s) classified but no longer read by alan/ source — remove from "
        f"tests/env_var_classification.py: {sorted(stale)}"
    )


def test_isolation_and_excepted_config_do_not_overlap() -> None:
    overlap = ISOLATION_VARS & EXCEPTED_CONFIG.keys()
    assert not overlap, (
        f"var(s) classified as both isolation and excepted-config: {sorted(overlap)}"
    )


def test_excepted_config_reason_is_categorical() -> None:
    invalid = {
        name: reason for name, reason in EXCEPTED_CONFIG.items() if reason not in _VALID_REASONS
    }
    assert not invalid, f"non-categorical reason(s), pick from {sorted(_VALID_REASONS)}: {invalid}"


def test_classification_bucket_counts_pinned() -> None:
    assert len(ISOLATION_VARS) == 18, (
        "ISOLATION_VARS grew or shrank past the pinned count (18) — review the "
        "added/removed isolation var against docs/architecture/ci_isolation.md "
        "before updating this literal"
    )
    assert len(EXCEPTED_CONFIG) == 62, (
        "EXCEPTED_CONFIG grew or shrank past the pinned count (62) — a new "
        "ALAN_* config var needs a named one-line reason in "
        "tests/env_var_classification.py before updating this literal"
    )


def test_discovery_regex_excludes_leading_underscore_constants() -> None:
    text = '_ALAN_PKG = "alan/"\n_ALAN_SUBDIR = ".alan"\n'
    assert _ENV_VAR_RE.findall(text) == []


def test_discovery_regex_captures_digits() -> None:
    text = '_HEALTH_FIXTURE_ENV = "ALAN_E2E_HEALTH_FIXTURE"\n'
    assert _ENV_VAR_RE.findall(text) == ["ALAN_E2E_HEALTH_FIXTURE"]


def test_unclassified_var_fails_with_registration_message(tmp_path: Path) -> None:
    synthetic = tmp_path / "synthetic.py"
    synthetic.write_text('_NEW_VAR_ENV = "ALAN_TOTALLY_NEW_VAR"\n', encoding="utf-8")
    discovered = _discover_env_vars(tmp_path)
    assert "ALAN_TOTALLY_NEW_VAR" in discovered
    unclassified = _unclassified(discovered)
    message = f"classify this var as isolation or excepted-config: {sorted(unclassified)}"
    assert "ALAN_TOTALLY_NEW_VAR" in message
    assert "classify this var" in message


def test_isolation_vars_superset_of_known_roots() -> None:
    assert ALL_KNOWN_ROOTS <= ISOLATION_VARS
