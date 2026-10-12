from __future__ import annotations

from pathlib import Path

import pytest

from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _pr_branch, _pr_flake_ledger
from alan.teams import pr_merge as pr_merge_mod

_E2E_GATE = "Frontend E2E (Playwright, blocking)"
_BACKEND_CHECK = "Backend tests + coverage"


def test_diagnostics_rerun_lines_render_spec_gated_record_same_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    survived = _pr_flake_ledger.spec_gated_survivors(
        (_E2E_GATE,), specs_before=("e2e/x.spec.ts",), specs_after=("e2e/x.spec.ts",),
    )
    _pr_flake_ledger.record_flake_survival(42, survived, "sha-1", forge_target=DEFAULT_FORGE_TARGET)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-1")
    lines = pr_merge_mod._diagnostics_rerun_lines(  # noqa: SLF001
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET
    )
    assert f"- survivor evidence: {_E2E_GATE} stayed red on sha-1" in lines


def test_diagnostics_rerun_lines_spec_gated_record_stays_check_to_sha_mapping(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    survived = _pr_flake_ledger.spec_gated_survivors(
        (_E2E_GATE, _BACKEND_CHECK),
        specs_before=("e2e/x.spec.ts",), specs_after=("e2e/y.spec.ts",),
    )
    _pr_flake_ledger.record_flake_survival(42, survived, "sha-1", forge_target=DEFAULT_FORGE_TARGET)
    on_disk = _pr_flake_ledger.load_gate_survived_checks(42, DEFAULT_FORGE_TARGET)
    assert on_disk == {_BACKEND_CHECK: "sha-1"}
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in on_disk.items())
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-1")
    lines = pr_merge_mod._diagnostics_rerun_lines(  # noqa: SLF001
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET
    )
    assert f"- survivor evidence: {_BACKEND_CHECK} stayed red on sha-1" in lines
    assert not any(_E2E_GATE in line for line in lines)
