from __future__ import annotations

from pathlib import Path

from alan.engine_config import (
    _DEFAULT as _ENGINE_DEFAULT,  # pyright: ignore[reportPrivateUsage]
)
from alan.engine_config import EngineConfig, RepoIdentity
from alan.teams import implementation as impl_mod
from alan.teams import implementation_fix as fix_mod

_GENERIC_CFG = EngineConfig(
    repo=RepoIdentity(owner="rustco", repo="cargo-service"),
    gate_commands=[["cargo", "test"]],
    warmup_commands=[["cargo", "fetch"]],
)

_FALSE_WORKTREE_CLAIM = "fresh git worktree"
_FALSE_WORKTREE_PATH = "<repo_root>/.worktrees/teams-<task_id>-<inv_id>"
_OLD_PROBE_BAN = "do NOT probe the environment"


def test_default_env_block_does_not_claim_a_fixed_per_invocation_worktree() -> None:
    block = impl_mod.render_environment_block(_ENGINE_DEFAULT)
    assert _FALSE_WORKTREE_CLAIM not in block
    assert _FALSE_WORKTREE_PATH not in block
    assert _OLD_PROBE_BAN not in block


def test_default_env_block_describes_leased_checkout() -> None:
    block = impl_mod.render_environment_block(_ENGINE_DEFAULT)
    assert "leased checkout" in block
    assert "pooled" in block
    assert "current working directory is authoritative" in block
    assert "NO .venv" in block


def test_default_env_block_allows_bounded_probing() -> None:
    block = impl_mod.render_environment_block(_ENGINE_DEFAULT)
    assert "`pwd` and `ls` are cheap and safe to run first" in block


def test_generic_env_block_does_not_claim_a_fixed_per_invocation_worktree() -> None:
    block = impl_mod.render_environment_block(_GENERIC_CFG)
    assert _FALSE_WORKTREE_CLAIM not in block
    assert _FALSE_WORKTREE_PATH not in block
    assert _OLD_PROBE_BAN not in block
    assert "fresh checkout of" not in block


def test_generic_env_block_describes_leased_checkout_for_named_repo() -> None:
    block = impl_mod.render_environment_block(_GENERIC_CFG)
    assert "rustco/cargo-service" in block
    assert "leased checkout" in block
    assert "pooled" in block
    assert "current working directory is authoritative" in block
    assert "`pwd` and `ls` are cheap and safe to run first" in block


def test_implementation_fix_default_system_prompt_inherits_leased_checkout_text(
    tmp_path: Path,
) -> None:
    prompt = fix_mod.render_system_prompt(_ENGINE_DEFAULT, tmp_path)
    assert _FALSE_WORKTREE_CLAIM not in prompt
    assert _FALSE_WORKTREE_PATH not in prompt
    assert "leased checkout" in prompt


def test_implementation_fix_generic_system_prompt_inherits_leased_checkout_text(
    tmp_path: Path,
) -> None:
    prompt = fix_mod.render_system_prompt(_GENERIC_CFG, tmp_path)
    assert _FALSE_WORKTREE_CLAIM not in prompt
    assert _FALSE_WORKTREE_PATH not in prompt
    assert "leased checkout" in prompt
    assert "rustco/cargo-service" in prompt


def test_implementation_default_system_prompt_inherits_leased_checkout_text() -> None:
    prompt = impl_mod.render_system_prompt(_ENGINE_DEFAULT, Path("/nonexistent"))
    assert _FALSE_WORKTREE_CLAIM not in prompt
    assert _FALSE_WORKTREE_PATH not in prompt
    assert "leased checkout" in prompt


def test_implementation_generic_system_prompt_inherits_leased_checkout_text(
    tmp_path: Path,
) -> None:
    prompt = impl_mod.render_system_prompt(_GENERIC_CFG, tmp_path)
    assert _FALSE_WORKTREE_CLAIM not in prompt
    assert _FALSE_WORKTREE_PATH not in prompt
    assert "leased checkout" in prompt
    assert "rustco/cargo-service" in prompt
