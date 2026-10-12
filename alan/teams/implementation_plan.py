from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from alan import agent_key
from alan.claude_cli import ClaudeCliError, OnProcStarted, format_user_text, run_agent
from alan.engine_config import _DEFAULT as _ENGINE_DEFAULT  # pyright: ignore[reportPrivateUsage]
from alan.engine_config import EngineConfig
from alan.handover import Decision, Handover, TestScenario
from alan.roots import plans_root
from alan.teams import _pr_forge, scenario_authoring
from alan.teams._base import (
    PLANNING_SCREEN_CAPTURE_INSTRUCTION,
    PLANNING_UX_LENS,
    EmitFn,
    TeamEvent,
    build_forward_handover,
)
from alan.teams._static_pipeline import (
    FinalizeFn,
    Role,
    Stage,
    StaticPipelineParseError,
    StaticPipelineTeam,
    decisions_block_needed_repair,
    load_decisions_yaml_block,
    parse_decisions_block,
)
from alan.workspace import Workspace

_MODEL_OPUS = _ENGINE_DEFAULT.roles["judge"].model
_MODEL_SONNET = _ENGINE_DEFAULT.roles["diff_reviewer"].model
_TEAM_INTRO = "You are the {role} on a planning team."
_SECTION_SUFFIX = "Respond with only your section."

_ARCHITECT_PROMPT = (
    f"{_TEAM_INTRO.format(role='architect')} Read the task description and "
    "propose one or two high-level technical approaches. State which you "
    "recommend and why, and call out the main tradeoffs. Ground your "
    "recommendation in patterns already present in the codebase. Output "
    "sections: ## Recommended approach, ## Tradeoffs considered, ## Open "
    f"questions. {_SECTION_SUFFIX} No preamble, no markdown fencing around "
    "the whole response."
)

_BACKEND_ANALYST_PROMPT = (
    f"{_TEAM_INTRO.format(role='backend analyst')} The architect has "
    "proposed an approach (in prior agent outputs). Detail the concrete "
    "backend changes: exact file paths, new functions and classes with full "
    "signatures, endpoints, data models, and error handling. Cite existing "
    "helpers to reuse. Output sections: ## Backend changes, ## New code, "
    f"## Reused helpers. {_SECTION_SUFFIX}"
)

_FRONTEND_ANALYST_PROMPT = (
    f"{_TEAM_INTRO.format(role='frontend analyst')} The architect and "
    "backend analyst have specified the API and approach. Detail the "
    "frontend changes: components to add or modify, state shape, network "
    "calls, and user-visible behavior. Output sections: ## Frontend "
    f"changes, ## Components, ## State and effects. {_SECTION_SUFFIX}\n\n"
    + PLANNING_UX_LENS
    + "\n\n" + PLANNING_SCREEN_CAPTURE_INSTRUCTION
    + "\n\nIf the task has no frontend surface, say so in one line and "
    "stop — do not invent frontend work."
)

_QA_PLANNER_PROMPT = (
    f"{_TEAM_INTRO.format(role='QA planner')} Produce concrete test "
    "scenarios for the planned change: unit, integration, and end-to-end. "
    "Name the existing test files to extend and the new test files to "
    "create. Output sections: ## Test scenarios, ## Unit tests, ## E2E "
    f"scenarios. {_SECTION_SUFFIX}"
)

_DEVIL_ADVOCATE_PROMPT = (
    _TEAM_INTRO.format(role="devil's advocate")
    + " Review the prior agents' outputs critically. Probe four angles "
    "the other agents typically miss: (1) **sibling code** — for each "
    "primary file named, glob the same directory for siblings sharing the "
    "naming convention; surface files the change probably also needs to "
    "touch (callers, shared helpers, parallel implementations); (2) "
    "**invariant risk** — grep docs/architecture/ for invariants (layered "
    "architecture, schema versions, gate thresholds, CCN limits) the "
    "proposed change might violate; surface any that the plan has not "
    "explicitly addressed; (3) **ripple effects** on callers, persisted "
    "data, wire contracts, and invariants not enumerated above; "
    "(4) **UX consistency** — flows, affordances, terminology, and "
    "keyboard/accessibility parity with comparable surfaces; ground any "
    "parity claim in `docs/DESIGN_SYSTEM.md` and `alan/web/src/ui-spec.ts` "
    "as its basis. Also surface "
    "general risks, gaps, and open questions. Output sections: "
    "## Adjacent code surface, ## Invariant risk, ## Ripple effects, "
    "## UX consistency, ## Risks, ## Gaps, ## Suggested clarifications. "
    + _SECTION_SUFFIX
)

_SYNTHESIZER_PROMPT = scenario_authoring.synthesizer_prompt(None)


ROLES: tuple[Role, ...] = (
    Role("architect", _ARCHITECT_PROMPT, _MODEL_OPUS),
    Role("backend_analyst", _BACKEND_ANALYST_PROMPT, _MODEL_SONNET),
    Role("frontend_analyst", _FRONTEND_ANALYST_PROMPT, _MODEL_SONNET),
    Role("qa_planner", _QA_PLANNER_PROMPT, _MODEL_SONNET),
    Role("devil_advocate", _DEVIL_ADVOCATE_PROMPT, _MODEL_SONNET),
    Role("synthesizer", _SYNTHESIZER_PROMPT, _MODEL_OPUS),
)

_MAX_SLUG_SUFFIX = 100
_SLUG_MAX_LEN = 60


ImplementationPlanError = StaticPipelineParseError
_parse_decisions_block = parse_decisions_block

_SCENARIO_REQUIRED_FIELDS = ("id", "description", "given", "when", "then")


def _validate_scenario_entry(idx: int, entry: object) -> TestScenario:
    if not isinstance(entry, dict):
        raise ImplementationPlanError(
            f"test_scenarios[{idx}] must be a mapping, "
            f"got {type(entry).__name__}"
        )
    scenario = cast(dict[str, Any], entry)
    validated: dict[str, str] = {}
    for key in _SCENARIO_REQUIRED_FIELDS:
        value = scenario.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ImplementationPlanError(
                f"test_scenarios[{idx}].{key} must be a non-empty string"
            )
        validated[key] = value.strip()
    return TestScenario.model_validate(validated)


def _parse_test_scenarios(
    synthesis: str, cfg: EngineConfig | None = None
) -> list[TestScenario]:
    payload = load_decisions_yaml_block(synthesis)
    raw_items = payload.get("test_scenarios")
    if raw_items is None:
        raise ImplementationPlanError(
            "decisions.yaml block missing required `test_scenarios:` key"
        )
    if not isinstance(raw_items, list):
        raise ImplementationPlanError(
            "decisions.yaml `test_scenarios:` must be a list, "
            f"got {type(raw_items).__name__}"
        )
    if not raw_items:
        raise ImplementationPlanError(
            "decisions.yaml `test_scenarios:` must list at least one entry"
        )
    items = cast(list[Any], raw_items)  # type: ignore[redundant-cast]
    scenarios = [_validate_scenario_entry(idx, entry) for idx, entry in enumerate(items)]
    rejection = scenario_authoring.collect_rejections(list(enumerate(scenarios)), cfg)
    if rejection is not None:
        raise ImplementationPlanError(rejection)
    return scenarios


_STAGES: tuple[Stage, ...] = (
    ("architect",),
    ("backend_analyst",),
    ("frontend_analyst", "qa_planner"),
    ("devil_advocate",),
    ("synthesizer",),
)


@dataclass(frozen=True)
class ImplementationPlanTeam(StaticPipelineTeam):
    name: str = "implementation_plan"
    roles: tuple[Role, ...] = ROLES
    to_team_suggestions: tuple[str, ...] = field(default=(), init=False, repr=False)
    finalize: FinalizeFn | None = None
    stages: tuple[Stage, ...] = _STAGES

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
        del resume_inv_id, task_id  # workflow.yaml is the source of truth for resume state
        # Use run_pipeline (not super().run) so the fenced decisions.yaml
        # block at the end of the synthesizer output isn't lost to the
        # 2500-char truncation that build_forward_handover applies.
        last_output = self.run_pipeline(
            workspace,
            prior,
            emit,
            inv_id=inv_id,
            instructions=instructions,
            on_proc_started=on_proc_started,
        )
        cfg = _pr_forge.governing_config_for_ws(workspace)

        def _parse(text: str) -> tuple[list[Decision], list[str], list[TestScenario]]:
            dec, files = parse_decisions_block(text)
            scen = _parse_test_scenarios(text, cfg)
            return dec, files, scen

        decisions, files_to_edit, scenarios = self._parse_or_invalidate(
            workspace,
            inv_id,
            _parse,
            last_output,
            block_name="decisions.yaml",
            emit=emit,
            on_proc_started=on_proc_started,
            detect_repair=decisions_block_needed_repair,
        )
        handover = build_forward_handover(
            workspace,
            from_team=self.name,
            to_team_suggestions=["decomposer"],
            summary=last_output,
            prior=prior,
            invocation_id=inv_id,
        )
        return handover.model_copy(
            update={
                "decisions": decisions,
                "files_to_edit": files_to_edit,
                "test_scenarios": scenarios,
            }
        )

    def _role_system_prompt(self, role: Role, workspace: Workspace) -> str:
        if role.name != "synthesizer":
            return super()._role_system_prompt(role, workspace)
        cfg = _pr_forge.governing_config_for_ws(workspace)
        return scenario_authoring.synthesizer_prompt(cfg)


TEAM = ImplementationPlanTeam()


def run_team(title: str, description: str, emit: Callable[[TeamEvent], None]) -> None:
    base = format_user_text(title, description)
    outputs: list[tuple[str, str]] = []
    for role in ROLES:
        emit(TeamEvent("role_started", {"role": role.name}))
        try:
            output = run_agent(
                role.system_prompt,
                _build_user_text(base, outputs),
                agent=agent_key.legacy_planning(role.name),
            )
        except ClaudeCliError as exc:
            emit(TeamEvent("error", {"role": role.name, "detail": str(exc)}))
            return
        outputs.append((role.name, output))
        emit(TeamEvent("role_completed", {"role": role.name}))
    _finalize(title, outputs[-1][1], emit)


def _finalize(title: str, plan: str, emit: Callable[[TeamEvent], None]) -> None:
    try:
        path = _write_plan(title, plan)
    except OSError as exc:
        emit(TeamEvent("error", {"role": "synthesizer", "detail": f"write failed: {exc}"}))
        return
    emit(TeamEvent("complete", {"path": str(path), "plan": plan}))


def _build_user_text(base: str, prior: list[tuple[str, str]]) -> str:
    if not prior:
        return base
    parts = [base, "\n\nPrior agent outputs:"]
    for name, text in prior:
        parts.append(f"\n\n## {name}\n\n{text}")
    return "".join(parts)


def _write_plan(title: str, plan: str) -> Path:
    plans_dir = plans_root()
    plans_dir.mkdir(parents=True, exist_ok=True)
    slug = _slugify(title)
    for i in range(1, _MAX_SLUG_SUFFIX + 1):
        candidate = plans_dir / (f"{slug}.md" if i == 1 else f"{slug}-{i}.md")
        try:
            with candidate.open("x", encoding="utf-8") as handle:
                handle.write(plan)
        except FileExistsError:
            continue
        return candidate
    raise FileExistsError(f"no unique path for {slug} under {plans_dir}")


def _slugify(title: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return cleaned[:_SLUG_MAX_LEN] or "plan"
