from __future__ import annotations

from pathlib import Path

from alan.engine_config import _DEFAULT as _ENGINE_DEFAULT  # pyright: ignore[reportPrivateUsage]
from alan.engine_config import EngineConfig, RepoIdentity
from alan.teams import implementation as impl_mod
from alan.teams import implementation_fix as fix_mod
from alan.teams.implementation import _SYSTEM_PROMPT as _IMPL_SYSTEM_PROMPT
from alan.teams.implementation_fix import _SYSTEM_PROMPT as _FIX_SYSTEM_PROMPT

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BOTH_TEAM_PROMPTS = (_IMPL_SYSTEM_PROMPT, _FIX_SYSTEM_PROMPT)
_ALAN_ONLY_TOKENS = ("uv run", "pytest", "npm", "pytest_full.log", "fail_under")


def _cargo_cfg(*gates: str) -> EngineConfig:
    return EngineConfig(
        repo=RepoIdentity(owner="rustco", repo="cargo-service"),
        gate_commands=[gate.split() for gate in gates],
    )


def test_environment_block_caps_the_full_suite_at_one_run_with_a_hard_cap_of_two() -> None:
    block = impl_mod.ENVIRONMENT_BLOCK
    assert "TEST-RUN BUDGET" in block
    assert "run the full suite AT MOST ONCE, as the LAST gate" in block
    assert "two full runs per session is the hard cap" in block
    assert "never by running it again" in block


def test_environment_block_captures_the_full_run_inside_the_worktree_not_tmp() -> None:
    block = impl_mod.ENVIRONMENT_BLOCK
    assert (
        "`uv run --no-active pytest > pytest_full.log 2>&1; rc=$?; tail -40 pytest_full.log; "
        "(exit $rc)`"
    ) in block
    assert "keeps the run's own exit status" in block
    assert "`grep -n 'FAILED\\|ERROR\\|TOTAL' pytest_full.log`" in block
    assert "gitignored file in this worktree" in block
    assert "/tmp/pytest" not in block  # noqa: S108


def test_environment_block_lists_the_captured_form_as_the_only_full_suite_gate() -> None:
    block = impl_mod.ENVIRONMENT_BLOCK
    assert "in exactly this captured form and no other" in block
    assert block.count("> pytest_full.log 2>&1") == 1
    assert "`uv run --no-active pytest` (bare, NO path args" not in block


def test_environment_block_iterates_on_targeted_files_and_keeps_the_bare_run_as_proof() -> None:
    block = impl_mod.ENVIRONMENT_BLOCK
    assert "`uv run --no-active pytest tests/<file>.py --no-cov -n 0` for each touched" in block
    assert "subsets are for iteration only" in block
    assert "NO path args and NO `--no-cov`" in block
    assert "CI gates on this full run" in block


def test_default_environment_block_renders_every_placeholder() -> None:
    rendered = impl_mod.render_environment_block(_ENGINE_DEFAULT)
    assert rendered == impl_mod.ENVIRONMENT_BLOCK
    assert "{" not in rendered
    assert "}" not in rendered


def test_success_clause_keeps_the_full_run_as_the_only_proof_and_runs_it_once_in_both_teams(
) -> None:
    for prompt in _BOTH_TEAM_PROMPTS:
        assert "Only the full run both passes and proves coverage." in prompt
        assert "Run it once, at the end, captured to `pytest_full.log`" in prompt
        assert "a second full run is allowed only after fixing a red first one" in prompt
        assert "cap two per session" in prompt
        assert "fail_under=100" in prompt


def test_discipline_reruns_the_affected_test_file_never_the_suite_in_both_teams() -> None:
    for prompt in _BOTH_TEAM_PROMPTS:
        assert "6. If a test fails: fix and re-run the affected test FILE (`--no-cov -n 0`)" in (
            prompt
        )
        assert "— never the full suite. Don't single-step." in prompt


def test_generic_environment_block_states_run_once_without_alan_commands() -> None:
    block = impl_mod.render_environment_block(_cargo_cfg("cargo test", "cargo clippy"))
    assert "- Run each gate once, at the end" in block
    assert "narrowest targeted command the tool offers" in block
    assert "gitignored file inside this checkout" in block
    assert "the only reason to run a gate again is that you fixed what made it red" in block
    assert "never to re-read its output" in block
    for token in _ALAN_ONLY_TOKENS:
        assert token not in block


def test_generic_environment_block_without_gates_has_no_run_once_line() -> None:
    block = impl_mod.render_environment_block(EngineConfig(repo=RepoIdentity(owner="r", repo="b")))
    assert "No local quality gates are configured" in block
    assert "Run each gate once" not in block


def test_generic_success_block_states_run_once_without_alan_commands() -> None:
    block = impl_mod.generic_success_block(_cargo_cfg("cargo test"))
    assert "Run each gate once, at the end" in block
    assert "a repeat run is for a fix, never for re-reading output" in block
    assert "gitignored file inside this checkout" in block
    for token in _ALAN_ONLY_TOKENS:
        assert token not in block


def test_generic_success_block_without_gates_has_no_run_once_sentence() -> None:
    block = impl_mod.generic_success_block(EngineConfig(repo=RepoIdentity(owner="r", repo="b")))
    assert "declares no local quality gates" in block
    assert "Run each gate once" not in block


def test_generic_fix_prompt_carries_the_run_once_policy_without_alan_commands(
    tmp_path: Path,
) -> None:
    prompt = fix_mod.render_system_prompt(_cargo_cfg("cargo test"), tmp_path)
    assert "a repeat run is for a fix, never for re-reading output" in prompt
    assert "the only reason to run a gate again is that you fixed what made it red" in prompt
    assert "pytest_full.log" not in prompt


def test_gitignore_covers_the_captured_pytest_log() -> None:
    lines = (_REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "*.log" in lines
