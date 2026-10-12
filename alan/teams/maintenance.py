from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from alan import roots
from alan.claude_cli import OnProcStarted
from alan.claude_cli_grants import with_gate_reachability
from alan.engine_config import _DEFAULT as _ENGINE_DEFAULT  # pyright: ignore[reportPrivateUsage]
from alan.handover import Handover, OpenQuestion
from alan.teams import _pr_forge
from alan.teams._base import (
    EmitFn,
    WorkerOverrides,
    build_forward_handover,
    build_terminal_handover,
    render_allowed_tools,
    workflow_lifecycle,
)
from alan.teams._maintenance_secrets import secret_permission_block
from alan.teams._static_pipeline import (
    Role,
    StaticPipelineTeam,
    _depends_on_map,  # pyright: ignore[reportPrivateUsage]
    _run_stage_roles,  # pyright: ignore[reportPrivateUsage]
)
from alan.workspace import Workspace

_MODEL_OPUS = _ENGINE_DEFAULT.roles["judge"].model
_MODEL_SONNET = _ENGINE_DEFAULT.roles["diff_reviewer"].model

SEVERITY_LEVELS: tuple[str, ...] = ("NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL")
_SEVERITY_RANK: dict[str, int] = {level: idx for idx, level in enumerate(SEVERITY_LEVELS)}
_MEDIUM_RANK = _SEVERITY_RANK["MEDIUM"]
_CRITICAL_RANK = _SEVERITY_RANK["CRITICAL"]

_ROLE_SEVERITY_PREFIX = "ROLE_SEVERITY:"
_UNVERIFIED_PREFIX = "UNVERIFIED:"
RUN_VERDICT_HEADING = "## Run verdict"
_RUN_VERDICT_SEVERITY_PREFIX = "RUN_VERDICT_SEVERITY:"

_DECOR_CHARS = " \t*_`#>~-"
_DECOR = f"[{re.escape(_DECOR_CHARS)}]*"
_SEVERITY_ALTERNATION = "(NONE|LOW|MEDIUM|HIGH|CRITICAL)"
_ROLE_SEVERITY_RE = re.compile(
    rf"^{_DECOR}{re.escape(_ROLE_SEVERITY_PREFIX)}{_DECOR}{_SEVERITY_ALTERNATION}{_DECOR}$",
    re.MULTILINE,
)
_UNVERIFIED_RE = re.compile(
    rf"^{_DECOR}{re.escape(_UNVERIFIED_PREFIX)}{_DECOR}(.+)$",
    re.MULTILINE,
)
_RUN_VERDICT_SEVERITY_RE = re.compile(
    rf"^{re.escape(_RUN_VERDICT_SEVERITY_PREFIX)} {_SEVERITY_ALTERNATION}$",
    re.MULTILINE,
)

_REPORT_CONTRACT = (
    "\n\nREPORT CONTRACT — end your output with EXACTLY ONE machine-readable "
    "tail line, verbatim, on its own line, after all your prose findings:\n"
    f"{_ROLE_SEVERITY_PREFIX} <NONE|LOW|MEDIUM|HIGH|CRITICAL>\n"
    "— NONE means you completed every check in SCOPE and found nothing "
    "worth flagging; otherwise the highest severity among your own "
    "findings.\n"
    "OR, if any check in SCOPE could not be completed because your "
    "environment denies the access it needs (a command is refused, a tool "
    "is not granted), do NOT guess, fabricate, or silently skip it — emit "
    "this line INSTEAD:\n"
    f"{_UNVERIFIED_PREFIX} <one-line reason naming exactly what was "
    "blocked and why>\n"
    "Emit only one of the two lines, never both, never neither, and never "
    "use either exact token anywhere else in your output — only as this "
    "single final tail line."
)

_GUARDRAIL = (
    "\n\nGUARDRAIL: this is a report-only, read-only run. You have no "
    "Edit/Write grant, and any mutating Bash command (git push/commit, rm, "
    "mv, kill, package install, or anything else that changes state) is "
    "silently denied with no approval channel available and will stall "
    "this run. Only inspect and read; never modify, move, delete, or fix "
    "anything you find — report it instead."
)

_LIVE_STATE_ACCESS = (
    "\n\nLIVE STATE ACCESS: the live alan state tree — `~/.alan` on a "
    "default deployment — is granted as an additional working directory "
    "for this run; inspect it via its literal path. The `ALAN_*_ROOT` env "
    "vars visible in this subprocess point at an empty per-worker sandbox, "
    "never at the live tree — do not resolve any audit path through them."
)

_CODE_HEALTH_PROMPT = (
    "You are the code_health_analyst on the maintenance team.\n\n"
    "SCOPE: accumulated environment residue on this live checkout and "
    "fleet state that no PR gate ever inspects — NOT static source lint "
    "(ruff/dead-import checks already run on every PR; do not re-run "
    "them). Look for: (1) git worktrees (`git worktree list`) that no "
    "longer correspond to any active slot or workspace — checkouts "
    "orphaned by a crashed or killed worker; (2) workspace directories "
    "under the live workspaces root (`~/.alan/workspaces`) that look "
    "completed or abandoned "
    "but were never archived per docs/architecture/retention.md — a sign "
    "the retention sweep is silently falling behind; (3) stray lock/tmp "
    "files anywhere under the alan state tree that outlived the process "
    "that created them.\n"
    "SUCCESS: for each finding, name the concrete path/worktree/workspace, "
    "how you confirmed it is actually orphaned (not merely old), and a "
    "severity. Use only read-only inspection (`git worktree list`, `ls`, "
    "`cat`, `git log`, `git status`, `Read`/`Grep`/`Glob`).\n\n"
    "Output sections: ## Orphaned worktrees, ## Workspace-retention "
    "residue, ## Stray lock/tmp files."
    + _REPORT_CONTRACT
    + _GUARDRAIL
    + _LIVE_STATE_ACCESS
)

_CI_DEPENDENCY_PROMPT = (
    "You are the ci_dependency_auditor on the maintenance team.\n\n"
    "SCOPE: recent CI/automation run history for this repo — data only a "
    "live `gh` query can see, and that pip-audit (a PR gate already "
    "covering known CVEs on every PR; do not re-run it) does not. Run "
    "`gh run list --limit 10` and `gh run list --status failure --limit "
    "10`.\n"
    "TOOL GRANT: `gh run list` is pre-approved in this role's tool "
    "allowlist — run both commands for real. If either still fails "
    "(auth failure, network outage, rate limit), do not guess at recent "
    "CI health from any other source and do not report this as a pass — "
    "go straight to the REPORT CONTRACT's UNVERIFIED line naming the "
    "exact failure.\n"
    "As background context only (not a substitute for the check above, "
    "and not itself a severity signal), you may also inspect "
    "`~/.alan/logs/dep_sweep_audit.jsonl` and "
    "note in prose whether the autonomous dependency-sweep looks like it "
    "is still running on its configured cadence — the sweep's own live "
    "operational health, not the packages it audits.\n\n"
    "Output sections: ## CI/automation run history, ## Dependency-sweep "
    "cadence (context only)."
    + _REPORT_CONTRACT
    + _GUARDRAIL
    + _LIVE_STATE_ACCESS
)

_LOG_REPO_PROMPT = (
    "You are the log_repo_scanner on the maintenance team.\n\n"
    "SCOPE: live signals only a running deployment produces, not anything "
    "a static gate re-derives from the checkout. (1) "
    "`~/.alan/logs/alan.error.log`: find "
    "recurring ERROR patterns, count occurrences, and for each pattern "
    "state your best-supported root-cause hypothesis (which module/call "
    "path, and why) — a bare occurrence count without a root-cause read "
    "is not a complete finding. (2) repo hygiene visible only from git's "
    "live state: merged-but-undeleted remote branches (`git branch -r "
    "--merged main`), unusually large or orphaned tracked files.\n"
    "SUCCESS: cite the log excerpt or branch/file name and a severity for "
    "each finding.\n\n"
    "Output sections: ## Recurring errors (with root cause), ## Stale "
    "branches, ## Large/orphaned files."
    + _REPORT_CONTRACT
    + _GUARDRAIL
    + _LIVE_STATE_ACCESS
)

_SECURITY_PROMPT = (
    "You are the security_scanner on the maintenance team.\n\n"
    "SCOPE: live filesystem state that only exists on this running "
    "deployment — NOT a bandit scan of the git-tracked source (bandit "
    "already runs on every PR; do not re-run it) and not gitleaks' "
    "pre-commit scan of tracked files. Check (1) permission bits on "
    "runtime secret files that must stay `600`: the host already read "
    "them and lists each file's label and mode in the `## HOST-READ "
    "SECRET FILE PERMISSIONS` block at the top of your input — the "
    "secret paths themselves are denied to this session, so never try to "
    "list or read them; flag every entry marked `GROUP/WORLD-ACCESSIBLE` "
    "by its label and mode, and report an `unreadable` entry as a "
    "verification gap; (2) secret-shaped strings (API keys, tokens, "
    "bearer/Authorization patterns) that have leaked into RUNTIME-WRITTEN "
    "artifacts — handovers, logs, workflow files under the live "
    "workspaces root — as opposed to the tracked source tree.\n"
    "SUCCESS: name the exact path and permission bits or matched pattern "
    "for each finding, a severity, and redact any actual secret value in "
    "your own output.\n\n"
    "Output sections: ## Secret-file permissions, ## Leaked-secret "
    "patterns in runtime artifacts."
    + _REPORT_CONTRACT
    + _GUARDRAIL
    + _LIVE_STATE_ACCESS
)


_SECURITY_ROLE = "security_scanner"

ROLES: tuple[Role, ...] = (
    Role("code_health_analyst", _CODE_HEALTH_PROMPT, _MODEL_SONNET),
    Role("ci_dependency_auditor", _CI_DEPENDENCY_PROMPT, _MODEL_SONNET),
    Role("log_repo_scanner", _LOG_REPO_PROMPT, _MODEL_SONNET),
    Role(_SECURITY_ROLE, _SECURITY_PROMPT, _MODEL_OPUS),
)


def _state_tree_root() -> Path:
    return roots.state_root().parent


def _last_non_empty_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        if line.strip():
            return line
    return ""


def _parse_role_tail(text: str) -> tuple[str | None, str | None]:
    tail_line = _last_non_empty_line(text)
    match = _ROLE_SEVERITY_RE.fullmatch(tail_line)
    if match is not None:
        return match.group(1), None
    unverified = _UNVERIFIED_RE.fullmatch(tail_line)
    if unverified is not None:
        return None, unverified.group(1).strip(_DECOR_CHARS)
    return None, "no ROLE_SEVERITY or UNVERIFIED tail found in this role's output"


def _severity_partition(
    parsed: list[tuple[str, str | None, str | None]],
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    verified: list[tuple[str, str]] = []
    gaps: list[tuple[str, str]] = []
    for name, sev, reason in parsed:
        if sev is not None:
            verified.append((name, sev))
        else:
            gaps.append((name, reason or "no reason given"))
    return verified, gaps


def _append_bulleted_section(
    lines: list[str], header: str, items: list[tuple[str, str]]
) -> None:
    if not items:
        return
    lines.append(header)
    lines.extend(f"- {name}: {value}" for name, value in items)
    lines.append("")


def _compose_run_verdict(parsed: list[tuple[str, str | None, str | None]]) -> str:
    verified, gaps = _severity_partition(parsed)
    overall_rank = max(
        (_SEVERITY_RANK[sev] for _, sev in verified), default=_SEVERITY_RANK["NONE"]
    )
    lines = [
        RUN_VERDICT_HEADING,
        "",
        f"{_RUN_VERDICT_SEVERITY_PREFIX} {SEVERITY_LEVELS[overall_rank]}",
        "",
    ]
    _append_bulleted_section(lines, "Roles reporting:", verified)
    _append_bulleted_section(
        lines, "Gaps (unverified — excluded from the severity roll-up):", gaps
    )
    return "\n".join(lines).rstrip() + "\n"


def _compose_report(outputs: list[tuple[str, str]]) -> str:
    parsed = [(name, *_parse_role_tail(text)) for name, text in outputs]
    verdict = _compose_run_verdict(parsed)
    sections = "\n\n".join(f"## {name}\n\n{text}" for name, text in outputs)
    return f"{verdict}\n{sections}\n"


def _composed_severity_rank(report: str) -> int:
    match = _RUN_VERDICT_SEVERITY_RE.search(report)
    if match is None:
        return _CRITICAL_RANK
    return _SEVERITY_RANK[match.group(1)]


@dataclass(frozen=True)
class MaintenanceTeam(StaticPipelineTeam):
    name: str = "maintenance"
    roles: tuple[Role, ...] = ROLES
    to_team_suggestions: tuple[str, ...] = ()

    def _role_user_prefix(self, role_name: str) -> str:
        return secret_permission_block() if role_name == _SECURITY_ROLE else ""

    def _role_overrides(
        self, role_name: str, workspace: Workspace
    ) -> WorkerOverrides | None:
        del role_name
        cfg = _pr_forge.governing_config_for_ws(workspace)
        return WorkerOverrides(
            allowed_tools=with_gate_reachability(
                render_allowed_tools(cfg, self.name), cfg.gate_commands
            ),
            add_dirs=[str(_state_tree_root())],
        )

    def _collect_outputs(
        self,
        workspace: Workspace,
        inv_id: str,
        base: str,
        emit: EmitFn,
        on_proc_started: OnProcStarted | None,
    ) -> str:
        role_by_name = {role.name: role for role in self.roles}
        depends_on_map = _depends_on_map(self._effective_stages())
        with workflow_lifecycle(
            workspace, team_name=self.name, inv_id=inv_id, emit=emit
        ) as graph:
            outputs: list[tuple[str, str]] = []
            for stage in self._effective_stages():
                stage_roles = tuple(role_by_name[name] for name in stage)

                def run_role(role: Role) -> str:
                    return self._run_role(
                        workspace, graph, inv_id, emit, role,
                        depends_on_map[role.name], base, outputs, on_proc_started,
                    )

                outputs.extend(_run_stage_roles(stage_roles, run_role))
        return _compose_report(outputs)

    def run(
        self,
        workspace: Workspace,
        prior: Handover | None,
        emit: EmitFn,
        *,
        inv_id: str,
        resume_inv_id: str | None = None,
        instructions: str | None = None,
        on_proc_started: OnProcStarted | None = None,
        task_id: str | None = None,
    ) -> Handover:
        del resume_inv_id, task_id
        last_output = self.run_pipeline(
            workspace,
            prior,
            emit,
            inv_id=inv_id,
            instructions=instructions,
            on_proc_started=on_proc_started,
        )
        if _composed_severity_rank(last_output) < _MEDIUM_RANK:
            return build_terminal_handover(
                workspace,
                from_team=self.name,
                summary=last_output,
                prior=prior,
                invocation_id=inv_id,
            )
        question = OpenQuestion(
            id="maintenance-findings",
            question=(
                "This maintenance run reported findings at medium severity "
                "or higher. See the ## Run verdict block in the report for "
                "what was found and which checks are unverified."
            ),
            resolver_role="human",
            blocking=True,
        )
        return build_forward_handover(
            workspace,
            from_team=self.name,
            to_team_suggestions=[],
            summary=last_output,
            prior=prior,
            invocation_id=inv_id,
            open_questions=[question],
        )


TEAM = MaintenanceTeam()


__all__ = [
    "RUN_VERDICT_HEADING",
    "ROLES",
    "SEVERITY_LEVELS",
    "TEAM",
    "MaintenanceTeam",
]
