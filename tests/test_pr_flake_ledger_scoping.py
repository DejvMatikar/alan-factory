from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan import e2e_failures
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _pr_branch, _pr_flake_ledger
from tests import _pr_branch_support as support
from tests._pr_branch_support import _E2E_GATE, _gate_readiness
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript

_BACKEND_CHECK = "Backend tests + coverage"
_FRONTEND_CHECK = "Frontend tests + coverage (100/100/100/100)"
_TESTS_YML = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "tests.yml"


@pytest.fixture(autouse=True)
def _default_transport_token(monkeypatch: pytest.MonkeyPatch) -> None:
    support.set_verifier_transport_token(monkeypatch)


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


def test_failing_e2e_spec_names_scopes_to_e2e_when_backend_also_failing_and_records_own_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    monkeypatch.setattr(
        _pr_branch,
        "fetch_failing_e2e_tests",
        lambda *_a, **_kw: (e2e_failures.FailingE2ETest("e2e/a.spec.ts", "t"),),
    )
    readiness = _gate_readiness(failing_checks=(_E2E_GATE, _BACKEND_CHECK))
    specs = _pr_flake_ledger.failing_e2e_spec_names(1, readiness, forge_target=DEFAULT_FORGE_TARGET)
    assert specs == {_E2E_GATE: ("e2e/a.spec.ts",)}

    monkeypatch.setattr(_pr_flake_ledger, "_has_open_flake_proposal", lambda *_a, **_kw: True)
    _pr_flake_ledger.record_flake_occurrence(
        7, (_E2E_GATE, _BACKEND_CHECK), "task-x", rescued_specs=specs,
        forge_target=DEFAULT_FORGE_TARGET,
    )
    on_disk = json.loads(_pr_flake_ledger.flake_failures_path(DEFAULT_FORGE_TARGET).read_text())
    assert on_disk == {"spec:a.spec.ts": [7], _BACKEND_CHECK: [7]}


def test_failing_e2e_spec_names_non_e2e_allowlisted_only_skips_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_a: object, **_kw: object) -> object:
        raise AssertionError("no fetch expected when E2E is not among the failing checks")

    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", _boom)
    readiness = _gate_readiness(failing_checks=(_BACKEND_CHECK,))
    assert _pr_flake_ledger.failing_e2e_spec_names(
        1, readiness, forge_target=DEFAULT_FORGE_TARGET,
    ) == {}


def test_diff_attributable_e2e_and_backend_both_failing_returns_e2e_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_branch,
        "fetch_failing_e2e_tests",
        lambda *_a, **_kw: (e2e_failures.FailingE2ETest("e2e/a.spec.ts", "t"),),
    )
    monkeypatch.setattr(
        _pr_branch,
        "fetch_diff_files",
        lambda *_a, **_kw: frozenset({"alan/web/e2e/a.spec.ts"}),
    )
    readiness = _gate_readiness(failing_checks=(_E2E_GATE, _BACKEND_CHECK))
    assert _pr_flake_ledger.diff_attributable_e2e_checks(
        readiness, 42, forge_target=DEFAULT_FORGE_TARGET,
    ) == (_E2E_GATE,)


def test_diff_attributable_e2e_and_backend_both_failing_reads_the_patch_once_through_the_port(
    monkeypatch: pytest.MonkeyPatch, forge: ForgeScript,
) -> None:
    monkeypatch.setattr(
        _pr_branch,
        "fetch_failing_e2e_tests",
        lambda *_a, **_kw: (
            e2e_failures.FailingE2ETest(
                "e2e/other.spec.ts", "t", message="waiting for getByTestId('gone-token')",
            ),
        ),
    )
    monkeypatch.setattr(
        _pr_branch, "fetch_diff_files", lambda *_a, **_kw: frozenset({"alan/x.py"})
    )
    forge.queue("pr_patch", "-  <span data-testid={`gone-token`}>\n+  <div />\n")
    readiness = _gate_readiness(failing_checks=(_E2E_GATE, _BACKEND_CHECK))
    assert _pr_flake_ledger.diff_attributable_e2e_checks(
        readiness, 42, forge_target=DEFAULT_FORGE_TARGET,
    ) == (_E2E_GATE,)
    assert len(forge.calls("pr_patch")) == 1


def test_diff_attributable_non_e2e_allowlisted_only_skips_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_a: object, **_kw: object) -> object:
        raise AssertionError("no fetch expected when E2E is not among the failing checks")

    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", _boom)
    readiness = _gate_readiness(failing_checks=(_BACKEND_CHECK,))
    assert _pr_flake_ledger.diff_attributable_e2e_checks(
        readiness, 42, forge_target=DEFAULT_FORGE_TARGET,
    ) == ()


def test_diff_attributable_quarantined_e2e_with_backend_also_failing_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_a: object, **_kw: object) -> object:
        raise AssertionError("no fetch expected when E2E is quarantined")

    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", _boom)
    readiness = _gate_readiness(
        failing_checks=(_E2E_GATE, _BACKEND_CHECK), quarantined_checks=(_E2E_GATE,)
    )
    assert _pr_flake_ledger.diff_attributable_e2e_checks(
        readiness, 42, forge_target=DEFAULT_FORGE_TARGET,
    ) == ()


def test_flaky_only_failing_true_for_backend_check_only() -> None:
    readiness = _gate_readiness(failing_checks=(_BACKEND_CHECK,))
    assert _pr_flake_ledger.flaky_only_failing(readiness) is True


def test_flaky_only_failing_true_for_frontend_tests_check_only() -> None:
    readiness = _gate_readiness(failing_checks=(_FRONTEND_CHECK,))
    assert _pr_flake_ledger.flaky_only_failing(readiness) is True


def test_actionable_failing_checks_excludes_backend_and_frontend_tests_checks() -> None:
    readiness = _gate_readiness(failing_checks=(_BACKEND_CHECK, _FRONTEND_CHECK))
    assert _pr_flake_ledger.actionable_failing_checks(readiness) == ()


def test_evidenced_actionable_promotes_backend_check_with_survivor_at_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readiness = _gate_readiness(failing_checks=(_BACKEND_CHECK,))
    monkeypatch.setattr(
        _pr_flake_ledger, "load_gate_survived_checks", lambda *_a, **_kw: {_BACKEND_CHECK: "sha-1"}
    )
    assert _pr_flake_ledger.evidenced_actionable_failing_checks(
        readiness, 1, "sha-1", forge_target=DEFAULT_FORGE_TARGET,
    ) == (
        _BACKEND_CHECK,
    )


def test_evidenced_actionable_backend_check_without_survivor_stays_unreliable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readiness = _gate_readiness(failing_checks=(_BACKEND_CHECK,))
    monkeypatch.setattr(_pr_flake_ledger, "load_gate_survived_checks", lambda *_a, **_kw: {})
    assert _pr_flake_ledger.evidenced_actionable_failing_checks(
        readiness, 1, "sha-1", forge_target=DEFAULT_FORGE_TARGET,
    ) == ()


def test_evidenced_actionable_quarantined_backend_check_excluded_despite_survivor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readiness = _gate_readiness(
        failing_checks=(_BACKEND_CHECK,), quarantined_checks=(_BACKEND_CHECK,)
    )
    monkeypatch.setattr(
        _pr_flake_ledger, "load_gate_survived_checks", lambda *_a, **_kw: {_BACKEND_CHECK: "sha-1"}
    )
    assert _pr_flake_ledger.evidenced_actionable_failing_checks(
        readiness, 1, "sha-1", forge_target=DEFAULT_FORGE_TARGET,
    ) == ()


def test_rebase_blocking_failing_checks_excludes_only_e2e() -> None:
    readiness = _gate_readiness(failing_checks=(_E2E_GATE, _BACKEND_CHECK))
    assert _pr_flake_ledger.rebase_blocking_failing_checks(readiness) == (_BACKEND_CHECK,)


def test_rebase_blocking_failing_checks_excludes_quarantined() -> None:
    readiness = _gate_readiness(
        failing_checks=(_BACKEND_CHECK,), quarantined_checks=(_BACKEND_CHECK,)
    )
    assert _pr_flake_ledger.rebase_blocking_failing_checks(readiness) == ()


def test_rebase_safe_flaky_checks_is_e2e_only() -> None:
    assert _pr_flake_ledger.REBASE_SAFE_FLAKY_CHECKS == frozenset({_E2E_GATE})


@pytest.mark.parametrize("member", sorted(_pr_flake_ledger.SHARED_FLAKY_ALLOWLIST))
def test_allowlist_members_are_real_job_names_in_tests_yml(member: str) -> None:
    text = _TESTS_YML.read_text(encoding="utf-8")
    assert f"name: {member}" in text
