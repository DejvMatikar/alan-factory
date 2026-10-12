from __future__ import annotations

from pathlib import Path

import pytest

from alan.engine_config import DEFAULT_FORGE_TARGET, ForgeTarget, RepoIdentity
from alan.teams import _fix_non_actionable, _pr_flake_ledger, _pr_state_paths
from tests.aggregate_pr_forge import E2E, GATE, PrForge
from tests.fakes.forge import FakeForge

_HEAD = "b" * 40
_PR = 42

_COPY_TARGET = ForgeTarget(
    repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
    credential_env_name="ACME_WIDGET_GH_TOKEN",
    authoritative_checks=frozenset({GATE}),
    aggregate_checks=frozenset({GATE}),
)

_UNDECLARED_TARGET = _COPY_TARGET.model_copy(update={"aggregate_checks": frozenset()})


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> PrForge:
    scenario = PrForge(_active_fake_forge, head_sha=_HEAD)
    scenario.script_reads((E2E, GATE))
    return scenario


def _exhaust_reruns_at_head(forge_target: ForgeTarget) -> None:
    for _ in range(_pr_flake_ledger.max_gate_reruns()):
        _pr_state_paths.bump_gate_rerun_count(_PR, forge_target, head_sha=_HEAD)
    assert _pr_flake_ledger.gate_reruns_exhausted(_PR, _HEAD, forge_target=forge_target)


@pytest.mark.usefixtures("forge")
def test_blocking_state_on_copy_does_not_contradict_on_aggregate() -> None:
    _exhaust_reruns_at_head(_COPY_TARGET)
    state = _fix_non_actionable._blocking_state(_PR, "task-x", _COPY_TARGET)
    assert state is _fix_non_actionable._NO_BLOCKING_STATE


@pytest.mark.usefixtures("forge")
def test_blocking_state_on_owner_still_lists_gate() -> None:
    _exhaust_reruns_at_head(DEFAULT_FORGE_TARGET)
    state = _fix_non_actionable._blocking_state(_PR, "task-x", DEFAULT_FORGE_TARGET)
    assert state.contradicted == (GATE,)
    assert state.head_sha == _HEAD


@pytest.mark.usefixtures("forge")
def test_blocking_state_on_undeclared_copy_target_still_lists_gate() -> None:
    _exhaust_reruns_at_head(_UNDECLARED_TARGET)
    state = _fix_non_actionable._blocking_state(_PR, "task-x", _UNDECLARED_TARGET)
    assert state.contradicted == (GATE,)
