from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from alan import base_ref
from alan.handover import TestScenario
from alan.teams._env import float_env, positive_int_env

_E2E_DIR = "alan/web/e2e"
_EXCLUDED_PREFIXES = (
    f"{_E2E_DIR}/mobile-audit/",
    f"{_E2E_DIR}/capture/",
    f"{_E2E_DIR}/desktop-audit/",
)
_MAX_SPECS_ENV = "ALAN_CHANGED_E2E_MAX_SPECS"
_DEFAULT_MAX_SPECS = 10
_TIMEOUT_ENV = "ALAN_CHANGED_E2E_TIMEOUT_S"
_DEFAULT_TIMEOUT_S = 900.0
_GIT_TIMEOUT_S = 30.0
_REMOVED_TESTID_RE = re.compile(r'^-.*data-testid=[{"\'`]+([\w./-]{3,})', re.MULTILINE)

CHANGED_E2E_SCENARIO_ID = "changed-e2e-specs"


@dataclass(frozen=True)
class ChangedE2ESelection:
    scenario: TestScenario | None
    base_unresolved: bool


def changed_e2e_timeout_s() -> float:
    return float_env(_TIMEOUT_ENV, _DEFAULT_TIMEOUT_S)


def _max_specs() -> int:
    return positive_int_env(_MAX_SPECS_ENV, _DEFAULT_MAX_SPECS)


def _is_runnable_spec(path: str, checkout: Path) -> bool:
    if not path.startswith(f"{_E2E_DIR}/") or not path.endswith(".spec.ts"):
        return False
    if path.startswith(_EXCLUDED_PREFIXES):
        return False
    return (checkout / path).is_file()


def _run_git(args: list[str], checkout: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=checkout,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def local_diff_files(checkout: Path, base: str) -> frozenset[str]:
    out = _run_git(["diff", "--name-only", f"{base}...HEAD"], checkout)
    if out is None:
        return frozenset()
    return frozenset(filter(None, map(str.strip, out.splitlines())))


def _removed_testids(checkout: Path, base: str) -> frozenset[str]:
    out = _run_git(["diff", f"{base}...HEAD", "--", "alan/web/src"], checkout)
    if out is None:
        return frozenset()
    removed_tokens = set(_REMOVED_TESTID_RE.findall(out))
    added_lines = "\n".join(
        line for line in out.splitlines() if line.startswith("+")
    )
    return frozenset(t for t in removed_tokens if t not in added_lines)


def changed_e2e_specs(diff_files: frozenset[str], checkout: Path) -> tuple[str, ...]:
    return tuple(
        sorted(f for f in diff_files if _is_runnable_spec(f, checkout))
    )


def testid_referencing_specs(checkout: Path, base: str) -> tuple[str, ...]:
    tokens = _removed_testids(checkout, base)
    if not tokens:
        return ()
    hits: list[str] = []
    e2e_dir = checkout / _E2E_DIR
    if not e2e_dir.is_dir():
        return ()
    for spec in sorted(e2e_dir.glob("*.spec.ts")):
        rel = f"{_E2E_DIR}/{spec.name}"
        text = spec.read_text(encoding="utf-8", errors="replace")
        if any(tok in text for tok in tokens):
            hits.append(rel)
    return tuple(hits)


def changed_e2e_scenario(specs: tuple[str, ...]) -> TestScenario | None:
    if not specs:
        return None
    capped = specs[: _max_specs()]
    truncated = len(specs) - len(capped)
    paths = " ".join(s.removeprefix("alan/web/") for s in capped)
    note = f" ({truncated} more spec file(s) over the cap not run)" if truncated else ""
    return TestScenario(
        id=CHANGED_E2E_SCENARIO_ID,
        description=(
            "Playwright spec files this diff adds, edits, or breaks by removing "
            f"a data-testid they assert on{note}"
        ),
        given="The PR branch is checked out in the grounded verification checkout",
        when=f"npm --prefix alan/web run e2e -- {paths}",
        then="",
    )


def build_changed_e2e_scenario(
    checkout: Path, *, env: Mapping[str, str] | None = None
) -> ChangedE2ESelection:
    base = base_ref.resolve(checkout, env=env)
    if base is None:
        return ChangedE2ESelection(scenario=None, base_unresolved=True)
    diff_files = local_diff_files(checkout, base)
    specs = changed_e2e_specs(diff_files, checkout)
    extra = tuple(
        s for s in testid_referencing_specs(checkout, base) if s not in specs
    )
    return ChangedE2ESelection(
        scenario=changed_e2e_scenario(specs + extra), base_unresolved=False
    )
