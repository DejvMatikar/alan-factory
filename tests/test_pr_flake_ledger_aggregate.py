from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from alan.engine_config import ForgeTarget, RepoIdentity
from alan.teams import _pr_flake_ledger
from alan.teams._pr_ready import PrReadiness, ReadinessUnavailable

_E2E = "Frontend E2E (Playwright, blocking)"
_GATE = "gate"
_LINT = "Widget Lint"
_SECOND_AGGREGATE = "Widget Roll-up"
_HEAD = "d" * 40

_COPY = ForgeTarget(
    repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
    credential_env_name="ACME_WIDGET_GH_TOKEN",
    authoritative_checks=frozenset({_GATE}),
    aggregate_checks=frozenset({_GATE}),
)
_UNDECLARED = _COPY.model_copy(update={"aggregate_checks": frozenset()})
_TWO_AGGREGATES = _COPY.model_copy(
    update={
        "authoritative_checks": frozenset({_GATE, _SECOND_AGGREGATE}),
        "aggregate_checks": frozenset({_GATE, _SECOND_AGGREGATE}),
    }
)
_NOT_AUTHORITATIVE = _COPY.model_copy(update={"authoritative_checks": frozenset()})


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))


def _readiness(
    failing: tuple[str, ...], *, quarantined: tuple[str, ...] = (),
) -> PrReadiness:
    return PrReadiness(
        ok=False,
        merge_state_status="BLOCKED",
        review_decision="REVIEW_REQUIRED",
        failing_checks=failing,
        pending_checks=("Widget Deploy Preview",),
        passing_checks=("Widget Typecheck",),
        failed_reasons=(f"failing checks: {', '.join(failing)}",),
        quarantined_checks=quarantined,
        head_sha=_HEAD,
        head_ref_name="feature/widget",
        check_count=len(failing) + 2,
        failing_status_contexts=("widget/context",),
    )


def test_empty_declaration_returns_the_same_object() -> None:
    readiness = _readiness((_E2E, _GATE))
    assert _pr_flake_ledger.aggregate_set_aside(readiness, _UNDECLARED) is readiness


def test_aggregate_failing_alone_returns_the_same_object() -> None:
    readiness = _readiness((_GATE,))
    assert _pr_flake_ledger.aggregate_set_aside(readiness, _COPY) is readiness


def test_aggregate_absent_from_the_failing_checks_returns_the_same_object() -> None:
    readiness = _readiness((_E2E, _LINT))
    assert _pr_flake_ledger.aggregate_set_aside(readiness, _COPY) is readiness


def test_aggregate_plus_flaky_job_drops_the_aggregate() -> None:
    viewed = _pr_flake_ledger.aggregate_set_aside(_readiness((_E2E, _GATE)), _COPY)
    assert viewed.failing_checks == (_E2E,)


def test_aggregate_plus_non_allowlisted_job_drops_the_aggregate() -> None:
    viewed = _pr_flake_ledger.aggregate_set_aside(_readiness((_LINT, _GATE)), _COPY)
    assert viewed.failing_checks == (_LINT,)


def test_aggregate_plus_quarantined_failing_check_drops_the_aggregate() -> None:
    readiness = _readiness((_GATE, _LINT), quarantined=(_LINT,))
    viewed = _pr_flake_ledger.aggregate_set_aside(readiness, _COPY)
    assert viewed.failing_checks == (_LINT,)
    assert viewed.quarantined_checks == (_LINT,)


def test_two_aggregates_failing_alone_return_the_same_object() -> None:
    readiness = _readiness((_GATE, _SECOND_AGGREGATE))
    assert _pr_flake_ledger.aggregate_set_aside(readiness, _TWO_AGGREGATES) is readiness


def test_two_aggregates_plus_flaky_job_drop_both_aggregates() -> None:
    readiness = _readiness((_GATE, _E2E, _SECOND_AGGREGATE))
    viewed = _pr_flake_ledger.aggregate_set_aside(readiness, _TWO_AGGREGATES)
    assert viewed.failing_checks == (_E2E,)


def test_remaining_failing_checks_keep_their_order() -> None:
    readiness = _readiness((_LINT, _GATE, _E2E, "Widget Docs"))
    viewed = _pr_flake_ledger.aggregate_set_aside(readiness, _COPY)
    assert viewed.failing_checks == (_LINT, _E2E, "Widget Docs")


def test_declared_but_not_authoritative_aggregate_is_left_alone() -> None:
    readiness = _readiness((_E2E, _GATE))
    assert _pr_flake_ledger.aggregate_set_aside(readiness, _NOT_AUTHORITATIVE) is readiness


def test_every_other_field_is_preserved() -> None:
    readiness = _readiness((_E2E, _GATE), quarantined=())
    viewed = _pr_flake_ledger.aggregate_set_aside(readiness, _COPY)
    assert viewed == replace(readiness, failing_checks=(_E2E,))
    assert viewed.failed_reasons == readiness.failed_reasons
    assert viewed.pending_checks == readiness.pending_checks
    assert viewed.passing_checks == readiness.passing_checks
    assert viewed.failing_status_contexts == readiness.failing_status_contexts
    assert viewed.head_sha == readiness.head_sha


def test_a_red_readiness_never_turns_green() -> None:
    viewed = _pr_flake_ledger.aggregate_set_aside(_readiness((_E2E, _GATE)), _COPY)
    assert viewed.ok is False
    assert viewed.failed_reasons


def test_the_input_readiness_is_not_mutated() -> None:
    readiness = _readiness((_E2E, _GATE))
    _pr_flake_ledger.aggregate_set_aside(readiness, _COPY)
    assert readiness.failing_checks == (_E2E, _GATE)


def test_the_view_is_idempotent() -> None:
    once = _pr_flake_ledger.aggregate_set_aside(_readiness((_E2E, _GATE)), _COPY)
    assert _pr_flake_ledger.aggregate_set_aside(once, _COPY) is once


def test_an_unavailable_readiness_passes_through_the_result_view() -> None:
    unavailable = ReadinessUnavailable("forge unavailable")
    assert _pr_flake_ledger.aggregate_set_aside_result(unavailable, _COPY) is unavailable


def test_an_available_readiness_goes_through_the_result_view() -> None:
    viewed = _pr_flake_ledger.aggregate_set_aside_result(_readiness((_E2E, _GATE)), _COPY)
    assert isinstance(viewed, PrReadiness)
    assert viewed.failing_checks == (_E2E,)


@pytest.mark.parametrize(
    ("predicate", "parameters"),
    [
        (_pr_flake_ledger.flaky_only_failing, ["readiness"]),
        (_pr_flake_ledger.actionable_failing_checks, ["readiness"]),
        (_pr_flake_ledger.rebase_blocking_failing_checks, ["readiness"]),
        (
            _pr_flake_ledger.flake_survived_checks,
            ["before", "after", "head_sha_before", "head_sha_after"],
        ),
    ],
    ids=["flaky_only", "actionable", "rebase_blocking", "survived"],
)
def test_flake_predicates_keep_their_signatures(
    predicate: Callable[..., object], parameters: list[str]
) -> None:
    assert list(inspect.signature(predicate).parameters) == parameters


def test_evidenced_actionable_list_ignores_the_aggregate_without_a_head() -> None:
    readiness = _readiness((_E2E, _LINT, _GATE))
    actionable = _pr_flake_ledger.evidenced_actionable_failing_checks(
        readiness, 42, None, forge_target=_COPY
    )
    assert actionable == (_LINT,)


def test_evidenced_actionable_list_is_empty_for_flaky_plus_aggregate_without_a_head() -> None:
    actionable = _pr_flake_ledger.evidenced_actionable_failing_checks(
        _readiness((_E2E, _GATE)), 42, None, forge_target=_COPY
    )
    assert actionable == ()


def test_evidenced_actionable_list_still_names_the_aggregate_on_an_undeclared_target() -> None:
    actionable = _pr_flake_ledger.evidenced_actionable_failing_checks(
        _readiness((_E2E, _GATE)), 42, _HEAD, forge_target=_UNDECLARED
    )
    assert actionable == (_GATE,)


def test_evidenced_actionable_list_keeps_a_survivor_and_drops_the_aggregate() -> None:
    _pr_flake_ledger.record_flake_survival(42, (_E2E,), _HEAD, forge_target=_COPY)
    actionable = _pr_flake_ledger.evidenced_actionable_failing_checks(
        _readiness((_E2E, _GATE)), 42, _HEAD, forge_target=_COPY
    )
    assert actionable == (_E2E,)
