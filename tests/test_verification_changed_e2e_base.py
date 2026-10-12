from __future__ import annotations

from pathlib import Path

import pytest

from alan import secrets_store
from alan.engine_config import _DEFAULT as _ENGINE_DEFAULT  # pyright: ignore[reportPrivateUsage]
from alan.teams import _changed_e2e, _scenario_runner
from alan.teams import verification as verif_mod
from alan.teams._verification_scenarios import _changed_e2e_results, _to_scenario_outcomes
from alan.workspace import Workspace, create


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("ce2e-base-task", "do it", root=tmp_path / "ws")


def _boom(*_a: object, **_kw: object) -> object:
    raise AssertionError("run_scenario must not be called")


def test_changed_e2e_results_synthesizes_advisory_when_base_unresolved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        _changed_e2e,
        "build_changed_e2e_scenario",
        lambda _c, **_kw: _changed_e2e.ChangedE2ESelection(
            scenario=None, base_unresolved=True
        ),
    )
    monkeypatch.setattr(_scenario_runner, "run_scenario", _boom)
    results = _changed_e2e_results(tmp_path, None)
    assert len(results) == 1
    result = results[0]
    assert result.scenario_id == _changed_e2e.CHANGED_E2E_SCENARIO_ID
    assert result.status == "ADVISORY"
    assert result.returncode is None
    assert result.elapsed_s == 0.0
    assert "base unresolved" in result.diagnostic


def test_changed_e2e_results_empty_when_base_resolved_and_no_specs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        _changed_e2e,
        "build_changed_e2e_scenario",
        lambda _c, **_kw: _changed_e2e.ChangedE2ESelection(
            scenario=None, base_unresolved=False
        ),
    )
    monkeypatch.setattr(_scenario_runner, "run_scenario", _boom)
    assert _changed_e2e_results(tmp_path, None) == []


def test_changed_e2e_results_runs_scenario_when_resolved_and_selected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scenario = _changed_e2e.changed_e2e_scenario(("alan/web/e2e/a.spec.ts",))
    monkeypatch.setattr(
        _changed_e2e,
        "build_changed_e2e_scenario",
        lambda _c, **_kw: _changed_e2e.ChangedE2ESelection(
            scenario=scenario, base_unresolved=False
        ),
    )
    canned = _scenario_runner.ScenarioResult(
        scenario_id="changed-e2e-specs", status="PASS", diagnostic="",
        stdout_tail="", stderr_tail="", returncode=0, elapsed_s=0.1,
    )
    monkeypatch.setattr(_scenario_runner, "run_scenario", lambda *_a, **_kw: canned)
    assert _changed_e2e_results(tmp_path, None) == [canned]


def test_changed_e2e_results_advisory_diagnostic_survives_to_scenario_outcomes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        _changed_e2e,
        "build_changed_e2e_scenario",
        lambda _c, **_kw: _changed_e2e.ChangedE2ESelection(
            scenario=None, base_unresolved=True
        ),
    )
    outcomes = _to_scenario_outcomes(_changed_e2e_results(tmp_path, None))
    assert len(outcomes) == 1
    assert outcomes[0].status == "ADVISORY"
    assert outcomes[0].scenario_id == _changed_e2e.CHANGED_E2E_SCENARIO_ID
    assert "base unresolved" in outcomes[0].diagnostic


def test_changed_e2e_env_degrades_to_none_on_missing_secret(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace
) -> None:
    def _raise_missing(*_a: object, **_kw: object) -> object:
        raise secrets_store.MissingSecretError("no token")

    monkeypatch.setattr(
        "alan.teams.verification._secrets_store.transport_env", _raise_missing
    )
    assert verif_mod._changed_e2e_env(workspace, _ENGINE_DEFAULT) is None


def test_changed_e2e_env_returns_transport_env_result(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace
) -> None:
    monkeypatch.setattr(
        "alan.teams.verification._secrets_store.transport_env", lambda _ft: {"TOK": "x"}
    )
    assert verif_mod._changed_e2e_env(workspace, _ENGINE_DEFAULT) == {"TOK": "x"}
