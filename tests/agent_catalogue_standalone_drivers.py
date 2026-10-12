from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import pytest

from alan import (
    _chat_core,
    agent_catalogue,
    agent_key,
    behavioral_eval,
    build_chat,
    collision,
    compression,
    driver_loop_cmd,
    driver_loop_registry,
    engine_config,
    engine_config_proposal,
    explore_chat,
    investigator,
    judge_eval,
    manager_summary,
    operator_launch,
    operator_repos,
)
from alan.agent_catalogue_helpers import (
    ResolvedAgent,
    ResolverGroup,
    segments_from_text,
    to_entry,
)
from alan.agent_catalogue_standalone_driver_loops import TEMPLATE_NAME, _template_entry
from alan.claude_cli import CONSULTANTS
from alan.engine_config import EngineConfig, RepoIdentity, default_engine_config
from alan.engine_stack_detect import StackDetection
from alan.events import PubSub
from alan.operator_checkout import CheckoutResult
from alan.operator_prompt import Scope
from alan.routes import build_chat as build_chat_route
from alan.routes import explore_chat as explore_chat_route
from alan.routes._chat_router import ChatRouterCtx, do_turn
from alan.teams import implementation_plan
from tests import agent_catalogue_parity as parity
from tests.agent_catalogue_parity import Driver, ParityEnv

_INSTALL = RepoIdentity(owner="install-owner", repo="install-repo")
_OTHER = RepoIdentity(owner="acme", repo="widget")

S = TypeVar("S", bound=_chat_core.ChatSession)


class _Locked(_chat_core.ChatCoreError):
    pass


def _keys(*keys: agent_key.AgentKey) -> Sequence[agent_key.AgentKey]:
    return keys


def _chat_turn(ctx: ChatRouterCtx[S], session: S, root: Path, text: str) -> None:
    locks = _chat_core.ChatSessionLocks(_Locked, "parity")
    entry = locks.acquire(session.session_id)
    do_turn(
        ctx=ctx,
        session=session,
        user_text=text,
        pubsub=PubSub(),
        root=root,
        locks=locks,
        entry=entry,
    )
    locks.release(entry)


def _build_chat(env: ParityEnv) -> None:
    root = env.tmp_path / "build-chats"
    session = build_chat.create_session(root=root)
    _chat_turn(build_chat_route._CTX, session, root, "hello")
    with_stats = build_chat_route._ctx_with_shop_stats("first-pass approval 91%")
    _chat_turn(with_stats, session, root, "propose")


def _explore_chats(env: ParityEnv) -> None:
    root = env.tmp_path / "explore-chats"
    for consultant in CONSULTANTS:
        session = explore_chat.create_session(consultant.key, root=root)
        _chat_turn(explore_chat_route._CTX, session, root, "hello")


def _collision(env: ParityEnv) -> None:
    pr = collision._PrCandidate(7, "Fix the thing", "https://x.test/7", frozenset({"a.py"}))
    ws = collision._WsCandidate("ws-1", "Another title", True)
    for prompt in (
        collision._build_prompt("the brief", [pr], [ws]),
        collision._build_prompt("the brief", [], [ws]),
    ):
        collision._call_model(prompt, stream_path=env.tmp_path / "collision.jsonl")


def _manager_summary(env: ParityEnv) -> None:
    detailed = env.tmp_path / "ws-detailed"
    detailed.mkdir()
    meta = {"park_reason": "waiting", "tasks": [{"dod_clauses": [{"text": "t", "evidence": "e"}]}]}
    (detailed / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    plain = env.tmp_path / "ws-plain"
    plain.mkdir()
    generate = manager_summary._do_generate
    generate(detailed, "ws-1", "Title", "completed", "the synthesis", lambda: None, "stopped")
    generate(plain, "ws-2", "Other", "failed", None, lambda: None)


def _investigator(env: ParityEnv) -> None:
    workspace = env.tmp_path / "ws-incident"
    workspace.mkdir()
    investigator._generate_rca_text(
        workspace, "fp-1", {"signal": ["a", "b"]}, frozenset({"retry", "park"})
    )


def _compactor(_env: ParityEnv) -> None:
    compression._call_compactor_llm("a long synthesis " * 20)


def _proposal(env: ParityEnv) -> None:
    baseline = default_engine_config()
    dest = env.tmp_path / "proposal-checkout"
    for additional in (("node",), ()):
        detection = StackDetection(
            recommended_preset="python", detected_stacks=("python",), additional_stacks=additional
        )
        engine_config_proposal._run_inference(dest, baseline, detection)


def _judge_eval(env: ParityEnv) -> None:
    engine_config.register_replacing(env.cfg, default=True)
    case = judge_eval.GoldenCase(
        case_id="case",
        situation="gate_park",
        context="the golden context",
        intended_verdict="APPROVE",
        citation="source",
        must_not_flip=False,
    )
    judge_eval.judge_case(case)


def _planner_grader(_env: ParityEnv) -> None:
    case = behavioral_eval.GoldenCase(case_id="case", brief="the brief", synthesizer_result="plan")
    rubric = {"scale": "pass or fail", "criteria": [{"id": "c1", "description": "is concrete"}]}
    behavioral_eval.judge_case(case, rubric)


def _legacy_planning(env: ParityEnv) -> None:
    env.monkeypatch.setattr(implementation_plan, "plans_root", lambda: env.tmp_path / "plans")
    implementation_plan.run_team("A title", "A description", lambda _event: None)


def _fake_operator_checkouts(env: ParityEnv) -> None:
    checkout = CheckoutResult(path=env.tmp_path / "operator-checkout", warning=None)
    target = operator_repos.RepoTarget(identity=_INSTALL, clone_url="u", migrates_legacy=True)
    env.monkeypatch.setattr(operator_repos, "ensure_all", lambda: checkout)
    env.monkeypatch.setattr(operator_repos, "ensure_repo", lambda _target: checkout)
    env.monkeypatch.setattr(operator_repos, "install_target", lambda: target)
    env.monkeypatch.setattr(operator_repos, "optional_install_target", lambda: target)


def _operator_sessions(env: ParityEnv) -> None:
    _fake_operator_checkouts(env)
    operator_launch.global_plan()
    operator_launch.repo_plan(_OTHER)
    for identity in (_INSTALL, _OTHER):
        operator_launch.workspace_plan(
            identity, task_id="fix-the-thing", workspace_dir=env.tmp_path / "workspace"
        )


def _driver_loops(_env: ParityEnv) -> None:
    entries, _quarantined = driver_loop_registry.load_registry()
    for entry in entries or [_template_entry()]:
        driver_loop_cmd.build_argv(entry)


def _driver_loop_keys(_cfg: EngineConfig) -> Sequence[agent_key.AgentKey]:
    entries, _quarantined = driver_loop_registry.load_registry()
    return [agent_key.driver_loop(entry.name) for entry in entries] or [
        agent_key.driver_loop(TEMPLATE_NAME)
    ]


def _legacy_keys(_cfg: EngineConfig) -> Sequence[agent_key.AgentKey]:
    return [agent_key.legacy_planning(role.name) for role in implementation_plan.ROLES]


def _operator_keys(_cfg: EngineConfig) -> Sequence[agent_key.AgentKey]:
    return [agent_key.operator_session(scope.value) for scope in Scope]


STANDALONE_DRIVERS: tuple[Driver, ...] = (
    Driver("build chat", lambda _cfg: _keys(agent_key.BUILD_CHAT), _build_chat),
    Driver(
        "explore chats",
        lambda _cfg: [agent_key.explore_chat(c.key) for c in CONSULTANTS],
        _explore_chats,
    ),
    Driver("collision", lambda _cfg: _keys(agent_key.COLLISION), _collision),
    Driver("manager summary", lambda _cfg: _keys(agent_key.MANAGER_SUMMARY), _manager_summary),
    Driver("investigator", lambda _cfg: _keys(agent_key.INVESTIGATOR), _investigator),
    Driver("handover compactor", lambda _cfg: _keys(agent_key.HANDOVER_COMPACTOR), _compactor),
    Driver(
        "engine config proposal", lambda _cfg: _keys(agent_key.ENGINE_CONFIG_PROPOSAL), _proposal
    ),
    Driver("judge eval", lambda _cfg: _keys(agent_key.JUDGE_EVAL), _judge_eval),
    Driver(
        "planner grader eval", lambda _cfg: _keys(agent_key.PLANNER_GRADER_EVAL), _planner_grader
    ),
    Driver("legacy planning", _legacy_keys, _legacy_planning),
    Driver("operator sessions", _operator_keys, _operator_sessions),
    Driver("driver loops", _driver_loop_keys, _driver_loops),
)

def _compare(agent: ResolvedAgent, captured: list[parity.Captured]) -> list[str]:
    if not captured:
        return [f"{agent.key.id}: the driver never launched this agent"]
    prompt = to_entry(agent, []).prompt
    user_template = parity.template_regex(segments_from_text(agent.user_message, "user_message"))
    problems: list[str] = []
    for launch in captured:
        diffs = parity.spec_differences(agent.spec, launch.spec, agent)
        if diffs:
            problems.append(f"{agent.key.id}: spec differs {diffs}")
        mismatch = parity.prompt_mismatch(agent, prompt, launch.spec)
        if mismatch is not None:
            problems.append(mismatch)
        if launch.user_text is not None and user_template.fullmatch(launch.user_text) is None:
            problems.append(f"{agent.key.id}: user text does not match {launch.user_text!r}")
    return problems


def parity_failures(
    cfg: EngineConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    groups: Sequence[ResolverGroup],
    drivers: Sequence[Driver] = STANDALONE_DRIVERS,
) -> list[str]:
    resolved, errors = agent_catalogue.resolve_all(cfg, groups)
    recorder = parity.install_recorder(monkeypatch)
    env = ParityEnv(monkeypatch, tmp_path, recorder, cfg)
    failures = [f"{entry.id}: {entry.error}" for entry in errors]
    ran: set[str] = set()
    for agent in resolved:
        driver = next((d for d in drivers if agent.key in d.covers(cfg)), None)
        if driver is None:
            failures.append(f"{agent.key.id}: no driver")
            continue
        if driver.name not in ran:
            ran.add(driver.name)
            driver.run(env)
        failures.extend(_compare(agent, recorder.for_agent(agent.key)))
    return failures


@dataclass(frozen=True)
class ModelAndEffort:
    launched: tuple[str | None, str | None]
    catalogued: tuple[str | None, str | None]
    agent: ResolvedAgent


def launched_models(
    cfg: EngineConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    groups: Sequence[ResolverGroup],
) -> dict[str, ModelAndEffort]:
    resolved, _errors = agent_catalogue.resolve_all(cfg, groups)
    tmp_path.mkdir(parents=True, exist_ok=True)
    found: dict[str, ModelAndEffort] = {}
    ran: set[str] = set()
    with monkeypatch.context() as patch:
        recorder = parity.install_recorder(patch)
        env = ParityEnv(patch, tmp_path, recorder, cfg)
        for agent in resolved:
            driver = next(d for d in STANDALONE_DRIVERS if agent.key in d.covers(cfg))
            if driver.name not in ran:
                ran.add(driver.name)
                driver.run(env)
            spec = recorder.for_agent(agent.key)[0].spec
            found[agent.key.id] = ModelAndEffort(
                (spec.model, spec.effort), (agent.spec.model, agent.spec.effort), agent
            )
    return found


__all__ = ["STANDALONE_DRIVERS", "launched_models", "parity_failures"]
