from __future__ import annotations

import sys

import pytest

from alan import process_runner

PROBE_PREFIX = "PROCESS_RUNNER_PROBE_"
LISTING = (
    "import os, sys; "
    f"sys.stdout.write(','.join(sorted(k for k in os.environ if k.startswith('{PROBE_PREFIX}'))))"
)


def listed(env: dict[str, str | None] | None) -> list[str]:
    result = process_runner.run_subprocess(
        [sys.executable, "-c", LISTING], cwd=None, timeout=60, env=env
    )
    assert result.returncode == 0
    return [name for name in result.stdout.split(",") if name]


@pytest.fixture(autouse=True)
def _inherited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(f"{PROBE_PREFIX}KEEP", "1")
    monkeypatch.setenv(f"{PROBE_PREFIX}DROP", "1")


def test_a_run_without_an_environment_inherits_every_variable() -> None:
    assert listed(None) == [f"{PROBE_PREFIX}DROP", f"{PROBE_PREFIX}KEEP"]


def test_an_environment_overlay_is_merged_over_the_inherited_variables() -> None:
    assert listed({f"{PROBE_PREFIX}ADD": "1"}) == [
        f"{PROBE_PREFIX}ADD", f"{PROBE_PREFIX}DROP", f"{PROBE_PREFIX}KEEP",
    ]


def test_a_none_valued_entry_removes_the_inherited_variable_from_the_child() -> None:
    assert listed({f"{PROBE_PREFIX}DROP": None}) == [f"{PROBE_PREFIX}KEEP"]


def test_an_empty_valued_entry_keeps_the_variable_set_in_the_child() -> None:
    assert listed({f"{PROBE_PREFIX}DROP": ""}) == [f"{PROBE_PREFIX}DROP", f"{PROBE_PREFIX}KEEP"]
