from __future__ import annotations

from pathlib import Path

import pytest

from alan import e2e_failures
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _pr_branch, _pr_flake_ledger
from tests._pr_branch_support import _audit_entries, _gate_readiness
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript

_E2E_GATE = "Frontend E2E (Playwright, blocking)"
_BACKEND_CHECK = "Backend tests + coverage"
_FRONTEND_CHECK = "Frontend tests + coverage (100/100/100/100)"


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


@pytest.mark.parametrize(
    ("survived", "specs_before", "specs_after", "expected"),
    [
        ((_E2E_GATE,), (), (), (_E2E_GATE,)),
        ((_E2E_GATE,), (), ("e2e/a.spec.ts",), (_E2E_GATE,)),
        ((_E2E_GATE,), ("e2e/a.spec.ts",), (), (_E2E_GATE,)),
        (
            (_E2E_GATE,),
            ("e2e/a.spec.ts",),
            ("e2e/b.spec.ts",),
            (),
        ),
        (
            (_E2E_GATE,),
            ("e2e/a.spec.ts",),
            ("e2e/a.spec.ts",),
            (_E2E_GATE,),
        ),
        (
            (_E2E_GATE, _BACKEND_CHECK),
            ("e2e/a.spec.ts",),
            ("e2e/b.spec.ts",),
            (_BACKEND_CHECK,),
        ),
        (
            (_BACKEND_CHECK, _FRONTEND_CHECK),
            ("e2e/a.spec.ts",),
            ("e2e/b.spec.ts",),
            (_BACKEND_CHECK, _FRONTEND_CHECK),
        ),
    ],
)
def test_spec_gated_survivors_table(
    survived: tuple[str, ...],
    specs_before: tuple[str, ...],
    specs_after: tuple[str, ...],
    expected: tuple[str, ...],
) -> None:
    assert _pr_flake_ledger.spec_gated_survivors(
        survived, specs_before=specs_before, specs_after=specs_after,
    ) == expected


def test_spec_basename_collision_across_directories_counts_as_overlap() -> None:
    result = _pr_flake_ledger.spec_gated_survivors(
        (_E2E_GATE,),
        specs_before=("alan/web/e2e/nested/dir-a/chatty.spec.ts",),
        specs_after=("alan/web/e2e/nested/dir-b/chatty.spec.ts",),
    )
    assert result == (_E2E_GATE,)


def test_spec_ledger_key_still_uses_basename_after_extraction() -> None:
    assert (
        _pr_flake_ledger._spec_ledger_key("e2e/build-chat.spec.ts")  # noqa: SLF001
        == "spec:build-chat.spec.ts"
    )
    assert (
        _pr_flake_ledger._spec_ledger_key("plain.spec.ts")  # noqa: SLF001
        == "spec:plain.spec.ts"
    )


def test_spec_basename_direct() -> None:
    assert _pr_flake_ledger._spec_basename("a/b/c.spec.ts") == "c.spec.ts"  # noqa: SLF001
    assert _pr_flake_ledger._spec_basename("c.spec.ts") == "c.spec.ts"  # noqa: SLF001


def test_flatten_spec_names_dedupes_across_and_within_values() -> None:
    result = _pr_flake_ledger.flatten_spec_names(
        {
            _E2E_GATE: ("e2e/a.spec.ts", "e2e/b.spec.ts"),
            "other": ("e2e/a.spec.ts", "e2e/c.spec.ts"),
        }
    )
    assert result == ("e2e/a.spec.ts", "e2e/b.spec.ts", "e2e/c.spec.ts")


def test_flatten_spec_names_empty_dict_is_empty_tuple() -> None:
    assert _pr_flake_ledger.flatten_spec_names({}) == ()


def test_failing_e2e_specs_empty_when_e2e_not_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_a: object, **_kw: object) -> object:
        raise AssertionError("no fetch expected without the e2e check failing")

    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", _boom)
    readiness = _gate_readiness(failing_checks=(_BACKEND_CHECK,))
    assert _pr_flake_ledger.failing_e2e_specs(1, readiness, forge_target=DEFAULT_FORGE_TARGET) == ()


def test_failing_e2e_specs_flattens_and_dedupes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_branch,
        "fetch_failing_e2e_tests",
        lambda *_a, **_kw: (
            e2e_failures.FailingE2ETest("e2e/a.spec.ts", "one"),
            e2e_failures.FailingE2ETest("e2e/a.spec.ts", "two"),
            e2e_failures.FailingE2ETest("e2e/b.spec.ts", "three"),
        ),
    )
    readiness = _gate_readiness(failing_checks=(_E2E_GATE,))
    assert _pr_flake_ledger.failing_e2e_specs(1, readiness, forge_target=DEFAULT_FORGE_TARGET) == (
        "e2e/a.spec.ts", "e2e/b.spec.ts",
    )


def test_spec_gated_record_still_drives_survived_at_head_and_promotion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    survived = _pr_flake_ledger.spec_gated_survivors(
        (_E2E_GATE,),
        specs_before=("e2e/chatty.spec.ts",),
        specs_after=("e2e/chatty.spec.ts",),
    )
    _pr_flake_ledger.record_flake_survival(7, survived, "sha-1", forge_target=DEFAULT_FORGE_TARGET)
    assert _pr_flake_ledger.survived_at_head_checks(
        7, "sha-1", forge_target=DEFAULT_FORGE_TARGET,
    ) == frozenset({_E2E_GATE})
    readiness = _gate_readiness(failing_checks=(_E2E_GATE,))
    assert _pr_flake_ledger.evidenced_actionable_failing_checks(
        readiness, 7, "sha-1", forge_target=DEFAULT_FORGE_TARGET,
    ) == (
        _E2E_GATE,
    )


def test_spec_gated_record_dropped_e2e_stays_unreliable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    survived = _pr_flake_ledger.spec_gated_survivors(
        (_E2E_GATE,),
        specs_before=("e2e/chatty.spec.ts",),
        specs_after=("e2e/different.spec.ts",),
    )
    _pr_flake_ledger.record_flake_survival(8, survived, "sha-1", forge_target=DEFAULT_FORGE_TARGET)
    assert _pr_flake_ledger.survived_at_head_checks(
        8, "sha-1", forge_target=DEFAULT_FORGE_TARGET,
    ) == frozenset()
    readiness = _gate_readiness(failing_checks=(_E2E_GATE,))
    assert _pr_flake_ledger.evidenced_actionable_failing_checks(
        readiness, 8, "sha-1", forge_target=DEFAULT_FORGE_TARGET,
    ) == ()


def test_diff_attributable_e2e_checks_still_promotes_with_pinned_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_branch,
        "fetch_diff_files",
        lambda *_a, **_kw: frozenset({"alan/web/e2e/pinned.spec.ts"}),
    )
    readiness = _gate_readiness(failing_checks=(_E2E_GATE,))
    pinned_tests = (e2e_failures.FailingE2ETest("e2e/pinned.spec.ts", "t"),)
    assert _pr_flake_ledger.diff_attributable_e2e_checks(
        readiness, 1, tests=pinned_tests,
        forge_target=DEFAULT_FORGE_TARGET,
    ) == (_E2E_GATE,)


def test_spec_ledger_key_drives_the_spec_scoped_proposal_search_and_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    forge.queue("search_open_issues", (), ())
    forge.queue("create_label", True)
    forge.queue("create_issue", True)
    for pr in (1, 2, 3):
        _pr_flake_ledger.record_flake_occurrence(
            pr, (_E2E_GATE,), "task-x", rescued_specs={_E2E_GATE: ("e2e/chatty.spec.ts",)},
            forge_target=DEFAULT_FORGE_TARGET,
        )
    title = "Flake quarantine proposal: spec chatty.spec.ts"
    assert [args[0] for args, _kw in forge.calls("search_open_issues")] == [title, title]
    assert [args[0] for args, _kw in forge.calls("create_issue")] == [title]
    entry = next(
        e for e in _audit_entries(tmp_path) if e.get("action") == "flake_proposal_opened"
    )
    assert entry["check_name"] == "spec:chatty.spec.ts"
