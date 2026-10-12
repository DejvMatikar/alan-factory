from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from alan.engine_config import ForgeTarget
from alan.teams import _pr_ready
from tests import _advisory_check_runs_support as support

_SONAR_LISTING = [support.check_run(support.SONAR, support.SONAR_APP)]


@pytest.fixture
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(root))
    return root


def _advisory(
    readiness: _pr_ready.PrReadiness, forge_target: ForgeTarget = support.FORGE,
) -> tuple[str, ...]:
    return _pr_ready.advisory_failing_checks(
        readiness, 42, support.TASK_ID, forge_target=forge_target,
    )


def _sonar_red(**replacements: Any) -> _pr_ready.PrReadiness:
    return support.merge_readiness([support.failing_run(support.SONAR)], **replacements)


def test_failing_status_contexts_collect_only_failing_contexts_without_a_name() -> None:
    readiness = support.merge_readiness([
        support.failing_run(support.BACKEND),
        support.failing_context("legacy/ci"),
        {"context": "legacy/lint", "state": "SUCCESS"},
        {"name": "named", "context": "shadowed", "state": "FAILURE"},
        {"state": "FAILURE"},
        "not-a-dict",
    ])
    assert readiness.failing_status_contexts == ("legacy/ci",)


def test_failing_status_contexts_empty_when_rollup_is_not_a_list() -> None:
    parsed = _pr_ready._parse_readiness_payload(  # noqa: SLF001
        {"mergeStateStatus": "CLEAN", "statusCheckRollup": None}
    )
    assert parsed is not None
    assert parsed.failing_status_contexts == ()


def test_third_party_app_check_run_is_advisory() -> None:
    shas = support.serve_check_runs(_SONAR_LISTING)
    assert _advisory(_sonar_red()) == (support.SONAR,)
    assert shas == [support.HEAD_SHA]


def test_github_actions_check_run_is_blocking() -> None:
    support.serve_check_runs([support.check_run(support.DRIFT_GATE, support.ACTIONS_APP)])
    readiness = support.merge_readiness([support.failing_run(support.DRIFT_GATE)])
    assert _advisory(readiness) == ()


def test_check_run_without_an_app_is_blocking() -> None:
    support.serve_check_runs([support.app_less_run(support.SONAR)])
    assert _advisory(_sonar_red()) == ()


def test_check_run_with_a_null_app_is_blocking() -> None:
    support.serve_check_runs([{"name": support.SONAR, "app": None}])
    assert _advisory(_sonar_red()) == ()


def test_check_run_with_an_app_but_no_slug_is_blocking() -> None:
    support.serve_check_runs([{"name": support.SONAR, "app": {}}])
    assert _advisory(_sonar_red()) == ()


@pytest.mark.parametrize(
    "slug", [{"unexpected": "object"}, ["sonarqubecloud"], 7, True, None, ""],
    ids=["object", "list", "number", "bool", "null", "empty"],
)
def test_check_run_with_a_malformed_slug_is_blocking(slug: object) -> None:
    support.serve_check_runs([{"name": support.SONAR, "app": {"slug": slug}}])
    assert _advisory(_sonar_red()) == ()


@pytest.mark.parametrize(
    "element", [None, "text", 7, ["nested"]], ids=["null", "string", "number", "list"],
)
def test_listing_with_a_non_object_element_is_blocking_and_audited(
    audit_root: Path, element: object,
) -> None:
    support.serve_check_runs([element, *_SONAR_LISTING])
    assert _advisory(_sonar_red()) == ()
    [row] = support.impl_audit_rows(audit_root, "advisory_check_runs_query_failed")
    assert row["task_id"] == support.TASK_ID
    assert row["pr_number"] == 42
    assert row["reason"] == "check_runs_element_malformed"


def test_check_absent_from_the_listing_is_blocking() -> None:
    support.serve_check_runs([support.check_run("other", support.SONAR_APP)])
    assert _advisory(_sonar_red()) == ()


def test_same_named_runs_must_all_be_third_party() -> None:
    support.serve_check_runs([
        support.check_run(support.SONAR, support.SONAR_APP),
        support.check_run(support.SONAR, support.ACTIONS_APP),
    ])
    assert _advisory(_sonar_red()) == ()


def test_status_context_sharing_a_check_run_name_is_blocking() -> None:
    shas = support.serve_check_runs(_SONAR_LISTING)
    readiness = support.merge_readiness([
        support.failing_run(support.SONAR), support.failing_context(support.SONAR),
    ])
    assert _advisory(readiness) == ()
    assert shas == []


def test_authoritative_check_is_blocking_without_a_listing_read() -> None:
    shas = support.serve_check_runs(_SONAR_LISTING)
    assert _advisory(_sonar_red(), support.AUTHORITATIVE_SONAR_FORGE) == ()
    assert shas == []


def test_no_red_check_reads_no_listing() -> None:
    shas = support.serve_check_runs([])
    green = support.merge_readiness([support.passing_run("ci")], merge_state="CLEAN")
    assert _advisory(green) == ()
    assert shas == []


def test_missing_head_sha_reads_no_listing() -> None:
    shas = support.serve_check_runs(_SONAR_LISTING)
    assert _advisory(_sonar_red(head_sha="")) == ()
    assert shas == []


def test_listing_failure_is_blocking_and_audited(audit_root: Path) -> None:
    support.serve_check_runs(None)
    assert _advisory(_sonar_red()) == ()
    [row] = support.impl_audit_rows(audit_root, "advisory_check_runs_query_failed")
    assert row["task_id"] == support.TASK_ID
    assert row["pr_number"] == 42
    assert row["reason"] == "gh returncode=1: rate limited"


def test_blocking_red_next_to_advisory_red_returns_nothing() -> None:
    support.serve_check_runs([
        *_SONAR_LISTING, support.check_run(support.BACKEND, support.ACTIONS_APP),
    ])
    readiness = support.merge_readiness([
        support.failing_run(support.SONAR), support.failing_run(support.BACKEND),
    ])
    assert _advisory(readiness) == ()


def test_status_context_red_next_to_advisory_red_returns_nothing_without_a_read() -> None:
    shas = support.serve_check_runs(_SONAR_LISTING)
    readiness = support.merge_readiness([
        support.failing_run(support.SONAR), support.failing_context("legacy/ci"),
    ])
    assert _advisory(readiness) == ()
    assert shas == []


def test_quarantined_red_does_not_veto_an_advisory_red() -> None:
    support.serve_check_runs([
        *_SONAR_LISTING, support.check_run(support.BACKEND, support.ACTIONS_APP),
    ])
    readiness = support.merge_readiness(
        [support.failing_run(support.SONAR), support.failing_run(support.BACKEND)],
        quarantined_checks=(support.BACKEND,),
    )
    assert _advisory(readiness) == (support.SONAR,)


def test_only_quarantined_reds_that_cannot_be_advisory_read_no_listing() -> None:
    shas = support.serve_check_runs(_SONAR_LISTING)
    readiness = _sonar_red(quarantined_checks=(support.SONAR,))
    assert _advisory(readiness, support.AUTHORITATIVE_SONAR_FORGE) == ()
    assert shas == []


def test_every_advisory_red_is_returned_in_rollup_order() -> None:
    support.serve_check_runs([
        support.check_run("Coverage comment", "codecov"),
        support.check_run(support.SONAR, support.SONAR_APP),
    ])
    readiness = support.merge_readiness([
        support.failing_run(support.SONAR), support.failing_run("Coverage comment"),
    ])
    assert _advisory(readiness) == (support.SONAR, "Coverage comment")


def _third_party(
    readiness: _pr_ready.PrReadiness, forge_target: ForgeTarget = support.FORGE,
) -> frozenset[str]:
    return _pr_ready.third_party_advisory_checks(
        readiness, 42, support.TASK_ID, forge_target=forge_target,
    )


def test_third_party_advisory_checks_names_a_sonar_red_beside_a_blocking_red() -> None:
    support.serve_check_runs([
        support.check_run(support.SONAR, support.SONAR_APP),
        support.check_run(support.BACKEND, support.ACTIONS_APP),
    ])
    readiness = support.merge_readiness(
        [support.failing_run(support.SONAR), support.failing_run(support.BACKEND)]
    )
    assert _third_party(readiness) == frozenset({support.SONAR})
    assert _advisory(readiness) == ()


def test_third_party_advisory_checks_skips_the_listing_when_every_red_is_authoritative() -> None:
    shas = support.serve_check_runs(_SONAR_LISTING)
    assert _third_party(_sonar_red(), support.AUTHORITATIVE_SONAR_FORGE) == frozenset()
    assert shas == []


def test_third_party_advisory_checks_is_empty_when_the_listing_is_unreadable(
    audit_root: Path,
) -> None:
    support.serve_check_runs(None)
    assert _third_party(_sonar_red()) == frozenset()
    assert support.impl_audit_rows(audit_root, "advisory_check_runs_query_failed")
