from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import alan.teams._clause_audit as ca_mod
from alan.engine_config import EngineConfig, ForgeTarget, RepoIdentity
from alan.teams import _pr_branch, _pr_ready
from alan.teams._static_pipeline import Role
from alan.workspace import Requirement, Task, TaskDoDClause, TaskStatus, Workspace, create
from tests.fakes.forge import FakeForge
from tests.forge_stubs import always, gh_unreachable, pr_read

_FORGE = ForgeTarget(
    repo=RepoIdentity(owner="acme", repo="widget"),
    credential_env_name="ACME_GH_TOKEN",  # noqa: S106  # nosec B106
)


def _ws(tmp_path: Path, *, requirements: list[Requirement] | None = None,
        tasks: list[Task] | None = None) -> Workspace:
    base = create("clause-audit-ws", "d", root=tmp_path / "ws")
    meta = base.meta.model_copy(update={"requirements": requirements, "tasks": tasks or []})
    return base.model_copy(update={"meta": meta})


@pytest.fixture(autouse=True)
def _gh_down() -> None:
    gh_unreachable()


@pytest.fixture(autouse=True)
def _reset_clause_audit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_CLAUSE_AUDIT", raising=False)


@pytest.fixture
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(root))
    return root


class TestClauseAuditMode:
    def test_unset_defaults_enforce(self) -> None:
        assert ca_mod.clause_audit_mode() == "enforce"

    def test_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALAN_CLAUSE_AUDIT", "off")
        assert ca_mod.clause_audit_mode() == "off"

    def test_report(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALAN_CLAUSE_AUDIT", "report")
        assert ca_mod.clause_audit_mode() == "report"

    def test_enforce_explicit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALAN_CLAUSE_AUDIT", "enforce")
        assert ca_mod.clause_audit_mode() == "enforce"

    def test_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALAN_CLAUSE_AUDIT", "OFF")
        assert ca_mod.clause_audit_mode() == "off"

    def test_garbage_defaults_enforce(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALAN_CLAUSE_AUDIT", "bogus")
        assert ca_mod.clause_audit_mode() == "enforce"


class TestSelectRequirements:
    def test_undecomposed_none_requirements(self, tmp_path: Path) -> None:
        ws = _ws(tmp_path, requirements=None)
        assert ca_mod._select_requirements(ws, ws.task_id) == []  # noqa: SLF001

    def test_undecomposed_returns_all_requirements(self, tmp_path: Path) -> None:
        req = Requirement(text="deliver X")
        ws = _ws(tmp_path, requirements=[req])
        assert ca_mod._select_requirements(ws, ws.task_id) == [req]  # noqa: SLF001

    def test_decomposed_task_not_found_skips_unscoped(
        self, tmp_path: Path, audit_root: Path
    ) -> None:
        req = Requirement(text="A")
        task_a = Task(id="t-a", status=TaskStatus.READY, dod_clauses=[
            TaskDoDClause(text="done a", covers=[req.id])
        ])
        task_b = Task(id="t-b", status=TaskStatus.READY, dod_clauses=[
            TaskDoDClause(text="done b", covers=[req.id])
        ])
        ws = _ws(tmp_path, requirements=[req], tasks=[task_a, task_b])
        result = ca_mod._select_requirements(ws, "nonexistent")  # noqa: SLF001
        assert result == []
        entries = [
            json.loads(line)
            for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
        ]
        assert any(e["action"] == "clause_audit_skipped_unscoped" for e in entries)

    def test_decomposed_no_dod_clauses_skips_unscoped(
        self, tmp_path: Path, audit_root: Path
    ) -> None:
        req = Requirement(text="A")
        task_a = Task(id="t-a", status=TaskStatus.READY, dod_clauses=[])
        task_b = Task(id="t-b", status=TaskStatus.READY, dod_clauses=[
            TaskDoDClause(text="done b", covers=[req.id])
        ])
        ws = _ws(tmp_path, requirements=[req], tasks=[task_a, task_b])
        result = ca_mod._select_requirements(ws, "t-a")  # noqa: SLF001
        assert result == []
        entries = [
            json.loads(line)
            for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
        ]
        assert any(e["action"] == "clause_audit_skipped_unscoped" for e in entries)

    def test_decomposed_owned_clauses_returned(self, tmp_path: Path) -> None:
        req_a = Requirement(text="feature A")
        req_b = Requirement(text="feature B")
        task_a = Task(id="t-a", status=TaskStatus.READY, parent_task_id="parent", dod_clauses=[
            TaskDoDClause(text="done a", covers=[req_a.id])
        ])
        task_b = Task(id="t-b", status=TaskStatus.READY, parent_task_id="parent", dod_clauses=[
            TaskDoDClause(text="done b", covers=[req_b.id])
        ])
        ws = _ws(tmp_path, requirements=[req_a, req_b], tasks=[task_a, task_b])
        result = ca_mod._select_requirements(ws, "t-a")  # noqa: SLF001
        assert [r.id for r in result] == [req_a.id]

    def test_decomposed_sibling_owned_clause_excluded(self, tmp_path: Path) -> None:
        req_a = Requirement(text="feature A")
        req_b = Requirement(text="feature B")
        task_a = Task(id="t-a", status=TaskStatus.READY, parent_task_id="parent", dod_clauses=[
            TaskDoDClause(text="done a", covers=[req_a.id])
        ])
        task_b = Task(id="t-b", status=TaskStatus.READY, parent_task_id="parent", dod_clauses=[
            TaskDoDClause(text="done b", covers=[req_b.id])
        ])
        ws = _ws(tmp_path, requirements=[req_a, req_b], tasks=[task_a, task_b])
        result = ca_mod._select_requirements(ws, "t-a")  # noqa: SLF001
        assert req_b.id not in [r.id for r in result]

    def test_undecomposed_all_withdrawn_returns_empty_and_logs_distinct_action(
        self, tmp_path: Path, audit_root: Path
    ) -> None:
        req = Requirement(text="A", withdrawn_by_question_id="clause-stall-a")
        ws = _ws(tmp_path, requirements=[req])
        result = ca_mod._select_requirements(ws, ws.task_id)  # noqa: SLF001
        assert result == []
        entries = [
            json.loads(line)
            for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
        ]
        assert any(e["action"] == "clause_audit_skipped_all_withdrawn" for e in entries)

    def test_decomposed_owned_clauses_all_withdrawn_returns_empty_and_logs_distinct_action(
        self, tmp_path: Path, audit_root: Path
    ) -> None:
        req = Requirement(text="feature A", withdrawn_by_question_id="clause-stall-a")
        task_a = Task(id="t-a", status=TaskStatus.READY, dod_clauses=[
            TaskDoDClause(text="done a", covers=[req.id])
        ])
        task_b = Task(id="t-b", status=TaskStatus.READY, dod_clauses=[])
        ws = _ws(tmp_path, requirements=[req], tasks=[task_a, task_b])
        result = ca_mod._select_requirements(ws, "t-a")  # noqa: SLF001
        assert result == []
        entries = [
            json.loads(line)
            for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
        ]
        assert any(e["action"] == "clause_audit_skipped_all_withdrawn" for e in entries)

    def test_decomposed_dod_clauses_cover_nothing_returns_empty_no_audit(
        self, tmp_path: Path, audit_root: Path
    ) -> None:
        req = Requirement(text="A")
        task_a = Task(id="t-a", status=TaskStatus.READY, dod_clauses=[
            TaskDoDClause(text="done a", covers=["req-bogus"])
        ])
        task_b = Task(id="t-b", status=TaskStatus.READY, dod_clauses=[])
        ws = _ws(tmp_path, requirements=[req], tasks=[task_a, task_b])
        result = ca_mod._select_requirements(ws, "t-a")  # noqa: SLF001
        assert result == []
        log_path = audit_root / "verification_audit.jsonl"
        entries = (
            [json.loads(line) for line in log_path.read_text().splitlines()]
            if log_path.exists() else []
        )
        assert not any(e["action"] == "clause_audit_skipped_unscoped" for e in entries)

    def test_second_dispatch_task_audits_only_its_own_owned_clauses(
        self, tmp_path: Path
    ) -> None:
        req_a = Requirement(text="feature A")
        req_b = Requirement(text="feature B")
        task_a = Task(id="t-a", status=TaskStatus.READY, dod_clauses=[
            TaskDoDClause(text="done a", covers=[req_a.id])
        ])
        task_b = Task(id="t-b", status=TaskStatus.READY, dod_clauses=[
            TaskDoDClause(text="done b", covers=[req_b.id])
        ])
        ws = _ws(tmp_path, requirements=[req_a, req_b], tasks=[task_a, task_b])
        result = ca_mod._select_requirements(ws, "t-b")  # noqa: SLF001
        assert [r.id for r in result] == [req_b.id]
        assert req_a.id not in [r.id for r in result]

    def test_zero_task_workspace_audits_full_dod_not_skipped(
        self, tmp_path: Path
    ) -> None:
        req = Requirement(text="A")
        ws = _ws(tmp_path, requirements=[req], tasks=[])
        assert ws.meta.tasks == []
        result = ca_mod._select_requirements(ws, ws.task_id)  # noqa: SLF001
        assert result == [req]


class TestScopeAuditRow:
    def test_records_owner_of_selected_and_unselected_clauses(
        self, tmp_path: Path, audit_root: Path
    ) -> None:
        req_a = Requirement(text="feature A")
        req_b = Requirement(text="feature B")
        task_a = Task(id="t-a", status=TaskStatus.READY, dod_clauses=[
            TaskDoDClause(text="done a", covers=[req_a.id])
        ])
        task_b = Task(id="t-b", status=TaskStatus.READY, dod_clauses=[
            TaskDoDClause(text="done b", covers=[req_b.id])
        ])
        ws = _ws(tmp_path, requirements=[req_a, req_b], tasks=[task_a, task_b])
        ca_mod._select_requirements(ws, "t-a")  # noqa: SLF001
        entries = [
            json.loads(line)
            for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
        ]
        scope_rows = [e for e in entries if e["action"] == "clause_audit_scope"]
        assert len(scope_rows) == 1
        row = scope_rows[0]
        assert row["audited_task_id"] == "t-a"
        assert row["selected"] == {req_a.id: "t-a"}
        assert row["not_selected"] == {req_b.id: "t-b"}

    def test_selected_owner_is_the_audited_task_under_overlapping_root_coverage(
        self, tmp_path: Path, audit_root: Path
    ) -> None:
        req = Requirement(text="shared clause")
        task_a = Task(id="t-a", status=TaskStatus.READY, dod_clauses=[
            TaskDoDClause(text="done a", covers=[req.id])
        ])
        task_b = Task(id="t-b", status=TaskStatus.READY, dod_clauses=[
            TaskDoDClause(text="done b", covers=[req.id])
        ])
        ws = _ws(tmp_path, requirements=[req], tasks=[task_a, task_b])
        ca_mod._select_requirements(ws, "t-a")  # noqa: SLF001
        entries = [
            json.loads(line)
            for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
        ]
        scope_rows = [e for e in entries if e["action"] == "clause_audit_scope"]
        assert len(scope_rows) == 1
        assert scope_rows[0]["selected"] == {req.id: "t-a"}

    def test_not_written_when_no_clauses_selected(
        self, tmp_path: Path, audit_root: Path
    ) -> None:
        req = Requirement(text="A")
        task_a = Task(id="t-a", status=TaskStatus.READY, dod_clauses=[
            TaskDoDClause(text="done a", covers=["req-bogus"])
        ])
        task_b = Task(id="t-b", status=TaskStatus.READY, dod_clauses=[])
        ws = _ws(tmp_path, requirements=[req], tasks=[task_a, task_b])
        result = ca_mod._select_requirements(ws, "t-a")  # noqa: SLF001
        assert result == []
        entries = [
            json.loads(line)
            for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
        ]
        assert len(entries) == 1
        assert entries[0]["action"] == "clause_audit_skipped_no_owned_requirements"


class TestFetchPrState:
    def test_success(self, _active_fake_forge: FakeForge) -> None:
        always(_active_fake_forge, "read_pr", pr_read({
            "isDraft": True, "headRefName": "feature-x", "baseRefName": "main",
        }))
        state = ca_mod._fetch_pr_state(1, forge_target=_FORGE)  # noqa: SLF001
        assert state == ca_mod.PrState(
            is_draft=True, head_ref="feature-x", base_ref="main", body=None
        )

    def test_nonzero_returncode(self, _active_fake_forge: FakeForge) -> None:
        always(_active_fake_forge, "read_pr", pr_read(returncode=1, stderr="boom"))
        assert ca_mod._fetch_pr_state(1, forge_target=_FORGE) is None  # noqa: SLF001

    def test_raises_file_not_found(self, _active_fake_forge: FakeForge) -> None:
        always(_active_fake_forge, "read_pr", FileNotFoundError())
        assert ca_mod._fetch_pr_state(1, forge_target=_FORGE) is None  # noqa: SLF001

    def test_raises_timeout(self, _active_fake_forge: FakeForge) -> None:
        always(
            _active_fake_forge, "read_pr", subprocess.TimeoutExpired(cmd="gh", timeout=60.0),
        )
        assert ca_mod._fetch_pr_state(1, forge_target=_FORGE) is None  # noqa: SLF001

    def test_malformed_json(self, _active_fake_forge: FakeForge) -> None:
        always(_active_fake_forge, "read_pr", pr_read(fields={}))
        assert ca_mod._fetch_pr_state(1, forge_target=_FORGE) is None  # noqa: SLF001

    def test_non_dict_json(self, _active_fake_forge: FakeForge) -> None:
        always(_active_fake_forge, "read_pr", pr_read(fields={}))
        assert ca_mod._fetch_pr_state(1, forge_target=_FORGE) is None  # noqa: SLF001

    def test_wrong_field_types_fall_back_to_none(self, _active_fake_forge: FakeForge) -> None:
        always(_active_fake_forge, "read_pr", pr_read({
            "isDraft": "yes", "headRefName": 5, "baseRefName": None,
        }))
        state = ca_mod._fetch_pr_state(1, forge_target=_FORGE)  # noqa: SLF001
        assert state == ca_mod.PrState(is_draft=None, head_ref=None, base_ref=None, body=None)


class TestBeginEnd:
    def test_mode_off_no_context(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALAN_CLAUSE_AUDIT", "off")
        ws = _ws(tmp_path, requirements=[Requirement(text="A")])
        token = ca_mod.begin(ws, 1, ws.task_id, _FORGE)
        try:
            assert ca_mod.evidence_prefix() == ""
        finally:
            ca_mod.end(token)

    def test_no_requirements_no_context(self, tmp_path: Path) -> None:
        ws = _ws(tmp_path, requirements=None)
        token = ca_mod.begin(ws, 1, ws.task_id, _FORGE)
        try:
            assert ca_mod.evidence_prefix() == ""
        finally:
            ca_mod.end(token)

    def test_with_requirements_fetches_evidence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
    ) -> None:
        req = Requirement(text="deliver X")
        ws = _ws(tmp_path, requirements=[req])
        monkeypatch.setattr(
            _pr_branch, "fetch_diff_files", lambda *_a, **_kw: frozenset({"alan/foo.py"})
        )
        always(_active_fake_forge, "read_pr", pr_read(
            {"isDraft": False, "headRefName": "h", "baseRefName": "main"}
        ))
        token = ca_mod.begin(ws, 7, ws.task_id, _FORGE)
        try:
            prefix = ca_mod.evidence_prefix()
            assert "alan/foo.py" in prefix
            assert req.id in prefix
            assert "isDraft=False" in prefix
        finally:
            ca_mod.end(token)

    def test_with_requirements_includes_live_pr_body_not_dispatch_brief(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
    ) -> None:
        req = Requirement(text="PR description must mention a rollback plan")
        ws = _ws(tmp_path, requirements=[req])
        monkeypatch.setattr(_pr_branch, "fetch_diff_files", lambda *_a, **_kw: frozenset())
        always(_active_fake_forge, "read_pr", pr_read({
            "isDraft": False, "headRefName": "h", "baseRefName": "main",
            "body": "Rollback plan: revert the merge commit.",
        }))
        token = ca_mod.begin(ws, 7, ws.task_id, _FORGE)
        try:
            prefix = ca_mod.evidence_prefix()
            assert "Rollback plan: revert the merge commit." in prefix
            assert "never against the dispatch brief" in prefix
        finally:
            ca_mod.end(token)

    def test_evidence_lists_host_fetched_check_names_without_their_conclusions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = _ws(tmp_path, requirements=[Requirement(text="A")])
        monkeypatch.setattr(_pr_branch, "fetch_diff_files", lambda *_a, **_kw: frozenset())
        monkeypatch.setattr(
            _pr_ready, "check_pr_merge_readiness",
            lambda *_a, **_kw: _pr_ready.PrReadiness(
                ok=False, merge_state_status="BLOCKED", review_decision="",
                passing_checks=("Backend tests + coverage",),
                pending_checks=("Frontend E2E (Playwright, blocking)",),
                failing_checks=("SonarCloud Code Analysis",),
                head_sha="a" * 40,
            ),
        )
        token = ca_mod.begin(ws, 7, ws.task_id, _FORGE)
        try:
            prefix = ca_mod.evidence_prefix()
        finally:
            ca_mod.end(token)
        listed = [line for line in prefix.splitlines() if line.startswith("- ")][:3]
        assert listed == [
            "- Backend tests + coverage",
            "- Frontend E2E (Playwright, blocking)",
            "- SonarCloud Code Analysis",
        ]
        assert "never grounds a PASS" in prefix
        assert prefix.index("Required-check names") < prefix.index("## CLAUSES TO AUDIT")

    def test_evidence_omits_the_check_name_block_when_the_head_has_no_checks_yet(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = _ws(tmp_path, requirements=[Requirement(text="A")])
        monkeypatch.setattr(_pr_branch, "fetch_diff_files", lambda *_a, **_kw: frozenset())
        monkeypatch.setattr(
            _pr_ready, "check_pr_merge_readiness",
            lambda *_a, **_kw: _pr_ready.PrReadiness(
                ok=False, merge_state_status="BLOCKED", review_decision="", head_sha="a" * 40,
            ),
        )
        token = ca_mod.begin(ws, 7, ws.task_id, _FORGE)
        try:
            assert "Required-check names" not in ca_mod.evidence_prefix()
        finally:
            ca_mod.end(token)

    def test_pr_state_with_empty_body_renders_placeholder(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
    ) -> None:
        req = Requirement(text="A")
        ws = _ws(tmp_path, requirements=[req])
        monkeypatch.setattr(_pr_branch, "fetch_diff_files", lambda *_a, **_kw: frozenset())
        always(_active_fake_forge, "read_pr", pr_read({
            "isDraft": False, "headRefName": "h", "baseRefName": "main", "body": "",
        }))
        token = ca_mod.begin(ws, 7, ws.task_id, _FORGE)
        try:
            assert "(empty)" in ca_mod.evidence_prefix()
        finally:
            ca_mod.end(token)

    def test_end_resets_context(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        req = Requirement(text="deliver X")
        ws = _ws(tmp_path, requirements=[req])
        monkeypatch.setattr(_pr_branch, "fetch_diff_files", lambda *_a, **_kw: None)
        token = ca_mod.begin(ws, 7, ws.task_id, _FORGE)
        assert ca_mod.evidence_prefix() != ""
        ca_mod.end(token)
        assert ca_mod.evidence_prefix() == ""


class TestDiffFilesEvidenceLine:
    def test_under_cap_lists_all_no_omission_note(self) -> None:
        line = ca_mod._diff_files_evidence_line(frozenset({"a.py", "b.py"}))  # noqa: SLF001
        assert "2 total" in line
        assert "more" not in line

    def test_over_cap_lists_capped_prefix_with_omission_count(self) -> None:
        files = frozenset(f"f{i}.py" for i in range(205))
        line = ca_mod._diff_files_evidence_line(files)  # noqa: SLF001
        assert "205 total" in line
        assert "5 more" in line


class TestStagesAndRoles:
    def test_stages_for_no_context_returns_unchanged(self, tmp_path: Path) -> None:
        ws = _ws(tmp_path, requirements=None)
        token = ca_mod.begin(ws, 1, ws.task_id, _FORGE)
        try:
            stages = (("diff_reviewer", "scenario_runner"), ("judge",))
            assert ca_mod.stages_for(stages) == stages
        finally:
            ca_mod.end(token)

    def test_stages_for_with_context_appends_clause_auditor(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        req = Requirement(text="deliver X")
        ws = _ws(tmp_path, requirements=[req])
        monkeypatch.setattr(_pr_branch, "fetch_diff_files", lambda *_a, **_kw: None)
        token = ca_mod.begin(ws, 1, ws.task_id, _FORGE)
        try:
            stages = (("diff_reviewer", "scenario_runner"), ("judge",))
            new_stages = ca_mod.stages_for(stages)
            assert new_stages == (
                ("diff_reviewer", "scenario_runner", "clause_auditor"), ("judge",),
            )
        finally:
            ca_mod.end(token)

    def test_stage_roles_passes_through_known_roles(self) -> None:
        role_by_name = {"diff_reviewer": Role("diff_reviewer", "prompt", "sonnet")}
        result = ca_mod.stage_roles(("diff_reviewer",), role_by_name)
        assert result == (role_by_name["diff_reviewer"],)

    def test_stage_roles_synthesizes_clause_auditor(self) -> None:
        role_by_name = {"diff_reviewer": Role("diff_reviewer", "prompt", "opus-x")}
        result = ca_mod.stage_roles(("diff_reviewer", "clause_auditor"), role_by_name)
        assert result[0].name == "diff_reviewer"
        assert result[1].name == "clause_auditor"
        assert result[1].model == "opus-x"
        assert "clause_auditor" in result[1].system_prompt

    def test_stage_roles_renders_clause_auditor_prompt_from_governing_engine(
        self,
    ) -> None:
        cfg = EngineConfig(
            repo=RepoIdentity(owner="rustco", repo="cargo-service"),
            gate_commands=[["cargo", "test"]],
        )
        role_by_name = {"diff_reviewer": Role("diff_reviewer", "prompt", "opus-x")}
        result = ca_mod.stage_roles(("diff_reviewer", "clause_auditor"), role_by_name, cfg)
        assert "cargo test" in result[1].system_prompt
        assert "uv run --no-active pytest" not in result[1].system_prompt
