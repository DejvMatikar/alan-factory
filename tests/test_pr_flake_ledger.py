from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from alan import e2e_failures
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _pr_branch, _pr_flake_ledger, _pr_state_paths
from alan.teams._pr_ready import ReadinessUnavailable
from tests import _pr_branch_support as support
from tests._pr_branch_support import (
    _E2E_GATE,
    _FLAKY_GATE,
    _WIDGET_FORGE,
    _audit_entries,
    _flake_pr_readiness,
    _flaky_e2e_readiness,
    _gate_readiness,
)
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript

_FLAKY_ISSUE_TITLE = f"Flake quarantine proposal: {_E2E_GATE}"

@pytest.fixture(autouse=True)
def _default_transport_token(monkeypatch: pytest.MonkeyPatch) -> None:
    support.set_verifier_transport_token(monkeypatch)


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


def test_load_flake_failures_reads_through_legacy_for_default_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    (tmp_path / "flake_failures.json").write_text(json.dumps({"check": [1, 2]}))
    assert _pr_flake_ledger._load_flake_failures(
        DEFAULT_FORGE_TARGET,
    ) == {"check": [1, 2]}  # noqa: SLF001


def test_load_flake_failures_second_engine_does_not_fall_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    (tmp_path / "flake_failures.json").write_text(json.dumps({"check": [1, 2]}))
    assert _pr_flake_ledger._load_flake_failures(_WIDGET_FORGE) == {}  # noqa: SLF001


def test_record_flake_occurrence_persists_to_namespaced_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    monkeypatch.setattr(_pr_flake_ledger, "_has_open_flake_proposal", lambda *_a, **_kw: True)
    _pr_flake_ledger.record_flake_occurrence(1, (_E2E_GATE,), "task-x", forge_target=_WIDGET_FORGE)
    assert _pr_flake_ledger.flake_failures_path(_WIDGET_FORGE).exists()
    assert not _pr_flake_ledger.flake_failures_path(DEFAULT_FORGE_TARGET).exists()
    on_disk = json.loads(_pr_flake_ledger.flake_failures_path(_WIDGET_FORGE).read_text())
    assert on_disk == {_E2E_GATE: [1]}


def test_flake_failures_isolated_per_engine_counts_do_not_sum(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    monkeypatch.setattr(_pr_flake_ledger, "_has_open_flake_proposal", lambda *_a, **_kw: True)
    _pr_flake_ledger.record_flake_occurrence(
        1, (_E2E_GATE,), "task-x", forge_target=DEFAULT_FORGE_TARGET,
    )
    _pr_flake_ledger.record_flake_occurrence(2, (_E2E_GATE,), "task-x", forge_target=_WIDGET_FORGE)
    default_data = json.loads(_pr_flake_ledger.flake_failures_path(
        DEFAULT_FORGE_TARGET,
    ).read_text())
    widget_data = json.loads(_pr_flake_ledger.flake_failures_path(_WIDGET_FORGE).read_text())
    assert default_data == {_E2E_GATE: [1]}
    assert widget_data == {_E2E_GATE: [2]}


def test_flake_rescued_checks_green_same_sha_is_rescue() -> None:
    before = _flake_pr_readiness(failing_checks=(_E2E_GATE,))
    after = _flake_pr_readiness()
    assert _pr_flake_ledger.flake_rescued_checks(
        before, after, head_sha_before="sha-1", head_sha_after="sha-1"
    ) == (_E2E_GATE,)


def test_flake_rescued_checks_transient_exit_is_not_rescue() -> None:
    before = _flake_pr_readiness(failing_checks=(_E2E_GATE,))
    after = ReadinessUnavailable("gh unavailable")
    assert _pr_flake_ledger.flake_rescued_checks(
        before, after, head_sha_before="sha-1", head_sha_after="sha-1"
    ) == ()


def test_flake_rescued_checks_pending_checks_is_not_rescue() -> None:
    before = _flake_pr_readiness(failing_checks=(_E2E_GATE,))
    after = _flake_pr_readiness(pending_checks=(_E2E_GATE,))
    assert _pr_flake_ledger.flake_rescued_checks(
        before, after, head_sha_before="sha-1", head_sha_after="sha-1"
    ) == ()


def test_flake_rescued_checks_sha_mismatch_is_not_rescue() -> None:
    before = _flake_pr_readiness(failing_checks=(_E2E_GATE,))
    after = _flake_pr_readiness()
    assert _pr_flake_ledger.flake_rescued_checks(
        before, after, head_sha_before="sha-1", head_sha_after="sha-2"
    ) == ()


def test_flake_rescued_checks_sha_before_none_is_not_rescue() -> None:
    before = _flake_pr_readiness(failing_checks=(_E2E_GATE,))
    after = _flake_pr_readiness()
    assert _pr_flake_ledger.flake_rescued_checks(
        before, after, head_sha_before=None, head_sha_after="sha-1"
    ) == ()


def test_flake_rescued_checks_still_failing_excluded() -> None:
    before = _flake_pr_readiness(failing_checks=(_E2E_GATE,))
    after = _flake_pr_readiness(failing_checks=(_E2E_GATE,))
    assert _pr_flake_ledger.flake_rescued_checks(
        before, after, head_sha_before="sha-1", head_sha_after="sha-1"
    ) == ()


def test_flake_rescued_checks_partial_rescue() -> None:
    before = _flake_pr_readiness(failing_checks=(_E2E_GATE, "ci/other"))
    after = _flake_pr_readiness(failing_checks=("ci/other",))
    assert _pr_flake_ledger.flake_rescued_checks(
        before, after, head_sha_before="sha-1", head_sha_after="sha-1"
    ) == (_E2E_GATE,)


def test_fetch_flake_guard_head_sha_audits_flake_scoped_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def fake_fetch(
        pr_number: int, *, forge_target: object = None,
        on_fail: "Callable[[int, str], None]" = lambda *_a: None,
    ) -> str | None:
        del forge_target
        on_fail(pr_number, "boom")
        return None

    monkeypatch.setattr(_pr_branch, "_fetch_pr_head_sha", fake_fetch)
    result = _pr_branch.fetch_flake_guard_head_sha(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result is None
    entries = _audit_entries(tmp_path)
    assert entries[-1]["action"] == "flake_head_sha_fetch_failed"
    assert entries[-1]["pr_number"] == 42
    assert entries[-1]["reason"] == "boom"
    assert not any(e.get("action") == "list_sonarcloud_annotations_failed" for e in entries)


def test_fetch_flake_guard_head_sha_honors_custom_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def fake_fetch(
        pr_number: int, *, forge_target: object = None,
        on_fail: "Callable[[int, str], None]" = lambda *_a: None,
    ) -> str | None:
        del forge_target
        on_fail(pr_number, "boom")
        return None

    monkeypatch.setattr(_pr_branch, "_fetch_pr_head_sha", fake_fetch)
    result = _pr_branch.fetch_flake_guard_head_sha(
        42, "task-x", action="custom_probe_head_sha_fetch_failed",
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert result is None
    entries = _audit_entries(tmp_path)
    assert entries[-1]["action"] == "custom_probe_head_sha_fetch_failed"


def test_flake_proposal_body_states_rescue_counting_rule() -> None:
    body = _pr_flake_ledger._flake_proposal_body(_E2E_GATE, [793, 794, 797])  # noqa: SLF001
    assert "rescued" in body
    assert "same head SHA" in body
    assert "stayed red are not counted" in body
    assert "auto-rerun as a suspected flake" not in body


def test_flake_failures_path_under_state_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert (
        _pr_flake_ledger.flake_failures_path(DEFAULT_FORGE_TARGET)
        == tmp_path / "DejvMatikar" / "alan" / "flake_failures.json"
    )


def test_load_flake_failures_non_dict_returns_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    p = _pr_flake_ledger.flake_failures_path(DEFAULT_FORGE_TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("[1, 2, 3]")
    assert _pr_flake_ledger._load_flake_failures(DEFAULT_FORGE_TARGET) == {}  # noqa: SLF001


def test_load_flake_failures_skips_malformed_entries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    p = _pr_flake_ledger.flake_failures_path(DEFAULT_FORGE_TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"good": [1, 2], "bad": "not-a-list"}))
    assert _pr_flake_ledger._load_flake_failures(
        DEFAULT_FORGE_TARGET,
    ) == {"good": [1, 2]}  # noqa: SLF001


def test_load_flake_failures_skips_boolean_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    p = _pr_flake_ledger.flake_failures_path(DEFAULT_FORGE_TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"good": [1, True, 2, False]}))
    assert _pr_flake_ledger._load_flake_failures(
        DEFAULT_FORGE_TARGET,
    ) == {"good": [1, 2]}  # noqa: SLF001


def test_record_flake_occurrence_ignores_non_allowlisted_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))

    def boom(*_a: object, **_kw: object) -> bool:
        raise AssertionError("must not check for an existing proposal")

    monkeypatch.setattr(_pr_flake_ledger, "_has_open_flake_proposal", boom)
    _pr_flake_ledger.record_flake_occurrence(
        1, ("ci/other",), "task-x", forge_target=DEFAULT_FORGE_TARGET,
    )
    assert not _pr_flake_ledger.flake_failures_path(DEFAULT_FORGE_TARGET).exists()


def test_record_flake_occurrence_dedupes_by_pr_and_persists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    monkeypatch.setattr(_pr_flake_ledger, "_has_open_flake_proposal", lambda *_a, **_kw: True)
    _pr_flake_ledger.record_flake_occurrence(
        1, (_E2E_GATE,), "task-x", forge_target=DEFAULT_FORGE_TARGET,
    )
    _pr_flake_ledger.record_flake_occurrence(
        2, (_E2E_GATE,), "task-x", forge_target=DEFAULT_FORGE_TARGET,
    )
    _pr_flake_ledger.record_flake_occurrence(
        1, (_E2E_GATE,), "task-x", forge_target=DEFAULT_FORGE_TARGET,
    )
    on_disk = json.loads(_pr_flake_ledger.flake_failures_path(DEFAULT_FORGE_TARGET).read_text())
    assert on_disk == {_E2E_GATE: [1, 2]}


def test_record_flake_occurrence_below_threshold_does_not_open_issue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def boom(*_a: object, **_kw: object) -> bool:
        raise AssertionError("must not query gh below the quarantine threshold")

    monkeypatch.setattr(_pr_flake_ledger, "_has_open_flake_proposal", boom)
    _pr_flake_ledger.record_flake_occurrence(
        1, (_E2E_GATE,), "task-x", forge_target=DEFAULT_FORGE_TARGET,
    )
    _pr_flake_ledger.record_flake_occurrence(
        2, (_E2E_GATE,), "task-x", forge_target=DEFAULT_FORGE_TARGET,
    )
    assert not any(
        e.get("action") == "flake_proposal_opened" for e in _audit_entries(tmp_path)
    )


def test_record_flake_occurrence_at_threshold_opens_proposal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(_pr_flake_ledger, "_has_open_flake_proposal", lambda *_a, **_kw: False)
    forge.queue("create_label", True)
    forge.queue("create_issue", True)
    for pr in (1, 2, 3):
        _pr_flake_ledger.record_flake_occurrence(
            pr, (_E2E_GATE,), "task-x", forge_target=DEFAULT_FORGE_TARGET,
        )
    assert len(forge.calls("create_label")) == 1
    issue_calls = forge.calls("create_issue")
    assert len(issue_calls) == 1
    (title, body_arg), kwargs = issue_calls[0]
    body = str(body_arg)
    assert title == _FLAKY_ISSUE_TITLE
    assert kwargs["label"] == "flake-quarantine"
    assert "rescued" in body
    assert "same head SHA" in body
    assert "stayed red are not counted" in body
    entry = next(
        e for e in _audit_entries(tmp_path) if e.get("action") == "flake_proposal_opened"
    )
    assert entry["check_name"] == _E2E_GATE
    assert entry["pr_count"] == 3
    assert entry["ok"] is True


def test_record_flake_occurrence_idempotent_when_proposal_already_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(_pr_flake_ledger, "_has_open_flake_proposal", lambda *_a, **_kw: True)
    for pr in (1, 2, 3):
        _pr_flake_ledger.record_flake_occurrence(
            pr, (_E2E_GATE,), "task-x", forge_target=DEFAULT_FORGE_TARGET,
        )
    assert forge.fake.calls == []
    assert not any(
        e.get("action") == "flake_proposal_opened" for e in _audit_entries(tmp_path)
    )


def test_record_flake_occurrence_rechecks_open_proposal_after_label_creation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    calls = [False, True]
    monkeypatch.setattr(
        _pr_flake_ledger, "_has_open_flake_proposal", lambda *_a, **_kw: calls.pop(0)
    )
    forge.queue("create_label", True)
    for pr in (1, 2, 3):
        _pr_flake_ledger.record_flake_occurrence(
            pr, (_E2E_GATE,), "task-x", forge_target=DEFAULT_FORGE_TARGET,
        )
    assert len(forge.calls("create_label")) == 1
    assert forge.calls("create_issue") == []
    assert not any(
        e.get("action") == "flake_proposal_opened" for e in _audit_entries(tmp_path)
    )


def test_has_open_flake_proposal_true_on_matching_title(forge: ForgeScript) -> None:
    forge.queue("search_open_issues", ({"title": _FLAKY_ISSUE_TITLE},))
    assert _pr_flake_ledger._has_open_flake_proposal(
        _E2E_GATE, forge_target=DEFAULT_FORGE_TARGET,
    ) is True  # noqa: SLF001


def test_has_open_flake_proposal_false_when_no_match(forge: ForgeScript) -> None:
    forge.queue("search_open_issues", ({"title": "some other issue"},))
    assert _pr_flake_ledger._has_open_flake_proposal(
        _E2E_GATE, forge_target=DEFAULT_FORGE_TARGET,
    ) is False  # noqa: SLF001


def test_has_open_flake_proposal_false_when_search_yields_nothing(forge: ForgeScript) -> None:
    forge.queue("search_open_issues", ())
    assert _pr_flake_ledger._has_open_flake_proposal(
        _E2E_GATE, forge_target=DEFAULT_FORGE_TARGET,
    ) is False  # noqa: SLF001


def test_open_flake_issue_returns_false_on_failure(forge: ForgeScript) -> None:
    forge.queue("create_issue", False)
    assert _pr_flake_ledger._open_flake_issue(
        "title", "body", forge_target=DEFAULT_FORGE_TARGET,
    ) is False  # noqa: SLF001


def test_open_flake_issue_returns_true_on_success(forge: ForgeScript) -> None:
    forge.queue("create_issue", True)
    assert _pr_flake_ledger._open_flake_issue(
        "title", "body", forge_target=DEFAULT_FORGE_TARGET,
    ) is True  # noqa: SLF001


def test_ensure_flake_label_discards_a_failed_creation(forge: ForgeScript) -> None:
    forge.queue("create_label", False)
    _pr_flake_ledger._ensure_flake_label(forge_target=DEFAULT_FORGE_TARGET)  # noqa: SLF001
    assert len(forge.calls("create_label")) == 1


def test_flake_survived_checks_same_sha_still_failing_is_survival() -> None:
    before = _flake_pr_readiness(failing_checks=(_E2E_GATE,))
    after = _flake_pr_readiness(failing_checks=(_E2E_GATE,))
    assert _pr_flake_ledger.flake_survived_checks(
        before, after, head_sha_before="sha-1", head_sha_after="sha-1"
    ) == (_E2E_GATE,)


def test_flake_survived_checks_rescued_check_is_not_survival() -> None:
    before = _flake_pr_readiness(failing_checks=(_E2E_GATE,))
    after = _flake_pr_readiness()
    assert _pr_flake_ledger.flake_survived_checks(
        before, after, head_sha_before="sha-1", head_sha_after="sha-1"
    ) == ()


def test_flake_survived_checks_sha_mismatch_is_not_survival() -> None:
    before = _flake_pr_readiness(failing_checks=(_E2E_GATE,))
    after = _flake_pr_readiness(failing_checks=(_E2E_GATE,))
    assert _pr_flake_ledger.flake_survived_checks(
        before, after, head_sha_before="sha-1", head_sha_after="sha-2"
    ) == ()


def test_flake_survived_checks_transient_exit_is_not_survival() -> None:
    before = _flake_pr_readiness(failing_checks=(_E2E_GATE,))
    after = ReadinessUnavailable("gh unavailable")
    assert _pr_flake_ledger.flake_survived_checks(
        before, after, head_sha_before="sha-1", head_sha_after="sha-1"
    ) == ()


def test_flake_survived_checks_pending_checks_is_not_survival() -> None:
    before = _flake_pr_readiness(failing_checks=(_E2E_GATE,))
    after = _flake_pr_readiness(pending_checks=(_E2E_GATE,))
    assert _pr_flake_ledger.flake_survived_checks(
        before, after, head_sha_before="sha-1", head_sha_after="sha-1"
    ) == ()


def test_record_flake_survival_persists_to_namespaced_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_flake_ledger.record_flake_survival(7, (_E2E_GATE,), "sha-1", forge_target=_WIDGET_FORGE)
    assert _pr_flake_ledger.gate_survived_checks_path(7, _WIDGET_FORGE).exists()
    assert not _pr_flake_ledger.gate_survived_checks_path(7, DEFAULT_FORGE_TARGET).exists()
    on_disk = json.loads(_pr_flake_ledger.gate_survived_checks_path(7, _WIDGET_FORGE).read_text())
    assert on_disk == {_E2E_GATE: "sha-1"}


def test_record_flake_survival_noop_on_empty() -> None:
    _pr_flake_ledger.record_flake_survival(7, (), "sha-1", forge_target=DEFAULT_FORGE_TARGET)
    assert _pr_flake_ledger.load_gate_survived_checks(7, DEFAULT_FORGE_TARGET) == {}


def test_record_flake_survival_overwrites_stale_sha_for_same_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_flake_ledger.record_flake_survival(
        7, (_E2E_GATE,), "sha-1", forge_target=DEFAULT_FORGE_TARGET,
    )
    _pr_flake_ledger.record_flake_survival(
        7, (_E2E_GATE,), "sha-2", forge_target=DEFAULT_FORGE_TARGET,
    )
    assert _pr_flake_ledger.load_gate_survived_checks(
        7, DEFAULT_FORGE_TARGET,
    ) == {_E2E_GATE: "sha-2"}


def test_evidenced_actionable_head_sha_none_matches_actionable() -> None:
    readiness = _gate_readiness(failing_checks=(_FLAKY_GATE,))
    assert _pr_flake_ledger.evidenced_actionable_failing_checks(
        readiness, 1, None, forge_target=DEFAULT_FORGE_TARGET,
    ) == ()


def test_evidenced_actionable_survivor_at_head_promotes_with_budget_remaining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readiness = _gate_readiness(failing_checks=(_FLAKY_GATE,))
    monkeypatch.setattr(_pr_flake_ledger, "max_gate_reruns", lambda: 2)
    monkeypatch.setattr(
        _pr_flake_ledger, "load_gate_survived_checks", lambda *_a, **_kw: {_FLAKY_GATE: "sha-1"}
    )
    assert _pr_flake_ledger.evidenced_actionable_failing_checks(
        readiness, 1, "sha-1", forge_target=DEFAULT_FORGE_TARGET,
    ) == (
        _FLAKY_GATE,
    )


def test_evidenced_actionable_no_survivor_record_stays_unreliable_even_budget_spent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readiness = _gate_readiness(failing_checks=(_FLAKY_GATE,))
    monkeypatch.setattr(_pr_flake_ledger, "load_gate_survived_checks", lambda *_a, **_kw: {})
    assert _pr_flake_ledger.evidenced_actionable_failing_checks(
        readiness, 1, "sha-1", forge_target=DEFAULT_FORGE_TARGET,
    ) == ()


def test_evidenced_actionable_survivor_stale_sha_stays_unreliable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readiness = _gate_readiness(failing_checks=(_FLAKY_GATE,))
    monkeypatch.setattr(
        _pr_flake_ledger, "load_gate_survived_checks", lambda *_a, **_kw: {_FLAKY_GATE: "sha-0"}
    )
    assert _pr_flake_ledger.evidenced_actionable_failing_checks(
        readiness, 1, "sha-1", forge_target=DEFAULT_FORGE_TARGET,
    ) == ()


def test_gate_reruns_exhausted_same_sha_at_cap_is_exhausted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    monkeypatch.setattr(_pr_flake_ledger, "max_gate_reruns", lambda: 2)
    _pr_state_paths.bump_gate_rerun_count(42, DEFAULT_FORGE_TARGET, head_sha="sha-1")
    _pr_state_paths.bump_gate_rerun_count(42, DEFAULT_FORGE_TARGET, head_sha="sha-1")
    assert _pr_flake_ledger.gate_reruns_exhausted(
        42, "sha-1", forge_target=DEFAULT_FORGE_TARGET,
    ) is True


def test_gate_reruns_exhausted_new_sha_resets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    monkeypatch.setattr(_pr_flake_ledger, "max_gate_reruns", lambda: 2)
    p = _pr_state_paths.gate_rerun_count_path(42, DEFAULT_FORGE_TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"head_sha": "aaa", "count": 2, "lifetime": 2}))
    assert _pr_flake_ledger.gate_reruns_exhausted(
        42, "bbb", forge_target=DEFAULT_FORGE_TARGET,
    ) is False


def test_gate_reruns_exhausted_lifetime_ceiling_binds_despite_sha_reset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    monkeypatch.setattr(_pr_flake_ledger, "max_gate_reruns", lambda: 2)
    p = _pr_state_paths.gate_rerun_count_path(42, DEFAULT_FORGE_TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"head_sha": "aaa", "count": 0, "lifetime": 6}))
    assert _pr_flake_ledger.gate_reruns_exhausted(
        42, "bbb", forge_target=DEFAULT_FORGE_TARGET,
    ) is True


def test_gate_reruns_exhausted_legacy_bare_int_lifetime_carries_through_ceiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    p = _pr_state_paths.gate_rerun_count_path(42, DEFAULT_FORGE_TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("6")
    assert _pr_flake_ledger.gate_reruns_exhausted(
        42, "fresh-sha", forge_target=DEFAULT_FORGE_TARGET,
    ) is True


def test_gate_reruns_exhausted_legacy_bare_int_under_cap_not_exhausted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    monkeypatch.setattr(_pr_flake_ledger, "max_gate_reruns", lambda: 2)
    p = _pr_state_paths.gate_rerun_count_path(42, DEFAULT_FORGE_TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("2")
    assert _pr_flake_ledger.gate_reruns_exhausted(
        42, "fresh-sha", forge_target=DEFAULT_FORGE_TARGET,
    ) is False


def test_gate_reruns_exhausted_no_sha_available_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    monkeypatch.setattr(_pr_flake_ledger, "max_gate_reruns", lambda: 2)
    p = _pr_state_paths.gate_rerun_count_path(42, DEFAULT_FORGE_TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"head_sha": "aaa", "count": 2, "lifetime": 2}))
    assert _pr_flake_ledger.gate_reruns_exhausted(
        42, None, forge_target=DEFAULT_FORGE_TARGET,
    ) is True


def test_evidenced_actionable_still_excludes_quarantined_even_with_survivor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readiness = _gate_readiness(
        failing_checks=(_FLAKY_GATE,), quarantined_checks=(_FLAKY_GATE,)
    )
    monkeypatch.setattr(
        _pr_flake_ledger, "load_gate_survived_checks", lambda *_a, **_kw: {_FLAKY_GATE: "sha-1"}
    )
    assert _pr_flake_ledger.evidenced_actionable_failing_checks(
        readiness, 1, "sha-1", forge_target=DEFAULT_FORGE_TARGET,
    ) == ()


def test_evidenced_actionable_keeps_genuine_checks_regardless_of_survivor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readiness = _gate_readiness(
        failing_checks=(_FLAKY_GATE, "SonarCloud (blocking quality gate)")
    )
    monkeypatch.setattr(_pr_flake_ledger, "load_gate_survived_checks", lambda *_a, **_kw: {})
    assert _pr_flake_ledger.evidenced_actionable_failing_checks(
        readiness, 1, "sha-1", forge_target=DEFAULT_FORGE_TARGET,
    ) == (
        "SonarCloud (blocking quality gate)",
    )


def test_survived_at_head_checks_matching_stale_and_none_sha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_flake_ledger,
        "load_gate_survived_checks",
        lambda *_a, **_kw: {_FLAKY_GATE: "sha-1", "Other check": "sha-0"},
    )
    assert _pr_flake_ledger.survived_at_head_checks(
        1, "sha-1", forge_target=DEFAULT_FORGE_TARGET,
    ) == frozenset({_FLAKY_GATE})
    assert _pr_flake_ledger.survived_at_head_checks(
        1, "sha-2", forge_target=DEFAULT_FORGE_TARGET,
    ) == frozenset()
    assert _pr_flake_ledger.survived_at_head_checks(
        1, None, forge_target=DEFAULT_FORGE_TARGET,
    ) == frozenset()


def test_locator_tokens_extracts_get_by_testid_and_bracket_variants() -> None:
    msg = (
        "waiting for getByTestId('repo-spend-acme/x')\n"
        'then getByTestId("second-token")\n'
        "locator('[data-testid=bracket-token]')"
    )
    assert _pr_flake_ledger._locator_tokens(msg) == frozenset(
        {"repo-spend-acme/x", "second-token", "bracket-token"}
    )


def test_locator_tokens_drops_short_tokens_and_plain_messages() -> None:
    assert _pr_flake_ledger._locator_tokens("getByTestId('ab') timed out") == frozenset()
    assert _pr_flake_ledger._locator_tokens("expected 1 got 0") == frozenset()


def test_patch_removed_added_splits_and_skips_headers() -> None:
    patch = (
        "--- a/f.tsx\n+++ b/f.tsx\n"
        "-  old line with token-one\n"
        "+  new line with token-two\n"
        " context\n"
    )
    removed, added = _pr_flake_ledger._patch_removed_added(patch)
    assert "token-one" in removed and "a/f.tsx" not in removed
    assert "token-two" in added and "b/f.tsx" not in added


def test_spec_matches_diff_exact_suffix_and_negative() -> None:
    files = frozenset({"alan/web/e2e/foo.spec.ts"})
    assert _pr_flake_ledger._spec_matches_diff("alan/web/e2e/foo.spec.ts", files)
    assert _pr_flake_ledger._spec_matches_diff("e2e/foo.spec.ts", files)
    assert not _pr_flake_ledger._spec_matches_diff("e2e/bar.spec.ts", files)


def test_fetch_pr_diff_patch_ok(forge: ForgeScript) -> None:
    forge.queue("pr_patch", "-x\n+y\n")
    assert _pr_flake_ledger.fetch_pr_diff_patch(42, forge_target=DEFAULT_FORGE_TARGET) == "-x\n+y\n"


def test_fetch_pr_diff_patch_fault_returns_none(forge: ForgeScript) -> None:
    forge.queue("pr_patch", None)
    assert _pr_flake_ledger.fetch_pr_diff_patch(42, forge_target=DEFAULT_FORGE_TARGET) is None


def test_fetch_pr_diff_patch_over_cap_returns_none(forge: ForgeScript) -> None:
    forge.queue("pr_patch", "-" + "x" * _pr_flake_ledger._DIFF_PATCH_MAX_BYTES)  # noqa: SLF001
    assert _pr_flake_ledger.fetch_pr_diff_patch(42, forge_target=DEFAULT_FORGE_TARGET) is None


def test_diff_attributable_no_allowlisted_failing_returns_empty_without_fetches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_a: object, **_kw: object) -> object:
        raise AssertionError("no fetch expected when nothing allowlisted is failing")

    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", _boom)
    readiness = _gate_readiness(failing_checks=("sonar",))
    assert _pr_flake_ledger.diff_attributable_e2e_checks(
        readiness, 42, forge_target=DEFAULT_FORGE_TARGET,
    ) == ()


def test_diff_attributable_quarantined_check_never_promoted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_a: object, **_kw: object) -> object:
        raise AssertionError("no fetch expected for a fully quarantined failure")

    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", _boom)
    readiness = _gate_readiness(
        failing_checks=(_FLAKY_GATE,), quarantined_checks=(_FLAKY_GATE,)
    )
    assert _pr_flake_ledger.diff_attributable_e2e_checks(
        readiness, 42, forge_target=DEFAULT_FORGE_TARGET,
    ) == ()


def test_diff_attributable_tests_none_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", lambda *_a, **_kw: None)
    assert _pr_flake_ledger.diff_attributable_e2e_checks(
        _flaky_e2e_readiness(), 42, forge_target=DEFAULT_FORGE_TARGET,
    ) == ()


def test_diff_attributable_uses_prefetched_tests_without_fetching_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_a: object, **_kw: object) -> object:
        raise AssertionError("prefetched tests must not trigger a second fetch")

    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", _boom)
    monkeypatch.setattr(
        _pr_branch, "fetch_diff_files", lambda *_a, **_kw: frozenset({"alan/web/e2e/a.spec.ts"})
    )
    prefetched = (e2e_failures.FailingE2ETest("alan/web/e2e/a.spec.ts", "t"),)
    assert _pr_flake_ledger.diff_attributable_e2e_checks(
        _flaky_e2e_readiness(), 42, tests=prefetched,
        forge_target=DEFAULT_FORGE_TARGET,
    ) == (_FLAKY_GATE,)


def test_diff_attributable_prefetched_empty_tests_returns_empty_without_fetching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_a: object, **_kw: object) -> object:
        raise AssertionError("an empty prefetched tuple must not trigger a fetch")

    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", _boom)
    monkeypatch.setattr(_pr_branch, "fetch_diff_files", _boom)
    assert _pr_flake_ledger.diff_attributable_e2e_checks(
        _flaky_e2e_readiness(), 42, tests=(), forge_target=DEFAULT_FORGE_TARGET,
    ) == ()


def test_diff_attributable_diff_files_none_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_branch,
        "fetch_failing_e2e_tests",
        lambda *_a, **_kw: (e2e_failures.FailingE2ETest("e2e/a.spec.ts", "t"),),
    )
    monkeypatch.setattr(_pr_branch, "fetch_diff_files", lambda *_a, **_kw: None)
    assert _pr_flake_ledger.diff_attributable_e2e_checks(
        _flaky_e2e_readiness(), 42, forge_target=DEFAULT_FORGE_TARGET,
    ) == ()


def test_diff_attributable_spec_in_diff_promotes(
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
    assert _pr_flake_ledger.diff_attributable_e2e_checks(
        _flaky_e2e_readiness(), 42, forge_target=DEFAULT_FORGE_TARGET,
    ) == (
        _FLAKY_GATE,
    )


def test_diff_attributable_removed_testid_token_promotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_branch,
        "fetch_failing_e2e_tests",
        lambda *_a, **_kw: (
            e2e_failures.FailingE2ETest(
                "e2e/other.spec.ts", "t",
                message="waiting for getByTestId('repo-spend-acme')",
            ),
        ),
    )
    monkeypatch.setattr(
        _pr_branch,
        "fetch_diff_files",
        lambda *_a, **_kw: frozenset({"alan/web/src/RepoSection.tsx"}),
    )
    monkeypatch.setattr(
        _pr_flake_ledger,
        "fetch_pr_diff_patch",
        lambda *_a, **_kw: '-  <span data-testid={`repo-spend-acme`}>\n+  <div>new</div>\n',
    )
    assert _pr_flake_ledger.diff_attributable_e2e_checks(
        _flaky_e2e_readiness(), 42, forge_target=DEFAULT_FORGE_TARGET,
    ) == (
        _FLAKY_GATE,
    )


def test_diff_attributable_token_removed_and_readded_not_promoted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_branch,
        "fetch_failing_e2e_tests",
        lambda *_a, **_kw: (
            e2e_failures.FailingE2ETest(
                "e2e/other.spec.ts", "t",
                message="waiting for getByTestId('moved-token')",
            ),
        ),
    )
    monkeypatch.setattr(
        _pr_branch,
        "fetch_diff_files",
        lambda *_a, **_kw: frozenset({"alan/web/src/RepoSection.tsx"}),
    )
    monkeypatch.setattr(
        _pr_flake_ledger,
        "fetch_pr_diff_patch",
        lambda *_a, **_kw: "-  old spot moved-token\n+  new spot moved-token\n",
    )
    assert _pr_flake_ledger.diff_attributable_e2e_checks(
        _flaky_e2e_readiness(), 42, forge_target=DEFAULT_FORGE_TARGET,
    ) == ()


def test_diff_attributable_no_tokens_skips_patch_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_branch,
        "fetch_failing_e2e_tests",
        lambda *_a, **_kw: (
            e2e_failures.FailingE2ETest("e2e/other.spec.ts", "t", message="timed out"),
        ),
    )
    monkeypatch.setattr(
        _pr_branch, "fetch_diff_files", lambda *_a, **_kw: frozenset({"alan/x.py"})
    )

    def _boom(*_a: object, **_kw: object) -> object:
        raise AssertionError("patch fetch must be skipped when no tokens found")

    monkeypatch.setattr(_pr_flake_ledger, "fetch_pr_diff_patch", _boom)
    assert _pr_flake_ledger.diff_attributable_e2e_checks(
        _flaky_e2e_readiness(), 42, forge_target=DEFAULT_FORGE_TARGET,
    ) == ()


def test_diff_attributable_patch_none_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_branch,
        "fetch_failing_e2e_tests",
        lambda *_a, **_kw: (
            e2e_failures.FailingE2ETest(
                "e2e/other.spec.ts", "t",
                message="waiting for getByTestId('gone-token')",
            ),
        ),
    )
    monkeypatch.setattr(
        _pr_branch, "fetch_diff_files", lambda *_a, **_kw: frozenset({"alan/x.py"})
    )
    monkeypatch.setattr(_pr_flake_ledger, "fetch_pr_diff_patch", lambda *_a, **_kw: None)
    assert _pr_flake_ledger.diff_attributable_e2e_checks(
        _flaky_e2e_readiness(), 42, forge_target=DEFAULT_FORGE_TARGET,
    ) == ()


def test_spec_ledger_key_uses_basename() -> None:
    assert _pr_flake_ledger._spec_ledger_key("e2e/build-chat.spec.ts") == "spec:build-chat.spec.ts"
    assert _pr_flake_ledger._spec_ledger_key("plain.spec.ts") == "spec:plain.spec.ts"


def test_failing_e2e_spec_names_no_flaky_failing_skips_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_a: object, **_kw: object) -> object:
        raise AssertionError("no fetch expected without an allowlisted failing check")

    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", _boom)
    readiness = _gate_readiness(failing_checks=("sonar",))
    assert _pr_flake_ledger.failing_e2e_spec_names(
        1, readiness, forge_target=DEFAULT_FORGE_TARGET,
    ) == {}


def test_failing_e2e_spec_names_fetch_none_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", lambda *_a, **_kw: None)
    readiness = _gate_readiness(failing_checks=(_FLAKY_GATE,))
    assert _pr_flake_ledger.failing_e2e_spec_names(
        1, readiness, forge_target=DEFAULT_FORGE_TARGET,
    ) == {}


def test_failing_e2e_spec_names_dedupes_specs(
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
    readiness = _gate_readiness(failing_checks=(_FLAKY_GATE,))
    assert _pr_flake_ledger.failing_e2e_spec_names(
        1, readiness, forge_target=DEFAULT_FORGE_TARGET,
    ) == {
        _FLAKY_GATE: ("e2e/a.spec.ts", "e2e/b.spec.ts")
    }


def test_record_flake_occurrence_with_specs_records_spec_keys_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    monkeypatch.setattr(_pr_flake_ledger, "_has_open_flake_proposal", lambda *_a, **_kw: True)
    _pr_flake_ledger.record_flake_occurrence(
        7, (_E2E_GATE,), "task-x",
        rescued_specs={_E2E_GATE: ("e2e/a.spec.ts", "e2e/b.spec.ts")},
        forge_target=DEFAULT_FORGE_TARGET,
    )
    on_disk = json.loads(_pr_flake_ledger.flake_failures_path(DEFAULT_FORGE_TARGET).read_text())
    assert on_disk == {"spec:a.spec.ts": [7], "spec:b.spec.ts": [7]}


def test_record_flake_occurrence_without_specs_falls_back_to_check_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    monkeypatch.setattr(_pr_flake_ledger, "_has_open_flake_proposal", lambda *_a, **_kw: True)
    _pr_flake_ledger.record_flake_occurrence(
        8, (_E2E_GATE,), "task-x", rescued_specs={}, forge_target=DEFAULT_FORGE_TARGET,
    )
    on_disk = json.loads(_pr_flake_ledger.flake_failures_path(DEFAULT_FORGE_TARGET).read_text())
    assert on_disk == {_E2E_GATE: [8]}


def test_spec_proposal_title_and_body_name_the_spec() -> None:
    title = _pr_flake_ledger._flake_proposal_title("spec:build-chat.spec.ts")
    assert title == "Flake quarantine proposal: spec build-chat.spec.ts"
    body = _pr_flake_ledger._flake_proposal_body("spec:build-chat.spec.ts", [1, 2, 3])
    assert "alan/web/e2e/build-chat.spec.ts" in body
    assert "flaky-baseline.json" in body
    assert "allowlist" not in body.split("flaky-baseline.json")[0]


def test_spec_proposal_body_relocates_the_baseline_edit_to_its_own_pr() -> None:
    body = _pr_flake_ledger._flake_proposal_body("spec:build-chat.spec.ts", [1, 2, 3])
    assert "open a separate pull request whose only content is adding" in body
    assert "referencing this issue and the rerun-rescued evidence above" in body
    assert "add `build-chat.spec.ts` to `alan/web/e2e/flaky-baseline.json` so the" not in body


def test_spec_proposal_body_states_the_bright_line() -> None:
    body = _pr_flake_ledger._flake_proposal_body("spec:build-chat.spec.ts", [1, 2, 3])
    assert "do not widen any gate in response to this" in body.lower()
    assert (
        "never add this spec to the baseline from inside a pull request "
        "that the ratchet is blocking"
    ) in body.lower()


def test_check_proposal_body_no_longer_proposes_allowlisting() -> None:
    body = _pr_flake_ledger._flake_proposal_body(_E2E_GATE, [1, 2, 3])
    assert "should be added to the flaky-check allowlist" not in body
    assert "already sits on the shared flaky allowlist" in body


def test_spec_level_proposal_fires_at_threshold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    monkeypatch.setattr(_pr_flake_ledger, "_has_open_flake_proposal", lambda *_a, **_kw: False)
    monkeypatch.setattr(_pr_flake_ledger, "_ensure_flake_label", lambda **_kw: None)
    opened: list[tuple[str, str]] = []

    def _fake_open(title: str, body: str, **_kw: object) -> bool:
        opened.append((title, body))
        return True

    monkeypatch.setattr(_pr_flake_ledger, "_open_flake_issue", _fake_open)
    for pr in (11, 12, 13):
        _pr_flake_ledger.record_flake_occurrence(
            pr, (_E2E_GATE,), "task-x",
            rescued_specs={_E2E_GATE: ("e2e/chatty.spec.ts",)},
            forge_target=DEFAULT_FORGE_TARGET,
        )
    assert [t for t, _b in opened] == ["Flake quarantine proposal: spec chatty.spec.ts"]
    assert "#11, #12, #13" in opened[0][1]


_MOVED_PUBLIC_NAMES = (
    "SHARED_FLAKY_ALLOWLIST",
    "flaky_only_failing",
    "actionable_failing_checks",
    "max_gate_reruns",
    "flake_failures_path",
    "flake_rescued_checks",
    "failing_e2e_spec_names",
    "record_flake_occurrence",
    "gate_survived_checks_path",
    "load_gate_survived_checks",
    "flake_survived_checks",
    "record_flake_survival",
    "survived_at_head_checks",
    "evidenced_actionable_failing_checks",
    "fetch_pr_diff_patch",
    "diff_attributable_e2e_checks",
)


@pytest.mark.parametrize("name", _MOVED_PUBLIC_NAMES)
def test_moved_name_lives_only_on_flake_ledger(name: str) -> None:
    assert hasattr(_pr_flake_ledger, name)
    assert not hasattr(_pr_branch, name)
