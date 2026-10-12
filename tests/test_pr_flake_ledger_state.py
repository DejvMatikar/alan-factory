from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _pr_flake_ledger
from tests import _pr_branch_support as support
from tests._pr_branch_support import _E2E_GATE, _WIDGET_FORGE


@pytest.fixture(autouse=True)
def _default_transport_token(monkeypatch: pytest.MonkeyPatch) -> None:
    support.set_verifier_transport_token(monkeypatch)


def test_load_gate_survived_checks_missing_file_is_fail_open() -> None:
    assert _pr_flake_ledger.load_gate_survived_checks(999, DEFAULT_FORGE_TARGET) == {}


def test_load_gate_survived_checks_reads_through_legacy_for_default_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    (tmp_path / "pr_5_gate_survived.json").write_text(json.dumps({_E2E_GATE: "sha-1"}))
    assert _pr_flake_ledger.load_gate_survived_checks(
        5, DEFAULT_FORGE_TARGET,
    ) == {_E2E_GATE: "sha-1"}


def test_load_gate_survived_checks_second_engine_does_not_fall_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    (tmp_path / "pr_5_gate_survived.json").write_text(json.dumps({_E2E_GATE: "sha-1"}))
    assert _pr_flake_ledger.load_gate_survived_checks(5, _WIDGET_FORGE) == {}


def test_load_gate_survived_checks_ignores_non_string_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_flake_ledger.gate_survived_checks_path(
        5, DEFAULT_FORGE_TARGET,
    ).parent.mkdir(parents=True, exist_ok=True)
    _pr_flake_ledger.gate_survived_checks_path(
        5, DEFAULT_FORGE_TARGET,
    ).write_text(json.dumps({_E2E_GATE: 123}))
    assert _pr_flake_ledger.load_gate_survived_checks(5, DEFAULT_FORGE_TARGET) == {}


def test_load_gate_survived_checks_non_dict_payload_is_fail_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_flake_ledger.gate_survived_checks_path(
        5, DEFAULT_FORGE_TARGET,
    ).parent.mkdir(parents=True, exist_ok=True)
    _pr_flake_ledger.gate_survived_checks_path(
        5, DEFAULT_FORGE_TARGET,
    ).write_text(json.dumps([_E2E_GATE]))
    assert _pr_flake_ledger.load_gate_survived_checks(5, DEFAULT_FORGE_TARGET) == {}
