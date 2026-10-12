from __future__ import annotations

from pathlib import Path

import yaml

from alan.engine_config import (
    _DEFAULT as _ENGINE_DEFAULT,  # pyright: ignore[reportPrivateUsage]
)
from alan.engine_config import EngineConfig, Posture, PromptOverride, RepoIdentity, RoleConfig
from alan.prompt_templates import (
    MERGE_GATE_CLAUSE,
    VERIFIER_ANTI_SELF_APPROVAL_CLAUSE,
    override_key,
)
from alan.teams import _declared_blocker, _pr_body
from alan.teams import implementation as impl_mod
from alan.teams import implementation_fix as fix_mod
from alan.teams import verification as verif_mod
from alan.teams._base import render_allowed_tools

_FIXTURES = Path(__file__).parent / "fixtures"


def _nonpython_cfg() -> EngineConfig:
    raw = yaml.safe_load((_FIXTURES / "engine_config_forge_nonpython.yaml").read_text())
    return EngineConfig.model_validate(raw)


def _no_gates_cfg() -> EngineConfig:
    return EngineConfig(repo=RepoIdentity(owner="empty-co", repo="empty-repo"))


def test_default_engine_prompt_env_baseline(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("some other repo's guidance\n")

    assert impl_mod.render_environment_block(_ENGINE_DEFAULT) == impl_mod.ENVIRONMENT_BLOCK
    assert impl_mod.render_system_prompt(_ENGINE_DEFAULT, tmp_path) == (
        impl_mod._SYSTEM_PROMPT + "\n\n" + _pr_body.PR_BODY_BLOCK_INSTRUCTIONS  # noqa: SLF001
    )
    assert render_allowed_tools(_ENGINE_DEFAULT, "implementation") == impl_mod.ALLOWED_TOOLS
    assert "REPOSITORY GUIDANCE" not in impl_mod.render_system_prompt(_ENGINE_DEFAULT, tmp_path)

    assert fix_mod.render_system_prompt(_ENGINE_DEFAULT, tmp_path) == (
        fix_mod._SYSTEM_PROMPT + "\n\n" + _pr_body.PR_BODY_BLOCK_INSTRUCTIONS  # noqa: SLF001
    )
    assert "REPOSITORY GUIDANCE" not in fix_mod.render_system_prompt(_ENGINE_DEFAULT, tmp_path)


def test_default_engine_allowed_tools_matches_team_scoped_default(tmp_path: Path) -> None:
    del tmp_path
    assert render_allowed_tools(_ENGINE_DEFAULT, "implementation_fix") == (
        impl_mod.ALLOWED_TOOLS
    )


def test_generic_engine_env_no_crash() -> None:
    cfg = _nonpython_cfg()

    env_block = impl_mod.render_environment_block(cfg)

    assert "cargo test" in env_block
    assert "cargo clippy" in env_block
    assert "cargo fetch" in env_block
    assert "uv run" not in env_block
    assert "pytest" not in env_block
    assert "npm" not in env_block


def test_generic_engine_system_prompt_no_crash_and_no_default_fallback() -> None:
    cfg = _nonpython_cfg()

    prompt = impl_mod.render_system_prompt(cfg, Path("/nonexistent/worktree"))

    assert prompt != impl_mod._SYSTEM_PROMPT  # noqa: SLF001
    assert "rustco/cargo-service" in prompt
    assert "cargo test" in prompt
    assert "fail_under=100" not in prompt
    assert "uv run --no-active pytest" not in prompt


def test_generic_engine_implementation_prompt_carries_declared_blocker_guidance() -> None:
    cfg = _nonpython_cfg()

    prompt = impl_mod.render_system_prompt(cfg, Path("/nonexistent/worktree"))

    assert _declared_blocker.BLOCKING_MARK in prompt


def test_generic_engine_fix_system_prompt_no_crash_and_no_default_fallback() -> None:
    cfg = _nonpython_cfg()

    prompt = fix_mod.render_system_prompt(cfg, Path("/nonexistent/worktree"))

    assert prompt != fix_mod._SYSTEM_PROMPT  # noqa: SLF001
    assert "cargo test" in prompt
    assert "fail_under=100" not in prompt


def test_generic_engine_no_gates_no_warmup_renders_without_crash() -> None:
    cfg = _no_gates_cfg()

    env_block = impl_mod.render_environment_block(cfg)

    assert "No warmup/bootstrap commands are configured" in env_block
    assert "No local quality gates are configured" in env_block

    prompt = impl_mod.render_system_prompt(cfg, Path("/nonexistent/worktree"))
    assert "This engine declares no local quality gates" in prompt


def test_generic_engine_allowed_tools_falls_back_when_undeclared() -> None:
    cfg = _nonpython_cfg()

    tools = render_allowed_tools(cfg, "implementation")

    assert "Edit" in tools
    assert "Read" in tools


def test_generic_engine_allowed_tools_falls_back_to_web_grant_for_research() -> None:
    cfg = _no_gates_cfg()

    tools = render_allowed_tools(cfg, "research")

    assert "WebSearch" in tools
    assert "WebFetch" in tools
    assert "Edit" not in tools


def test_generic_engine_allowed_tools_uses_declared_team_tools() -> None:
    cfg = EngineConfig(
        repo=RepoIdentity(owner="acme", repo="widget"),
        base_tools=["Read"],
        team_tools={"implementation": ["Edit"], "implementation_fix": ["Write"]},
    )

    assert render_allowed_tools(cfg, "implementation") == ("Read", "Edit")
    assert render_allowed_tools(cfg, "implementation_fix") == ("Read", "Write")


def test_generic_engine_allowed_tools_uses_declared_web_grant_for_research() -> None:
    cfg = EngineConfig(
        repo=RepoIdentity(owner="acme", repo="widget"),
        base_tools=["Read"],
        team_tools={"research": ["WebSearch"]},
    )

    assert render_allowed_tools(cfg, "research") == ("Read", "WebSearch")


def test_generic_engine_allowed_tools_no_extras_for_undeclared_team_with_partial_config() -> None:
    cfg = EngineConfig(
        repo=RepoIdentity(owner="acme", repo="widget"),
        base_tools=["Read"],
        team_tools={"implementation": ["Edit"]},
    )

    assert render_allowed_tools(cfg, "implementation_fix") == ("Read",)


def test_repo_guidance_reference_graceful(tmp_path: Path) -> None:
    cfg = _nonpython_cfg()

    without_guidance = impl_mod.render_system_prompt(cfg, tmp_path)
    assert "@CLAUDE.md" not in without_guidance
    assert "REPOSITORY GUIDANCE" not in without_guidance

    (tmp_path / "CLAUDE.md").write_text("cargo-service contributor guide\n")
    with_guidance = impl_mod.render_system_prompt(cfg, tmp_path)
    assert "@CLAUDE.md" in with_guidance
    assert "REPOSITORY GUIDANCE" in with_guidance


def test_repo_guidance_reference_graceful_fix_team(tmp_path: Path) -> None:
    cfg = _nonpython_cfg()

    without_guidance = fix_mod.render_system_prompt(cfg, tmp_path)
    assert "@CLAUDE.md" not in without_guidance

    (tmp_path / "CLAUDE.md").write_text("cargo-service contributor guide\n")
    with_guidance = fix_mod.render_system_prompt(cfg, tmp_path)
    assert "@CLAUDE.md" in with_guidance


def test_repo_guidance_block_directly(tmp_path: Path) -> None:
    assert impl_mod.repo_guidance_block(tmp_path) == ""
    (tmp_path / "CLAUDE.md").write_text("guidance\n")
    assert "@CLAUDE.md" in impl_mod.repo_guidance_block(tmp_path)


def test_roles_for_config_default_matches_module_roles() -> None:
    roles = verif_mod.roles_for_config(_ENGINE_DEFAULT)
    assert [r.model for r in roles] == [r.model for r in verif_mod.ROLES]
    assert [r.name for r in roles] == [r.name for r in verif_mod.ROLES]


def test_roles_for_config_uses_engine_declared_models() -> None:
    cfg = EngineConfig(
        repo=RepoIdentity(owner="acme", repo="widget"),
        roles={
            "diff_reviewer": RoleConfig(model="haiku"),
            "judge": RoleConfig(model="sonnet"),
        },
    )
    roles = verif_mod.roles_for_config(cfg)
    by_name = {r.name: r.model for r in roles}
    assert by_name["diff_reviewer"] == "haiku"
    assert by_name["scenario_runner"] == "haiku"
    assert by_name["judge"] == "sonnet"


def test_roles_for_config_falls_back_to_default_role_when_undeclared() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="acme", repo="widget"))
    roles = verif_mod.roles_for_config(cfg)
    by_name = {r.name: r.model for r in roles}
    assert by_name["judge"] == _ENGINE_DEFAULT.roles["judge"].model
    assert by_name["diff_reviewer"] == _ENGINE_DEFAULT.roles["diff_reviewer"].model


_OVERRIDE_TEXT = "custom scaffolding {{ENVIRONMENT}} {{SUCCESS}} {{REPO_GUIDANCE}}"


def _with_impl_override(cfg: EngineConfig, *, team: str) -> EngineConfig:
    return cfg.model_copy(
        update={
            "prompt_overrides": {
                override_key(team, "implementer"): PromptOverride(
                    text=_OVERRIDE_TEXT, base_fingerprint="fingerprint"
                )
            }
        }
    )


def test_implementation_override_splices_into_default_engine_prompt(tmp_path: Path) -> None:
    cfg = _with_impl_override(_ENGINE_DEFAULT, team="implementation")
    rendered = impl_mod.render_system_prompt(cfg, tmp_path)
    assert "uv run --no-active pytest" in rendered
    assert "{{ENVIRONMENT}}" not in rendered
    assert "{{SUCCESS}}" not in rendered
    assert "custom scaffolding" in rendered
    assert impl_mod._SYSTEM_PROMPT not in rendered  # noqa: SLF001


def test_implementation_override_splices_into_generic_engine_prompt(tmp_path: Path) -> None:
    cfg = _with_impl_override(_nonpython_cfg(), team="implementation")
    rendered = impl_mod.render_system_prompt(cfg, tmp_path)
    assert "cargo test" in rendered
    assert "custom scaffolding" in rendered
    assert "{{ENVIRONMENT}}" not in rendered


def test_implementation_override_splices_in_repo_guidance_when_claude_md_present(
    tmp_path: Path,
) -> None:
    (tmp_path / "CLAUDE.md").write_text("guidance\n")
    cfg = _with_impl_override(_nonpython_cfg(), team="implementation")
    rendered = impl_mod.render_system_prompt(cfg, tmp_path)
    assert "@CLAUDE.md" in rendered


def test_implementation_override_no_gates_engine_renders_without_crash(tmp_path: Path) -> None:
    cfg = _with_impl_override(_no_gates_cfg(), team="implementation")
    rendered = impl_mod.render_system_prompt(cfg, tmp_path)
    assert "This engine declares no local quality gates" in rendered
    assert "custom scaffolding" in rendered


def test_implementation_fix_override_splices_into_default_engine_prompt(tmp_path: Path) -> None:
    cfg = _with_impl_override(_ENGINE_DEFAULT, team="implementation_fix")
    rendered = fix_mod.render_system_prompt(cfg, tmp_path)
    assert "custom scaffolding" in rendered
    assert fix_mod._SYSTEM_PROMPT not in rendered  # noqa: SLF001


def test_implementation_fix_override_splices_into_generic_engine_prompt(tmp_path: Path) -> None:
    cfg = _with_impl_override(_nonpython_cfg(), team="implementation_fix")
    rendered = fix_mod.render_system_prompt(cfg, tmp_path)
    assert "cargo test" in rendered
    assert "custom scaffolding" in rendered


def test_override_on_one_role_never_leaks_into_an_unoverridden_sibling_role(
    tmp_path: Path,
) -> None:
    cfg = _with_impl_override(_nonpython_cfg(), team="implementation")
    fix_rendered = fix_mod.render_system_prompt(cfg, tmp_path)
    assert "custom scaffolding" not in fix_rendered
    roles = verif_mod.roles_for_config(cfg)
    judge_role = next(r for r in roles if r.name == "judge")
    assert judge_role.system_prompt == verif_mod._JUDGE_PROMPT  # noqa: SLF001
    assert "custom scaffolding" not in judge_role.system_prompt


def test_verification_judge_override_used_verbatim_with_safety_clause_appended() -> None:
    override_text = "custom judge rules. VERDICT: line here. DIFF_FINDINGS_CRITICAL: line here."
    cfg = _ENGINE_DEFAULT.model_copy(
        update={
            "prompt_overrides": {
                override_key("verification", "judge"): PromptOverride(
                    text=override_text, base_fingerprint="fingerprint"
                )
            }
        }
    )
    roles = verif_mod.roles_for_config(cfg)
    judge_role = next(r for r in roles if r.name == "judge")
    assert override_text in judge_role.system_prompt
    assert VERIFIER_ANTI_SELF_APPROVAL_CLAUSE in judge_role.system_prompt
    assert MERGE_GATE_CLAUSE in judge_role.system_prompt
    diff_reviewer_role = next(r for r in roles if r.name == "diff_reviewer")
    assert diff_reviewer_role.system_prompt == verif_mod._DIFF_REVIEWER_PROMPT  # noqa: SLF001


def test_verification_judge_override_at_propose_posture_has_no_merge_gate_clause() -> None:
    override_text = "custom judge rules. VERDICT: line here. DIFF_FINDINGS_CRITICAL: line here."
    cfg = EngineConfig(
        repo=RepoIdentity(owner="acme", repo="widget"),
        posture=Posture.propose,
        prompt_overrides={
            override_key("verification", "judge"): PromptOverride(
                text=override_text, base_fingerprint="fingerprint"
            )
        },
    )
    roles = verif_mod.roles_for_config(cfg)
    judge_role = next(r for r in roles if r.name == "judge")
    assert VERIFIER_ANTI_SELF_APPROVAL_CLAUSE in judge_role.system_prompt
    assert MERGE_GATE_CLAUSE not in judge_role.system_prompt
