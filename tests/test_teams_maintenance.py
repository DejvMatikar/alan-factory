from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from alan.claude_cli import AgentStreamingResult, StreamEvent
from alan.handover import Handover
from alan.router import select_next_team
from alan.teams import TEAM_ROLE_SUMMARIES, TEAMS
from alan.teams import _base as base_mod
from alan.teams.maintenance import (
    ROLES,
    SEVERITY_LEVELS,
    TEAM,
    MaintenanceTeam,
    _compose_report,
    _compose_run_verdict,
    _composed_severity_rank,
    _parse_role_tail,
)
from alan.workspace import Workspace, create
from tests._helpers import seed_and_run

_ROLE_NAMES: list[str] = [r.name for r in ROLES]

_REMOVED_GATE_SNIPPETS: tuple[str, ...] = (
    "ruff check --select F401",
    "bandit -r alan",
    "pip-audit --strict",
)


def _patch_streaming(
    monkeypatch: pytest.MonkeyPatch,
    *,
    output_factory: Callable[[int], str],
) -> None:
    counter = {"n": 0}

    def fake(
        system_prompt: str,
        user_text: str,
        *,
        model: str = "sonnet",
        on_event: Callable[[StreamEvent], None] | None = None,
        stream_path: Path | None = None,
        max_turns: int = 10,  # noqa: ARG001
        cwd: object = None,  # noqa: ARG001
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text, model
        counter["n"] += 1
        text = output_factory(counter["n"])
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            with stream_path.open("a", encoding="utf-8") as h:
                h.write(json.dumps({"type": "result", "result": text}) + "\n")
        if on_event is not None:
            on_event({"type": "result", "result": text})
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)


def _role_outputs(overrides: dict[str, str] | None = None) -> Callable[[int], str]:
    defaults = {name: f"{name} findings look clean.\n\nROLE_SEVERITY: NONE" for name in _ROLE_NAMES}
    defaults.update(overrides or {})
    ordered = [defaults[name] for name in _ROLE_NAMES]

    def factory(call_n: int) -> str:
        return ordered[call_n - 1]

    return factory


def _run_maintenance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    overrides: dict[str, str] | None = None,
) -> Handover:
    _patch_streaming(monkeypatch, output_factory=_role_outputs(overrides))
    ws: Workspace = create("maintenance test", "d", root=tmp_path / "ws")
    return seed_and_run(MaintenanceTeam(), ws, None, lambda _e: None)


def test_team_registered_under_maintenance_name() -> None:
    assert TEAMS["maintenance"] is TEAM
    assert TEAM.name == "maintenance"
    assert isinstance(TEAM, MaintenanceTeam)


def test_role_order_unchanged() -> None:
    assert _ROLE_NAMES == [
        "code_health_analyst",
        "ci_dependency_auditor",
        "log_repo_scanner",
        "security_scanner",
    ]


def test_maintenance_summary_present_and_mentions_severity_rollup() -> None:
    assert "maintenance" in TEAM_ROLE_SUMMARIES
    assert "findings" in TEAM_ROLE_SUMMARIES["maintenance"].lower()


@pytest.mark.parametrize("role_name", _ROLE_NAMES)
def test_role_prompts_carry_report_contract(role_name: str) -> None:
    role = next(r for r in ROLES if r.name == role_name)
    assert "ROLE_SEVERITY:" in role.system_prompt
    assert "UNVERIFIED:" in role.system_prompt


@pytest.mark.parametrize("role_name", _ROLE_NAMES)
def test_role_prompts_forbid_mutating_commands(role_name: str) -> None:
    role = next(r for r in ROLES if r.name == role_name)
    assert "GUARDRAIL" in role.system_prompt
    assert "report-only" in role.system_prompt.lower()


@pytest.mark.parametrize("role_name", _ROLE_NAMES)
def test_role_prompts_never_rerun_pr_blocking_gates(role_name: str) -> None:
    role = next(r for r in ROLES if r.name == role_name)
    for snippet in _REMOVED_GATE_SNIPPETS:
        assert snippet not in role.system_prompt


def test_ci_dependency_prompt_names_the_gh_boundary() -> None:
    role = next(r for r in ROLES if r.name == "ci_dependency_auditor")
    assert "gh" in role.system_prompt
    assert "allowlist" in role.system_prompt


def test_parse_role_tail_reads_role_severity() -> None:
    sev, reason = _parse_role_tail("some findings\n\nROLE_SEVERITY: HIGH\n")
    assert sev == "HIGH"
    assert reason is None


def test_parse_role_tail_reads_explicit_unverified() -> None:
    sev, reason = _parse_role_tail("body text\n\nUNVERIFIED: gh denied\n")
    assert sev is None
    assert reason == "gh denied"


def test_parse_role_tail_missing_tail_defaults_to_unverified_gap() -> None:
    sev, reason = _parse_role_tail("no machine-readable tail here at all")
    assert sev is None
    assert reason is not None
    assert "no ROLE_SEVERITY or UNVERIFIED tail" in reason


def test_parse_role_tail_tolerates_markdown_decoration() -> None:
    sev, _ = _parse_role_tail("**ROLE_SEVERITY: MEDIUM**")
    assert sev == "MEDIUM"


def test_parse_role_tail_strips_trailing_decoration_from_unverified_reason() -> None:
    sev, reason = _parse_role_tail("**UNVERIFIED: reason**")
    assert sev is None
    assert reason == "reason"


def test_parse_role_tail_uses_only_the_last_non_empty_line() -> None:
    text = (
        "Reminder to self: the contract wants a ROLE_SEVERITY: CRITICAL "
        "line, quoted here for reference.\n\n"
        "Actual finding text.\n\n"
        "ROLE_SEVERITY: LOW"
    )
    sev, reason = _parse_role_tail(text)
    assert sev == "LOW"
    assert reason is None


def test_parse_role_tail_skips_trailing_blank_lines_to_find_the_tail() -> None:
    sev, _ = _parse_role_tail("ROLE_SEVERITY: HIGH\n\n\n")
    assert sev == "HIGH"


def test_parse_role_tail_all_blank_text_defaults_to_unverified_gap() -> None:
    sev, reason = _parse_role_tail("\n\n   \n")
    assert sev is None
    assert reason is not None
    assert "no ROLE_SEVERITY or UNVERIFIED tail" in reason


def test_compose_run_verdict_rolls_up_max_severity() -> None:
    parsed: list[tuple[str, str | None, str | None]] = [
        ("a", "LOW", None), ("b", "HIGH", None), ("c", "NONE", None),
    ]
    block = _compose_run_verdict(parsed)
    assert "RUN_VERDICT_SEVERITY: HIGH" in block
    assert "- a: LOW" in block
    assert "- b: HIGH" in block


def test_compose_run_verdict_excludes_gaps_from_max() -> None:
    parsed: list[tuple[str, str | None, str | None]] = [
        ("a", "LOW", None), ("b", None, "gh denied"),
    ]
    block = _compose_run_verdict(parsed)
    assert "RUN_VERDICT_SEVERITY: LOW" in block
    assert "Gaps" in block
    assert "- b: gh denied" in block


def test_compose_run_verdict_all_gaps_defaults_to_none_not_critical() -> None:
    parsed: list[tuple[str, str | None, str | None]] = [
        ("a", None, "blocked a"), ("b", None, "blocked b"),
    ]
    block = _compose_run_verdict(parsed)
    assert "RUN_VERDICT_SEVERITY: NONE" in block
    assert "- a: blocked a" in block
    assert "- b: blocked b" in block


def test_compose_report_leads_with_run_verdict_before_role_sections() -> None:
    outputs = [("role_one", "finding one\n\nROLE_SEVERITY: LOW")]
    report = _compose_report(outputs)
    assert report.index("## Run verdict") < report.index("## role_one")


def test_compose_report_includes_every_role_verbatim() -> None:
    outputs = [
        ("code_health_analyst", "residue found\n\nROLE_SEVERITY: LOW"),
        ("ci_dependency_auditor", "gh denied\n\nUNVERIFIED: gh not allowlisted"),
        ("log_repo_scanner", "clean\n\nROLE_SEVERITY: NONE"),
        ("security_scanner", "perms drifted\n\nROLE_SEVERITY: HIGH"),
    ]
    report = _compose_report(outputs)
    for name, text in outputs:
        assert f"## {name}" in report
        assert text in report


def test_composed_severity_rank_defensive_default_is_critical() -> None:
    from alan.teams.maintenance import _CRITICAL_RANK  # noqa: PLC0415

    assert _composed_severity_rank("no machine-readable verdict line here") == _CRITICAL_RANK


def test_composed_severity_rank_reads_generated_line() -> None:
    idx = SEVERITY_LEVELS.index("HIGH")
    assert _composed_severity_rank("## Run verdict\n\nRUN_VERDICT_SEVERITY: HIGH\n") == idx


def test_clean_run_all_roles_none_is_terminal_with_full_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    handover = _run_maintenance(monkeypatch, tmp_path)
    assert handover.handover_type == "terminal"
    assert "RUN_VERDICT_SEVERITY: NONE" in handover.full_synthesis
    for name in _ROLE_NAMES:
        assert f"## {name}" in handover.full_synthesis


def test_run_verdict_leads_full_synthesis_within_manager_summary_truncation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    long_text = "x" * 3000
    overrides = {name: f"{long_text}\n\nROLE_SEVERITY: LOW" for name in _ROLE_NAMES}
    handover = _run_maintenance(monkeypatch, tmp_path, overrides)
    truncated_head = handover.full_synthesis[:2000]
    assert "## Run verdict" in truncated_head
    assert "RUN_VERDICT_SEVERITY:" in truncated_head


def test_one_role_reports_high_severity_never_shows_all_clear(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    overrides = {"security_scanner": "world-writable secrets file\n\nROLE_SEVERITY: HIGH"}
    handover = _run_maintenance(monkeypatch, tmp_path, overrides)
    assert "RUN_VERDICT_SEVERITY: HIGH" in handover.full_synthesis
    assert handover.handover_type == "forward"


def test_medium_severity_is_the_escalation_boundary_inclusive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    overrides = {"log_repo_scanner": "recurring OOM errors\n\nROLE_SEVERITY: MEDIUM"}
    handover = _run_maintenance(monkeypatch, tmp_path, overrides)
    assert handover.handover_type == "forward"
    assert handover.to_team_suggestions == []
    blocking = [q for q in handover.open_questions if q.blocking]
    assert len(blocking) == 1
    assert blocking[0].resolver_role == "human"


def test_low_severity_alone_stays_terminal_below_the_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    overrides = {"code_health_analyst": "one orphaned worktree\n\nROLE_SEVERITY: LOW"}
    handover = _run_maintenance(monkeypatch, tmp_path, overrides)
    assert handover.handover_type == "terminal"
    assert handover.open_questions == []


def test_unverified_role_is_reported_as_gap_not_silently_dropped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    overrides = {
        "ci_dependency_auditor": (
            "attempted gh run list\n\nUNVERIFIED: gh CLI is not in this "
            "role's tool allowlist"
        )
    }
    handover = _run_maintenance(monkeypatch, tmp_path, overrides)
    assert "Gaps" in handover.full_synthesis
    assert "gh CLI is not in this role's tool allowlist" in handover.full_synthesis
    assert handover.handover_type == "terminal"


def test_gaps_alone_never_park_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    overrides = {name: f"blocked\n\nUNVERIFIED: {name} denied" for name in _ROLE_NAMES}
    handover = _run_maintenance(monkeypatch, tmp_path, overrides)
    assert "RUN_VERDICT_SEVERITY: NONE" in handover.full_synthesis
    assert handover.handover_type == "terminal"
    for name in _ROLE_NAMES:
        assert f"{name} denied" in handover.full_synthesis


def test_missing_tail_is_reported_as_gap_with_default_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    overrides = {"security_scanner": "some prose with no machine-readable tail at all"}
    handover = _run_maintenance(monkeypatch, tmp_path, overrides)
    assert "no ROLE_SEVERITY or UNVERIFIED tail found" in handover.full_synthesis
    assert handover.handover_type == "terminal"


def test_medium_severity_handover_routes_to_await_human(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    overrides = {"security_scanner": "leaked token in a handover\n\nROLE_SEVERITY: CRITICAL"}
    handover = _run_maintenance(monkeypatch, tmp_path, overrides)
    decision = select_next_team(handover, TEAMS)
    assert decision.kind == "await_human"


def test_clean_run_handover_routes_to_done(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    handover = _run_maintenance(monkeypatch, tmp_path)
    decision = select_next_team(handover, TEAMS)
    assert decision.kind == "done"


def test_role_overrides_grant_state_tree_parent_from_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "tree" / "state"))
    ws = create("maint-add-dirs-env", "d", root=tmp_path / "ws")
    overrides = TEAM._role_overrides("code_health_analyst", ws)  # noqa: SLF001
    assert overrides is not None
    assert overrides.add_dirs == [str(tmp_path / "tree")]


def test_role_overrides_default_to_home_state_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("ALAN_STATE_ROOT", raising=False)
    ws = create("maint-add-dirs-home", "d", root=tmp_path / "ws")
    overrides = TEAM._role_overrides("security_scanner", ws)  # noqa: SLF001
    assert overrides is not None
    assert overrides.add_dirs == [str(Path.home() / ".alan")]


def test_role_overrides_keep_maintenance_allowed_tools(tmp_path: Path) -> None:
    ws = create("maint-add-dirs-tools", "d", root=tmp_path / "ws")
    overrides = TEAM._role_overrides("ci_dependency_auditor", ws)  # noqa: SLF001
    assert overrides is not None
    assert overrides.allowed_tools is not None
    assert "Bash(gh run list:*)" in overrides.allowed_tools
    assert "Bash(stat:*)" in overrides.allowed_tools


def test_all_role_prompts_carry_live_state_access_block() -> None:
    for role in ROLES:
        assert "LIVE STATE ACCESS" in role.system_prompt
        assert "empty per-worker sandbox" in role.system_prompt


def test_ci_dependency_prompt_grants_gh_run_list() -> None:
    prompt = next(
        r.system_prompt for r in ROLES if r.name == "ci_dependency_auditor"
    )
    assert "TOOL GRANT" in prompt
    assert "KNOWN BOUNDARY" not in prompt
