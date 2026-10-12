from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import workspace as workspace_mod
from alan._dispatcher_selfheal import (
    _pending_test_refs,
    _probe_checkout_for,
    _scenarios_confirm_done,
)
from alan.dispatcher import Dispatcher
from alan.engine_config import EngineConfig, ForgeConfig, Posture, RepoIdentity
from alan.handover import Handover
from alan.queue import Queue
from alan.router import RouterDecision
from alan.teams._pr_ready import PrReadiness, ReadinessUnavailable
from alan.teams._scenario_runner import ScenarioResult, ScenarioStatus
from alan.workspace import (
    EngineSnapshot,
    Requirement,
    Status,
    Task,
    TaskDoDClause,
    TaskStatus,
    TestScenario,
    Workspace,
)

_NO_COMMIT_SYNTHESIS = (
    "implementation produced no commits — escalating. "
    "Worker synthesis:\nalready on main"
)
_ESCALATE = RouterDecision(kind="escalate", reason="escalation handover")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        with contextlib.suppress(Exception):
            q.close()


@pytest.fixture(autouse=True)
def _no_summary_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "alan.manager_summary.trigger_manager_summary", lambda *_a, **_kw: None
    )


def _scenario() -> TestScenario:
    return TestScenario(
        id="s1",
        description="collision usage sums",
        given="two runs",
        when="uv run pytest tests/test_collision.py::test_x",
        then="1 passed",
    )


def _result(status: ScenarioStatus) -> ScenarioResult:
    return ScenarioResult(
        scenario_id="s1",
        status=status,
        diagnostic="",
        stdout_tail="",
        stderr_tail="",
        returncode=0 if status == "PASS" else 1,
        elapsed_s=0.01,
    )


def _sibling(status: TaskStatus = TaskStatus.COMPLETED, evidence: str = "PR #498") -> Task:
    return Task(
        id="t1",
        team="implementation",
        status=status,
        depends_on=[],
        dod_clauses=[TaskDoDClause(text="seam", evidence=evidence)],
    )


def _leaf(*, scenarios: list[TestScenario] | None = None) -> Task:
    return Task(
        id="t2",
        team="implementation",
        status=TaskStatus.PENDING,
        depends_on=["t1"],
        dod_clauses=[TaskDoDClause(text="collision", evidence=None)],
        scenarios=[_scenario()] if scenarios is None else scenarios,
    )


def _ws(root: Path, tasks: list[Task], *, name: str = "heal-ws") -> Workspace:
    return workspace_mod.create(name, "desc", root=root, tasks=tasks)


def _no_commit_handover() -> Handover:
    return Handover(
        handover_id="h-esc",
        handover_type="escalation",
        task_id="heal-ws",
        from_team="implementation",
        from_invocation_id="inv_012",
        to_team_suggestions=[],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=_NO_COMMIT_SYNTHESIS,
    )


def _no_commit_handover_with_pr(pr_number: int) -> Handover:
    return Handover(
        handover_id="h-esc-pr",
        handover_type="escalation",
        task_id="heal-ws",
        from_team="implementation_fix",
        from_invocation_id="inv_013",
        to_team_suggestions=[],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=_NO_COMMIT_SYNTHESIS,
        pr_number=pr_number,
    )


def _forward_handover() -> Handover:
    return Handover(
        handover_id="h-fwd",
        handover_type="forward",
        task_id="heal-ws",
        from_team="implementation",
        from_invocation_id="inv_012",
        to_team_suggestions=["verification"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="made commits",
    )


class TestScenariosConfirmDone:
    def test_pass_only_confirms(self) -> None:
        assert _scenarios_confirm_done([_result("PASS")]) is True

    def test_pass_with_documented_does_not_confirm(self) -> None:
        assert _scenarios_confirm_done([_result("PASS"), _result("DOCUMENTED")]) is False

    def test_pass_with_skipped_does_not_confirm(self) -> None:
        assert _scenarios_confirm_done([_result("PASS"), _result("SKIPPED")]) is False

    def test_advisory_only_confirms(self) -> None:
        assert _scenarios_confirm_done([_result("ADVISORY")]) is True

    def test_advisory_with_fail_blocks(self) -> None:
        assert _scenarios_confirm_done([_result("ADVISORY"), _result("FAIL")]) is False

    def test_pass_with_timeout_does_not_confirm(self) -> None:
        assert _scenarios_confirm_done([_result("PASS"), _result("TIMEOUT")]) is False

    def test_pass_with_budget_exhausted_does_not_confirm(self) -> None:
        assert (
            _scenarios_confirm_done([_result("PASS"), _result("BUDGET_EXHAUSTED")]) is False
        )

    def test_any_fail_blocks(self) -> None:
        assert _scenarios_confirm_done([_result("PASS"), _result("FAIL")]) is False

    def test_no_pass_does_not_confirm(self) -> None:
        assert _scenarios_confirm_done([_result("DOCUMENTED")]) is False

    def test_empty_does_not_confirm(self) -> None:
        assert _scenarios_confirm_done([]) is False


class TestMaybeSelfHealGuards:
    def _assert_unchanged(
        self, d: Dispatcher, ws: Workspace, handover: Handover, task_id: str = "t2"
    ) -> None:
        decision, out = d._maybe_self_heal(ws, handover, _ESCALATE, task_id)  # noqa: SLF001
        assert decision is _ESCALATE
        assert out is handover

    def test_non_escalate_decision_skips(
        self, root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = _ws(root, [_sibling(), _leaf()])
        d = Dispatcher(queue, {}, workspaces_root=root)
        called = _spy_run_scenarios(monkeypatch)
        enqueue = RouterDecision(kind="enqueue", team="verification")
        decision, out = d._maybe_self_heal(  # noqa: SLF001
            ws, _no_commit_handover(), enqueue, "t2"
        )
        assert decision is enqueue
        assert called == []

    def test_escalation_without_no_commit_sentinel_skips(
        self, root: Path, queue: Queue
    ) -> None:
        ws = _ws(root, [_sibling(), _leaf()])
        d = Dispatcher(queue, {}, workspaces_root=root)
        self._assert_unchanged(d, ws, _forward_handover())

    def test_no_tasks_skips(self, root: Path, queue: Queue) -> None:
        ws = workspace_mod.create("no-tasks", "d", root=root)
        d = Dispatcher(queue, {}, workspaces_root=root)
        self._assert_unchanged(d, ws, _no_commit_handover())

    def test_no_merged_sibling_skips(self, root: Path, queue: Queue) -> None:
        ws = _ws(root, [_sibling(status=TaskStatus.PENDING), _leaf()])
        d = Dispatcher(queue, {}, workspaces_root=root)
        self._assert_unchanged(d, ws, _no_commit_handover())

    def test_merged_sibling_without_pr_number_skips(
        self, root: Path, queue: Queue
    ) -> None:
        ws = _ws(root, [_sibling(evidence="PR #not-a-number"), _leaf()])
        d = Dispatcher(queue, {}, workspaces_root=root)
        self._assert_unchanged(d, ws, _no_commit_handover())

    def test_unknown_task_id_skips(self, root: Path, queue: Queue) -> None:
        ws = _ws(root, [_sibling(), _leaf()])
        d = Dispatcher(queue, {}, workspaces_root=root)
        self._assert_unchanged(d, ws, _no_commit_handover(), task_id="missing")

    def test_leaf_without_scenarios_skips(self, root: Path, queue: Queue) -> None:
        ws = _ws(root, [_sibling(), _leaf(scenarios=[])])
        d = Dispatcher(queue, {}, workspaces_root=root)
        self._assert_unchanged(d, ws, _no_commit_handover())

    def test_scenarios_fail_skips(
        self, root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = _ws(root, [_sibling(), _leaf()])
        d = Dispatcher(queue, {}, workspaces_root=root)
        monkeypatch.setattr(
            "alan._dispatcher_selfheal.run_scenarios", lambda *_a, **_kw: [_result("FAIL")]
        )
        self._assert_unchanged(d, ws, _no_commit_handover())

    def test_scenario_run_raises_falls_back_to_escalation(
        self, root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = _ws(root, [_sibling(), _leaf()])
        d = Dispatcher(queue, {}, workspaces_root=root)

        def _boom(*_a: object, **_kw: object) -> list[ScenarioResult]:
            raise RuntimeError("subprocess exploded")

        monkeypatch.setattr("alan._dispatcher_selfheal.run_scenarios", _boom)
        self._assert_unchanged(d, ws, _no_commit_handover())


class TestMaybeSelfHealConfirmed:
    def test_confirmed_returns_done_decision_and_stamped_handover(
        self, root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = _ws(root, [_sibling(), _leaf()])
        d = Dispatcher(queue, {}, workspaces_root=root)
        monkeypatch.setattr(
            "alan._dispatcher_selfheal.run_scenarios", lambda *_a, **_kw: [_result("PASS")]
        )
        handover = _no_commit_handover()
        decision, out = d._maybe_self_heal(ws, handover, _ESCALATE, "t2")  # noqa: SLF001
        assert decision.kind == "done"
        assert "PR #498" in (decision.reason or "")
        assert out.pr_number == 498
        assert out is not handover
        reloaded = workspace_mod.read(ws.task_id, root=root)
        assert _task_status(reloaded, "t2") == TaskStatus.PENDING

    def test_writes_self_heal_audit_row(
        self,
        root: Path,
        queue: Queue,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        logs = tmp_path / "logs"
        monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
        ws = _ws(root, [_sibling(), _leaf()])
        d = Dispatcher(queue, {}, workspaces_root=root)
        monkeypatch.setattr(
            "alan._dispatcher_selfheal.run_scenarios", lambda *_a, **_kw: [_result("PASS")]
        )
        d._maybe_self_heal(ws, _no_commit_handover(), _ESCALATE, "t2")  # noqa: SLF001
        audit = (logs / "self_heal_audit.jsonl").read_text(encoding="utf-8")
        assert '"pr_number": 498' in audit
        assert '"task_id": "t2"' in audit


class TestMaybeSelfHealScenarioResultsGap:
    def test_confirmed_self_heal_does_not_populate_scenario_results(
        self, root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = _ws(root, [_sibling(), _leaf()])
        d = Dispatcher(queue, {}, workspaces_root=root)
        monkeypatch.setattr(
            "alan._dispatcher_selfheal.run_scenarios", lambda *_a, **_kw: [_result("PASS")]
        )
        handover = _no_commit_handover()
        assert handover.scenario_results == []
        _decision, out = d._maybe_self_heal(ws, handover, _ESCALATE, "t2")  # noqa: SLF001
        assert out.scenario_results == []


class TestMaybeSelfHealNonDefaultForgeSkip:
    def test_never_declares_done(
        self, root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = _non_default_ws(root, tasks=[_sibling(), _leaf()])
        d = Dispatcher(queue, {}, workspaces_root=root)
        called = _spy_run_scenarios(monkeypatch)
        handover = _no_commit_handover()
        decision, out = d._maybe_self_heal(  # noqa: SLF001
            ws, handover, _ESCALATE, "t2"
        )
        assert decision is _ESCALATE
        assert out is handover
        assert called == []

    def test_writes_audit_row_with_named_skip_reason_and_unevaluated_ids(
        self,
        root: Path,
        queue: Queue,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        logs = tmp_path / "logs"
        monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
        ws = _non_default_ws(root, tasks=[_sibling(), _leaf()])
        d = Dispatcher(queue, {}, workspaces_root=root)
        d._maybe_self_heal(ws, _no_commit_handover(), _ESCALATE, "t2")  # noqa: SLF001
        audit = (logs / "self_heal_audit.jsonl").read_text(encoding="utf-8")
        assert '"scenarios": []' in audit
        assert '"unevaluated_scenario_ids": ["s1"]' in audit
        assert '"skipped_reason":' in audit
        assert "acme/widget" in audit


def _leaf_only(root: Path) -> Workspace:
    return _ws(root, [_leaf()])


class TestRouteNoopNoCommit:
    def test_green_pr_on_atomic_workspace_routes_to_verification(
        self, root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = _leaf_only(root)
        d = Dispatcher(queue, {}, workspaces_root=root)
        monkeypatch.setattr(
            "alan._dispatcher_selfheal._pr_ready.readiness_or_unavailable",
            lambda *_a, **_kw: PrReadiness(
                ok=True, merge_state_status="CLEAN", review_decision="",
            ),
        )
        decision, out = d._maybe_self_heal(  # noqa: SLF001
            ws, _no_commit_handover_with_pr(701), _ESCALATE, "t2"
        )
        assert decision.kind == "enqueue"
        assert decision.team == "verification"
        assert out.pr_number == 701

    def test_failing_check_still_escalates(
        self, root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = _leaf_only(root)
        d = Dispatcher(queue, {}, workspaces_root=root)
        monkeypatch.setattr(
            "alan._dispatcher_selfheal._pr_ready.readiness_or_unavailable",
            lambda *_a, **_kw: PrReadiness(
                ok=False, merge_state_status="CLEAN", review_decision="",
                failing_checks=("ci/tests",),
            ),
        )
        decision, out = d._maybe_self_heal(  # noqa: SLF001
            ws, _no_commit_handover_with_pr(702), _ESCALATE, "t2"
        )
        assert decision is _ESCALATE
        assert out.pr_number == 702

    def test_pending_check_still_escalates(
        self, root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = _leaf_only(root)
        d = Dispatcher(queue, {}, workspaces_root=root)
        monkeypatch.setattr(
            "alan._dispatcher_selfheal._pr_ready.readiness_or_unavailable",
            lambda *_a, **_kw: PrReadiness(
                ok=False, merge_state_status="CLEAN", review_decision="",
                pending_checks=("ci/e2e",),
            ),
        )
        decision, out = d._maybe_self_heal(  # noqa: SLF001
            ws, _no_commit_handover_with_pr(703), _ESCALATE, "t2"
        )
        assert decision is _ESCALATE
        assert out.pr_number == 703

    def test_changes_requested_review_still_escalates(
        self, root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = _leaf_only(root)
        d = Dispatcher(queue, {}, workspaces_root=root)
        monkeypatch.setattr(
            "alan._dispatcher_selfheal._pr_ready.readiness_or_unavailable",
            lambda *_a, **_kw: PrReadiness(
                ok=False, merge_state_status="CLEAN",
                review_decision="CHANGES_REQUESTED",
            ),
        )
        decision, out = d._maybe_self_heal(  # noqa: SLF001
            ws, _no_commit_handover_with_pr(704), _ESCALATE, "t2"
        )
        assert decision is _ESCALATE
        assert out.pr_number == 704

    def test_blocked_merge_state_with_no_checks_or_review_still_escalates(
        self, root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = _leaf_only(root)
        d = Dispatcher(queue, {}, workspaces_root=root)
        monkeypatch.setattr(
            "alan._dispatcher_selfheal._pr_ready.readiness_or_unavailable",
            lambda *_a, **_kw: PrReadiness(
                ok=False, merge_state_status="BLOCKED", review_decision="",
            ),
        )
        decision, out = d._maybe_self_heal(  # noqa: SLF001
            ws, _no_commit_handover_with_pr(706), _ESCALATE, "t2"
        )
        assert decision is _ESCALATE
        assert out.pr_number == 706

    def test_forge_unavailable_still_escalates(
        self, root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = _leaf_only(root)
        d = Dispatcher(queue, {}, workspaces_root=root)
        monkeypatch.setattr(
            "alan._dispatcher_selfheal._pr_ready.readiness_or_unavailable",
            lambda *_a, **_kw: ReadinessUnavailable("gh unavailable"),
        )
        decision, out = d._maybe_self_heal(  # noqa: SLF001
            ws, _no_commit_handover_with_pr(705), _ESCALATE, "t2"
        )
        assert decision is _ESCALATE
        assert out.pr_number == 705

    def test_merged_sibling_path_taken_before_noop_route(
        self, root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = _ws(root, [_sibling(), _leaf()])
        d = Dispatcher(queue, {}, workspaces_root=root)
        monkeypatch.setattr(
            "alan._dispatcher_selfheal.run_scenarios", lambda *_a, **_kw: [_result("PASS")]
        )
        called: list[object] = []

        def _fake_readiness(*_a: object, **_kw: object) -> PrReadiness:
            called.append(True)
            return PrReadiness(ok=True, merge_state_status="CLEAN", review_decision="")

        monkeypatch.setattr(
            "alan._dispatcher_selfheal._pr_ready.readiness_or_unavailable", _fake_readiness
        )
        decision, _out = d._maybe_self_heal(  # noqa: SLF001
            ws, _no_commit_handover(), _ESCALATE, "t2"
        )
        assert decision.kind == "done"
        assert called == []


class TestPendingTestRefsWithdrawal:
    def test_withdrawn_pending_clause_is_excluded(self) -> None:
        req = Requirement(
            text="x", test_ref="s1", status="PENDING", withdrawn_by_question_id="clause-stall-x"
        )
        assert _pending_test_refs([req]) == set()

    def test_live_pending_clause_is_still_included(self) -> None:
        req = Requirement(text="x", test_ref="s1", status="PENDING")
        assert _pending_test_refs([req]) == {"s1"}


def _non_default_cfg() -> EngineConfig:
    return EngineConfig(
        repo=RepoIdentity(owner="acme", repo="widget"),
        forge=ForgeConfig(
            repo=RepoIdentity(owner="acme", repo="widget"),
            credential_env_name="ACME_WIDGET_GH_TOKEN",
        ),
        posture=Posture.autonomous,
    )


def _non_default_ws(root: Path, *, tasks: list[Task] | None = None) -> Workspace:
    ws = _ws(root, tasks or [], name="probe-second-engine")
    return workspace_mod.stamp_engine_snapshot(
        ws,
        EngineSnapshot(
            posture=Posture.autonomous, transition_graph={}, config=_non_default_cfg()
        ),
    )


class TestProbeCheckoutFor:
    def test_default_engine_returns_process_cwd(self, root: Path) -> None:
        ws = _ws(root, [_leaf()])
        checkout = _probe_checkout_for(ws)
        assert checkout.path == Path.cwd()
        assert checkout.skip_reason is None

    def test_second_engine_returns_no_checkout_with_named_reason(
        self, root: Path
    ) -> None:
        ws = _non_default_ws(root)
        checkout = _probe_checkout_for(ws)
        assert checkout.path is None
        assert checkout.skip_reason is not None
        assert "acme/widget" in checkout.skip_reason


class TestScenarioProbeCwdThreading:
    def test_run_self_heal_scenarios_passes_probe_result_as_cwd(
        self, root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = _ws(root, [_sibling(), _leaf()])
        d = Dispatcher(queue, {}, workspaces_root=root)
        captured: list[str] = []

        def _fake(
            _scenarios: object, *, cwd: str, aggregate_budget: object
        ) -> list[ScenarioResult]:
            captured.append(cwd)
            return [_result("PASS")]

        monkeypatch.setattr("alan._dispatcher_selfheal.run_scenarios", _fake)
        task = next(t for t in ws.meta.tasks if t.id == "t2")
        d._run_self_heal_scenarios(ws, task)  # noqa: SLF001
        assert captured == [str(Path.cwd())]

    def test_run_self_heal_scenarios_applies_workspace_binding_overlay(
        self, root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from alan.workspace import ScenarioBinding, update_scenario_binding

        ws = _ws(root, [_sibling(), _leaf()])
        ws = update_scenario_binding(
            ws, "s1", ScenarioBinding(when="uv run pytest -k rebound", reason="test moved")
        )
        d = Dispatcher(queue, {}, workspaces_root=root)
        captured: list[list[TestScenario]] = []

        def _fake(
            scenarios: list[TestScenario], *, cwd: object, aggregate_budget: object
        ) -> list[ScenarioResult]:
            captured.append(scenarios)
            return [_result("PASS")]

        monkeypatch.setattr("alan._dispatcher_selfheal.run_scenarios", _fake)
        task = next(t for t in ws.meta.tasks if t.id == "t2")
        d._run_self_heal_scenarios(ws, task)  # noqa: SLF001
        assert captured[0][0].when == "uv run pytest -k rebound"

    def test_run_self_heal_scenarios_returns_empty_on_non_default_forge(
        self, root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = _non_default_ws(root, tasks=[_sibling(), _leaf()])
        d = Dispatcher(queue, {}, workspaces_root=root)
        spy = _spy_run_scenarios(monkeypatch)
        task = next(t for t in ws.meta.tasks if t.id == "t2")
        results = d._run_self_heal_scenarios(ws, task)  # noqa: SLF001
        assert results == []
        assert spy == []

    def test_run_oob_requirement_scenarios_passes_probe_result_as_cwd(
        self, root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = _ws(root, [_leaf()])
        d = Dispatcher(queue, {}, workspaces_root=root)
        captured: list[str] = []

        def _fake(
            _scenarios: object, *, cwd: str, aggregate_budget: object
        ) -> list[ScenarioResult]:
            captured.append(cwd)
            return [_result("PASS")]

        monkeypatch.setattr("alan._dispatcher_reconcile.run_scenarios", _fake)
        d._run_oob_requirement_scenarios(ws)  # noqa: SLF001
        assert captured == [str(Path.cwd())]


class TestApplyHealedDecision:
    def test_done_decision_with_pr_completes_and_stamps_evidence(
        self, root: Path, queue: Queue
    ) -> None:
        ws = _ws(root, [_sibling(), _leaf()])
        d = Dispatcher(queue, {}, workspaces_root=root)
        handover = _no_commit_handover().model_copy(update={"pr_number": 498})
        done = RouterDecision(kind="done", reason="self-heal (PR #498)")
        d._apply_router_decision(  # noqa: SLF001
            ws, handover, task_id="t2", decision=done
        )
        reloaded = workspace_mod.read(ws.task_id, root=root)
        assert _task_status(reloaded, "t2") == TaskStatus.COMPLETED
        assert _leaf_evidence(reloaded, "t2") == "PR #498"
        assert reloaded.meta.status == Status.COMPLETED


class TestMaybeSelfHealUnevaluated:
    def test_skipped_scenario_declines_and_leaves_decision_unchanged(
        self, root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = _ws(root, [_sibling(), _leaf()])
        d = Dispatcher(queue, {}, workspaces_root=root)
        monkeypatch.setattr(
            "alan._dispatcher_selfheal.run_scenarios", lambda *_a, **_kw: [_result("SKIPPED")]
        )
        handover = _no_commit_handover()
        decision, out = d._maybe_self_heal(ws, handover, _ESCALATE, "t2")  # noqa: SLF001
        assert decision is _ESCALATE
        assert out is handover

    def test_skipped_scenario_still_writes_audit_row_with_unevaluated_ids(
        self,
        root: Path,
        queue: Queue,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        logs = tmp_path / "logs"
        monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
        ws = _ws(root, [_sibling(), _leaf()])
        d = Dispatcher(queue, {}, workspaces_root=root)
        monkeypatch.setattr(
            "alan._dispatcher_selfheal.run_scenarios", lambda *_a, **_kw: [_result("SKIPPED")]
        )
        d._maybe_self_heal(ws, _no_commit_handover(), _ESCALATE, "t2")  # noqa: SLF001
        audit = (logs / "self_heal_audit.jsonl").read_text(encoding="utf-8")
        assert '"unevaluated_scenario_ids": ["s1"]' in audit

    def test_confirmed_self_heal_has_empty_unevaluated_ids(
        self,
        root: Path,
        queue: Queue,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        logs = tmp_path / "logs"
        monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
        ws = _ws(root, [_sibling(), _leaf()])
        d = Dispatcher(queue, {}, workspaces_root=root)
        monkeypatch.setattr(
            "alan._dispatcher_selfheal.run_scenarios", lambda *_a, **_kw: [_result("PASS")]
        )
        d._maybe_self_heal(ws, _no_commit_handover(), _ESCALATE, "t2")  # noqa: SLF001
        audit = (logs / "self_heal_audit.jsonl").read_text(encoding="utf-8")
        assert '"unevaluated_scenario_ids": []' in audit

    def test_crashed_scenario_run_writes_no_audit_row(
        self,
        root: Path,
        queue: Queue,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        logs = tmp_path / "logs"
        monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
        ws = _ws(root, [_sibling(), _leaf()])
        d = Dispatcher(queue, {}, workspaces_root=root)

        def _boom(*_a: object, **_kw: object) -> list[ScenarioResult]:
            raise RuntimeError("subprocess exploded")

        monkeypatch.setattr("alan._dispatcher_selfheal.run_scenarios", _boom)
        d._maybe_self_heal(ws, _no_commit_handover(), _ESCALATE, "t2")  # noqa: SLF001
        assert not (logs / "self_heal_audit.jsonl").exists()


def _spy_run_scenarios(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    calls: list[object] = []

    def _fake(*args: object, **kwargs: object) -> list[ScenarioResult]:
        calls.append((args, kwargs))
        return [_result("PASS")]

    monkeypatch.setattr("alan._dispatcher_selfheal.run_scenarios", _fake)
    return calls


def _task_status(ws: Workspace, task_id: str) -> TaskStatus:
    return next(t.status for t in ws.meta.tasks if t.id == task_id)


def _leaf_evidence(ws: Workspace, task_id: str) -> str | None:
    task = next(t for t in ws.meta.tasks if t.id == task_id)
    return task.dod_clauses[0].evidence
