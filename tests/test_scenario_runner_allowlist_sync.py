from __future__ import annotations

import re
from typing import get_args

from alan.handover import ScenarioOutcome
from alan.teams._base import BASE_ALLOWED_TOOLS
from alan.teams._scenario_runner import _ALLOWED_COMMAND_PREFIXES, ScenarioStatus

_BASH_RE = re.compile(r"^Bash\((.+?):\*\)$")


def _base_bash_prefixes() -> set[tuple[str, ...]]:
    prefixes: set[tuple[str, ...]] = set()
    for tool in BASE_ALLOWED_TOOLS:
        m = _BASH_RE.match(tool)
        if m:
            cmd = m.group(1)
            prefixes.add(tuple(cmd.split()))
    return prefixes


def test_allowed_command_prefixes_matches_base_allowed_tools() -> None:
    expected = _base_bash_prefixes()
    actual = set(_ALLOWED_COMMAND_PREFIXES)
    assert actual == expected, (
        "_ALLOWED_COMMAND_PREFIXES out of sync with BASE_ALLOWED_TOOLS.\n"
        f"  In _scenario_runner but not BASE: {actual - expected}\n"
        f"  In BASE but not _scenario_runner: {expected - actual}"
    )


def test_scenario_outcome_status_matches_scenario_status() -> None:
    assert set(get_args(ScenarioOutcome.model_fields["status"].annotation)) == set(
        get_args(ScenarioStatus)
    )
