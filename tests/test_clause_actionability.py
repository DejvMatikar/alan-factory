from __future__ import annotations

from alan.teams import _clause_actionability as ca_action
from alan.teams._clause_audit import ClauseAuditRow
from alan.teams._pr_ready import CheckOutcome, PrReadiness, ReadinessUnavailable

_CHECK = "Backend tests + coverage"


def _readiness(
    *, failing: tuple[str, ...] = (), pending: tuple[str, ...] = (),
    passing: tuple[str, ...] = (),
) -> PrReadiness:
    return PrReadiness(
        ok=not failing and not pending,
        merge_state_status="CLEAN",
        review_decision="APPROVED",
        failing_checks=failing,
        pending_checks=pending,
        passing_checks=passing,
    )


class TestCitedCheckName:
    def test_extracts_quoted_name_with_spaces(self) -> None:
        assert (
            ca_action._cited_check_name('local run failed check="Backend tests + coverage"')  # noqa: SLF001
            == "Backend tests + coverage"
        )

    def test_returns_none_without_citation(self) -> None:
        assert ca_action._cited_check_name("the file is missing") is None  # noqa: SLF001

    def test_returns_none_for_unquoted_check_mention(self) -> None:
        assert ca_action._cited_check_name("check=Backend tests") is None  # noqa: SLF001


class TestClassifyFailure:
    def test_pr_state_prefix_wins_regardless_of_readiness(self) -> None:
        outcome = ca_action.classify_failure("PR_STATE: isDraft is false", None)
        assert outcome is ca_action.ClauseActionability.PR_STATE

    def test_pr_state_prefix_wins_even_with_corroborating_readiness(self) -> None:
        outcome = ca_action.classify_failure(
            "PR_STATE: isDraft is false", _readiness(passing=(_CHECK,)),
        )
        assert outcome is ca_action.ClauseActionability.PR_STATE

    def test_none_readiness_is_actionable(self) -> None:
        outcome = ca_action.classify_failure(f'failed check="{_CHECK}"', None)
        assert outcome is ca_action.ClauseActionability.ACTIONABLE

    def test_gh_not_ok_is_actionable(self) -> None:
        outcome = ca_action.classify_failure(
            f'failed check="{_CHECK}"', ReadinessUnavailable("gh unavailable"),
        )
        assert outcome is ca_action.ClauseActionability.ACTIONABLE

    def test_no_check_citation_is_actionable(self) -> None:
        outcome = ca_action.classify_failure("the file is missing", _readiness())
        assert outcome is ca_action.ClauseActionability.ACTIONABLE

    def test_cited_check_absent_from_rollup_is_actionable(self) -> None:
        outcome = ca_action.classify_failure(f'failed check="{_CHECK}"', _readiness())
        assert outcome is ca_action.ClauseActionability.ACTIONABLE

    def test_cited_check_failing_is_actionable(self) -> None:
        outcome = ca_action.classify_failure(
            f'failed check="{_CHECK}"', _readiness(failing=(_CHECK,)),
        )
        assert outcome is ca_action.ClauseActionability.ACTIONABLE

    def test_cited_check_pending_is_actionable(self) -> None:
        outcome = ca_action.classify_failure(
            f'failed check="{_CHECK}"', _readiness(pending=(_CHECK,)),
        )
        assert outcome is ca_action.ClauseActionability.ACTIONABLE

    def test_corroborated_check_contradicted(self) -> None:
        outcome = ca_action.classify_failure(
            f'local pytest failed check="{_CHECK}"', _readiness(passing=(_CHECK,)),
        )
        assert outcome is ca_action.ClauseActionability.CHECK_CONTRADICTED

    def test_corroborated_host_env(self) -> None:
        outcome = ca_action.classify_failure(
            f'ENV: stale cache check="{_CHECK}"', _readiness(passing=(_CHECK,)),
        )
        assert outcome is ca_action.ClauseActionability.HOST_ENV

    def test_uncorroborated_env_nomination_stays_actionable(self) -> None:
        outcome = ca_action.classify_failure(
            f'ENV: stale cache check="{_CHECK}"', _readiness(failing=(_CHECK,)),
        )
        assert outcome is ca_action.ClauseActionability.ACTIONABLE

    def test_operator_only_evidence_classifies_as_operator_only(self) -> None:
        outcome = ca_action.classify_failure(
            "OPERATOR_ONLY: needs a 10-PR live batch on two separate days", None,
        )
        assert outcome is ca_action.ClauseActionability.OPERATOR_ONLY

    def test_operator_only_evidence_classifies_without_readiness(self) -> None:
        outcome = ca_action.classify_failure(
            "OPERATOR_ONLY: needs live traffic", ReadinessUnavailable("gh unavailable"),
        )
        assert outcome is ca_action.ClauseActionability.OPERATOR_ONLY

    def test_operator_only_evidence_wins_even_with_check_citation(self) -> None:
        outcome = ca_action.classify_failure(
            f'OPERATOR_ONLY: needs live traffic check="{_CHECK}"',
            _readiness(passing=(_CHECK,)),
        )
        assert outcome is ca_action.ClauseActionability.OPERATOR_ONLY

    def test_pr_state_evidence_unchanged_after_extraction(self) -> None:
        outcome = ca_action.classify_failure("PR_STATE: isDraft is false", _readiness())
        assert outcome is ca_action.ClauseActionability.PR_STATE


class TestPartition:
    def test_splits_actionable_and_unactionable(self) -> None:
        rows = (
            ClauseAuditRow(req_id="req-a", verdict="FAIL", evidence="the file is missing"),
            ClauseAuditRow(
                req_id="req-b", verdict="FAIL",
                evidence=f'ENV: stale cache check="{_CHECK}"',
            ),
        )
        result = ca_action.partition(rows, _readiness(passing=(_CHECK,)))
        assert result.actionable == (rows[0],)
        assert len(result.unactionable) == 1
        demoted = result.unactionable[0]
        assert demoted.row == rows[1]
        assert demoted.classification is ca_action.ClauseActionability.HOST_ENV
        assert demoted.check_name == _CHECK
        assert demoted.check_outcome is CheckOutcome.PASS

    def test_all_actionable_leaves_unactionable_empty(self) -> None:
        rows = (ClauseAuditRow(req_id="req-a", verdict="FAIL", evidence="missing"),)
        result = ca_action.partition(rows, _readiness())
        assert result.actionable == rows
        assert result.unactionable == ()

    def test_all_unactionable_leaves_actionable_empty(self) -> None:
        rows = (
            ClauseAuditRow(req_id="req-a", verdict="FAIL", evidence="PR_STATE: isDraft false"),
        )
        result = ca_action.partition(rows, None)
        assert result.actionable == ()
        assert len(result.unactionable) == 1
        assert result.unactionable[0].check_name is None
        assert result.unactionable[0].check_outcome is None

    def test_empty_rows_returns_empty_partition(self) -> None:
        result = ca_action.partition((), _readiness())
        assert result == ca_action.ClausePartition((), ())


class TestStandsDown:
    def test_true_when_every_demotion_corroborated(self) -> None:
        rows = (
            ClauseAuditRow(
                req_id="req-a", verdict="FAIL",
                evidence=f'ENV: stale cache check="{_CHECK}"',
            ),
        )
        parts = ca_action.partition(rows, _readiness(passing=(_CHECK,)))
        assert ca_action.stands_down(parts) is True

    def test_true_for_mixed_host_env_and_check_contradicted(self) -> None:
        rows = (
            ClauseAuditRow(
                req_id="req-a", verdict="FAIL",
                evidence=f'ENV: stale cache check="{_CHECK}"',
            ),
            ClauseAuditRow(
                req_id="req-b", verdict="FAIL",
                evidence=f'local pytest failed check="{_CHECK}"',
            ),
        )
        parts = ca_action.partition(rows, _readiness(passing=(_CHECK,)))
        assert ca_action.stands_down(parts) is True

    def test_false_when_pr_state_demotion_present(self) -> None:
        rows = (
            ClauseAuditRow(req_id="req-a", verdict="FAIL", evidence="PR_STATE: isDraft false"),
        )
        parts = ca_action.partition(rows, None)
        assert ca_action.stands_down(parts) is False

    def test_false_when_any_check_outcome_not_pass(self) -> None:
        row = ClauseAuditRow(req_id="req-a", verdict="FAIL", evidence="ENV: stale cache")
        demoted = ca_action.DemotedClause(
            row=row, classification=ca_action.ClauseActionability.HOST_ENV,
            check_name=_CHECK, check_outcome=CheckOutcome.FAIL_OR_PENDING,
        )
        parts = ca_action.ClausePartition((), (demoted,))
        assert ca_action.stands_down(parts) is False

    def test_false_when_unactionable_empty(self) -> None:
        parts = ca_action.ClausePartition((), ())
        assert ca_action.stands_down(parts) is False

    def test_false_when_actionable_nonempty(self) -> None:
        rows = (
            ClauseAuditRow(req_id="req-a", verdict="FAIL", evidence="the file is missing"),
            ClauseAuditRow(
                req_id="req-b", verdict="FAIL",
                evidence=f'ENV: stale cache check="{_CHECK}"',
            ),
        )
        parts = ca_action.partition(rows, _readiness(passing=(_CHECK,)))
        assert ca_action.stands_down(parts) is False

    def test_operator_only_batch_never_stands_down(self) -> None:
        rows = (
            ClauseAuditRow(
                req_id="req-a", verdict="FAIL", evidence="OPERATOR_ONLY: needs live traffic",
            ),
        )
        parts = ca_action.partition(rows, None)
        assert ca_action.stands_down(parts) is False

    def test_operator_only_never_stands_down_even_with_passing_check(self) -> None:
        row = ClauseAuditRow(
            req_id="req-a", verdict="FAIL", evidence="OPERATOR_ONLY: needs live traffic",
        )
        demoted = ca_action.DemotedClause(
            row=row, classification=ca_action.ClauseActionability.OPERATOR_ONLY,
            check_name=_CHECK, check_outcome=CheckOutcome.PASS,
        )
        parts = ca_action.ClausePartition((), (demoted,))
        assert ca_action.stands_down(parts) is False

    def test_cause_text_names_every_operator_only_clause(self) -> None:
        rows = (
            ClauseAuditRow(req_id="req-a", verdict="FAIL", evidence="OPERATOR_ONLY: batch 1"),
            ClauseAuditRow(req_id="req-b", verdict="FAIL", evidence="OPERATOR_ONLY: batch 2"),
        )
        parts = ca_action.partition(rows, None)
        text = ca_action.cause_text(parts.unactionable)
        assert "req-a" in text
        assert "req-b" in text
        assert "operator can take after merge" in text


def _row(evidence: str) -> ClauseAuditRow:
    return ClauseAuditRow(req_id="req-a", verdict="FAIL", evidence=evidence)


class TestCitesCheck:
    def test_true_when_a_row_cites_a_check(self) -> None:
        assert ca_action.cites_check((_row("no citation"), _row(f'failed check="{_CHECK}"')))

    def test_false_when_no_row_cites_a_check(self) -> None:
        assert not ca_action.cites_check((_row("the file is missing"),))

    def test_false_for_an_empty_batch(self) -> None:
        assert not ca_action.cites_check(())

    def test_false_when_the_only_citation_sits_under_an_operator_only_prefix(self) -> None:
        assert not ca_action.cites_check((_row(f'OPERATOR_ONLY: later check="{_CHECK}"'),))

    def test_false_when_the_only_citation_sits_under_a_pr_state_prefix(self) -> None:
        assert not ca_action.cites_check((_row(f'PR_STATE: draft check="{_CHECK}"'),))


def _at(head_sha: str) -> PrReadiness:
    return PrReadiness(
        ok=True, merge_state_status="CLEAN", review_decision="APPROVED", head_sha=head_sha,
    )


class TestSameHead:
    def test_returns_the_decision_read_when_the_head_is_unchanged(self) -> None:
        decision = _at("a" * 40)
        assert ca_action.same_head(_at("a" * 40), decision) is decision

    def test_none_when_the_head_moved(self) -> None:
        assert ca_action.same_head(_at("a" * 40), _at("b" * 40)) is None

    def test_none_without_a_begin_read(self) -> None:
        assert ca_action.same_head(None, _at("a" * 40)) is None

    def test_none_when_the_begin_read_has_no_head(self) -> None:
        assert ca_action.same_head(_at(""), _at("")) is None


class TestCheckNameLines:
    def test_empty_without_a_readiness(self) -> None:
        assert ca_action.check_name_lines(None) == []

    def test_empty_when_the_forge_read_failed(self) -> None:
        assert ca_action.check_name_lines(ReadinessUnavailable("gh unavailable")) == []

    def test_empty_when_the_head_has_no_checks(self) -> None:
        assert ca_action.check_name_lines(_readiness()) == []

    def test_lists_every_state_sorted_and_deduplicated(self) -> None:
        lines = ca_action.check_name_lines(
            _readiness(passing=("b", "a"), pending=("c", "a"), failing=("d",))
        )
        assert lines[2:] == ["- a", "- b", "- c", "- d"]

    def test_opens_with_a_blank_line_so_a_free_text_pr_body_cannot_run_into_it(self) -> None:
        lines = ca_action.check_name_lines(_readiness(passing=("a",)))
        assert lines[0] == ""
        assert lines[1].startswith("Required-check names")

    def test_never_renders_a_conclusion(self) -> None:
        text = "\n".join(
            ca_action.check_name_lines(_readiness(passing=("a",), pending=("b",), failing=("c",)))
        ).lower()
        assert "passed" not in text
        assert "pending" not in text
        assert "failing" not in text
