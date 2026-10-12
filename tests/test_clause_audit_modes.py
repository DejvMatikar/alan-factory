from __future__ import annotations

import json
from pathlib import Path

import pytest

import alan.teams._base as base_mod
import alan.teams._clause_actionability as ca_actionability_mod
import alan.teams._clause_audit as ca_mod
import alan.teams._pr_forge as _pr_forge
import alan.teams._pr_ready as _pr_ready
from alan import workflow as workflow_mod
from alan.claude_cli import AgentStreamingResult
from alan.engine_config import _DEFAULT as _ENGINE_DEFAULT  # pyright: ignore[reportPrivateUsage]
from alan.engine_config import DEFAULT_FORGE_TARGET, EngineConfig, Posture, RepoIdentity, RoleConfig
from alan.teams._clause_line_parse import ClauseVerdict
from alan.workspace import EngineSnapshot, Requirement, Workspace, create, stamp_engine_snapshot


@pytest.fixture
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(root))
    return root


def _ws(tmp_path: Path, *, requirements: list[Requirement] | None = None,
        roadmap_item_id: str | None = None) -> Workspace:
    base = create("clause-modes-ws", "d", root=tmp_path / "ws")
    meta = base.meta.model_copy(
        update={"requirements": requirements, "roadmap_item_id": roadmap_item_id}
    )
    return base.model_copy(update={"meta": meta})


def _graph(ws: Workspace) -> workflow_mod.WorkflowGraph:
    inv_id = workflow_mod.seed_pending_workflow(ws, team="verification", tasks=[])
    return workflow_mod.load_graph(ws, inv_id)


def _row(
    req_id: str = "req-a", verdict: ClauseVerdict = "PASS", evidence: str = "cited",
) -> ca_mod.ClauseAuditRow:
    return ca_mod.ClauseAuditRow(req_id=req_id, verdict=verdict, evidence=evidence)


def _context(
    *, mode: ca_mod.ClauseAuditMode = "enforce", result: ca_mod.ClauseAuditResult | None = None,
    readiness: _pr_ready.PrReadiness | None = None,
) -> ca_mod.ClauseAuditContext:
    return ca_mod.ClauseAuditContext(
        requirements=(Requirement(text="A"),), diff_files=None, pr_state=None,
        pr_number=1, task_id="t1", mode=mode, forge_target=DEFAULT_FORGE_TARGET,
        readiness=readiness, result=result,
    )


def _overrides(tmp_path: Path) -> base_mod.WorkerOverrides:
    checkout = tmp_path / "grant-checkout"
    (checkout / ".git").mkdir(parents=True, exist_ok=True)
    return base_mod.WorkerOverrides(cwd=checkout, allowed_tools=["Read", "Bash(dotnet:*)"])


_PARSE_IDS = frozenset({"req-1", "req-2"})


class TestParseClauseAudit:
    def test_no_clause_lines_returns_none(self) -> None:
        assert ca_mod.parse_clause_audit("nothing to see here", _PARSE_IDS) is None

    def test_single_pass_row(self) -> None:
        result = ca_mod.parse_clause_audit(
            "CLAUSE req-1: PASS — file.py:10 proves it\n", _PARSE_IDS
        )
        assert result is not None
        assert result.rows == (ca_mod.ClauseAuditRow("req-1", "PASS", "file.py:10 proves it"),)

    def test_fail_with_evidence(self) -> None:
        result = ca_mod.parse_clause_audit(
            "CLAUSE req-1: FAIL — missing the file\n", _PARSE_IDS
        )
        assert result is not None
        assert result.rows[0].verdict == "FAIL"

    def test_fail_without_evidence_downgrades_to_unverifiable(self) -> None:
        result = ca_mod.parse_clause_audit("CLAUSE req-1: FAIL — \n", _PARSE_IDS)
        assert result is not None
        assert result.rows[0].verdict == "UNVERIFIABLE"

    def test_unverifiable_row(self) -> None:
        result = ca_mod.parse_clause_audit(
            "CLAUSE req-1: UNVERIFIABLE — can't tell\n", _PARSE_IDS
        )
        assert result is not None
        assert result.rows[0].verdict == "UNVERIFIABLE"

    def test_multiple_rows(self) -> None:
        text = (
            "CLAUSE req-1: PASS — a\n"
            "CLAUSE req-2: FAIL — b\n"
            "CLAUSE_FAILURES: 1\n"
        )
        result = ca_mod.parse_clause_audit(text, _PARSE_IDS)
        assert result is not None
        assert len(result.rows) == 2
        assert result.reported_failures == 1
        assert result.count_mismatch is False

    def test_count_mismatch_flagged(self) -> None:
        text = "CLAUSE req-1: FAIL — b\nCLAUSE_FAILURES: 0\n"
        result = ca_mod.parse_clause_audit(text, _PARSE_IDS)
        assert result is not None
        assert result.count_mismatch is True

    def test_count_line_absent(self) -> None:
        result = ca_mod.parse_clause_audit("CLAUSE req-1: PASS — a\n", _PARSE_IDS)
        assert result is not None
        assert result.reported_failures is None
        assert result.count_mismatch is False

    def test_hyphen_separator_accepted(self) -> None:
        result = ca_mod.parse_clause_audit("CLAUSE req-1: PASS - looks fine\n", _PARSE_IDS)
        assert result is not None
        assert result.rows[0].evidence == "looks fine"

    def test_double_hyphen_separator_accepted(self) -> None:
        result = ca_mod.parse_clause_audit("CLAUSE req-1: FAIL -- missing file\n", _PARSE_IDS)
        assert result is not None
        assert result.rows[0].evidence == "missing file"


class TestRenderClauseTable:
    def test_basic_row_rendering(self) -> None:
        result = ca_mod.ClauseAuditResult(
            rows=(_row(),), reported_failures=None, count_mismatch=False, parse_failed=False,
        )
        table = ca_mod.render_clause_table(result)
        assert "req-a" in table
        assert "PASS" in table
        assert "cited" in table

    def test_blank_evidence_renders_dash(self) -> None:
        result = ca_mod.ClauseAuditResult(
            rows=(_row(evidence=""),), reported_failures=None,
            count_mismatch=False, parse_failed=False,
        )
        assert "| — |" in ca_mod.render_clause_table(result)

    def test_parse_failed_note_included(self) -> None:
        result = ca_mod.ClauseAuditResult(
            rows=(_row(verdict="UNVERIFIABLE"),), reported_failures=None,
            count_mismatch=False, parse_failed=True,
        )
        assert "degraded to" in ca_mod.render_clause_table(result)

    def test_count_mismatch_note_included(self) -> None:
        result = ca_mod.ClauseAuditResult(
            rows=(_row(verdict="FAIL"),), reported_failures=0,
            count_mismatch=True, parse_failed=False,
        )
        assert "did not match" in ca_mod.render_clause_table(result)


class TestAbsorb:
    def test_no_context_returns_pairs_unchanged(self, tmp_path: Path) -> None:
        ws = _ws(tmp_path)
        graph = _graph(ws)
        pairs = [("diff_reviewer", "x")]
        result = ca_mod.absorb(
            pairs, ws, graph, "inv_1", lambda _e: None, "base", [], None,
            overrides=_overrides(tmp_path),
        )
        assert result == pairs

    def test_no_auditor_pair_returns_unchanged(self, tmp_path: Path) -> None:
        ws = _ws(tmp_path, requirements=[Requirement(text="A")])
        graph = _graph(ws)
        token = ca_mod._clause_audit_var.set(_context())  # noqa: SLF001
        try:
            pairs = [("diff_reviewer", "x")]
            result = ca_mod.absorb(
                pairs, ws, graph, "inv_1", lambda _e: None, "base", [], None,
                overrides=_overrides(tmp_path),
            )
            assert result == pairs
        finally:
            ca_mod._clause_audit_var.reset(token)  # noqa: SLF001

    def test_parses_on_first_try_enforce_keeps_pair(self, tmp_path: Path) -> None:
        ws = _ws(tmp_path, requirements=[Requirement(text="A")])
        graph = _graph(ws)
        token = ca_mod._clause_audit_var.set(_context(mode="enforce"))  # noqa: SLF001
        try:
            auditor_line = f"CLAUSE {Requirement(text='A').id}: PASS — ok\n"
            pairs = [("diff_reviewer", "x"), ("clause_auditor", auditor_line)]
            result = ca_mod.absorb(
                pairs, ws, graph, "inv_1", lambda _e: None, "base", [], None,
                overrides=_overrides(tmp_path),
            )
            assert result == pairs
            context = ca_mod._clause_audit_var.get()  # noqa: SLF001
            assert context is not None
            assert context.result is not None
            assert context.result.rows[0].verdict == "PASS"
        finally:
            ca_mod._clause_audit_var.reset(token)  # noqa: SLF001

    def test_parses_on_first_try_report_strips_pair(self, tmp_path: Path) -> None:
        ws = _ws(tmp_path, requirements=[Requirement(text="A")])
        graph = _graph(ws)
        token = ca_mod._clause_audit_var.set(_context(mode="report"))  # noqa: SLF001
        try:
            auditor_line = f"CLAUSE {Requirement(text='A').id}: PASS — ok\n"
            pairs = [("diff_reviewer", "x"), ("clause_auditor", auditor_line)]
            result = ca_mod.absorb(
                pairs, ws, graph, "inv_1", lambda _e: None, "base", [], None,
                overrides=_overrides(tmp_path),
            )
            assert result == [("diff_reviewer", "x")]
        finally:
            ca_mod._clause_audit_var.reset(token)  # noqa: SLF001

    def test_retry_succeeds(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ws = _ws(tmp_path, requirements=[Requirement(text="A")])
        graph = _graph(ws)
        retry_line = f"CLAUSE {Requirement(text='A').id}: FAIL — retried\nCLAUSE_FAILURES: 1\n"
        monkeypatch.setattr(
            ca_mod, "_run_clause_auditor_retry", lambda *_a, **_kw: retry_line,
        )
        token = ca_mod._clause_audit_var.set(_context(mode="enforce"))  # noqa: SLF001
        try:
            pairs = [("clause_auditor", "not parseable at all")]
            ca_mod.absorb(
                pairs, ws, graph, "inv_1", lambda _e: None, "base", [], None,
                overrides=_overrides(tmp_path),
            )
            context = ca_mod._clause_audit_var.get()  # noqa: SLF001
            assert context is not None
            assert context.result is not None
            assert context.result.rows[0].evidence == "retried"
        finally:
            ca_mod._clause_audit_var.reset(token)  # noqa: SLF001

    def test_retry_also_fails_degrades_and_audits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path
    ) -> None:
        req = Requirement(text="A")
        ws = _ws(tmp_path, requirements=[req])
        graph = _graph(ws)
        monkeypatch.setattr(ca_mod, "_run_clause_auditor_retry", lambda *_a, **_kw: "still bad")
        context = _context(mode="enforce")
        context.requirements = (req,)
        token = ca_mod._clause_audit_var.set(context)  # noqa: SLF001
        try:
            pairs = [("clause_auditor", "not parseable")]
            ca_mod.absorb(
                pairs, ws, graph, "inv_1", lambda _e: None, "base", [], None,
                overrides=_overrides(tmp_path),
            )
            updated = ca_mod._clause_audit_var.get()  # noqa: SLF001
            assert updated is not None
            assert updated.result is not None
            assert updated.result.parse_failed is True
            assert updated.result.rows[0].verdict == "UNVERIFIABLE"
            entries = [
                json.loads(line)
                for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
            ]
            assert any(e["action"] == "clause_audit_parse_failed" for e in entries)
        finally:
            ca_mod._clause_audit_var.reset(token)  # noqa: SLF001


class TestClauseAuditorModel:
    def test_falls_back_to_engine_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = _ws(tmp_path)
        empty_cfg = EngineConfig(repo=RepoIdentity(owner="acme", repo="empty-roles"))
        monkeypatch.setattr(_pr_forge, "governing_config_for_ws", lambda _ws: empty_cfg)
        assert ca_mod._clause_auditor_model(ws) == (  # noqa: SLF001
            _ENGINE_DEFAULT.roles["diff_reviewer"].model
        )

    def test_uses_workspace_engine_override(self, tmp_path: Path) -> None:
        ws = _ws(tmp_path)
        cfg = EngineConfig(
            repo=RepoIdentity(owner="acme", repo="widget"),
            roles={"diff_reviewer": RoleConfig(model="custom-model")},
            posture=Posture.autonomous,
        )
        ws = stamp_engine_snapshot(
            ws, EngineSnapshot(posture=Posture.autonomous, transition_graph={}, config=cfg)
        )
        assert ca_mod._clause_auditor_model(ws) == "custom-model"  # noqa: SLF001


class TestRunClauseAuditorRetryReal:
    def test_real_retry_calls_run_or_resume_node(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        req = Requirement(text="A")
        ws = _ws(tmp_path, requirements=[req])
        inv_id = workflow_mod.seed_pending_workflow(ws, team="verification", tasks=[])
        graph = workflow_mod.load_graph(ws, inv_id)
        captured: dict[str, object] = {}

        def fake_run_agent_streaming(
            system_prompt: str, user_text: str, **kwargs: object
        ) -> AgentStreamingResult:
            captured["system_prompt"] = system_prompt
            captured["user_text"] = user_text
            captured["cwd"] = getattr(kwargs.get("proc_config"), "cwd", None)
            stream_path = kwargs.get("stream_path")
            if isinstance(stream_path, Path):
                stream_path.parent.mkdir(parents=True, exist_ok=True)
                stream_path.write_text(
                    json.dumps({
                        "type": "result",
                        "result": f"CLAUSE {req.id}: FAIL — retried\nCLAUSE_FAILURES: 1\n",
                    }) + "\n"
                )
            return AgentStreamingResult(
                result_text=f"CLAUSE {req.id}: FAIL — retried\nCLAUSE_FAILURES: 1\n"
            )

        monkeypatch.setattr(base_mod, "run_agent_streaming", fake_run_agent_streaming)
        context = _context(mode="enforce")
        context.requirements = (req,)
        token = ca_mod._clause_audit_var.set(context)  # noqa: SLF001
        grounded = _overrides(tmp_path)
        try:
            pairs = [("clause_auditor", "not parseable at all")]
            ca_mod.absorb(
                pairs, ws, graph, inv_id, lambda _e: None, "base text", [], None,
                overrides=grounded,
            )
            updated = ca_mod._clause_audit_var.get()  # noqa: SLF001
            assert updated is not None
            assert updated.result is not None
            assert updated.result.rows[0].evidence == "retried"
            assert "clause_auditor" in str(captured["system_prompt"])
            assert captured["cwd"] == grounded.cwd
            retry_task = next(
                t for t in graph.doc.tasks if t.id == ca_mod._CLAUSE_AUDITOR_RETRY_NODE_ID
            )
            assert retry_task.status == "success"
        finally:
            ca_mod._clause_audit_var.reset(token)  # noqa: SLF001


class TestAppendRouteAuditRow:
    def test_no_roadmap_item_id_is_noop(self, tmp_path: Path, audit_root: Path) -> None:
        ws = _ws(tmp_path, roadmap_item_id=None, requirements=None)
        ca_mod.append_route_audit_row(ws)
        assert not (audit_root / "verification_audit.jsonl").exists()

    def test_roadmap_item_id_with_none_requirements_audits(
        self, tmp_path: Path, audit_root: Path
    ) -> None:
        ws = _ws(tmp_path, roadmap_item_id="H8.1b", requirements=None)
        ca_mod.append_route_audit_row(ws)
        entries = [
            json.loads(line)
            for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
        ]
        assert any(e["action"] == "clause_audit_route_unaudited" for e in entries)

    def test_roadmap_item_id_with_empty_requirements_audits(
        self, tmp_path: Path, audit_root: Path
    ) -> None:
        ws = _ws(tmp_path, roadmap_item_id="H8.1b", requirements=[])
        ca_mod.append_route_audit_row(ws)
        entries = [
            json.loads(line)
            for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
        ]
        assert any(e["action"] == "clause_audit_route_unaudited" for e in entries)

    def test_roadmap_item_id_with_requirements_is_noop(
        self, tmp_path: Path, audit_root: Path
    ) -> None:
        ws = _ws(tmp_path, roadmap_item_id="H8.1b", requirements=[Requirement(text="A")])
        ca_mod.append_route_audit_row(ws)
        assert not (audit_root / "verification_audit.jsonl").exists()


_APPROVE = "## Verdict\n\nVERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
_REQUEST_CHANGES = "## Verdict\n\nVERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 1\n"


class TestApply:
    def test_no_context_passes_through_count_flip(self) -> None:
        flip_input = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 2\n"
        outcome = ca_mod.apply(flip_input)
        assert outcome.escalate_unactionable is False
        assert "VERDICT FLIPPED" in outcome.judge_output

    def test_context_without_result_passes_through(self) -> None:
        token = ca_mod._clause_audit_var.set(_context(result=None))  # noqa: SLF001
        try:
            outcome = ca_mod.apply(_APPROVE)
            assert outcome.judge_output == _APPROVE
            assert outcome.escalate_unactionable is False
        finally:
            ca_mod._clause_audit_var.reset(token)  # noqa: SLF001

    def test_report_mode_never_downgrades_but_appends_table(self) -> None:
        result = ca_mod.ClauseAuditResult(
            rows=(_row(verdict="FAIL"),), reported_failures=1,
            count_mismatch=False, parse_failed=False,
        )
        token = ca_mod._clause_audit_var.set(_context(mode="report", result=result))  # noqa: SLF001
        try:
            outcome = ca_mod.apply(_APPROVE)
            assert "VERDICT: APPROVE" in outcome.judge_output
            assert "Acceptance Clause Audit" in outcome.judge_output
            assert outcome.escalate_unactionable is False
        finally:
            ca_mod._clause_audit_var.reset(token)  # noqa: SLF001

    def test_enforce_mode_all_pass_no_downgrade(self) -> None:
        result = ca_mod.ClauseAuditResult(
            rows=(_row(verdict="PASS"),), reported_failures=None,
            count_mismatch=False, parse_failed=False,
        )
        token = ca_mod._clause_audit_var.set(_context(mode="enforce", result=result))  # noqa: SLF001
        try:
            outcome = ca_mod.apply(_APPROVE)
            assert "VERDICT: APPROVE" in outcome.judge_output
            assert outcome.escalate_unactionable is False
        finally:
            ca_mod._clause_audit_var.reset(token)  # noqa: SLF001

    def test_enforce_mode_fail_downgrades_approve(self) -> None:
        result = ca_mod.ClauseAuditResult(
            rows=(_row(req_id="req-x", verdict="FAIL", evidence="the file is missing"),),
            reported_failures=1, count_mismatch=False, parse_failed=False,
        )
        token = ca_mod._clause_audit_var.set(_context(mode="enforce", result=result))  # noqa: SLF001
        try:
            outcome = ca_mod.apply(_APPROVE)
            assert "VERDICT: REQUEST_CHANGES" in outcome.judge_output
            assert "ACCEPTANCE CLAUSE AUDIT" in outcome.judge_output
            assert "req-x" in outcome.judge_output
            assert outcome.escalate_unactionable is False
            assert outcome.unactionable_rows == ()
        finally:
            ca_mod._clause_audit_var.reset(token)  # noqa: SLF001

    def test_enforce_mode_fail_already_request_changes_no_banner(self) -> None:
        result = ca_mod.ClauseAuditResult(
            rows=(_row(verdict="FAIL", evidence="missing"),), reported_failures=1,
            count_mismatch=False, parse_failed=False,
        )
        token = ca_mod._clause_audit_var.set(_context(mode="enforce", result=result))  # noqa: SLF001
        try:
            outcome = ca_mod.apply(_REQUEST_CHANGES)
            assert "ACCEPTANCE CLAUSE AUDIT" not in outcome.judge_output
            assert "VERDICT: REQUEST_CHANGES" in outcome.judge_output
        finally:
            ca_mod._clause_audit_var.reset(token)  # noqa: SLF001

    def test_enforce_mode_pr_state_only_fail_escalates(self) -> None:
        result = ca_mod.ClauseAuditResult(
            rows=(_row(verdict="FAIL", evidence="PR_STATE: isDraft is false"),),
            reported_failures=1, count_mismatch=False, parse_failed=False,
        )
        token = ca_mod._clause_audit_var.set(_context(mode="enforce", result=result))  # noqa: SLF001
        try:
            outcome = ca_mod.apply(_APPROVE)
            assert outcome.escalate_unactionable is True
            assert len(outcome.unactionable_rows) == 1
            assert (
                outcome.unactionable_rows[0].classification
                is ca_actionability_mod.ClauseActionability.PR_STATE
            )
        finally:
            ca_mod._clause_audit_var.reset(token)  # noqa: SLF001

    def test_enforce_mode_mixed_evidence_does_not_escalate(self) -> None:
        result = ca_mod.ClauseAuditResult(
            rows=(
                _row(req_id="req-a", verdict="FAIL", evidence="PR_STATE: isDraft is false"),
                _row(req_id="req-b", verdict="FAIL", evidence="file missing from diff"),
            ),
            reported_failures=2, count_mismatch=False, parse_failed=False,
        )
        token = ca_mod._clause_audit_var.set(_context(mode="enforce", result=result))  # noqa: SLF001
        try:
            outcome = ca_mod.apply(_APPROVE)
            assert outcome.escalate_unactionable is False
            assert len(outcome.unactionable_rows) == 1
            assert outcome.unactionable_rows[0].row.req_id == "req-a"
        finally:
            ca_mod._clause_audit_var.reset(token)  # noqa: SLF001

    def test_stand_down_prepends_note_above_count_flip_banner(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        readiness = _pr_ready.PrReadiness(
            ok=True, merge_state_status="CLEAN", review_decision="APPROVED",
            passing_checks=("Backend tests + coverage",), head_sha="a" * 40,
        )
        monkeypatch.setattr(
            _pr_ready, "check_pr_merge_readiness", lambda *_a, **_kw: readiness,
        )
        result = ca_mod.ClauseAuditResult(
            rows=(
                _row(
                    req_id="req-x", verdict="FAIL",
                    evidence='ENV: stale cache check="Backend tests + coverage"',
                ),
            ),
            reported_failures=1, count_mismatch=False, parse_failed=False,
        )
        token = ca_mod._clause_audit_var.set(  # noqa: SLF001
            _context(mode="enforce", result=result, readiness=readiness)
        )
        try:
            flip_input = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 1\n"
            outcome = ca_mod.apply(flip_input)
            assert outcome.judge_output.startswith("> ACCEPTANCE CLAUSE AUDIT — stood down")
            note_idx = outcome.judge_output.index("ACCEPTANCE CLAUSE AUDIT — stood down")
            flip_idx = outcome.judge_output.index("VERDICT FLIPPED")
            assert note_idx < flip_idx
            assert outcome.escalate_unactionable is False
        finally:
            ca_mod._clause_audit_var.reset(token)  # noqa: SLF001

    def test_stand_down_still_renders_clause_table(self, monkeypatch: pytest.MonkeyPatch) -> None:
        readiness = _pr_ready.PrReadiness(
            ok=True, merge_state_status="CLEAN", review_decision="APPROVED",
            passing_checks=("Backend tests + coverage",), head_sha="a" * 40,
        )
        monkeypatch.setattr(
            _pr_ready, "check_pr_merge_readiness", lambda *_a, **_kw: readiness,
        )
        result = ca_mod.ClauseAuditResult(
            rows=(
                _row(
                    req_id="req-x", verdict="FAIL",
                    evidence='ENV: stale cache check="Backend tests + coverage"',
                ),
            ),
            reported_failures=1, count_mismatch=False, parse_failed=False,
        )
        token = ca_mod._clause_audit_var.set(  # noqa: SLF001
            _context(mode="enforce", result=result, readiness=readiness)
        )
        try:
            outcome = ca_mod.apply(_APPROVE)
            assert "## Acceptance Clause Audit" in outcome.judge_output
            assert "req-x" in outcome.judge_output
            assert "FAIL" in outcome.judge_output
        finally:
            ca_mod._clause_audit_var.reset(token)  # noqa: SLF001
