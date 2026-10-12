from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

from alan import agent_key, engine_config, workflow
from alan.agent_key import AgentKey
from alan.engine_config import EngineConfig
from alan.forge import active_forge
from alan.handover import ArtifactRef, Handover
from alan.launch_spec import LaunchSpec
from alan.teams import implementation, implementation_fix, verification
from alan.workspace import Requirement, Task, TaskDoDClause, TaskStatus, Workspace, create
from tests import _teams_verification_support as verification_support
from tests import agent_catalogue_parity as parity
from tests._helpers import make_handover, seed_and_run
from tests.agent_catalogue_parity_drivers_teams_orchestrated import isolate_state
from tests.fakes.forge import FakeForge
from tests.forge_stubs import always, api_result, gh_unreachable
from tests.gh_guard import GH_STUB_RETURNCODE, GH_STUB_STDERR

PR_NUMBER = 42
BRANCH = "teams/parity-existing"
INSTRUCTIONS = "Keep the change small."
SUBTASK = Task(
    id="subtask-1",
    status=TaskStatus.READY,
    title="Wire the export",
    instructions="Add the csv exporter",
    dod_clauses=[TaskDoDClause(text="The export lists every row")],
    files=["alan/export.py"],
)
APPROVE_OUTPUT = (
    "## Verdict\n\nVERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n\n## Rationale\nLooks good."
)
INCOMPLETE_OUTPUT = "## Findings\nI looked at the diff but wrote no verdict."
UNPARSEABLE_AUDIT = "The change looks fine to me."
REQUIREMENT = Requirement(text="the export must be csv")
REVIEW_FINDINGS = "## Verdict\nVERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 1"

Reply = Callable[[LaunchSpec], str]


def _ignore_event(_event: object) -> None:
    return None


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)  # noqa: S603,S607


def _use_repository(env: parity.ParityEnv, name: str, *, guidance: bool) -> None:
    repo = env.tmp_path / name
    repo.mkdir(parents=True)
    files = ["README.md", "CLAUDE.md"] if guidance else ["README.md"]
    for file in files:
        (repo / file).write_text("seed\n", encoding="utf-8")
    commands: list[tuple[str, ...]] = [
        ("init", "-q", "-b", "main"),
        ("config", "user.email", "parity@example.com"),
        ("config", "user.name", "Parity"),
        ("add", *files),
        ("commit", "-q", "-m", "initial"),
        ("branch", BRANCH),
    ]
    for args in commands:
        _git(repo, *args)
    env.monkeypatch.setenv("ALAN_WORKTREE_REPO_ROOT", str(repo))


def _prepare(env: parity.ParityEnv) -> None:
    isolate_state(env)
    engine_config.register_replacing(env.cfg)
    env.monkeypatch.setenv("ALAN_IMPL_DRY_RUN", "1")
    env.monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")


def _forge_unreachable(*, changed_files: frozenset[str] | None = None) -> None:
    fake = cast(FakeForge, active_forge())
    gh_unreachable()
    for method in ("head_sha", "unresolved_threads"):
        always(fake, method, api_result(returncode=GH_STUB_RETURNCODE, stderr=GH_STUB_STDERR))
    always(fake, "changed_files", changed_files)


def _workspace(
    env: parity.ParityEnv,
    title: str,
    *,
    tasks: list[Task] | None = None,
    requirements: list[Requirement] | None = None,
) -> Workspace:
    return create(
        title,
        f"{title} description",
        root=env.tmp_path / "workspaces",
        engine_key=env.cfg.repo.slug,
        tasks=tasks,
        requirements=requirements,
    )


def _plan_handover(workspace: Workspace) -> Handover:
    handover = make_handover(
        workspace.task_id,
        "plan",
        to_team="implementation",
        full_synthesis="## Plan\n- add the exporter",
        decision_statement="use json storage",
    )
    return handover.model_copy(
        update={
            "from_team": "implementation_plan",
            "plan_synthesis": "## Implementation steps\n1. add the exporter",
            "files_to_edit": ["alan/export.py"],
            "forbidden_actions": ["do not edit /etc/anything"],
            "artifacts_produced": [ArtifactRef(path="plan.md", description="the plan")],
        }
    )


def _review_handover(workspace: Workspace) -> Handover:
    handover = make_handover(
        workspace.task_id,
        "review",
        to_team="implementation_fix",
        iteration=2,
        full_synthesis=REVIEW_FINDINGS,
        decision_statement="verification verdict: REQUEST_CHANGES",
    )
    return handover.model_copy(
        update={
            "from_team": "verification",
            "pr_number": PR_NUMBER,
            "forbidden_actions": ["do not break the gates"],
        }
    )


def _run_implementation(env: parity.ParityEnv) -> None:
    _prepare(env)
    _use_repository(env, "implementation-plain", guidance=False)
    bare = _workspace(env, "implementation bare")
    seed_and_run(implementation.TEAM, bare, None, _ignore_event)
    _use_repository(env, "implementation-guided", guidance=True)
    full = _workspace(env, "implementation full", tasks=[SUBTASK])
    seed_and_run(
        implementation.TEAM,
        full,
        _plan_handover(full),
        _ignore_event,
        task_id=SUBTASK.id,
        instructions=INSTRUCTIONS,
    )


def _run_implementation_fix(env: parity.ParityEnv) -> None:
    _prepare(env)
    _forge_unreachable()
    env.monkeypatch.setattr(
        implementation_fix, "_resolve_existing_branch", lambda *_args, **_kwargs: BRANCH
    )
    _use_repository(env, "fix-plain", guidance=False)
    bare = _workspace(env, "fix bare")
    seed_and_run(implementation_fix.TEAM, bare, _review_handover(bare), _ignore_event)
    _use_repository(env, "fix-guided", guidance=True)
    full = _workspace(env, "fix full", tasks=[SUBTASK])
    seed_and_run(
        implementation_fix.TEAM,
        full,
        _review_handover(full),
        _ignore_event,
        task_id=SUBTASK.id,
        instructions=INSTRUCTIONS,
    )


def _run_drift_backstop(env: parity.ParityEnv) -> None:
    _prepare(env)
    workspace = _workspace(env, "backstop")
    checkout = env.tmp_path / "backstop-checkout"
    checkout.mkdir()
    inv_id = workflow.seed_pending_workflow(
        workspace,
        team=implementation.TEAM.name,
        tasks=implementation.TEAM.plan_workflow(workspace, None),
    )
    graph = workflow.load_graph(workspace, inv_id)
    graph.set_session_id("implementer", "recorded-session")
    handle = MagicMock()
    handle.path = checkout
    implementation._backstop_resume_attempt(graph, handle, env.cfg)  # pyright: ignore[reportPrivateUsage]


def _scripted(replies: dict[AgentKey, list[str]]) -> Reply:
    remaining = {key: list(texts) for key, texts in replies.items()}

    def reply(spec: LaunchSpec) -> str:
        queue = remaining.get(spec.agent) if spec.agent is not None else None
        return queue.pop(0) if queue else parity.DEFAULT_RESULT_TEXT

    return reply


def _verification_role(role: str) -> AgentKey:
    return agent_key.team_role(verification.TEAM.name, role)


def _verify(
    env: parity.ParityEnv,
    title: str,
    replies: dict[AgentKey, list[str]],
    *,
    requirements: list[Requirement] | None = None,
) -> None:
    workspace = _workspace(env, title, requirements=requirements)
    previous = env.recorder.reply
    env.recorder.reply = _scripted(replies)
    try:
        prior = verification_support.impl_handover(workspace, pr_number=PR_NUMBER)
        seed_and_run(verification.TEAM, workspace, prior, _ignore_event, instructions=INSTRUCTIONS)
    finally:
        env.recorder.reply = previous


def _run_verification(env: parity.ParityEnv) -> None:
    _prepare(env)
    _forge_unreachable(changed_files=frozenset({"alan/export.py"}))
    verification_support.stub_external_gates(env.monkeypatch)
    verification_support.stub_checkout_internals(env.monkeypatch, env.tmp_path)
    judge = _verification_role("judge")
    auditor = _verification_role("clause_auditor")
    _verify(env, "verification plain", {judge: [APPROVE_OUTPUT]})
    audited = f"CLAUSE {REQUIREMENT.id}: PASS — the exporter writes csv\nCLAUSE_FAILURES: 0"
    _verify(
        env,
        "verification audited",
        {auditor: [UNPARSEABLE_AUDIT, audited], judge: [INCOMPLETE_OUTPUT, APPROVE_OUTPUT]},
        requirements=[REQUIREMENT],
    )


def _verification_keys(cfg: EngineConfig) -> Sequence[AgentKey]:
    roles = [role.name for role in verification.roles_for_config(cfg)]
    return [_verification_role(role) for role in [*roles, "clause_auditor"]]


def register() -> None:
    drivers = (
        parity.Driver(
            name="implementation/implementer",
            covers=lambda _cfg: [agent_key.team_role("implementation", "implementer")],
            run=_run_implementation,
        ),
        parity.Driver(
            name="implementation/drift_backstop",
            covers=lambda _cfg: [agent_key.IMPLEMENTATION_DRIFT_BACKSTOP],
            run=_run_drift_backstop,
        ),
        parity.Driver(
            name="implementation_fix/implementer",
            covers=lambda _cfg: [agent_key.team_role("implementation_fix", "implementer")],
            run=_run_implementation_fix,
        ),
        parity.Driver(name="verification", covers=_verification_keys, run=_run_verification),
    )
    for driver in drivers:
        parity.register(driver)
