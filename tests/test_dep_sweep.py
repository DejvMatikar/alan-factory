from __future__ import annotations

import pytest

from alan.dep_sweep import (
    PeerCapBlock,
    Proposal,
    ProposalBatch,
    ProposalClass,
    classify_proposal,
    group_proposals,
    merge_target,
    parse_peer_cap,
)

_UV_LOCK = frozenset({"uv.lock"})
_NPM_LOCK = frozenset({"alan/web/package-lock.json"})
_BOTH_LOCKS = _UV_LOCK | _NPM_LOCK


def _proposal(
    number: int,
    *,
    state: str = "open",
    mergeable: str = "MERGEABLE",
    lockfiles: frozenset[str] = frozenset(),
    upstream_block: PeerCapBlock | None = None,
) -> Proposal:
    return Proposal(
        number=number,
        state=state,
        mergeable=mergeable,
        lockfiles=lockfiles,
        upstream_block=upstream_block,
    )


_REAL_ERESOLVE = (
    "npm error code ERESOLVE\n"
    "npm error Conflicting peer dependency: typescript@6.0.3\n"
    "npm error node_modules/typescript\n"
    'npm error   peer typescript@">=4.8.4 <6.1.0" from typescript-eslint@8.64.0\n'
    "npm error   node_modules/typescript-eslint\n"
    'npm error     dev typescript-eslint@"^8.64.0" from the root project'
)


class TestClassifyProposal:
    def test_open_mergeable_is_merge_ready(self) -> None:
        assert classify_proposal(_proposal(1)) == ProposalClass.MERGE_READY

    def test_open_unknown_mergeable_is_merge_ready(self) -> None:
        assert classify_proposal(_proposal(1, mergeable="UNKNOWN")) == ProposalClass.MERGE_READY

    def test_open_conflicting_is_needs_rebase(self) -> None:
        assert (
            classify_proposal(_proposal(1, mergeable="CONFLICTING")) == ProposalClass.NEEDS_REBASE
        )

    def test_closed_is_skip(self) -> None:
        assert classify_proposal(_proposal(1, state="closed")) == ProposalClass.SKIP

    def test_merged_is_skip(self) -> None:
        assert classify_proposal(_proposal(1, state="merged")) == ProposalClass.SKIP


class TestParsePeerCap:
    def test_real_eresolve_block_parses_dependency_and_cap(self) -> None:
        assert parse_peer_cap(_REAL_ERESOLVE) == PeerCapBlock(
            dependency="typescript",
            capping_package="typescript-eslint@8.64.0",
            peer_range=">=4.8.4 <6.1.0",
        )

    def test_empty_log_is_none(self) -> None:
        assert parse_peer_cap("") is None

    def test_conflict_without_peer_line_is_none(self) -> None:
        assert parse_peer_cap("Conflicting peer dependency: typescript@6.0.3") is None

    def test_peer_line_for_other_dependency_is_none(self) -> None:
        log = (
            "Conflicting peer dependency: typescript@6.0.3\n"
            '  peer react@"^18" from some-lib@1.0.0'
        )
        assert parse_peer_cap(log) is None

    def test_scoped_package_name_preserved(self) -> None:
        log = (
            "Conflicting peer dependency: @scope/pkg@1.2.3\n"
            '  peer @scope/pkg@"<2" from other@9.9.9'
        )
        assert parse_peer_cap(log) == PeerCapBlock(
            dependency="@scope/pkg", capping_package="other@9.9.9", peer_range="<2",
        )


class TestClassifyProposalUpstreamBlocked:
    _BLOCK = PeerCapBlock(
        dependency="typescript", capping_package="typescript-eslint@8.64.0", peer_range="<6.1.0",
    )

    def test_upstream_block_present_is_upstream_blocked(self) -> None:
        assert (
            classify_proposal(_proposal(1, upstream_block=self._BLOCK))
            == ProposalClass.UPSTREAM_BLOCKED
        )

    def test_upstream_block_takes_precedence_over_conflicting(self) -> None:
        p = _proposal(1, mergeable="CONFLICTING", upstream_block=self._BLOCK)
        assert classify_proposal(p) == ProposalClass.UPSTREAM_BLOCKED

    def test_closed_with_block_is_still_skip(self) -> None:
        p = _proposal(1, state="closed", upstream_block=self._BLOCK)
        assert classify_proposal(p) == ProposalClass.SKIP


class TestGroupProposals:
    def test_empty_returns_empty(self) -> None:
        assert group_proposals([]) == []

    def test_single_proposal_forms_one_batch(self) -> None:
        result = group_proposals([_proposal(1, lockfiles=_UV_LOCK)])
        assert result == [ProposalBatch(lockfiles=_UV_LOCK, proposal_numbers=(1,))]

    def test_disjoint_lockfiles_form_separate_concurrent_groups(self) -> None:
        result = group_proposals([
            _proposal(1, lockfiles=_UV_LOCK),
            _proposal(2, lockfiles=_NPM_LOCK),
        ])
        assert len(result) == 2
        by_first: dict[int, ProposalBatch] = {b.proposal_numbers[0]: b for b in result}
        assert by_first[1] == ProposalBatch(lockfiles=_UV_LOCK, proposal_numbers=(1,))
        assert by_first[2] == ProposalBatch(lockfiles=_NPM_LOCK, proposal_numbers=(2,))

    def test_shared_lockfile_collapses_into_sequential_group(self) -> None:
        result = group_proposals([
            _proposal(1, lockfiles=_UV_LOCK),
            _proposal(2, lockfiles=_UV_LOCK),
        ])
        assert len(result) == 1
        assert result[0].lockfiles == _UV_LOCK
        assert result[0].proposal_numbers == (1, 2)

    def test_three_proposals_sharing_lockfile_are_all_sequential(self) -> None:
        result = group_proposals([
            _proposal(1, lockfiles=_UV_LOCK),
            _proposal(2, lockfiles=_UV_LOCK),
            _proposal(3, lockfiles=_UV_LOCK),
        ])
        assert len(result) == 1
        assert result[0].proposal_numbers == (1, 2, 3)

    def test_connector_proposal_merges_two_existing_groups(self) -> None:
        a = _proposal(1, lockfiles=_UV_LOCK)
        c = _proposal(3, lockfiles=_NPM_LOCK)
        b = _proposal(2, lockfiles=_BOTH_LOCKS)
        result = group_proposals([a, c, b])
        assert len(result) == 1
        assert result[0].lockfiles == _BOTH_LOCKS
        assert result[0].proposal_numbers == (1, 2, 3)

    def test_proposal_with_no_lockfiles_forms_own_group(self) -> None:
        result = group_proposals([_proposal(1), _proposal(2)])
        assert len(result) == 2

    def test_no_lockfile_proposal_does_not_merge_with_lockfile_proposal(self) -> None:
        result = group_proposals([
            _proposal(1, lockfiles=_UV_LOCK),
            _proposal(2),
        ])
        assert len(result) == 2

    def test_third_proposal_merges_one_group_leaves_other_intact(self) -> None:
        result = group_proposals([
            _proposal(1, lockfiles=_UV_LOCK),
            _proposal(2, lockfiles=_NPM_LOCK),
            _proposal(3, lockfiles=_UV_LOCK),
        ])
        assert len(result) == 2
        by_lf = {frozenset(b.lockfiles): b for b in result}
        assert by_lf[_UV_LOCK].proposal_numbers == (1, 3)
        assert by_lf[_NPM_LOCK].proposal_numbers == (2,)


class TestMergeTarget:
    def test_merge_ready_with_switch_on_returns_pr_merge(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALAN_DEP_SWEEP_MERGE", "1")
        assert merge_target(ProposalClass.MERGE_READY) == "pr_merge"

    def test_merge_ready_with_switch_off_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALAN_DEP_SWEEP_MERGE", "0")
        assert merge_target(ProposalClass.MERGE_READY) is None

    def test_merge_ready_with_env_absent_defaults_to_pr_merge(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ALAN_DEP_SWEEP_MERGE", raising=False)
        assert merge_target(ProposalClass.MERGE_READY) == "pr_merge"

    def test_needs_rebase_always_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALAN_DEP_SWEEP_MERGE", "1")
        assert merge_target(ProposalClass.NEEDS_REBASE) is None

    def test_skip_always_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALAN_DEP_SWEEP_MERGE", "1")
        assert merge_target(ProposalClass.SKIP) is None

    def test_upstream_blocked_always_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALAN_DEP_SWEEP_MERGE", "1")
        assert merge_target(ProposalClass.UPSTREAM_BLOCKED) is None
