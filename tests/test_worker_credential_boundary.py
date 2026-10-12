from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

from alan import claude_cli, investigator
from alan import workspace as workspace_mod
from alan.claude_cli import run_agent_streaming, run_chat_turn
from alan.claude_cli_grants import (
    ACCEPT_EDITS_MODE,
    CREDENTIAL_BASH_DENIES,
    GENERAL_EXEC_GRANTS,
    PROMPT_INSTRUCTED_GRANTS,
    _claude_credential_files,  # pyright: ignore[reportPrivateUsage]
    _gh_config_dir,  # pyright: ignore[reportPrivateUsage]
    _path_forms,  # pyright: ignore[reportPrivateUsage]
    credential_deny_rules,
    credential_read_rules,
    disallowed_tools_args,
    gate_tool_grants,
    grant_args,
    narrowed_tools,
    with_gate_reachability,
)
from alan.engine_config import (
    EngineConfig,
    Posture,
    RepoIdentity,
    default_engine_config,
)
from alan.teams import TEAMS, _orchestrated
from alan.teams._base import WorkerOverrides, render_allowed_tools
from alan.teams._orchestrated import OrchestratedTeam
from alan.teams._static_pipeline import StaticPipelineTeam
from alan.workspace import EngineSnapshot, Workspace, create
from tests.test_claude_cli_streaming import _lines, _patch_popen, _result_event

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SESSION = "00000000-0000-4000-8000-000000000000"
_IMPLEMENTATION_TEAMS = ("implementation", "implementation_fix")
_MODES = ("auto", ACCEPT_EDITS_MODE)


@dataclass(frozen=True)
class SyntheticRoots:
    home: Path
    alan_home: Path
    gh_config: Path


@pytest.fixture(autouse=True)
def synthetic_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SyntheticRoots:
    home = tmp_path / "home"
    alan_home = home / ".alan"
    gh_config = tmp_path / "gh-config"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(alan_home / "state"))
    monkeypatch.setenv("ALAN_SECRETS_STORE_ROOT", str(alan_home / "secrets"))
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(alan_home / "engines"))
    monkeypatch.setenv("GH_CONFIG_DIR", str(gh_config))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return SyntheticRoots(home=home, alan_home=alan_home, gh_config=gh_config)


def fleet_role_ids() -> list[tuple[str, str]]:
    ids: list[tuple[str, str]] = []
    for name, team in TEAMS.items():
        if isinstance(team, StaticPipelineTeam):
            ids.extend((name, role.name) for role in team.roles)
        if isinstance(team, OrchestratedTeam):
            ids.extend([(name, "node"), (name, "worker")])
    return ids


def stamped_workspace(tmp_path: Path, cfg: EngineConfig) -> Workspace:
    ws = create("credential-boundary", "d", root=tmp_path / "ws")
    snapshot = EngineSnapshot(posture=Posture.autonomous, transition_graph={}, config=cfg)
    return workspace_mod.stamp_engine_snapshot(ws, snapshot)


def role_overrides(
    team_name: str, role_id: str, ws: Workspace, cfg: EngineConfig
) -> WorkerOverrides:
    team = TEAMS[team_name]
    if isinstance(team, OrchestratedTeam):
        if role_id == "node":
            return _orchestrated._node_overrides(ws)  # noqa: SLF001
        return _orchestrated._worker_overrides(cfg, team_name)  # noqa: SLF001
    assert isinstance(team, StaticPipelineTeam)
    overrides = team._role_overrides(role_id, ws)  # noqa: SLF001
    assert overrides is not None
    return overrides


def argv_for(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tools: list[str] | None,
    add_dirs: list[str] | None = None,
    mode: str = "auto",
) -> list[str]:
    captured: list[list[str]] = []
    _patch_popen(monkeypatch, stdout_lines=_lines(_result_event("ok")), capture_cmds=captured)
    run_agent_streaming(
        "sys", "user", allowed_tools=tools, add_dirs=add_dirs, permission_mode=mode
    )
    return captured[0]


def allowed_in(cmd: list[str]) -> list[str]:
    start = cmd.index("--allowedTools") + 1
    end = next((i for i in range(start, len(cmd)) if cmd[i].startswith("--")), len(cmd))
    return cmd[start:end]


def assert_every_rule_is_its_own_argument(cmd: list[str]) -> None:
    assert cmd.count("--disallowedTools") == 1
    for rule in credential_deny_rules():
        assert cmd.count(rule) == 1
        assert not any(arg != rule and rule in arg for arg in cmd)


def test_read_rules_protect_every_credential_location(synthetic_roots: SyntheticRoots) -> None:
    rules = credential_read_rules()
    state = synthetic_roots.alan_home / "state"
    expected = [
        f"Read(/{synthetic_roots.alan_home / 'secrets.env'})",
        f"Read(/{state / 'control_token'})",
        f"Read(/{state / 'control_token_meta.json'})",
        f"Read(/{synthetic_roots.home / '.claude' / '.credentials.json'})",
        f"Read(/{synthetic_roots.alan_home / 'secrets'}/**)",
        f"Read(/{synthetic_roots.alan_home / 'engines'}/**)",
        f"Read(/{synthetic_roots.gh_config}/**)",
    ]
    for rule in expected:
        assert rule in rules


def test_read_rules_use_the_double_slash_absolute_form() -> None:
    assert all(rule.startswith("Read(//") for rule in credential_read_rules())


def test_bash_denies_name_the_credential_commands() -> None:
    assert CREDENTIAL_BASH_DENIES == (
        "Bash(security:*)",
        "Bash(git credential:*)",
        "Bash(gh auth:*)",
        "Bash(git diff --no-index:*)",
        "Bash(git blame --contents:*)",
    )


def test_deny_rules_are_read_rules_then_bash_denies_without_duplicates(
) -> None:
    rules = credential_deny_rules()
    assert rules == [*credential_read_rules(), *CREDENTIAL_BASH_DENIES]
    assert len(rules) == len(set(rules))


def test_rules_follow_redirected_state_roots(
    synthetic_roots: SyntheticRoots, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = credential_deny_rules()
    moved = tmp_path / "moved"
    monkeypatch.setenv("ALAN_STATE_ROOT", str(moved / "state"))
    monkeypatch.setenv("ALAN_SECRETS_STORE_ROOT", str(moved / "vault"))
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(moved / "records"))
    after = credential_deny_rules()
    assert f"Read(/{moved / 'state' / 'control_token'})" in after
    assert f"Read(/{moved / 'vault'}/**)" in after
    assert f"Read(/{moved / 'records'}/**)" in after
    assert f"Read(/{moved / 'secrets.env'})" in after
    assert f"Read(/{synthetic_roots.alan_home / 'secrets'}/**)" in before
    assert f"Read(/{synthetic_roots.alan_home / 'secrets'}/**)" not in after


def test_gh_config_dir_follows_gh_config_dir_env(
    synthetic_roots: SyntheticRoots, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmp_path / "other-gh"
    monkeypatch.setenv("GH_CONFIG_DIR", str(other))
    assert _gh_config_dir() == other
    assert f"Read(/{other}/**)" in credential_read_rules()
    assert f"Read(/{synthetic_roots.gh_config}/**)" not in credential_read_rules()


def test_gh_config_dir_falls_back_to_xdg_config_home(
    synthetic_roots: SyntheticRoots, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert synthetic_roots.gh_config.is_absolute()
    monkeypatch.delenv("GH_CONFIG_DIR")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert _gh_config_dir() == tmp_path / "xdg" / "gh"


def test_gh_config_dir_falls_back_to_the_home_config_directory(
    synthetic_roots: SyntheticRoots, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GH_CONFIG_DIR")
    assert _gh_config_dir() == synthetic_roots.home / ".config" / "gh"


def test_blank_gh_config_dir_env_is_treated_as_unset(
    synthetic_roots: SyntheticRoots, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_CONFIG_DIR", "  ")
    assert _gh_config_dir() == synthetic_roots.home / ".config" / "gh"


def test_claude_config_dir_override_adds_a_second_credential_file(
    synthetic_roots: SyntheticRoots, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    override = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(override))
    files = _claude_credential_files()
    assert files == [
        synthetic_roots.home / ".claude" / ".credentials.json",
        override / ".credentials.json",
    ]


def test_symlinked_root_yields_the_configured_and_the_resolved_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "real-store"
    real.mkdir()
    link = tmp_path / "linked-store"
    link.symlink_to(real)
    monkeypatch.setenv("ALAN_SECRETS_STORE_ROOT", str(link))
    rules = credential_read_rules()
    assert f"Read(/{link.absolute()}/**)" in rules
    assert f"Read(/{real.resolve()}/**)" in rules


def test_path_forms_collapse_when_the_path_is_already_resolved(tmp_path: Path) -> None:
    resolved = tmp_path.resolve() / "plain"
    assert _path_forms(resolved) == [str(resolved)]


def test_root_path_containing_a_space_stays_a_single_argument(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spaced = tmp_path / "my state" / "state"
    monkeypatch.setenv("ALAN_STATE_ROOT", str(spaced))
    cmd = argv_for(monkeypatch, tools=["Read"])
    assert f"Read(/{spaced / 'control_token'})" in cmd
    assert_every_rule_is_its_own_argument(cmd)


@pytest.mark.parametrize(("team_name", "role_id"), fleet_role_ids())
def test_every_fleet_role_argv_carries_each_credential_rule_as_its_own_argument(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    team_name: str,
    role_id: str,
) -> None:
    cfg = default_engine_config()
    ws = stamped_workspace(tmp_path, cfg)
    overrides = role_overrides(team_name, role_id, ws, cfg)
    cmd = argv_for(
        monkeypatch,
        tools=overrides.allowed_tools,
        add_dirs=overrides.add_dirs,
        mode=overrides.permission_mode or "auto",
    )
    assert_every_rule_is_its_own_argument(cmd)


@pytest.mark.parametrize("team_name", _IMPLEMENTATION_TEAMS)
def test_implementation_roles_carry_each_credential_rule_under_accept_edits(
    monkeypatch: pytest.MonkeyPatch, team_name: str
) -> None:
    tools = list(render_allowed_tools(default_engine_config(), team_name))
    cmd = argv_for(monkeypatch, tools=tools, mode=ACCEPT_EDITS_MODE)
    assert_every_rule_is_its_own_argument(cmd)
    assert "Write Edit NotebookEdit" not in cmd


@pytest.mark.parametrize("mode", _MODES)
def test_investigator_argv_carries_each_credential_rule_in_every_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    tools = list(investigator._INVESTIGATOR_ALLOWED_TOOLS)  # noqa: SLF001
    cmd = argv_for(monkeypatch, tools=tools, add_dirs=[str(tmp_path)], mode=mode)
    assert_every_rule_is_its_own_argument(cmd)


def test_investigator_generation_spawns_with_the_credential_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[list[str]] = []
    _patch_popen(monkeypatch, stdout_lines=_lines(_result_event("ok")), capture_cmds=captured)
    investigator._generate_rca_text(tmp_path, "fp", {}, frozenset())  # noqa: SLF001
    assert_every_rule_is_its_own_argument(captured[0])


@pytest.mark.parametrize("chat_kind", ["build", "explore"])
@pytest.mark.parametrize("resume", [False, True])
def test_chat_argv_carries_each_credential_rule(
    monkeypatch: pytest.MonkeyPatch,
    chat_kind: str,
    resume: bool,
) -> None:
    prompts = {
        "build": claude_cli.format_build_system_prompt(),
        "explore": claude_cli.format_explore_system_prompt("architecture"),
    }
    captured: list[list[str]] = []
    _patch_popen(monkeypatch, stdout_lines=_lines(_result_event("ok")), capture_cmds=captured)
    run_chat_turn(
        user_text="x", session_id=_SESSION, resume=resume, system_prompt=prompts[chat_kind]
    )
    cmd = captured[0]
    assert_every_rule_is_its_own_argument(cmd)
    assert cmd[cmd.index("--disallowedTools") + 1] == "Write Edit NotebookEdit"


def test_chat_argv_drops_the_general_exec_grants_from_a_custom_tool_list(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[list[str]] = []
    _patch_popen(monkeypatch, stdout_lines=_lines(_result_event("ok")), capture_cmds=captured)
    run_chat_turn(
        user_text="x",
        session_id=_SESSION,
        resume=False,
        system_prompt="p",
        allowed_tools=["Read", *GENERAL_EXEC_GRANTS],
    )
    assert allowed_in(captured[0]) == ["Read"]


@pytest.mark.parametrize("mode", _MODES)
def test_no_tools_invocation_keeps_exactly_the_deny_everything_argument(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    captured: list[list[str]] = []
    _patch_popen(monkeypatch, stdout_lines=_lines(_result_event("ok")), capture_cmds=captured)
    run_agent_streaming("sys", "user", deny_all_tools=True, permission_mode=mode)
    cmd = captured[0]
    assert cmd[cmd.index("--disallowedTools") :] == ["--disallowedTools", "*", "--verbose"]
    assert cmd.count("--disallowedTools") == 1
    assert not any(rule in cmd for rule in credential_deny_rules())


@pytest.mark.parametrize("mode", _MODES)
def test_no_tools_build_cmd_has_no_credential_rules(
    mode: str
) -> None:
    cmd = claude_cli._build_cmd(  # noqa: SLF001
        "sys", output_format="json", max_turns=1, deny_all_tools=True, permission_mode=mode
    )
    assert cmd[cmd.index("--disallowedTools") :] == ["--disallowedTools", "*"]
    assert disallowed_tools_args(deny_all_tools=True, permission_mode=mode) == [
        "--disallowedTools",
        "*",
    ]


@pytest.mark.parametrize(("team_name", "role_id"), fleet_role_ids())
def test_non_implementation_roles_never_carry_the_general_exec_grants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    team_name: str,
    role_id: str,
) -> None:
    cfg = default_engine_config()
    assert all(grant in cfg.base_tools for grant in GENERAL_EXEC_GRANTS)
    ws = stamped_workspace(tmp_path, cfg)
    overrides = role_overrides(team_name, role_id, ws, cfg)
    cmd = argv_for(monkeypatch, tools=overrides.allowed_tools)
    assert not set(GENERAL_EXEC_GRANTS) & set(cmd)


def test_general_exec_grants_are_dropped_even_when_a_stored_record_grants_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = EngineConfig(
        repo=RepoIdentity(owner="acme", repo="widget"),
        base_tools=["Read", *GENERAL_EXEC_GRANTS],
        team_tools={"architecture": list(GENERAL_EXEC_GRANTS)},
        posture=Posture.autonomous,
    )
    ws = stamped_workspace(tmp_path, cfg)
    overrides = role_overrides("architecture", "structure_mapper", ws, cfg)
    cmd = argv_for(monkeypatch, tools=overrides.allowed_tools)
    assert not set(GENERAL_EXEC_GRANTS) & set(allowed_in(cmd))
    assert "Read" in allowed_in(cmd)


@pytest.mark.parametrize("team_name", _IMPLEMENTATION_TEAMS)
def test_implementation_roles_keep_their_stored_grants_exactly(
    monkeypatch: pytest.MonkeyPatch, team_name: str
) -> None:
    stored = list(render_allowed_tools(default_engine_config(), team_name))
    cmd = argv_for(monkeypatch, tools=stored, mode=ACCEPT_EDITS_MODE)
    assert allowed_in(cmd) == stored
    assert set(GENERAL_EXEC_GRANTS) <= set(allowed_in(cmd))
    assert "Bash(uv:*)" in allowed_in(cmd)


def test_accept_edits_literal_is_confined_to_the_implementation_roles() -> None:
    hits = {
        path.relative_to(_REPO_ROOT).as_posix()
        for path in (_REPO_ROOT / "alan").rglob("*.py")
        if "web" not in path.relative_to(_REPO_ROOT / "alan").parts
        and f'"{ACCEPT_EDITS_MODE}"' in path.read_text(encoding="utf-8")
    }
    assert hits == {
        "alan/claude_cli_grants.py",
        "alan/teams/implementation.py",
        "alan/teams/implementation_fix.py",
    }


def test_grant_args_default_mode_is_the_narrowing_mode() -> None:
    tools = ["Read", *GENERAL_EXEC_GRANTS]
    args = grant_args(allowed_tools=tools, add_dirs=None, deny_all_tools=False)
    assert args == ["--allowedTools", "Read"]


def test_grant_args_omit_the_flag_when_narrowing_leaves_nothing() -> None:
    args = grant_args(
        allowed_tools=list(GENERAL_EXEC_GRANTS), add_dirs=["/work"], deny_all_tools=False
    )
    assert args == ["--add-dir", "/work"]


def test_grant_args_keep_every_grant_under_accept_edits() -> None:
    tools = ["Read", *GENERAL_EXEC_GRANTS]
    args = grant_args(
        allowed_tools=tools, add_dirs=None, deny_all_tools=False, permission_mode=ACCEPT_EDITS_MODE
    )
    assert args == ["--allowedTools", *tools]


def test_grant_args_treat_missing_tools_as_empty() -> None:
    assert grant_args(allowed_tools=None, add_dirs=None, deny_all_tools=False) == []


def test_narrowed_tools_is_a_literal_three_grant_filter() -> None:
    assert GENERAL_EXEC_GRANTS == ("Bash(uv run:*)", "Bash(npx:*)", "Bash(npm --prefix:*)")
    kept = ["Read", "Bash(npm run:*)", "Bash(uv:*)", "Bash(uv run pytest:*)"]
    assert narrowed_tools([*GENERAL_EXEC_GRANTS, *kept]) == kept


def test_gate_tool_grants_derive_both_uv_run_forms_and_the_npm_prefix_form() -> None:
    grants = gate_tool_grants(default_engine_config().gate_commands)
    assert "Bash(uv run pytest:*)" in grants
    assert "Bash(uv run --no-active pytest:*)" in grants
    assert "Bash(uv run --no-active pip-audit:*)" in grants
    assert "Bash(npm --prefix alan/web run:*)" in grants
    assert not set(GENERAL_EXEC_GRANTS) & set(grants)


def test_gate_tool_grants_accept_uv_run_without_no_active() -> None:
    assert gate_tool_grants([["uv", "run", "pytest", "-q"]]) == [
        "Bash(uv run pytest:*)",
        "Bash(uv run --no-active pytest:*)",
    ]


@pytest.mark.parametrize("interpreter", ["python", "python3", "python3.11", "node", "bash", "env"])
def test_gate_tool_grants_never_derive_a_per_tool_grant_for_an_interpreter(
    interpreter: str,
) -> None:
    grants = gate_tool_grants([["uv", "run", "--no-active", interpreter, "scripts/check.py"]])
    assert grants == [f"Bash(uv run --no-active {interpreter} scripts/check.py:*)"]


def test_gate_tool_grants_drop_a_bare_interpreter_command() -> None:
    assert gate_tool_grants([["uv", "run", "--no-active", "python"]]) == []
    assert gate_tool_grants([["bash"]]) == []


def test_gate_tool_grants_drop_bare_uv_run_forms() -> None:
    assert gate_tool_grants([["uv", "run"], ["uv", "run", "--frozen"]]) == []


def test_gate_tool_grants_fall_back_to_the_full_command_for_an_optioned_uv_run() -> None:
    assert gate_tool_grants([["uv", "run", "--frozen", "pytest"]]) == [
        "Bash(uv run --frozen pytest:*)"
    ]


def test_gate_tool_grants_take_the_prefix_dir_from_an_npm_run_gate() -> None:
    assert gate_tool_grants([["npm", "--prefix", "web", "run", "lint"]]) == [
        "Bash(npm --prefix web run:*)"
    ]


@pytest.mark.parametrize(
    "command",
    [
        ["npm", "--prefix", "web"],
        ["npm", "--prefix", "web", "ci"],
        ["npm", "--prefix", "we b", "run", "x"],
        ["npm", "--prefixes", "web", "run", "x"],
        ["npm", "--prefix", "web", "exec", "x"],
    ],
)
def test_gate_tool_grants_never_turn_a_non_run_npm_gate_into_a_run_grant(
    command: list[str],
) -> None:
    assert "Bash(npm --prefix web run:*)" not in gate_tool_grants([command])
    assert not set(GENERAL_EXEC_GRANTS) & set(gate_tool_grants([command]))


def test_gate_tool_grants_use_the_full_command_for_other_gates() -> None:
    gates = [["bash", "scripts/gate.sh"], ["dotnet", "format", "--verify-no-changes"]]
    assert gate_tool_grants(gates) == [
        "Bash(bash scripts/gate.sh:*)",
        "Bash(dotnet format --verify-no-changes:*)",
    ]


@pytest.mark.parametrize(
    "command",
    [[], ["make", "check;rm"], ["make", "$(x)"], ["make", "a b"], ["make", "(x)"], ["make", "*"]],
)
def test_gate_tool_grants_skip_commands_that_cannot_be_expressed_safely(
    command: list[str],
) -> None:
    assert gate_tool_grants([command]) == []


def test_gate_tool_grants_of_no_gates_is_empty() -> None:
    assert gate_tool_grants([]) == []


def test_with_gate_reachability_appends_after_the_stored_tools_without_duplicates() -> None:
    tools = ["Read", "Bash(uv run pytest:*)", *GENERAL_EXEC_GRANTS]
    granted = with_gate_reachability(tools, [["uv", "run", "--no-active", "pytest"]])
    assert granted[: len(tools)] == tools
    assert granted.count("Bash(uv run pytest:*)") == 1
    assert set(PROMPT_INSTRUCTED_GRANTS) <= set(granted)


def test_prompt_instructed_grants_are_a_fixed_set() -> None:
    assert PROMPT_INSTRUCTED_GRANTS == (
        "Bash(uv run lizard:*)",
        "Bash(uv run xenon:*)",
        "Bash(uv run vulture:*)",
        "Bash(uv run pytest:*)",
        "Bash(uv run --no-active lizard:*)",
        "Bash(uv run --no-active xenon:*)",
        "Bash(uv run --no-active vulture:*)",
        "Bash(uv run --no-active pytest:*)",
        "Bash(npm --prefix alan/web run:*)",
        "Bash(npm --prefix alan/web ci:*)",
    )


def test_warmup_commands_take_no_part_in_the_derived_grants() -> None:
    cfg = default_engine_config()
    granted = with_gate_reachability(cfg.base_tools, cfg.gate_commands)
    assert cfg.warmup_commands == [
        ["uv", "sync", "--locked", "--extra", "dev"],
        ["npm", "--prefix", "alan/web", "ci"],
    ]
    assert not any(grant.startswith("Bash(uv sync") for grant in granted)


def _engine_record_bytes(engines_root: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(engines_root.glob("*")) if p.is_file()}


def test_building_the_arguments_never_rewrites_a_stored_engine_record(
    synthetic_roots: SyntheticRoots, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import engine_registry_store  # noqa: PLC0415

    cfg = default_engine_config()
    engine_registry_store.write_connected("acme/widget", cfg.model_dump(mode="json"))
    engines_root = synthetic_roots.alan_home / "engines"
    before = _engine_record_bytes(engines_root)
    assert before
    ws = stamped_workspace(tmp_path, cfg)
    overrides = role_overrides("architecture", "structure_mapper", ws, cfg)
    argv_for(monkeypatch, tools=overrides.allowed_tools)
    assert _engine_record_bytes(engines_root) == before
    assert cfg.base_tools.count("Bash(uv run:*)") == 1


def _team_call_sites(path: Path) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "WorkerOverrides"
    ]


def _is_reachability_wrapped(call: ast.Call) -> bool:
    for keyword in call.keywords:
        if keyword.arg == "allowed_tools":
            value = keyword.value
            return (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "with_gate_reachability"
            )
    return False


_UNWRAPPED_BY_DESIGN = {
    "_base.py",
    "_pr_handover.py",
    "implementation.py",
    "implementation_fix.py",
}


def test_every_non_implementation_worker_overrides_call_site_wraps_gate_reachability() -> None:
    unwrapped: list[str] = []
    for path in sorted((_REPO_ROOT / "alan" / "teams").glob("*.py")):
        if path.name in _UNWRAPPED_BY_DESIGN:
            continue
        unwrapped.extend(
            f"{path.name}:{call.lineno}"
            for call in _team_call_sites(path)
            if not _is_reachability_wrapped(call)
        )
    assert unwrapped == []


def test_the_structural_scan_finds_the_worker_overrides_call_sites() -> None:
    found = {
        path.name
        for path in (_REPO_ROOT / "alan" / "teams").glob("*.py")
        if _team_call_sites(path)
    }
    assert {
        "_orchestrated.py",
        "_static_pipeline.py",
        "maintenance.py",
        "verification.py",
        "brief_refine.py",
    } <= found
    assert _UNWRAPPED_BY_DESIGN <= found
