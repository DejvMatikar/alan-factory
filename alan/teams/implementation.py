from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from alan import agent_key
from alan import base_ref as _base_ref
from alan.claude_cli import OnProcStarted, ProcessConfig, TurnCeilingReached
from alan.compression import DEFAULT_MAX_CHARS
from alan.drift_gate import changed_paths as _drift_gate_changed_paths
from alan.drift_gate import drift_gate_violated
from alan.engine_config import _DEFAULT as _ENGINE_DEFAULT  # pyright: ignore[reportPrivateUsage]
from alan.engine_config import EngineConfig, RoleConfig
from alan.fleet_load import FLEET_IDLE_TIMEOUT_SECONDS
from alan.forge import active_forge
from alan.handover import Handover
from alan.teams import _base as _impl_base
from alan.teams import (
    _declared_blocker,
    _forge_errors,
    _inner_loop_gate,
    _native_capture,
    _plan_projection,
    _pr_audit_log,
    _pr_body,
    _pr_branch,
    _pr_closing,
    _pr_forge,
    _pr_handover,
    _pr_push,
)
from alan.teams._base import (
    BASE_ALLOWED_TOOLS,
    UX_LENS,
    EmitFn,
    WorkerOverrides,
    compose_system_prompt,
    render_allowed_tools,
)
from alan.teams._pr_branch import (
    resolve_existing_branch as _resolve_existing_branch,
)
from alan.teams._pr_forge import GH_TIMEOUT_S
from alan.teams._pr_push import (
    push_branch as _push_branch,
)
from alan.workflow import PendingTaskSpec, WorkflowGraph
from alan.workspace import Workspace
from alan.worktree import WorktreeError, WorktreeHandle, captured_commits

logger = logging.getLogger(__name__)

_DRY_RUN_ENV = "ALAN_IMPL_DRY_RUN"
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_PR_URL_RE = re.compile(r"/pull/(\d+)\b")

_TEAM_NAME = "implementation"
_NODE_ROLE = "implementer"
_MODEL = _ENGINE_DEFAULT.roles["implementer"].model
_EFFORT = _ENGINE_DEFAULT.roles["implementer"].effort
_MAX_TURNS = 500
_NEXT_TEAM: tuple[str, ...] = ("verification",)
_DECISION_PREFIX = "impl"
_BACKSTOP_MAX_TURNS = 30
_BACKSTOP_USER_TEXT = (
    "DRIFT GATE BACKSTOP: a load-bearing file was changed without a companion "
    "documentation update. Before you stop, add a substantive update to a "
    "file under docs/architecture/, CLAUDE.md, or "
    "docs/AGENTIC_CODING_GUIDE.md to describe the behaviour that changed — "
    "not a token edit. Then commit the documentation update and stop."
)
_DEFAULT_FORBIDDEN_ACTIONS: tuple[str, ...] = (
    "do not modify files outside the patch outline described in the plan",
    "do not run `pip install -e .` or any editable install — alan is uv-managed",
    "do not push branches or open PRs — that is the verification team's job",
    "do not widen a tightening-only ratchet baseline (a flake baseline, a "
    "stress floor, a size-ratchet cap, a UI audit baseline, or the "
    "duplication threshold) in this pull request — a legitimate raise "
    "belongs in its own pull request against the flake-quarantine issue, "
    "citing its rerun-rescued evidence",
)
_TEAM_CONFIG = _pr_handover.TeamConfig(
    team_name=_TEAM_NAME,
    default_forbidden=_DEFAULT_FORBIDDEN_ACTIONS,
    next_team=_NEXT_TEAM,
    decision_prefix=_DECISION_PREFIX,
    scenario_blind=True,
)

_IMPL_EXTRAS: tuple[str, ...] = tuple(_ENGINE_DEFAULT.team_tools["implementation"])

ALLOWED_TOOLS: tuple[str, ...] = BASE_ALLOWED_TOOLS + _IMPL_EXTRAS

_ENV_BLOCK_TEMPLATE = (
    "ENVIRONMENT — you are running inside a leased checkout for this "
    "task: a pooled, pre-warmed slot when one is available, a freshly "
    "created git worktree otherwise. Either way the current working "
    "directory is authoritative. It may have NO .venv and NO "
    "node_modules of its own (a fresh worktree never does; a pooled "
    "slot may already be warm from a prior lease) — the bootstrap step "
    "below is safe to run regardless. The parent process exports a "
    "VIRTUAL_ENV from the main repo that does NOT apply here. Use the "
    "command forms below verbatim; if you are ever unsure what you are "
    "in, `pwd` and `ls` are cheap and safe to run first.\n"
    "- Bootstrap (FIRST backend command of the session, before any "
    "`uv run --no-active` invocation): `{bootstrap_cmd}`. "
    "This hydrates the worktree's resolved venv with dev-only deps "
    "(`pytest-asyncio`, `pytest-cov`, etc.). Without it, `uv run --no-active "
    "pytest` fails with `INTERNALERROR pytest.PytestConfigWarning: "
    "Unknown config option: asyncio_mode` because the plugin that "
    "registers `asyncio_mode` is not importable.\n"
    "- Python tools: ALWAYS prefix with `uv run --no-active`. Examples: "
    "`uv run --no-active pytest tests/<file>.py`, "
    "`{ruff_cmd}`, "
    "`{mypy_cmd}`. Do NOT call bare `pytest`, "
    "`.venv/bin/pytest`, `uv run --active …`, or `VIRTUAL_ENV= uv run …` "
    "— they will fail in this worktree.\n"
    "- Frontend tools: DO NOT use `cd` — bash cwd PERSISTS between "
    "tool_uses, so `cd alan/web && npm ...` will silently break the "
    "next `uv run …` (it runs from `alan/web/` and finds no `tests/` "
    "or `pyproject.toml`). Instead, use `npm --prefix alan/web <cmd>` "
    "for every frontend invocation — npm reads `alan/web/package.json` "
    "and executes scripts from that directory while leaving the shell's "
    "cwd at the worktree root. First frontend tool_use of the run: "
    "`{npm_ci_cmd}`, then `{npm_typecheck}`, `{npm_lint}`, "
    "`{npm_test_coverage}`, etc.\n"
    "- Backend gates the worker should run before committing — as "
    "parallel Bash tool_uses in ONE turn when possible: the full suite, "
    "in exactly this captured form and no other — `{pytest_cmd} > "
    "pytest_full.log 2>&1; rc=$?; tail -40 pytest_full.log; (exit $rc)` "
    "(bare `{pytest_cmd}`, NO path args and NO `--no-cov`, redirected into "
    "a gitignored file in this worktree so its output is read back from "
    "the file — `grep -n 'FAILED\\|ERROR\\|TOTAL' pytest_full.log` — never "
    "by running it again, while `rc=$?`/`(exit $rc)` keeps the run's own "
    "exit status. CI gates on this full run; it enforces the 100% "
    "branch-coverage gate. `--no-cov` exits 0 but skips the gate; a "
    "`pytest tests/<file>` subset measures a low total and itself fails "
    "the gate — neither proves coverage; subsets are for iteration only. "
    "TEST-RUN BUDGET: iterate with targeted runs only — `uv run --no-active "
    "pytest tests/<file>.py --no-cov -n 0` for each touched test file — "
    "and run the full suite AT MOST ONCE, as the LAST gate once every "
    "targeted run is green (the lint/type gates below may run alongside "
    "it in the same turn). If the full run is red: fix, re-run the "
    "affected targeted files, then ONE more full run — two full runs per "
    "session is the hard cap), "
    "`{ruff_cmd}`, "
    "`{mypy_cmd}`, "
    "`{pyright_cmd}`, "
    "`uv run --no-active lizard --CCN 7 --length 60 --languages python "
    "--exclude 'alan/web/*' alan/`, "
    "`{xenon_cmd}`, "
    "`{lint_imports_cmd}`, "
    "`{bandit_cmd}`, "
    "`{vulture_cmd}`, "
    "`uv run --no-active pip-audit --strict --disable-pip -r "
    "<(uv export --frozen --all-extras --no-emit-project --format "
    "requirements-txt)`. Plain `uv run --no-active pip-audit --strict` "
    "fails because the local `alan` package isn't on PyPI; the command "
    "above (process substitution `<(...)`) audits only the locked deps. "
    "Do NOT `> /tmp/alan-req.txt` — claude-code's bash sandbox blocks "
    "writes outside the worktree, denying the redirect.\n"
    "- Frontend gates the worker should run before committing — also as "
    "parallel tool_uses: "
    "`{npm_typecheck}`, "
    "`{npm_lint}`, "
    "`{npm_test_coverage}`, "
    "`{npm_lint_knip}`, "
    "`{npm_lint_deps}`, "
    "`{npm_lint_dup}`. The FULL Playwright e2e suite "
    "(`npm --prefix alan/web run e2e`) is too heavy to run inline — "
    "leave it for CI. BUT when your diff adds or edits any "
    "`alan/web/e2e/*.spec.ts`, or removes a `data-testid` that an e2e "
    "spec references, you MUST run exactly those spec files before "
    "finishing: `git fetch origin main && git diff --name-only "
    "origin/main...HEAD -- alan/web/e2e` "
    "to list them, then `npm --prefix alan/web run e2e -- <spec files>` "
    "(the pre-hook builds the SPA, ~2 min — budget for it). A red spec "
    "there is your own defect to fix before stopping, never a flake. The "
    "same holds when CI's flake ratchet names any spec, not only one this "
    "diff adds or edits: the attempt-1 failure is on the check run's "
    "annotations and in the review's `GATE_FAILING_TEST:` lines — make the "
    "measurement robust to contention (settle the layout before capturing "
    "a baseline, `expect.poll` across the window). Never widen a "
    "tightening-only ratchet baseline (e.g. add the spec to "
    "`alan/web/e2e/flaky-baseline.json`) from inside this pull request — a "
    "legitimate raise belongs in its own pull request against the "
    "flake-quarantine issue, citing its rerun-rescued evidence.\n"
    "- Drift gate: before stopping, run "
    "`uv run --no-active python -m alan.drift_gate --check` — if any "
    "load-bearing file was changed without a companion doc update, "
    "add a substantive update to docs/architecture/*.md, "
    "CLAUDE.md, or docs/AGENTIC_CODING_GUIDE.md to "
    "describe the behaviour that changed before committing and "
    "stopping. The host backstop will attempt a same-pass session-resume "
    "to add the doc update if this check fails.\n\n"
)


def _shcmd(parts: list[str]) -> str:
    return " ".join(parts)


def _render_default_environment_block(cfg: EngineConfig) -> str:
    gc = cfg.gate_commands
    wc = cfg.warmup_commands
    (
        pytest_cmd, ruff_cmd, mypy_cmd, pyright_cmd,
        _, xenon_cmd, lint_imports_cmd, bandit_cmd, vulture_cmd,
        _, npm_typecheck, npm_lint, npm_test_coverage,
        npm_lint_knip, npm_lint_deps, npm_lint_dup,
    ) = gc
    bootstrap_cmd, npm_ci_parts = wc
    return _ENV_BLOCK_TEMPLATE.format_map({
        "bootstrap_cmd": _shcmd(bootstrap_cmd),
        "npm_ci_cmd": _shcmd(npm_ci_parts),
        "pytest_cmd": _shcmd(pytest_cmd),
        "ruff_cmd": _shcmd(ruff_cmd),
        "mypy_cmd": _shcmd(mypy_cmd),
        "pyright_cmd": _shcmd(pyright_cmd),
        "xenon_cmd": _shcmd(xenon_cmd),
        "lint_imports_cmd": _shcmd(lint_imports_cmd),
        "bandit_cmd": _shcmd(bandit_cmd),
        "vulture_cmd": _shcmd(vulture_cmd),
        "npm_typecheck": _shcmd(npm_typecheck),
        "npm_lint": _shcmd(npm_lint),
        "npm_test_coverage": _shcmd(npm_test_coverage),
        "npm_lint_knip": _shcmd(npm_lint_knip),
        "npm_lint_deps": _shcmd(npm_lint_deps),
        "npm_lint_dup": _shcmd(npm_lint_dup),
    })


_GENERIC_RUN_ONCE_GATE_LINE = (
    "- Run each gate once, at the end, after iterating with the narrowest "
    "targeted command the tool offers; the only reason to run a gate again "
    "is that you fixed what made it red — never to re-read its output. "
    "Capture long output to a gitignored file inside this checkout and "
    "read that file instead."
)


def _render_generic_environment_block(cfg: EngineConfig) -> str:
    lines = [
        "ENVIRONMENT — you are running inside a leased checkout of "
        f"{cfg.repo.slug} for this task: a pooled, pre-warmed slot when "
        "one is available, a freshly created git worktree otherwise. "
        "Either way the current working directory is authoritative. Use "
        "the command forms below verbatim; if you are ever unsure what "
        "you are in, `pwd` and `ls` are cheap and safe to run first.",
        "",
    ]
    if cfg.warmup_commands:
        lines.append(
            "- Bootstrap (FIRST commands of the session, run once to warm "
            "the checkout), in order:"
        )
        lines.extend(f"  `{_shcmd(cmd)}`" for cmd in cfg.warmup_commands)
    else:
        lines.append(
            "- No warmup/bootstrap commands are configured for this engine "
            "— do not invent any."
        )
    if cfg.gate_commands:
        lines.append(
            "- Quality gates the worker should run before committing — as "
            "parallel Bash tool_uses in ONE turn when possible:"
        )
        lines.extend(f"  `{_shcmd(cmd)}`" for cmd in cfg.gate_commands)
        lines.append(_GENERIC_RUN_ONCE_GATE_LINE)
    else:
        lines.append(
            "- No local quality gates are configured for this engine — do "
            "not invent gate commands. The authoritative merge gate is this "
            "repository's own continuous-integration checks, evaluated "
            "after the PR is opened; their local absence is not an approval."
        )
    return "\n".join(lines) + "\n\n"


def render_environment_block(cfg: EngineConfig = _ENGINE_DEFAULT) -> str:
    if cfg == _ENGINE_DEFAULT:
        return _render_default_environment_block(cfg)
    return _render_generic_environment_block(cfg)


def environment_block_for(cfg: EngineConfig, team: str) -> str:
    inner_loop_gate = _inner_loop_gate.inner_loop_gate_block(cfg, team)
    if inner_loop_gate:
        inner_loop_gate = "\n\n" + inner_loop_gate
    return (
        render_environment_block(cfg)
        + _native_capture.native_capture_block(cfg, team)
        + inner_loop_gate
    )


ENVIRONMENT_BLOCK = render_environment_block()

_SYSTEM_PROMPT = compose_system_prompt(
    "You are the implementation team running inside a leased checkout of "
    "origin/main: a pooled, pre-warmed slot when one is available, a "
    "freshly created git worktree otherwise. Execute "
    "the approved plan from the "
    "implementation_plan team. Write code, write tests, commit with "
    "Conventional Commits messages. STOP after committing — do not push, "
    "do not open a PR.\n\n"
    + ENVIRONMENT_BLOCK
    + "EXECUTION DISCIPLINE — you have a finite turn budget and the planner "
    "has already given you the full file scope. Burn turns on work, not "
    "exploration.\n"
    "1. Read in parallel. First turn: emit ONE assistant message containing "
    "parallel Read tool_uses for EVERY file the plan names. Do not read "
    "files one at a time.\n"
    "2. Edit in parallel and in batches. Group independent file edits into "
    "a single assistant turn (multiple Edit/Write tool_uses in one "
    "message).\n"
    "3. Run gates in parallel. Run pytest / lint / typecheck as parallel "
    "Bash tool_uses in one turn, not sequentially.\n"
    "4. Commit at natural checkpoints, not after every file. One commit "
    "per logical change (e.g. backend, frontend, tests). Each commit is a "
    "single Bash tool_use; group multi-step git sequences with "
    "`git add … && git commit …`.\n"
    "5. Be terse between tool calls. No \"Let me check…\", \"Now I'll…\", "
    "\"Great, that worked\" filler. Prose only in the final ## Summary / "
    "## Decisions / ## Commits / ## Open questions sections.\n"
    "6. If a test fails: fix and re-run the affected test FILE (`--no-cov "
    "-n 0`) in the SAME turn (parallel Edit + Bash pytest in one message) "
    "— never the full suite. Don't single-step.\n\n"
    "SCOPE: Implement every step from the prior plan's `Implementation "
    "steps` section. Stay within the file scope the plan names; if you "
    "must touch something outside it, stop and explain in your final "
    "synthesis instead of guessing. When you touch UI (`alan/web/src/`), "
    "conform to the design system in `@docs/DESIGN_SYSTEM.md` — tokens, "
    "conventions, WCAG 2.1 AA baseline. When your diff touches "
    "`alan/web/`, best-effort run `npm --prefix alan/web run "
    "capture:screens` and Read the resulting PNGs under `alan/web/"
    "screen-captures/` before finalizing — a rendered look at your own "
    "screens catches issues markup review misses.\n"
    + UX_LENS + "\n"
    "SIBLING CODE IS NOT PROOF OF COMPLETION: your worktree may already "
    "contain code from completed sibling subtasks or merged PRs that touch "
    "nearby files. That is NOT evidence your task is done. When a `## Your "
    "task` section is present, implement exactly what it names and verify it "
    "against the files it names — never conclude 'already implemented' on the "
    "basis of sibling work. If `## Your task`'s Definition of Done is not yet "
    "satisfied by code you can point to, it is not done; do it.\n"
    "SUCCESS: Output is complete when (1) every step has corresponding "
    "code + tests committed, (2) the FULL backend suite — bare "
    "`uv run --no-active pytest` with NO path args and WITHOUT `--no-cov` "
    "— exits 0. CI gates on exactly this run; it enforces the 100% "
    "branch-coverage gate (`fail_under=100`, from `[tool.coverage.report]`). "
    "Two footguns that look green but are not: `--no-cov` exits 0 while "
    "skipping the gate (CI then fails on coverage), and a subset run like "
    "`uv run --no-active pytest tests/<file>.py` measures a low total and "
    "itself fails `fail_under=100`, so passing tests on a subset proves "
    "nothing. Only the full run both passes and proves coverage. Run it "
    "once, at the end, captured to `pytest_full.log` as the environment "
    "block says; a second full run is allowed only after fixing a red "
    "first one (cap two per session). "
    "(3) you emit a final synthesis listing every commit (subject line + "
    "files touched) and the decisions you made. Stop as soon as gates "
    "pass and commits are in — do not burn additional turns polishing "
    "code that already meets the plan.\n\n"
    "FORBIDDEN — these come from the upstream handover's "
    "`forbidden_actions` plus baseline rules:\n"
    "- Do NOT push, do NOT open a PR — verification handles merge.\n"
    "- Do NOT run editable installs (`pip install -e .`).\n"
    "- Do NOT modify files outside the plan's file scope.\n\n"
    "Output sections in your final response: ## Summary (1-3 sentences), "
    "## Decisions (one bullet per non-obvious call you made, with "
    "rationale), ## Commits (one bullet per commit: subject + files), "
    "## Open questions (anything that needs human or verification team "
    "input).\n\n"
    + _declared_blocker.DECLARED_BLOCKER_GUIDANCE,
    terse=False,
)

_DEFAULT_ROLE = RoleConfig(model=_MODEL, effort=_EFFORT)


_REPO_GUIDANCE_FILE = "CLAUDE.md"


def repo_guidance_block(worktree_path: Path) -> str:
    if not (worktree_path / _REPO_GUIDANCE_FILE).is_file():
        return ""
    return (
        "\n\nREPOSITORY GUIDANCE — this repository has its own contributor "
        f"guidance at @{_REPO_GUIDANCE_FILE}. Read it and follow its "
        "conventions; it supersedes any generic guidance above where they "
        "conflict.\n"
    )


def generic_success_block(cfg: EngineConfig) -> str:
    if not cfg.gate_commands:
        return (
            "SUCCESS: Output is complete when (1) every step has "
            "corresponding code + tests committed, (2) you emit a final "
            "synthesis listing every commit (subject line + files touched) "
            "and the decisions you made. This engine declares no local "
            "quality gates — do not invent any; the authoritative merge "
            "gate is this repository's own continuous-integration "
            "workflow, evaluated after the PR is open."
        )
    gate_list = "\n".join(f"- `{_shcmd(cmd)}`" for cmd in cfg.gate_commands)
    return (
        "SUCCESS: Output is complete when (1) every step has corresponding "
        "code + tests committed, (2) every gate below exits 0:\n"
        f"{gate_list}\n"
        "Run each gate once, at the end, after iterating with the narrowest "
        "targeted command the tool offers; a repeat run is for a fix, never "
        "for re-reading output — capture long output to a gitignored file "
        "inside this checkout and re-read that. "
        "(3) you emit a final synthesis listing every commit (subject line "
        "+ files touched) and the decisions you made. Stop as soon as "
        "gates pass and commits are in — do not burn additional turns "
        "polishing code that already meets the plan."
    )


def _compose_generic_system_prompt(cfg: EngineConfig, worktree_path: Path) -> str:
    return compose_system_prompt(
        "You are the implementation team running inside a leased checkout "
        f"of {cfg.repo.slug}'s origin/main: a pooled, pre-warmed slot when "
        "one is available, a freshly created git worktree otherwise. "
        "Execute the approved plan from the "
        "implementation_plan team. Write code, write tests, commit with "
        "Conventional Commits messages. STOP after committing — do not "
        "push, do not open a PR.\n\n"
        + environment_block_for(cfg, _TEAM_NAME)
        + "EXECUTION DISCIPLINE — you have a finite turn budget and the "
        "planner has already given you the full file scope. Burn turns on "
        "work, not exploration.\n"
        "1. Read in parallel. First turn: emit ONE assistant message "
        "containing parallel Read tool_uses for EVERY file the plan "
        "names. Do not read files one at a time.\n"
        "2. Edit in parallel and in batches. Group independent file edits "
        "into a single assistant turn (multiple Edit/Write tool_uses in "
        "one message).\n"
        "3. Run the gates above in parallel, as parallel Bash tool_uses in "
        "one turn, not sequentially.\n"
        "4. Commit at natural checkpoints, not after every file. One "
        "commit per logical change. Each commit is a single Bash "
        "tool_use; group multi-step git sequences with `git add … && git "
        "commit …`.\n"
        "5. Be terse between tool calls. Prose only in the final "
        "## Summary / ## Decisions / ## Commits / ## Open questions "
        "sections.\n"
        "6. If a gate fails: fix and re-run in the SAME turn. Don't "
        "single-step.\n\n"
        "SCOPE: Implement every step from the prior plan's `Implementation "
        "steps` section. Stay within the file scope the plan names; if you "
        "must touch something outside it, stop and explain in your final "
        "synthesis instead of guessing.\n"
        "SIBLING CODE IS NOT PROOF OF COMPLETION: your worktree may "
        "already contain code from completed sibling subtasks or merged "
        "PRs that touch nearby files. That is NOT evidence your task is "
        "done. When a `## Your task` section is present, implement "
        "exactly what it names and verify it against the files it "
        "names.\n"
        + generic_success_block(cfg) + "\n\n"
        "FORBIDDEN — these come from the upstream handover's "
        "`forbidden_actions` plus baseline rules:\n"
        "- Do NOT push, do NOT open a PR — verification handles merge.\n"
        "- Do NOT modify files outside the plan's file scope.\n\n"
        "Output sections in your final response: ## Summary (1-3 "
        "sentences), ## Decisions (one bullet per non-obvious call you "
        "made, with rationale), ## Commits (one bullet per commit: "
        "subject + files), ## Open questions (anything that needs human "
        "or verification team input).\n\n"
        + _declared_blocker.DECLARED_BLOCKER_GUIDANCE
        + repo_guidance_block(worktree_path),
        terse=False,
    )


def render_system_prompt(cfg: EngineConfig, worktree_path: Path) -> str:
    base = (
        _SYSTEM_PROMPT
        if cfg == _ENGINE_DEFAULT
        else _compose_generic_system_prompt(cfg, worktree_path)
    )
    return _pr_body.render_with_pr_body_instructions(
        cfg,
        _TEAM_NAME,
        _NODE_ROLE,
        base,
        environment_block=environment_block_for(cfg, _TEAM_NAME),
        success_block=generic_success_block(cfg),
        repo_guidance_block=repo_guidance_block(worktree_path),
    )


@dataclass(frozen=True)
class ImplementationTeam:
    name: str = _TEAM_NAME
    to_team_suggestions: tuple[str, ...] = _NEXT_TEAM
    supports_resume: bool = True

    def plan_workflow(
        self,
        workspace: Workspace,
        prior: Handover | None,
    ) -> list[PendingTaskSpec]:
        del prior
        cfg = _pr_forge.governing_config_for_ws(workspace)
        model, effort = _pr_forge.role_model_effort(cfg, _NODE_ROLE, _DEFAULT_ROLE)
        return [
            PendingTaskSpec(id=_NODE_ROLE, role=_NODE_ROLE, model=model, effort=effort)
        ]

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
        cfg = _pr_forge.governing_config_for_ws(workspace)
        model, effort = _pr_forge.role_model_effort(cfg, _NODE_ROLE, _DEFAULT_ROLE)
        user_text = _build_user_text(workspace, prior, instructions, task_id)
        overrides = WorkerOverrides(
            cwd=None,
            allowed_tools=list(render_allowed_tools(cfg, _TEAM_NAME)),
            permission_mode="acceptEdits",
            max_turns=_MAX_TURNS,
        )
        prior_pr = prior.pr_number if prior is not None else None
        forge_target = _pr_forge.forge_target_for_ws(workspace)
        existing_branch = _resolve_existing_branch(
            prior_pr, workspace.task_id, forge_target=forge_target
        )
        existing_pr = prior_pr if existing_branch is not None else None
        with _pr_handover.worktree_iteration(
            workspace, self.name, inv_id, emit, existing_branch=existing_branch
        ) as (graph, handle):
            synthesis, commits = _run_worker(
                workspace, graph, handle, inv_id, emit, user_text,
                _pr_handover.cwd_overrides(overrides, handle.path),
                resume_inv_id, on_proc_started, cfg, model, effort,
            )
            push_result = _push_changes(
                workspace, handle, synthesis, commits, existing_pr=existing_pr
            )
            _branch = handle.branch
        iteration_push_failed = (
            bool(commits) and push_result.pr_number is None and not dry_run_enabled()
        )
        return _pr_handover.finalize_handover(
            _TEAM_CONFIG, workspace, prior, inv_id, synthesis, commits,
            push_result.pr_number,
            iteration_push_failed=iteration_push_failed,
            push_failure=_pr_handover.PushFailureContext(
                pushed=push_result.pushed,
                continuing_pr=existing_pr is not None,
                branch=_branch,
                credential_failure=push_result.credential_failure,
                forge_target=_pr_forge.forge_target_for_ws(workspace),
            ),
        )


def _run_worker(
    workspace: Workspace,
    graph: WorkflowGraph,
    handle: WorktreeHandle,
    inv_id: str,
    emit: EmitFn,
    user_text: str,
    overrides: WorkerOverrides,
    resume_inv_id: str | None,
    on_proc_started: OnProcStarted | None,
    cfg: EngineConfig,
    model: str,
    effort: str | None,
) -> tuple[str, list[str]]:
    try:
        synthesis = _pr_handover.run_or_resume(
            workspace, graph, inv_id, emit,
            node_id=_NODE_ROLE, role=_NODE_ROLE, model=model, effort=effort,
            system_prompt=render_system_prompt(cfg, handle.path),
            user_text=user_text,
            overrides=overrides,
            resume_inv_id=resume_inv_id,
            on_proc_started=on_proc_started,
        )
    except TurnCeilingReached as exc:
        _impl_base.emit_node_turns_audit(
            workspace.task_id, inv_id, _TEAM_NAME, graph, _NODE_ROLE, hit_ceiling=True
        )
        return _pr_handover.salvage_on_ceiling(handle, workspace.task_id, _TEAM_NAME, exc)
    except Exception:
        _impl_base.emit_node_turns_audit(
            workspace.task_id, inv_id, _TEAM_NAME, graph, _NODE_ROLE
        )
        raise
    salvage_result = _pr_handover.salvage_uncommitted_wip_detail(
        handle, workspace.task_id, _TEAM_NAME
    )
    synthesis = _pr_handover.with_salvage_notice(
        synthesis, salvage_result.outcome, skipped_count=salvage_result.skipped_count
    )
    commits = captured_commits(handle)
    return _apply_backstop(workspace, graph, handle, synthesis, commits, cfg)


def _build_user_text(
    workspace: Workspace,
    prior: Handover | None,
    instructions: str | None = None,
    task_id: str | None = None,
) -> str:
    return _pr_handover.compose_user_text(
        workspace, prior,
        mid_blocks=_prior_plan_and_decisions,
        config=_TEAM_CONFIG,
        instructions=instructions,
        current_task=_pr_handover.lookup_task(workspace, task_id),
    )


def _prior_plan_and_decisions(prior: Handover) -> list[str]:
    return [_prior_plan_block(prior), _decisions_inherited_block(prior)]


_IMPLEMENTATION_STEPS_HEADER = "## Implementation steps"
_NEXT_MARKDOWN_H2_RE = re.compile(r"\n##[ \t]")


def _plan_implementation_steps(plan_synthesis: str) -> str:
    start = plan_synthesis.find(_IMPLEMENTATION_STEPS_HEADER)
    if start == -1:
        return plan_synthesis
    next_header = _NEXT_MARKDOWN_H2_RE.search(
        plan_synthesis, start + len(_IMPLEMENTATION_STEPS_HEADER)
    )
    end = next_header.start() if next_header else len(plan_synthesis)
    return plan_synthesis[start:end].strip()


def _prior_plan_block(prior: Handover) -> str:
    if prior.plan_synthesis is not None:
        scrubbed = _plan_projection.strip_scenario_fences(prior.plan_synthesis)
        body = _plan_implementation_steps(scrubbed)
    else:
        scrubbed = _plan_projection.strip_scenario_fences(prior.full_synthesis)
        body = scrubbed[:DEFAULT_MAX_CHARS]
    return f"{_prior_plan_header(prior)}\n{body}"


def _prior_plan_header(prior: Handover) -> str:
    if prior.handover_type == "escalation":
        return (
            f"\n\n## Previous FAILED attempt (from {prior.from_team}, iteration "
            f"{prior.iteration}) — this attempt produced no usable result; its "
            "'already done' / 'no changes needed' claims are UNRELIABLE. "
            "Implement ## Your task regardless."
        )
    return f"\n\n## Prior plan (from {prior.from_team}, iteration {prior.iteration})"


def _decisions_inherited_block(prior: Handover) -> str:
    if not prior.decisions:
        return ""
    bullets = "\n".join(
        f"- {d.statement.strip()} (because: {d.rationale.strip()})"
        for d in prior.decisions
    )
    return f"\n\n## Decisions inherited\n{bullets}"


def dry_run_enabled() -> bool:
    return os.environ.get(_DRY_RUN_ENV, "").strip().lower() in _TRUTHY


def _backstop_required(handle: WorktreeHandle, commits: list[str]) -> bool:
    return bool(commits) and _drift_gate_check(handle.path)


def _drift_gate_check(worktree: Path) -> bool:
    base = _base_ref.resolve(worktree)
    if base is None:
        logger.warning("drift gate: base ref unresolved; skipping backstop check")
        return False
    try:
        paths = _drift_gate_changed_paths(base, worktree)
    except RuntimeError:
        return False
    return drift_gate_violated(paths)


def _apply_backstop(
    workspace: Workspace,
    graph: WorkflowGraph,
    handle: WorktreeHandle,
    synthesis: str,
    commits: list[str],
    cfg: EngineConfig,
) -> tuple[str, list[str]]:
    if not _backstop_required(handle, commits):
        return synthesis, commits
    resumed = _backstop_resume_attempt(graph, handle, cfg)
    if resumed is None or _drift_gate_check(handle.path):
        return synthesis, commits
    _pr_audit_log.append_audit({"task_id": workspace.task_id, "action": "drift_backstop_done"})
    return synthesis, captured_commits(handle)


def _backstop_resume_attempt(
    graph: WorkflowGraph,
    handle: WorktreeHandle,
    cfg: EngineConfig = _ENGINE_DEFAULT,
) -> str | None:
    node = next((t for t in graph.doc.tasks if t.id == _NODE_ROLE), None)
    if node is None or not node.claude_session_id:
        return None
    try:
        result = _impl_base.run_agent_streaming(  # type: ignore[attr-defined]
            _SYSTEM_PROMPT,
            _BACKSTOP_USER_TEXT,
            model=_MODEL,
            effort=_EFFORT,
            max_turns=_BACKSTOP_MAX_TURNS,
            proc_config=ProcessConfig(
                cwd=handle.path, timeout_seconds=FLEET_IDLE_TIMEOUT_SECONDS
            ),
            resume_session_id=node.claude_session_id,
            allowed_tools=list(render_allowed_tools(cfg, _TEAM_NAME)),
            permission_mode="acceptEdits",
            fleet=True,
            agent=agent_key.IMPLEMENTATION_DRIFT_BACKSTOP,
        )
        return result.result_text
    except Exception:  # noqa: BLE001
        return None


@dataclass(frozen=True)
class PushChangesResult:
    pr_number: int | None
    credential_failure: _pr_branch.CredentialFailureClass | None
    pushed: bool


def _push_changes(
    workspace: Workspace,
    handle: WorktreeHandle,
    synthesis: str,
    commits: list[str],
    *,
    existing_pr: int | None,
) -> PushChangesResult:
    if not commits:
        _pr_audit_log.append_audit({
            "task_id": workspace.task_id,
            "action": "skip",
            "reason": "no commits to push",
        })
        return PushChangesResult(pr_number=existing_pr, credential_failure=None, pushed=True)
    if dry_run_enabled():
        _pr_audit_log.append_audit({
            "task_id": workspace.task_id,
            "action": "dry_run",
            "branch": handle.branch,
            "commit_count": len(commits),
            "existing_pr": existing_pr,
        })
        return PushChangesResult(pr_number=existing_pr, credential_failure=None, pushed=True)
    push_result = _push_branch(
        handle.path, handle.branch, workspace.task_id,
        forge_target=_pr_forge.forge_target_for_ws(workspace),
    )
    if push_result.outcome is not _pr_push.PushOutcome.PUSHED:
        return PushChangesResult(
            pr_number=None, credential_failure=push_result.credential_failure, pushed=False
        )
    if existing_pr is not None:
        return PushChangesResult(pr_number=existing_pr, credential_failure=None, pushed=True)
    return PushChangesResult(
        pr_number=_open_pr(handle, workspace, synthesis),
        credential_failure=None,
        pushed=True,
    )


def _open_pr(handle: WorktreeHandle, workspace: Workspace, synthesis: str) -> int | None:
    body = (
        _pr_closing.compose_salvaged_pr_body(
            workspace, handle.path, handle.base_ref, team=_TEAM_NAME, pr_number=None,
        )
        if _pr_closing.is_salvage_only(handle.path, handle.base_ref)
        else _pr_closing.compose_pr_body(workspace, synthesis, team=_TEAM_NAME, pr_number=None)
    )
    try:
        result = active_forge().create_pr_from_checkout(
            workspace.meta.title or workspace.task_id, body, handle.branch,
            forge_target=_pr_forge.forge_target_for_ws(workspace),
            workdir=handle.path, timeout=GH_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("gh pr create raised %s", exc)
        _pr_audit_log.append_audit({
            "task_id": workspace.task_id,
            "action": "pr_create_failed",
            "branch": handle.branch,
            "error": str(exc),
        })
        raise _forge_errors.ForgeTransientError(f"gh pr create raised {exc}") from exc
    if result.returncode != 0:
        logger.warning(
            "gh pr create failed (rc=%s): %s",
            result.returncode,
            result.stderr,
        )
        _pr_audit_log.append_audit({
            "task_id": workspace.task_id,
            "action": "pr_create_failed",
            "branch": handle.branch,
            "returncode": result.returncode,
            "stderr": (result.stderr or "")[:500],
        })
        _forge_errors.raise_for_infra(
            "gh pr create", returncode=result.returncode, stderr=result.stderr,
        )
        return None
    pr_number = _extract_pr_number(result.stdout)
    _pr_audit_log.append_audit({
        "task_id": workspace.task_id,
        "action": "pr_create_ok",
        "branch": handle.branch,
        "pr_number": pr_number,
        "stdout": (result.stdout or "").strip()[:500],
    })
    return pr_number


def _extract_pr_number(stdout: str) -> int | None:
    match = _PR_URL_RE.search(stdout)
    if match is None:
        return None
    return int(match.group(1))


TEAM = ImplementationTeam()


__all__ = [
    "TEAM",
    "ImplementationTeam",
    "WorktreeError",
]
