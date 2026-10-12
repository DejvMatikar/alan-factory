from __future__ import annotations

import re
import types
from collections.abc import Callable
from pathlib import Path

import pytest

from alan import default_profile
from alan.claude_cli_grants import PROMPT_INSTRUCTED_GRANTS
from alan.default_profile import DefaultProfile, Resolved, Source
from alan.engine_config import (
    EngineConfig,
    Posture,
    RepoIdentity,
    default_engine_config,
)
from alan.engine_presets import build_config
from alan.teams import _base, _clause_audit, _verification_prompts, architecture
from alan.teams._changed_e2e import changed_e2e_scenario
from tests.test_worker_credential_boundary import (
    allowed_in,
    argv_for,
    fleet_role_ids,
    role_overrides,
    stamped_workspace,
)

_COMMAND_SPAN = re.compile(
    r"`((?:uv run (?:--no-active )?[\w-]+|npm --prefix [^`\s]+ (?:run|ci))[^`]*)`"
)
_SHELL_TAIL = re.compile(r"\s+>|\s*[;|&]")

_PROMPT_INSTRUCTED_COMMANDS = (
    "uv run lizard --CCN 7 --length 60 alan/teams",
    "uv run xenon",
    "uv run vulture --min-confidence 80",
    "uv run --no-active pytest",
    "uv run --no-active pytest tests/test_x.py --no-cov -n 0",
    "npm --prefix alan/web run test:coverage",
    "npm --prefix alan/web run test -- src/x.test.ts",
    "npm --prefix alan/web ci",
)

_NOT_REACHABLE = (
    "uv sync --locked --extra dev",
    "uv run --no-active python -c pass",
    "uv run python3 -c pass",
    "curl https://example.invalid",
    "npx playwright test",
    "npm --prefix alan/web exec vitest",
    "npm --prefix alan/web install",
)


def pre_approved(command: str, allowed: list[str]) -> bool:
    for rule in allowed:
        if not (rule.startswith("Bash(") and rule.endswith(")")):
            continue
        body = rule[len("Bash(") : -1]
        if body.endswith(":*"):
            prefix = body[:-2]
            if command == prefix or command.startswith(prefix + " "):
                return True
        elif command == body:
            return True
    return False


def commands_in(text: str) -> list[str]:
    return [_SHELL_TAIL.split(span, maxsplit=1)[0].strip() for span in _COMMAND_SPAN.findall(text)]


def string_constants(module: types.ModuleType) -> list[str]:
    return [value for value in vars(module).values() if isinstance(value, str)]


def _engine_default(monkeypatch: pytest.MonkeyPatch) -> EngineConfig:
    return default_engine_config()


def _engine_ios(monkeypatch: pytest.MonkeyPatch) -> EngineConfig:
    cfg = build_config("ios", "acme", "widget")
    assert cfg is not None
    return cfg


def _engine_without_gates(monkeypatch: pytest.MonkeyPatch) -> EngineConfig:
    return EngineConfig(
        repo=RepoIdentity(owner="acme", repo="widget"),
        base_tools=["Read", "Bash(dotnet:*)"],
        posture=Posture.autonomous,
    )


def _engine_for_a_copys_default_profile(monkeypatch: pytest.MonkeyPatch) -> EngineConfig:
    profile = DefaultProfile(owner="copyowner", repo="copyrepo", posture=Posture.observe)
    resolved = Resolved(profile, Source.profile)
    monkeypatch.setattr(default_profile, "resolved", lambda: resolved)
    cfg = default_engine_config()
    assert cfg.repo.slug == "copyowner/copyrepo"
    return cfg


_ENGINE_SHAPES: dict[str, Callable[[pytest.MonkeyPatch], EngineConfig]] = {
    "default": _engine_default,
    "ios_preset": _engine_ios,
    "no_gate_commands": _engine_without_gates,
    "copy_default_profile": _engine_for_a_copys_default_profile,
}


def _non_implementation_role_argvs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cfg: EngineConfig
) -> list[tuple[str, list[str]]]:
    ws = stamped_workspace(tmp_path, cfg)
    argvs: list[tuple[str, list[str]]] = []
    for team_name, role_id in fleet_role_ids():
        overrides = role_overrides(team_name, role_id, ws, cfg)
        cmd = argv_for(monkeypatch, tools=overrides.allowed_tools, add_dirs=overrides.add_dirs)
        argvs.append((f"{team_name}/{role_id}", allowed_in(cmd)))
    return argvs


@pytest.mark.parametrize("shape", list(_ENGINE_SHAPES))
def test_every_non_implementation_role_can_still_run_the_gate_and_prompt_commands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shape: str
) -> None:
    cfg = _ENGINE_SHAPES[shape](monkeypatch)
    commands = [" ".join(gate) for gate in cfg.gate_commands] + list(_PROMPT_INSTRUCTED_COMMANDS)
    for label, allowed in _non_implementation_role_argvs(monkeypatch, tmp_path, cfg):
        blocked = [c for c in commands if not pre_approved(c, allowed)]
        assert blocked == [], label


def test_the_ios_gate_is_reachable_by_derivation_when_the_stored_tools_omit_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _engine_ios(monkeypatch)
    stripped = cfg.model_copy(update={"base_tools": ["Read", "Grep"]})
    assert stripped.gate_commands == [["bash", "scripts/gate.sh"]]
    for label, allowed in _non_implementation_role_argvs(monkeypatch, tmp_path, stripped):
        assert pre_approved("bash scripts/gate.sh", allowed), label


def test_an_engine_without_gate_commands_still_reaches_only_the_prompt_instructed_commands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _engine_without_gates(monkeypatch)
    assert cfg.gate_commands == []
    for label, allowed in _non_implementation_role_argvs(monkeypatch, tmp_path, cfg):
        derived = [grant for grant in allowed if grant not in cfg.base_tools]
        assert derived == list(PROMPT_INSTRUCTED_GRANTS), label


@pytest.mark.parametrize("shape", list(_ENGINE_SHAPES))
@pytest.mark.parametrize("command", _NOT_REACHABLE)
def test_commands_outside_the_gates_and_prompts_are_not_pre_approved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shape: str, command: str
) -> None:
    cfg = _ENGINE_SHAPES[shape](monkeypatch)
    for label, allowed in _non_implementation_role_argvs(monkeypatch, tmp_path, cfg):
        assert not pre_approved(command, allowed), label


def test_the_pre_approval_matcher_honours_the_word_boundary() -> None:
    allowed = ["Bash(uv run pytest:*)", "Bash(exact cmd)"]
    assert pre_approved("uv run pytest", allowed)
    assert pre_approved("uv run pytest tests/x.py", allowed)
    assert not pre_approved("uv run pytester", allowed)
    assert pre_approved("exact cmd", allowed)
    assert not pre_approved("exact cmd extra", allowed)
    assert not pre_approved("uv run pytest", ["Read", "Bash(uv run pytest:*"])


def test_architecture_prompt_commands_are_pre_approved() -> None:
    found = [c for text in string_constants(architecture) for c in commands_in(text)]
    assert {"uv run xenon"} <= set(found)
    assert all(pre_approved(c, list(PROMPT_INSTRUCTED_GRANTS)) for c in found)


def test_verification_prompt_commands_are_pre_approved() -> None:
    found = [c for text in string_constants(_verification_prompts) for c in commands_in(text)]
    assert "npm --prefix alan/web run test -- <path>" in found
    assert "uv run --no-active pytest tests/test_x.py --no-cov -n 0" in found
    assert all(pre_approved(c, list(PROMPT_INSTRUCTED_GRANTS)) for c in found)


def test_clause_audit_prompt_commands_are_pre_approved() -> None:
    found = [c for text in string_constants(_clause_audit) for c in commands_in(text)]
    assert "npm --prefix alan/web ci" in found
    assert "uv run --no-active pytest" in found
    assert all(pre_approved(c, list(PROMPT_INSTRUCTED_GRANTS)) for c in found)


def test_planning_prompt_commands_are_pre_approved() -> None:
    found = [c for text in string_constants(_base) for c in commands_in(text)]
    assert "npm --prefix alan/web run capture:screens" in found
    assert all(pre_approved(c, list(PROMPT_INSTRUCTED_GRANTS)) for c in found)


def test_changed_e2e_runner_command_is_pre_approved() -> None:
    scenario = changed_e2e_scenario(("alan/web/e2e/a.spec.ts", "alan/web/e2e/b.spec.ts"))
    assert scenario is not None
    assert scenario.when == "npm --prefix alan/web run e2e -- e2e/a.spec.ts e2e/b.spec.ts"
    assert pre_approved(scenario.when, list(PROMPT_INSTRUCTED_GRANTS))


def test_the_prompt_command_parser_ignores_negative_instructions_and_shell_tails() -> None:
    text = (
        "never run `uv run`/`npm` here; "
        "run `uv run --no-active pytest > out.log 2>&1; rc=$?` then `npm --prefix web ci`"
    )
    assert commands_in(text) == ["uv run --no-active pytest", "npm --prefix web ci"]
