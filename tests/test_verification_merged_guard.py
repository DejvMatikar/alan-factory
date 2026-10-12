from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from alan import workflow as workflow_mod
from alan.claude_cli import AgentStreamingResult
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.forge import PrRead
from alan.router import select_next_team
from alan.teams import _base as base_mod
from alan.teams import _pr_branch, _pr_forge
from alan.teams import _verification_merged_guard as guard_mod
from alan.teams.verification import TEAM
from alan.workspace import Workspace
from tests import _teams_verification_support as support
from tests._helpers import seed_and_run
from tests._pr_branch_support import _WIDGET_FORGE
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, pr_read

_impl_handover = support.impl_handover
_FORGE = DEFAULT_FORGE_TARGET
_REAL_PR_STATE_NAME = guard_mod.pr_state_name


@pytest.fixture(autouse=True)
def _stub_external_gates_default(monkeypatch: pytest.MonkeyPatch) -> None:
    support.stub_external_gates(monkeypatch)


@pytest.fixture(autouse=True)
def _stub_checkout_internals_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    support.stub_checkout_internals(monkeypatch, tmp_path)


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return support.make_workspace(tmp_path)


@pytest.fixture
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return support.set_audit_root(tmp_path, monkeypatch)


class TestPrStateName:
    @pytest.fixture(autouse=True)
    def _restore_real_pr_state_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(guard_mod, "pr_state_name", _REAL_PR_STATE_NAME)

    @pytest.fixture
    def forge(self, _active_fake_forge: FakeForge) -> ForgeScript:
        return ForgeScript(_active_fake_forge)

    @pytest.mark.parametrize("state", ["MERGED", "OPEN", "CLOSED"])
    def test_a_string_state_is_returned_verbatim(self, forge: ForgeScript, state: str) -> None:
        forge.queue("read_pr", pr_read({"state": state}))
        assert guard_mod.pr_state_name(42, forge_target=_FORGE) == state

    @pytest.mark.parametrize(
        "read",
        [
            pr_read({"state": "MERGED"}, returncode=1, stderr="boom"),
            pr_read({}),
            pr_read({"mergeStateStatus": "CLEAN"}),
            pr_read({"state": ""}),
            pr_read({"state": 5}),
        ],
        ids=["failed-read", "unparseable-payload", "state-missing", "state-empty", "state-not-str"],
    )
    def test_no_usable_state_returns_none(self, forge: ForgeScript, read: PrRead) -> None:
        forge.queue("read_pr", read)
        assert guard_mod.pr_state_name(42, forge_target=_FORGE) is None

    @pytest.mark.parametrize(
        "error",
        [
            FileNotFoundError("no gh"),
            PermissionError("gh not executable"),
            subprocess.TimeoutExpired(cmd="gh", timeout=60.0),
        ],
        ids=["missing-gh", "not-executable", "timeout"],
    )
    def test_a_transport_failure_returns_none(
        self, forge: ForgeScript, error: BaseException
    ) -> None:
        forge.queue("read_pr", error)
        assert guard_mod.pr_state_name(42, forge_target=_FORGE) is None

    def test_reads_the_state_and_merge_state_of_the_pr_on_the_target(
        self, forge: ForgeScript
    ) -> None:
        forge.queue("read_pr", pr_read({"state": "OPEN"}))
        guard_mod.pr_state_name(42, forge_target=_WIDGET_FORGE)
        assert forge.calls("read_pr") == [
            (
                (42, ("state", "mergeStateStatus")),
                {"forge_target": _WIDGET_FORGE, "timeout": _pr_forge.GH_TIMEOUT_S},
            ),
        ]

    def test_an_unstubbed_read_pr_raises_rather_than_defaulting(self) -> None:
        with pytest.raises(AssertionError, match="unstubbed forge call: read_pr"):
            guard_mod.pr_state_name(42, forge_target=_FORGE)


def _seed(workspace: Workspace, team_name: str) -> str:
    return workflow_mod.seed_pending_workflow(workspace, team=team_name, tasks=[])


class TestTerminalIfMerged:
    def test_pr_number_none_is_a_noop(
        self, workspace: Workspace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*_a: object, **_kw: object) -> object:
            pytest.fail("must not query gh when pr_number is None")

        monkeypatch.setattr(guard_mod, "pr_state_name", _boom)
        inv_id = _seed(workspace, "verification")
        result = guard_mod.terminal_if_merged(
            workspace, None, lambda _e: None, inv_id, None,
            team_name="verification", forge_target=_FORGE,
        )
        assert result is None

    def test_open_pr_returns_none(
        self, workspace: Workspace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(guard_mod, "pr_state_name", lambda *_a, **_kw: "OPEN")
        inv_id = _seed(workspace, "verification")
        result = guard_mod.terminal_if_merged(
            workspace, None, lambda _e: None, inv_id, 42,
            team_name="verification", forge_target=_FORGE,
        )
        assert result is None

    def test_transient_failure_returns_none_never_terminates(
        self, workspace: Workspace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(guard_mod, "pr_state_name", lambda *_a, **_kw: None)
        inv_id = _seed(workspace, "verification")
        result = guard_mod.terminal_if_merged(
            workspace, None, lambda _e: None, inv_id, 42,
            team_name="verification", forge_target=_FORGE,
        )
        assert result is None

    def test_merged_pr_returns_terminal_handover(
        self, workspace: Workspace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(guard_mod, "pr_state_name", lambda *_a, **_kw: "MERGED")
        inv_id = _seed(workspace, "verification")
        result = guard_mod.terminal_if_merged(
            workspace, None, lambda _e: None, inv_id, 42,
            team_name="verification", forge_target=_FORGE,
        )
        assert result is not None
        assert result.handover_type == "terminal"
        assert result.pr_number == 42
        assert "already merged" in result.full_synthesis

    def test_merged_pr_writes_audit_row(
        self, workspace: Workspace, monkeypatch: pytest.MonkeyPatch, audit_root: Path
    ) -> None:
        monkeypatch.setattr(guard_mod, "pr_state_name", lambda *_a, **_kw: "MERGED")
        inv_id = _seed(workspace, "verification")
        guard_mod.terminal_if_merged(
            workspace, None, lambda _e: None, inv_id, 42,
            team_name="verification", forge_target=_FORGE,
        )
        entries = [
            json.loads(line)
            for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
        ]
        assert any(
            e["action"] == "verification_merged_short_circuit" and e["pr_number"] == 42
            for e in entries
        )


def test_run_short_circuits_before_precondition_on_squash_deleted_branch(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.setattr(guard_mod, "pr_state_name", lambda *_a, **_kw: "MERGED")
    monkeypatch.setattr(_pr_branch, "resolve_existing_branch", lambda _p, _t, **_kw: None)

    def boom_stream(*_a: object, **_k: object) -> AgentStreamingResult:
        pytest.fail("must not spawn a worker once the PR is confirmed merged")

    monkeypatch.setattr(base_mod, "run_agent_streaming", boom_stream)
    prior = _impl_handover(workspace, pr_number=42)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.handover_type == "terminal"
    assert handover.pr_number == 42
    entries_path = audit_root / "verification_audit.jsonl"
    entries = (
        [json.loads(ln) for ln in entries_path.read_text().splitlines()]
        if entries_path.exists() else []
    )
    assert not any(e["action"] == "pr_precondition_failed" for e in entries)
    assert not any(e["action"] == "verifier_checkout_ok" for e in entries)
    assert any(e["action"] == "verification_merged_short_circuit" for e in entries)


def test_run_short_circuits_before_grounded_checkout_when_branch_still_resolves(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.setattr(guard_mod, "pr_state_name", lambda *_a, **_kw: "MERGED")
    monkeypatch.setattr(_pr_branch, "resolve_existing_branch", lambda _p, _t, **_kw: "teams/x-1")

    def boom_stream(*_a: object, **_k: object) -> AgentStreamingResult:
        pytest.fail("must not spawn a worker once the PR is confirmed merged")

    monkeypatch.setattr(base_mod, "run_agent_streaming", boom_stream)
    prior = _impl_handover(workspace, pr_number=42)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.handover_type == "terminal"
    assert handover.pr_number == 42
    entries_path = audit_root / "verification_audit.jsonl"
    entries = (
        [json.loads(ln) for ln in entries_path.read_text().splitlines()]
        if entries_path.exists() else []
    )
    assert not any(e["action"] == "verifier_checkout_ok" for e in entries)


def test_run_merged_short_circuit_routes_done(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setattr(guard_mod, "pr_state_name", lambda *_a, **_kw: "MERGED")
    monkeypatch.setattr(_pr_branch, "resolve_existing_branch", lambda _p, _t, **_kw: None)
    prior = _impl_handover(workspace, pr_number=42)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    decision = select_next_team(handover, {})
    assert decision.kind == "done"


def test_run_not_merged_proceeds_to_precondition_as_before(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.setattr(guard_mod, "pr_state_name", lambda *_a, **_kw: "OPEN")
    monkeypatch.setattr(_pr_branch, "resolve_existing_branch", lambda _p, _t, **_kw: None)

    def boom(*_a: object, **_k: object) -> AgentStreamingResult:
        pytest.fail("must not spawn a worker when the branch cannot be resolved")

    monkeypatch.setattr(base_mod, "run_agent_streaming", boom)
    prior = _impl_handover(workspace, pr_number=42)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.handover_type == "escalation"
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    assert any(e["action"] == "pr_precondition_failed" for e in entries)
