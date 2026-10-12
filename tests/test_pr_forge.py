from __future__ import annotations

from alan.teams import _pr_forge


def test_gh_timeout_s_value() -> None:
    assert _pr_forge.GH_TIMEOUT_S == 60.0
