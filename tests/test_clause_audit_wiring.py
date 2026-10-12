from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from alan import acceptance as _acceptance_mod
from alan.claude_cli import AgentStreamingResult
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.handover import Handover, ScenarioOutcome
from alan.teams import _base as base_mod
from alan.teams import _clause_actionability, _pr_branch, _pr_ready, _scenario_gate
from alan.teams import _clause_audit as clause_audit_mod
from alan.teams.verification import ROLES, TEAM
from alan.workspace import Requirement, Task, TaskDoDClause, TaskStatus, Workspace
from tests import _teams_verification_support as support
from tests._helpers import seed_and_run
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, api_result, gh_unreachable

_impl_handover = support.impl_handover


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


@pytest.fixture(autouse=True)
def _gh_down() -> None:
    gh_unreachable()


@pytest.fixture(autouse=True)
def _stub_external_gates_default(monkeypatch: pytest.MonkeyPatch) -> None:
    support.stub_external_gates(monkeypatch)


@pytest.fixture(autouse=True)
def _stub_checkout_internals_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    support.stub_checkout_internals(monkeypatch, tmp_path)


@pytest.fixture(autouse=True)
def _reset_clause_audit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_CLAUSE_AUDIT", raising=False)


@pytest.fixture
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return support.set_audit_root(tmp_path, monkeypatch)


def _workspace(tmp_path: Path, *, requirements: list[Requirement] | None = None,
               tasks: list[Task] | None = None) -> Workspace:
    ws = support.make_workspace(tmp_path)
    meta = ws.meta.model_copy(update={"requirements": requirements, "tasks": tasks or []})
    return ws.model_copy(update={"meta": meta})


def _role_fake(
    outputs_by_role: dict[str, str],
    captured: dict[str, str] | None = None,
    invoked: list[str] | None = None,
) -> Callable[..., AgentStreamingResult]:
    def fake(
        system_prompt: str,
        user_text: str,
        *,
        model: str = "sonnet",  # noqa: ARG001
        stream_path: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        role = next(
            (name for name in outputs_by_role if system_prompt.startswith(f"You are the {name}")),
            None,
        )
        text = outputs_by_role[role] if role is not None else "DIFF_FINDINGS_CRITICAL: 0"
        if role is not None:
            if captured is not None:
                captured[role] = user_text
            if invoked is not None:
                invoked.append(role)
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(json.dumps({"type": "result", "result": text}) + "\n")
        return AgentStreamingResult(result_text=text)

    return fake


_APPROVE = (
    "## Verdict\n\nVERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n\n## Rationale\nfine"
)


class TestUndecomposedClauseSelection:
    def test_clause_ids_appear_in_prompt_and_table_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path  # noqa: ARG002
    ) -> None:
        req_a = Requirement(text="deliver the fenced file")
        req_b = Requirement(text="ship a draft PR")
        ws = _workspace(tmp_path, requirements=[req_a, req_b])
        monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
        monkeypatch.setattr(_pr_branch, "fetch_diff_files", lambda *_a, **_kw: frozenset())
        captured: dict[str, str] = {}
        auditor_output = (
            f"CLAUSE {req_a.id}: PASS — present\n"
            f"CLAUSE {req_b.id}: PASS — present\nCLAUSE_FAILURES: 0\n"
        )
        fake = _role_fake({
            "diff_reviewer": "DIFF_FINDINGS_CRITICAL: 0",
            "scenario_runner": "no scenarios",
            "clause_auditor": auditor_output,
            "judge": _APPROVE,
        }, captured=captured)
        monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
        prior = _impl_handover(ws)
        handover = seed_and_run(TEAM, ws, prior, lambda _e: None)
        assert req_a.id in captured["clause_auditor"]
        assert req_b.id in captured["clause_auditor"]
        last = handover.artifacts_produced[0]
        body = Path(last.path).read_text()
        assert body.count(req_a.id) == 1
        assert body.count(req_b.id) == 1


class TestSyntheticViolationShapes:
    def _run_with_auditor_output(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        build_auditor_output: Callable[[Requirement], str],
        judge_output: str = _APPROVE,
    ) -> Handover:
        req = Requirement(text="no fenced credential file in the diff")
        ws = _workspace(tmp_path, requirements=[req])
        monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
        monkeypatch.setattr(_pr_branch, "fetch_diff_files", lambda *_a, **_kw: frozenset())
        fake = _role_fake({
            "diff_reviewer": "DIFF_FINDINGS_CRITICAL: 0",
            "scenario_runner": "no scenarios",
            "clause_auditor": build_auditor_output(req),
            "judge": judge_output,
        })
        monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
        prior = _impl_handover(ws)
        return seed_and_run(TEAM, ws, prior, lambda _e: None)

    def test_fenced_file_present_forces_request_changes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path  # noqa: ARG002
    ) -> None:
        handover = self._run_with_auditor_output(
            tmp_path, monkeypatch,
            lambda req: f"CLAUSE {req.id}: FAIL — .env is present in the diff\n"
            "CLAUSE_FAILURES: 1\n",
        )
        assert handover.to_team_suggestions == ["implementation_fix"]
        body = Path(handover.artifacts_produced[0].path).read_text()
        assert "VERDICT: REQUEST_CHANGES" in body
        assert "ACCEPTANCE CLAUSE AUDIT" in body

    def test_required_artifact_absent_forces_request_changes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path  # noqa: ARG002
    ) -> None:
        handover = self._run_with_auditor_output(
            tmp_path, monkeypatch,
            lambda req: f"CLAUSE {req.id}: FAIL — docs/architecture/foo.md was never added\n"
            "CLAUSE_FAILURES: 1\n",
        )
        assert handover.to_team_suggestions == ["implementation_fix"]

    def test_forbidden_construct_delivered_forces_request_changes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path  # noqa: ARG002
    ) -> None:
        handover = self._run_with_auditor_output(
            tmp_path, monkeypatch,
            lambda req: f"CLAUSE {req.id}: FAIL — uses --dangerously-skip-permissions\n"
            "CLAUSE_FAILURES: 1\n",
        )
        assert handover.to_team_suggestions == ["implementation_fix"]

    def test_draft_required_pr_opened_ready_escalates_not_implementation_fix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path  # noqa: ARG002
    ) -> None:
        handover = self._run_with_auditor_output(
            tmp_path, monkeypatch,
            lambda req: f"CLAUSE {req.id}: FAIL — PR_STATE: isDraft is false, "
            "clause requires a draft PR\nCLAUSE_FAILURES: 1\n",
        )
        assert handover.to_team_suggestions == []
        assert len(handover.open_questions) == 1
        assert handover.open_questions[0].question_class == "verification_escalation"
        assert handover.open_questions[0].blocking is True
        assert handover.pr_number == 42

    def _stub_corroborating_readiness(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            _pr_ready, "check_pr_merge_readiness",
            lambda *_a, **_kw: _pr_ready.PrReadiness(
                ok=True, merge_state_status="CLEAN", review_decision="APPROVED",
                passing_checks=("Backend tests + coverage",), head_sha="a" * 40,
            ),
        )

    def _corroborated_auditor_output(self, req: Requirement) -> str:
        return (
            f'CLAUSE {req.id}: FAIL — ENV: stale build cache '
            'check="Backend tests + coverage"\nCLAUSE_FAILURES: 1\n'
        )

    def test_stand_down_corroborated_batch_routes_normally(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path  # noqa: ARG002
    ) -> None:
        self._stub_corroborating_readiness(monkeypatch)
        handover = self._run_with_auditor_output(
            tmp_path, monkeypatch, self._corroborated_auditor_output,
        )
        assert handover.to_team_suggestions == ["pr_merge"]
        assert handover.open_questions == []
        body = Path(handover.artifacts_produced[0].path).read_text()
        assert "ACCEPTANCE CLAUSE AUDIT — stood down" in body
        assert "VERDICT: REQUEST_CHANGES" not in body

    def test_stand_down_batch_with_judge_criticals_still_routes_to_fix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path  # noqa: ARG002
    ) -> None:
        self._stub_corroborating_readiness(monkeypatch)
        judge_output = (
            "## Verdict\n\nVERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 1\n\n"
            "## Rationale\nreal defect"
        )
        handover = self._run_with_auditor_output(
            tmp_path, monkeypatch, self._corroborated_auditor_output,
            judge_output=judge_output,
        )
        assert handover.to_team_suggestions == ["implementation_fix"]
        assert handover.open_questions == []
        body = Path(handover.artifacts_produced[0].path).read_text()
        assert "ACCEPTANCE CLAUSE AUDIT — stood down" in body
        assert "VERDICT: REQUEST_CHANGES" in body

    def test_stand_down_note_survives_blocking_scenario_gate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path  # noqa: ARG002
    ) -> None:
        self._stub_corroborating_readiness(monkeypatch)
        monkeypatch.setattr(
            _scenario_gate, "load_outcomes",
            lambda *_a, **_kw: [
                ScenarioOutcome(scenario_id="s1", status="FAIL", diagnostic="boom")
            ],
        )
        handover = self._run_with_auditor_output(
            tmp_path, monkeypatch, self._corroborated_auditor_output,
        )
        assert handover.to_team_suggestions == ["implementation_fix"]
        assert handover.open_questions == []
        body = Path(handover.artifacts_produced[0].path).read_text()
        assert "ACCEPTANCE CLAUSE AUDIT — stood down" in body
        assert "SCENARIO GATE" in body
        assert "VERDICT: REQUEST_CHANGES" in body


class TestModeSemantics:
    def test_off_mode_never_invokes_auditor(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path  # noqa: ARG002
    ) -> None:
        monkeypatch.setenv("ALAN_CLAUSE_AUDIT", "off")
        ws = _workspace(tmp_path, requirements=[Requirement(text="deliver X")])
        monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
        invoked: list[str] = []
        fake = _role_fake({
            "diff_reviewer": "DIFF_FINDINGS_CRITICAL: 0",
            "scenario_runner": "no scenarios",
            "judge": _APPROVE,
        }, invoked=invoked)
        monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
        prior = _impl_handover(ws)
        handover = seed_and_run(TEAM, ws, prior, lambda _e: None)
        assert "clause_auditor" not in invoked
        body = Path(handover.artifacts_produced[0].path).read_text()
        assert "Acceptance Clause Audit" not in body

    def test_no_requirements_never_invokes_auditor(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path  # noqa: ARG002
    ) -> None:
        ws = _workspace(tmp_path, requirements=None)
        monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
        invoked: list[str] = []
        fake = _role_fake({
            "diff_reviewer": "DIFF_FINDINGS_CRITICAL: 0",
            "scenario_runner": "no scenarios",
            "judge": _APPROVE,
        }, invoked=invoked)
        monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
        prior = _impl_handover(ws)
        seed_and_run(TEAM, ws, prior, lambda _e: None)
        assert "clause_auditor" not in invoked

    def test_multi_task_subtask_without_dod_clauses_skips_unscoped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path
    ) -> None:
        req = Requirement(text="deliver X")
        task_a = Task(id="t-a", status=TaskStatus.READY)
        task_b = Task(id="t-b", status=TaskStatus.READY)
        ws = _workspace(tmp_path, requirements=[req], tasks=[task_a, task_b])
        monkeypatch.setattr(_acceptance_mod, "scoped_clause_requirements", lambda _m, _t: None)
        monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
        invoked: list[str] = []
        fake = _role_fake({
            "diff_reviewer": "DIFF_FINDINGS_CRITICAL: 0",
            "scenario_runner": "no scenarios",
            "judge": _APPROVE,
        }, invoked=invoked)
        monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
        prior = _impl_handover(ws)
        seed_and_run(TEAM, ws, prior, lambda _e: None, task_id="t-a")
        assert "clause_auditor" not in invoked
        entries = [
            json.loads(line)
            for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
        ]
        assert any(e["action"] == "clause_audit_skipped_unscoped" for e in entries)
        assert not any(
            e["action"] == "clause_audit_skipped_no_owned_requirements" for e in entries
        )

    def test_select_requirements_records_skip_when_no_owned_requirements(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path
    ) -> None:
        req = Requirement(text="deliver X")
        task_a = Task(
            id="t-a", status=TaskStatus.READY, parent_task_id="parent",
            dod_clauses=[TaskDoDClause(text="done a", covers=[req.id])],
        )
        task_b = Task(
            id="t-b", status=TaskStatus.READY, parent_task_id="parent",
            dod_clauses=[TaskDoDClause(text="done b", covers=[req.id])],
        )
        ws = _workspace(tmp_path, requirements=[req], tasks=[task_a, task_b])
        monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
        invoked: list[str] = []
        fake = _role_fake({
            "diff_reviewer": "DIFF_FINDINGS_CRITICAL: 0",
            "scenario_runner": "no scenarios",
            "judge": _APPROVE,
        }, invoked=invoked)
        monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
        prior = _impl_handover(ws)
        seed_and_run(TEAM, ws, prior, lambda _e: None, task_id="t-a")
        assert "clause_auditor" not in invoked
        entries = [
            json.loads(line)
            for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
        ]
        assert any(
            e["action"] == "clause_audit_skipped_no_owned_requirements" for e in entries
        )
        assert not any(e["action"] == "clause_audit_skipped_unscoped" for e in entries)

    def test_report_mode_withholds_from_judge_but_appends_table(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path  # noqa: ARG002
    ) -> None:
        monkeypatch.setenv("ALAN_CLAUSE_AUDIT", "report")
        req = Requirement(text="deliver X")
        ws = _workspace(tmp_path, requirements=[req])
        monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
        monkeypatch.setattr(_pr_branch, "fetch_diff_files", lambda *_a, **_kw: frozenset())
        captured: dict[str, str] = {}
        fake = _role_fake({
            "diff_reviewer": "DIFF_FINDINGS_CRITICAL: 0",
            "scenario_runner": "no scenarios",
            "clause_auditor": f"CLAUSE {req.id}: FAIL — nope\nCLAUSE_FAILURES: 1\n",
            "judge": _APPROVE,
        }, captured=captured)
        monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
        prior = _impl_handover(ws)
        handover = seed_and_run(TEAM, ws, prior, lambda _e: None)
        assert "clause_auditor" not in captured["judge"]
        assert handover.to_team_suggestions == ["pr_merge"]
        body = Path(handover.artifacts_produced[0].path).read_text()
        assert "Acceptance Clause Audit" in body
        assert "VERDICT: APPROVE" in body

    def test_enforce_default_posts_table_in_review_body(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path,  # noqa: ARG002
        forge: ForgeScript,
    ) -> None:
        req = Requirement(text="deliver X")
        ws = _workspace(tmp_path, requirements=[req])
        monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
        monkeypatch.setattr(_pr_branch, "fetch_diff_files", lambda *_a, **_kw: frozenset())
        fake = _role_fake({
            "diff_reviewer": "DIFF_FINDINGS_CRITICAL: 0",
            "scenario_runner": "no scenarios",
            "clause_auditor": f"CLAUSE {req.id}: FAIL — nope\nCLAUSE_FAILURES: 1\n",
            "judge": _APPROVE,
        })
        monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
        forge.queue("post_review", api_result("{}"))
        prior = _impl_handover(ws)
        seed_and_run(TEAM, ws, prior, lambda _e: None)
        payloads = forge.review_payloads()
        assert payloads, "expected a review post"
        body = payloads[0]["body"]
        assert isinstance(body, str)
        assert "VERDICT: REQUEST_CHANGES" in body
        assert "Acceptance Clause Audit" in body


def test_outputs_ordering_preserved_with_clause_auditor_injected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path  # noqa: ARG001
) -> None:
    req = Requirement(text="deliver X")
    ws = _workspace(tmp_path, requirements=[req])
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    monkeypatch.setattr(_pr_branch, "fetch_diff_files", lambda *_a, **_kw: frozenset())
    judge_missing_count = "## Verdict\n\nVERDICT: APPROVE\n\n## Rationale\nfine"
    fake = _role_fake({
        "diff_reviewer": "DIFF_FINDINGS_CRITICAL: 3",
        "scenario_runner": "no scenarios",
        "clause_auditor": f"CLAUSE {req.id}: PASS — ok\nCLAUSE_FAILURES: 0\n",
        "judge": judge_missing_count,
    })
    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    prior = _impl_handover(ws)
    handover = seed_and_run(TEAM, ws, prior, lambda _e: None)
    body = Path(handover.artifacts_produced[0].path).read_text()
    assert "DIFF_FINDINGS_CRITICAL: 3" in body
    assert "VERDICT: REQUEST_CHANGES" in body


def test_all_roles_static_list_unaffected() -> None:
    assert [r.name for r in ROLES] == ["diff_reviewer", "scenario_runner", "judge"]


class TestEvidencePrefixOwnedClauseText:
    def test_appends_owned_clause_text_under_requirement_line(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path  # noqa: ARG002
    ) -> None:
        req = Requirement(text="deliver X")
        task_a = Task(
            id="t-a", status=TaskStatus.READY, parent_task_id="parent",
            dod_clauses=[TaskDoDClause(text="the endpoint returns 200", covers=[req.id])],
        )
        task_b = Task(id="t-b", status=TaskStatus.READY, parent_task_id="parent")
        ws = _workspace(tmp_path, requirements=[req], tasks=[task_a, task_b])
        monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
        monkeypatch.setattr(_pr_branch, "fetch_diff_files", lambda *_a, **_kw: frozenset())
        captured: dict[str, str] = {}
        auditor_output = f"CLAUSE {req.id}: PASS — present\nCLAUSE_FAILURES: 0\n"
        fake = _role_fake({
            "diff_reviewer": "DIFF_FINDINGS_CRITICAL: 0",
            "scenario_runner": "no scenarios",
            "clause_auditor": auditor_output,
            "judge": _APPROVE,
        }, captured=captured)
        monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
        prior = _impl_handover(ws)
        seed_and_run(TEAM, ws, prior, lambda _e: None, task_id="t-a")
        prompt = captured["clause_auditor"]
        assert f"- {req.id}: {req.text}" in prompt
        assert "    - the endpoint returns 200" in prompt

    def test_renders_no_sublines_when_no_owned_clause_texts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path  # noqa: ARG002
    ) -> None:
        req = Requirement(text="deliver X")
        ws = _workspace(tmp_path, requirements=[req])
        monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
        monkeypatch.setattr(_pr_branch, "fetch_diff_files", lambda *_a, **_kw: frozenset())
        captured: dict[str, str] = {}
        auditor_output = f"CLAUSE {req.id}: PASS — present\nCLAUSE_FAILURES: 0\n"
        fake = _role_fake({
            "diff_reviewer": "DIFF_FINDINGS_CRITICAL: 0",
            "scenario_runner": "no scenarios",
            "clause_auditor": auditor_output,
            "judge": _APPROVE,
        }, captured=captured)
        monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
        prior = _impl_handover(ws)
        seed_and_run(TEAM, ws, prior, lambda _e: None)
        prompt = captured["clause_auditor"]
        assert f"- {req.id}: {req.text}" in prompt
        assert "    - " not in prompt


class TestCheckCitationGuidance:
    def test_clause_auditor_prompt_owes_a_citation_for_a_command_it_did_not_run(self) -> None:
        prompt = clause_audit_mod._CLAUSE_AUDITOR_PROMPT  # noqa: SLF001
        assert "a command this pass did not or may not run" in prompt
        assert "rather than leaving the FAIL uncited" in prompt

    def test_clause_auditor_prompt_names_both_sources_of_an_exact_check_name(self) -> None:
        prompt = clause_audit_mod._CLAUSE_AUDITOR_PROMPT  # noqa: SLF001
        assert "host's required-check list" in prompt
        assert "`.github/workflows/`" in prompt

    def test_clause_auditor_prompt_asks_for_the_citation_before_the_check_finishes(self) -> None:
        prompt = clause_audit_mod._CLAUSE_AUDITOR_PROMPT  # noqa: SLF001
        assert "cite it even when you cannot tell whether it has finished" in prompt
        assert "reads the check's conclusion when it decides" in prompt


class TestFullSuiteOwnershipGuidance:
    def test_clause_auditor_prompt_names_it_sole_test_owner(self) -> None:
        prompt = clause_audit_mod._CLAUSE_AUDITOR_PROMPT  # noqa: SLF001
        assert "sole owner" in prompt
        assert "one full backend suite run" in prompt
        assert "one frontend coverage run" in prompt
        assert "diff_reviewer and scenario_runner are directed at" in prompt
        assert "targeted, diff-scoped test selections" in prompt

    def test_diff_reviewer_and_scenario_runner_defer_to_clause_auditor(self) -> None:
        from alan.teams import verification as verif_mod
        from alan.teams._verification_prompts import TARGETED_TEST_SCOPE_BLOCK

        token = verif_mod._review_context_var.set(TARGETED_TEST_SCOPE_BLOCK)  # noqa: SLF001
        try:
            for role_name in ("diff_reviewer", "scenario_runner"):
                prefix = TEAM._role_user_prefix(role_name)  # noqa: SLF001
                assert "clause_auditor is this verification pass's sole owner" in prefix
                assert "do not re-run either full suite yourself" in prefix
        finally:
            verif_mod._review_context_var.reset(token)  # noqa: SLF001

    def test_generic_engine_clause_auditor_prompt_uses_governing_gate_commands(
        self,
    ) -> None:
        from alan.engine_config import EngineConfig, RepoIdentity

        cfg = EngineConfig(
            repo=RepoIdentity(owner="rustco", repo="cargo-service"),
            gate_commands=[["cargo", "test"]],
            warmup_commands=[["cargo", "fetch"]],
        )
        prompt = clause_audit_mod.clause_auditor_prompt(cfg)
        assert "cargo test" in prompt
        assert "sole owner" in prompt
        assert "uv run --no-active pytest" not in prompt
        assert "npm --prefix alan/web run test:coverage" not in prompt
        assert "alan/web" not in prompt

    def test_generic_engine_clause_auditor_prompt_without_gate_commands(self) -> None:
        from alan.engine_config import EngineConfig, RepoIdentity

        cfg = EngineConfig(repo=RepoIdentity(owner="rustco", repo="bare"))
        prompt = clause_audit_mod.clause_auditor_prompt(cfg)
        assert "declares no local quality gates" in prompt
        assert "uv run --no-active pytest" not in prompt

    def test_default_engine_clause_auditor_prompt_is_the_static_constant(self) -> None:
        from alan.engine_config import _DEFAULT  # pyright: ignore[reportPrivateUsage]

        assert clause_audit_mod.clause_auditor_prompt(_DEFAULT) is (
            clause_audit_mod._CLAUSE_AUDITOR_PROMPT  # noqa: SLF001
        )

    def test_clause_auditor_prompt_directs_capture_once_and_forbids_reformat_reruns(
        self,
    ) -> None:
        prompt = clause_audit_mod._CLAUSE_AUDITOR_PROMPT  # noqa: SLF001
        assert "Capture each run the one time you run it" in prompt
        assert (
            "`uv run --no-active pytest > pytest_full.log 2>&1; rc=$?; "
            "tail -60 pytest_full.log; (exit $rc)`"
        ) in prompt
        assert (
            "`npm --prefix alan/web run test:coverage > vitest_full.log 2>&1; rc=$?; "
            "tail -60 vitest_full.log; (exit $rc)`"
        ) in prompt
        assert "keeping its own exit status" in prompt
        assert "cite from those files (`grep -n`) for every clause" in prompt
        assert "re-formatting output (`-q`, `--co`, `| tail`, `| grep`)" in prompt
        assert "never a reason to run a suite again" in prompt
        assert "a second run of either suite is a defect in the audit, not extra evidence" in (
            prompt
        )
        assert "/tmp/" not in prompt  # noqa: S108

    def test_retry_nudge_forbids_rerunning_suites_and_points_at_prior_output(self) -> None:
        nudge = clause_audit_mod._CLAUSE_AUDITOR_RETRY_NUDGE  # noqa: SLF001
        assert "Do NOT run any test suite or gate command again" in nudge
        assert "included above as prior clause_auditor output" in nudge
        assert "`### clause_auditor` section" in nudge
        assert "any log files you captured are still in this checkout" in nudge
        assert "re-read those and re-emit the lines" in nudge

    def test_retry_system_prompt_swaps_ownership_for_a_no_rerun_block_per_engine(self) -> None:
        from alan.engine_config import (
            _DEFAULT,  # pyright: ignore[reportPrivateUsage]
            EngineConfig,
            RepoIdentity,
        )

        forbid = "Do NOT run any test suite or gate command in this retry"
        default = clause_audit_mod.clause_auditor_retry_prompt(_DEFAULT)
        assert default.startswith("You are the clause_auditor")
        assert default.endswith("CLAUSE_FAILURES: <n> (n = count of FAIL lines above).")
        assert "TEST OWNERSHIP (RETRY)" in default
        assert "already owned and ran the one full backend suite run" in default
        assert "uv run --no-active pytest" in default
        assert "npm --prefix alan/web run test:coverage" in default
        assert forbid in default
        assert "`### clause_auditor` section" in default
        assert "run each exactly once" not in default
        assert "run each exactly once" in clause_audit_mod.clause_auditor_prompt(_DEFAULT)

        cargo = clause_audit_mod.clause_auditor_retry_prompt(EngineConfig(
            repo=RepoIdentity(owner="rustco", repo="cargo-service"),
            gate_commands=[["cargo", "test"]],
        ))
        assert "TEST OWNERSHIP (RETRY)" in cargo
        assert "already owned and ran this engine's gate command(s) — `cargo test` —" in cargo
        assert forbid in cargo
        assert "run each exactly once" not in cargo
        for token in ("uv run", "npm", "alan/web"):
            assert token not in cargo

        bare = clause_audit_mod.clause_auditor_retry_prompt(
            EngineConfig(repo=RepoIdentity(owner="rustco", repo="bare"))
        )
        assert "TEST OWNERSHIP (RETRY): this engine declares no local quality gates" in bare
        assert forbid in bare
        assert "already owned and ran" not in bare

    def test_generic_ownership_block_states_capture_once_without_alan_commands(self) -> None:
        from alan.engine_config import EngineConfig, RepoIdentity

        cfg = EngineConfig(
            repo=RepoIdentity(owner="rustco", repo="cargo-service"),
            gate_commands=[["cargo", "test"]],
        )
        block = clause_audit_mod._generic_test_ownership_block(cfg)  # noqa: SLF001
        assert "`cargo test`" in block
        assert "Capture each run's output to a gitignored file inside this checkout" in block
        assert "never run a gate again just to re-read or re-format its output" in block
        assert "a second run is a defect in the audit, not extra evidence" in block
        for token in ("uv run", "npm", "alan/web", "pytest_full.log", "vitest_full.log"):
            assert token not in block


class TestClauseOutcomeFromRows:
    def _context(
        self,
        *,
        requirements: tuple[Requirement, ...] = (),
        clause_texts: dict[str, tuple[str, ...]] | None = None,
        task_id: str = "t1",
    ) -> clause_audit_mod.ClauseAuditContext:
        return clause_audit_mod.ClauseAuditContext(
            requirements=requirements, diff_files=None, pr_state=None,
            pr_number=1, task_id=task_id, mode="enforce",
            forge_target=DEFAULT_FORGE_TARGET,
            clause_texts=clause_texts or {},
        )

    def _operator_only_demotion(self, req_id: str) -> _clause_actionability.DemotedClause:
        return _clause_actionability.DemotedClause(
            row=clause_audit_mod.ClauseAuditRow(
                req_id=req_id, verdict="FAIL", evidence="OPERATOR_ONLY: needs a probe"
            ),
            classification=_clause_actionability.ClauseActionability.OPERATOR_ONLY,
            check_name=None, check_outcome=None,
        )

    def _pr_state_demotion(self, req_id: str) -> _clause_actionability.DemotedClause:
        return _clause_actionability.DemotedClause(
            row=clause_audit_mod.ClauseAuditRow(
                req_id=req_id, verdict="FAIL", evidence="PR_STATE: isDraft is false"
            ),
            classification=_clause_actionability.ClauseActionability.PR_STATE,
            check_name=None, check_outcome=None,
        )

    def test_all_unactionable_batch_still_produces_an_outcome(self) -> None:
        demoted = (self._operator_only_demotion("req-op"),)
        outcome = clause_audit_mod._clause_outcome_from_rows(  # noqa: SLF001
            self._context(), (), demoted, "judge text"
        )
        assert outcome is not None
        assert outcome.actionable_ids == ()
        assert outcome.operator_only_ids == ("req-op",)

    def test_operator_only_ids_populated_only_from_operator_only_demotions(self) -> None:
        demoted = (self._operator_only_demotion("req-op"), self._pr_state_demotion("req-pr"))
        outcome = clause_audit_mod._clause_outcome_from_rows(  # noqa: SLF001
            self._context(), (), demoted, "judge text"
        )
        assert outcome is not None
        assert outcome.operator_only_ids == ("req-op",)

    def test_actionable_and_unactionable_both_empty_returns_none(self) -> None:
        outcome = clause_audit_mod._clause_outcome_from_rows(  # noqa: SLF001
            self._context(), (), (), "judge text"
        )
        assert outcome is None

    def test_populated_only_from_actionable_rows_and_stamps_task_id(self) -> None:
        req = Requirement(text="deliver X")
        actionable = (
            clause_audit_mod.ClauseAuditRow(req_id=req.id, verdict="FAIL", evidence="missing"),
        )
        context = self._context(requirements=(req,), task_id="sub-a")
        outcome = clause_audit_mod._clause_outcome_from_rows(  # noqa: SLF001
            context, actionable, (), "judge text"
        )
        assert outcome is not None
        assert outcome.task_id == "sub-a"
        assert outcome.actionable_ids == (req.id,)
        assert outcome.evidence == {req.id: "missing"}

    def test_text_joined_from_context_clause_texts(self) -> None:
        req = Requirement(text="deliver X")
        actionable = (
            clause_audit_mod.ClauseAuditRow(req_id=req.id, verdict="FAIL", evidence="missing"),
        )
        context = self._context(
            requirements=(req,), clause_texts={req.id: ("sub clause one", "sub clause two")},
        )
        outcome = clause_audit_mod._clause_outcome_from_rows(  # noqa: SLF001
            context, actionable, (), "judge text"
        )
        assert outcome is not None
        assert outcome.clause_texts[req.id] == "sub clause one; sub clause two"

    def test_falls_back_to_requirement_text_when_no_sub_clauses(self) -> None:
        req = Requirement(text="deliver X")
        actionable = (
            clause_audit_mod.ClauseAuditRow(req_id=req.id, verdict="FAIL", evidence="missing"),
        )
        context = self._context(requirements=(req,))
        outcome = clause_audit_mod._clause_outcome_from_rows(  # noqa: SLF001
            context, actionable, (), "judge text"
        )
        assert outcome is not None
        assert outcome.clause_texts[req.id] == "deliver X"

    def test_empty_actionable_returns_none(self) -> None:
        outcome = clause_audit_mod._clause_outcome_from_rows(  # noqa: SLF001
            self._context(), (), (), "judge text"
        )
        assert outcome is None

    def test_judge_rationale_truncated_to_2000_chars(self) -> None:
        req = Requirement(text="deliver X")
        actionable = (
            clause_audit_mod.ClauseAuditRow(req_id=req.id, verdict="FAIL", evidence="missing"),
        )
        outcome = clause_audit_mod._clause_outcome_from_rows(  # noqa: SLF001
            self._context(requirements=(req,)), actionable, (), "x" * 3000
        )
        assert outcome is not None
        assert len(outcome.judge_rationale) == 2000

    def test_clause_text_truncated_to_2000_chars(self) -> None:
        req = Requirement(text="deliver X")
        actionable = (
            clause_audit_mod.ClauseAuditRow(req_id=req.id, verdict="FAIL", evidence="missing"),
        )
        context = self._context(
            requirements=(req,), clause_texts={req.id: ("y" * 3000,)},
        )
        outcome = clause_audit_mod._clause_outcome_from_rows(  # noqa: SLF001
            context, actionable, (), "judge text"
        )
        assert outcome is not None
        assert len(outcome.clause_texts[req.id]) == 2000

    def test_evidence_truncated_to_2000_chars(self) -> None:
        req = Requirement(text="deliver X")
        actionable = (
            clause_audit_mod.ClauseAuditRow(req_id=req.id, verdict="FAIL", evidence="z" * 3000),
        )
        context = self._context(requirements=(req,))
        outcome = clause_audit_mod._clause_outcome_from_rows(  # noqa: SLF001
            context, actionable, (), "judge text"
        )
        assert outcome is not None
        assert len(outcome.evidence[req.id]) == 2000


def _apply_with_context(
    judge_output: str, context: clause_audit_mod.ClauseAuditContext | None,
) -> clause_audit_mod.ClauseAuditOutcome:
    token = clause_audit_mod._clause_audit_var.set(context)  # noqa: SLF001
    try:
        return clause_audit_mod.apply(judge_output)
    finally:
        clause_audit_mod._clause_audit_var.reset(token)  # noqa: SLF001


class TestClauseOutcomeOnApply:
    def test_none_when_context_is_none(self) -> None:
        outcome = _apply_with_context("## Verdict\n\nVERDICT: APPROVE\n", None)
        assert outcome.clause_outcome is None

    def test_none_when_result_is_none(self) -> None:
        context = clause_audit_mod.ClauseAuditContext(
            requirements=(), diff_files=None, pr_state=None, pr_number=1,
            task_id="t1", mode="enforce", forge_target=DEFAULT_FORGE_TARGET,
        )
        outcome = _apply_with_context("## Verdict\n\nVERDICT: APPROVE\n", context)
        assert outcome.clause_outcome is None

    def test_none_when_mode_is_report(self) -> None:
        result = clause_audit_mod.ClauseAuditResult(
            rows=(clause_audit_mod.ClauseAuditRow(req_id="req-a", verdict="FAIL", evidence="x"),),
            reported_failures=1, count_mismatch=False, parse_failed=False,
        )
        context = clause_audit_mod.ClauseAuditContext(
            requirements=(), diff_files=None, pr_state=None, pr_number=1,
            task_id="t1", mode="report", forge_target=DEFAULT_FORGE_TARGET,
            result=result,
        )
        outcome = _apply_with_context("## Verdict\n\nVERDICT: APPROVE\n", context)
        assert outcome.clause_outcome is None

    def test_none_when_no_failed_rows(self) -> None:
        result = clause_audit_mod.ClauseAuditResult(
            rows=(clause_audit_mod.ClauseAuditRow(req_id="req-a", verdict="PASS", evidence="ok"),),
            reported_failures=0, count_mismatch=False, parse_failed=False,
        )
        context = clause_audit_mod.ClauseAuditContext(
            requirements=(), diff_files=None, pr_state=None, pr_number=1,
            task_id="t1", mode="enforce", forge_target=DEFAULT_FORGE_TARGET,
            result=result,
        )
        outcome = _apply_with_context("## Verdict\n\nVERDICT: APPROVE\n", context)
        assert outcome.clause_outcome is None

    def test_none_on_stands_down_branch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        check_name = "Backend tests + coverage"
        result = clause_audit_mod.ClauseAuditResult(
            rows=(clause_audit_mod.ClauseAuditRow(
                req_id="req-a", verdict="FAIL",
                evidence=f'ENV: stale build cache check="{check_name}"',
            ),),
            reported_failures=1, count_mismatch=False, parse_failed=False,
        )
        readiness = _pr_ready.PrReadiness(
            ok=True, merge_state_status="CLEAN", review_decision="APPROVED",
            passing_checks=(check_name,), head_sha="a" * 40,
        )
        monkeypatch.setattr(
            _pr_ready, "check_pr_merge_readiness", lambda *_a, **_kw: readiness,
        )
        context = clause_audit_mod.ClauseAuditContext(
            requirements=(), diff_files=None, pr_state=None, pr_number=1,
            task_id="t1", mode="enforce", forge_target=DEFAULT_FORGE_TARGET,
            result=result, readiness=readiness,
        )
        outcome = _apply_with_context("## Verdict\n\nVERDICT: APPROVE\n", context)
        assert outcome.clause_outcome is None

    def test_populated_on_actionable_enforce_path(self) -> None:
        result = clause_audit_mod.ClauseAuditResult(
            rows=(clause_audit_mod.ClauseAuditRow(
                req_id="req-a", verdict="FAIL", evidence="the file is missing",
            ),),
            reported_failures=1, count_mismatch=False, parse_failed=False,
        )
        context = clause_audit_mod.ClauseAuditContext(
            requirements=(), diff_files=None, pr_state=None, pr_number=1,
            task_id="sub-a", mode="enforce", forge_target=DEFAULT_FORGE_TARGET,
            result=result,
        )
        outcome = _apply_with_context("## Verdict\n\nVERDICT: APPROVE\n", context)
        assert outcome.clause_outcome is not None
        assert outcome.clause_outcome.actionable_ids == ("req-a",)
        assert outcome.clause_outcome.task_id == "sub-a"
