from __future__ import annotations

from pathlib import Path

import pytest

from alan.self_update import _run
from tests.self_update_apply_fakes import HEALTH_URL, build_apply_scenario


def test_apply_scenario_restores_cwd_after_the_real_kit_extraction_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prior = Path.cwd()
    monkeypatch.chdir(prior)
    with pytest.MonkeyPatch.context() as scoped:
        scenario = build_apply_scenario(scoped, tmp_path)
        result = _run.trampoline(
            scenario.repo,
            scheduled=False,
            plist_path=scenario.plist_path,
            health_url=HEALTH_URL,
        )
        assert result == 0
        assert len(scenario.execs.calls) == 1
        assert Path.cwd().resolve().parent == (tmp_path / "deploy" / "kits").resolve()
    assert Path.cwd() == prior
