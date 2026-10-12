from __future__ import annotations

from typing import Any, cast

import pytest

from alan.teams import _pr_merge_checks, _pr_merge_conflict, _pr_merge_escalation


def test_unresolved_ceiling_matches_classifier_constant() -> None:
    assert _pr_merge_escalation.UNRESOLVED_CEILING == _pr_merge_checks._UNRESOLVED_CEILING


def test_concurrent_blockers_single_failed() -> None:
    ctx: dict[str, Any] = {"checks": [{"name": "ci", "state": "FAILURE"}]}
    assert _pr_merge_escalation.concurrent_blockers("failed", ctx) == ("failed",)


def test_concurrent_blockers_failed_plus_unresolved() -> None:
    ctx: dict[str, Any] = {
        "checks": [{"name": "ci", "state": "FAILURE"}],
        "threads": 2, "comments": 1, "total_threads": 2, "total_comments": 1,
    }
    blockers = _pr_merge_escalation.concurrent_blockers("failed", ctx)
    assert blockers == ("failed", "unresolved")


def test_concurrent_blockers_failed_plus_ceiling_plus_copilot() -> None:
    ctx: dict[str, Any] = {
        "checks": [{"name": "ci", "state": "FAILURE"}],
        "threads": 0, "comments": 0,
        "total_threads": 60, "total_comments": 50,
        "copilot_threads_count": 2, "copilot_comments_count": 1,
    }
    blockers = _pr_merge_escalation.concurrent_blockers("failed", ctx)
    assert blockers == ("failed", "unresolved-ceiling", "copilot-unresolved")


def test_concurrent_blockers_unresolved_leading_kind_not_duplicated() -> None:
    ctx: dict[str, Any] = {"threads": 2, "comments": 0, "total_threads": 2, "total_comments": 0}
    assert _pr_merge_escalation.concurrent_blockers("unresolved", ctx) == ("unresolved",)


def test_concurrent_blockers_inserts_leading_kind_when_not_naturally_present() -> None:
    ctx: dict[str, Any] = {"copilot_threads_count": 1, "copilot_comments_count": 0}
    blockers = _pr_merge_escalation.concurrent_blockers("unresolved", ctx)
    assert blockers == ("unresolved", "copilot-unresolved")


def test_concurrent_blockers_timeout_alone() -> None:
    ctx: dict[str, Any] = {"elapsed": 600.0, "polls": 20}
    assert _pr_merge_escalation.concurrent_blockers("timeout", ctx) == ("timeout",)


def test_concurrent_blockers_error_alone() -> None:
    ctx: dict[str, Any] = {"error": "boom"}
    assert _pr_merge_escalation.concurrent_blockers("error", ctx) == ("error",)


def test_checks_reason_names_every_concurrent_blocker_ahead_of_the_recovery_prose() -> None:
    ctx: dict[str, Any] = {
        "checks": [{"name": "Backend tests", "state": "FAILURE"}],
        "threads": 2, "comments": 1, "total_threads": 2, "total_comments": 1,
    }
    reason = _pr_merge_escalation.checks_reason("failed", 42, ctx)
    assert reason.startswith("failed: blockers: failed, unresolved. ")
    assert "Backend tests" in reason
    assert "push a new commit" in reason


def test_checks_reason_omits_the_blockers_prefix_for_a_single_blocker() -> None:
    ctx: dict[str, Any] = {"checks": [{"name": "Backend tests", "state": "FAILURE"}]}
    reason = _pr_merge_escalation.checks_reason("failed", 42, ctx)
    assert reason.startswith("failed: PR #42 failed checks:")
    assert "blockers:" not in reason


def test_checks_detail_unresolved_ceiling_uses_shared_constant() -> None:
    detail = _pr_merge_escalation.checks_detail(
        "unresolved_ceiling", 42, {"total_threads": 150, "total_comments": 5}
    )
    assert f"> {_pr_merge_escalation.UNRESOLVED_CEILING} ceiling" in detail


def test_unresolved_human_reason_counts_threads_and_comments() -> None:
    reason = _pr_merge_escalation.unresolved_human_reason(42, {"threads": 3, "comments": 2})
    assert reason == "PR #42 has 3 unresolved review thread(s) and 2 unresolved human comment(s)."


def test_merge_block_options_failed_maps_fix_option_to_implementation_fix() -> None:
    options, recommended, resolvers = _pr_merge_escalation.merge_block_options("failed")
    assert resolvers == {options[0]: "implementation_fix"}
    assert recommended == options[0]


@pytest.mark.parametrize(
    "kind", ["no_pr", "timeout", "unresolved", "copilot_unresolved", "conflict"]
)
def test_merge_block_options_excluded_kinds_have_no_resolvers(kind: str) -> None:
    _options, _recommended, resolvers = _pr_merge_escalation.merge_block_options(kind)
    assert resolvers == {}


def test_merge_block_options_timeout_defaults_to_the_unsettled_checks_options() -> None:
    options, recommended, resolvers = _pr_merge_escalation.merge_block_options("timeout")
    assert options == list(_pr_merge_escalation.TIMEOUT_OPTIONS)
    assert recommended == _pr_merge_escalation.TIMEOUT_OPTIONS[0]
    assert resolvers == {}


def test_merge_block_options_timeout_with_a_change_request_blocker_offers_review_options() -> None:
    options, recommended, resolvers = _pr_merge_escalation.merge_block_options(
        "timeout", timeout_blocker="changes_requested",
    )
    assert options == list(_pr_merge_escalation.SETTLED_REVIEW_OPTIONS)
    assert recommended == _pr_merge_escalation.SETTLED_REVIEW_OPTIONS[0]
    assert resolvers == {}


def test_settled_review_options_are_not_premised_on_unsettled_checks() -> None:
    for option in _pr_merge_escalation.SETTLED_REVIEW_OPTIONS:
        assert "settl" not in option.lower()
        assert "workflow" not in option.lower()
        assert "review" in option.lower()
        assert "re-dispatch pr_merge" in option


def test_settled_review_options_mirror_the_unresolved_options_verbs() -> None:
    dismiss = _pr_merge_escalation.SETTLED_REVIEW_OPTIONS[1]
    assert dismiss.startswith("Dismiss the stale")
    assert "if it no longer applies" in dismiss
    address = _pr_merge_escalation.SETTLED_REVIEW_OPTIONS[0]
    assert address.startswith("Address the requested changes")


@pytest.mark.parametrize(
    "kind", ["no_pr", "failed", "unresolved", "copilot_unresolved", "conflict"]
)
def test_merge_block_options_other_kinds_ignore_the_timeout_blocker(kind: str) -> None:
    assert _pr_merge_escalation.merge_block_options(
        kind, timeout_blocker="changes_requested"
    ) == _pr_merge_escalation.merge_block_options(kind)


def test_timeout_reason_with_a_review_state_line_names_the_decision_and_head() -> None:
    state = (
        "Recorded state at give-up: no failing and no pending checks, but review "
        "decision CHANGES_REQUESTED stands at head 0123456789ab — that change-request "
        "review, not the checks, is what blocks the merge."
    )
    ctx: dict[str, Any] = {
        "elapsed": 630.0, "polls": 21, "blocked_no_signal": False,
        "timeout_state": state, "timeout_blocker": "changes_requested",
    }
    reason = _pr_merge_escalation.checks_reason("timeout", 42, ctx)
    assert "CHANGES_REQUESTED stands at head 0123456789ab" in reason
    assert "stuck workflow run" not in reason
    assert "not settling" not in reason


def test_merge_block_options_conflict_matches_conflict_module() -> None:
    options, recommended, resolvers = _pr_merge_escalation.merge_block_options("conflict")
    assert options == list(_pr_merge_conflict.CONFLICT_OPTIONS)
    assert recommended == _pr_merge_conflict.CONFLICT_OPTIONS[0]
    assert resolvers == {}


def test_merge_block_options_unknown_kind_raises() -> None:
    with pytest.raises(ValueError, match="unexpected kind"):
        _pr_merge_escalation.merge_block_options("bogus")


def test_copilot_cap_reason_uses_shared_label_and_recipe() -> None:
    reason = _pr_merge_escalation.copilot_cap_reason(42, 3, 3)
    assert reason.startswith("copilot-unresolved: ")
    assert _pr_merge_escalation.COPILOT_CAP_RECOVERY_RECIPE in reason


def test_copilot_recovery_recipe_default_matches_bare_script() -> None:
    recipe = _pr_merge_escalation.copilot_recovery_recipe()
    assert "scripts/alan_copilot_resolve.py" in recipe
    assert "--repo" not in recipe
    assert "copilot_resolution.yaml" in recipe


def test_copilot_recovery_recipe_second_engine_adds_repo_flag() -> None:
    recipe = _pr_merge_escalation.copilot_recovery_recipe("acme-corp/widget-app")
    assert "scripts/alan_copilot_resolve.py --repo acme-corp/widget-app" in recipe


def test_checks_reason_copilot_unresolved_second_engine_qualifies_script() -> None:
    ctx: dict[str, object] = {
        "copilot_threads_count": 1, "copilot_threads": [],
        "copilot_comments_count": 0, "copilot_comments": [],
        "repo": "acme-corp/widget-app",
    }
    reason = _pr_merge_escalation.checks_reason("copilot_unresolved", 7, ctx)
    assert "scripts/alan_copilot_resolve.py --repo acme-corp/widget-app" in reason


def test_copilot_cap_recovery_recipe_second_engine_names_ack_ledger() -> None:
    recipe = _pr_merge_escalation.copilot_cap_recovery_recipe("acme-corp/widget-app")
    assert "scripts/alan_copilot_resolve.py --repo acme-corp/widget-app" in recipe
    assert "acme-corp/widget-app/pr_<n>_ack_budget.json" in recipe


def test_copilot_cap_recovery_recipe_default_matches_static_binding() -> None:
    assert (
        _pr_merge_escalation.copilot_cap_recovery_recipe()
        == _pr_merge_escalation.COPILOT_CAP_RECOVERY_RECIPE
    )


def test_copilot_ack_option_second_engine_adds_repo_flag() -> None:
    option = _pr_merge_escalation.copilot_ack_option("acme-corp/widget-app")
    assert "scripts/alan_copilot_resolve.py --repo acme-corp/widget-app" in option


def test_copilot_options_second_engine_both_entries_qualified() -> None:
    options = _pr_merge_escalation.copilot_options("acme-corp/widget-app")
    assert len(options) == 2
    for option in options:
        assert "acme-corp/widget-app" in option


def test_merge_block_options_copilot_unresolved_second_engine() -> None:
    options, recommended, resolvers = _pr_merge_escalation.merge_block_options(
        "copilot_unresolved", repo_slug="acme-corp/widget-app",
    )
    assert recommended == options[0]
    assert resolvers == {}
    assert any("acme-corp/widget-app" in o for o in options)


def test_copilot_cap_reason_second_engine_qualifies_recipe() -> None:
    reason = _pr_merge_escalation.copilot_cap_reason(
        42, 3, 3, repo_slug="acme-corp/widget-app",
    )
    assert "acme-corp/widget-app" in reason


@pytest.mark.parametrize("kind", ["codeowners_blocked", "codeowners_unsupported"])
def test_codeowners_options_never_route_to_merge(kind: str) -> None:
    options, recommended, resolvers = _pr_merge_escalation.merge_block_options(kind)
    assert resolvers == {}
    assert recommended == options[0]
    for option in options:
        assert "re-dispatch pr_merge" not in option
    checks_kind = cast(_pr_merge_checks.ChecksKind, kind)
    assert "re-dispatch pr_merge" not in _pr_merge_escalation.CHECKS_RECOVERY_RECIPE[checks_kind]


def test_codeowners_reason_carries_pinned_ownership_wording() -> None:
    blocked_reason = _pr_merge_escalation.checks_reason("codeowners_blocked", 42, {})
    assert blocked_reason.startswith("codeowners-blocked: ")
    assert (
        "file ownership (.github/CODEOWNERS on the PR's base branch) requires "
        "owner review for a path it touches, or ownership could not be verified."
    ) in blocked_reason

    unsupported_reason = _pr_merge_escalation.checks_reason("codeowners_unsupported", 42, {})
    assert unsupported_reason.startswith("codeowners-unsupported: ")
    assert (
        "file ownership could not be evaluated because .github/CODEOWNERS on "
        "the PR's base branch uses syntax the merge worker does not support."
    ) in unsupported_reason


def test_merge_block_options_still_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="unexpected kind"):
        _pr_merge_escalation.merge_block_options("still-bogus")
