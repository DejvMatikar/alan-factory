from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from alan import claude_cli, driver_loop_cmd, fleet_load
from alan import default_profile as default_profile_mod
from alan.control_plane import CONTROL_TOKEN_ENV
from alan.engine_config import registered_secret_env_names
from alan.launch_spec import EnvEntry
from alan.node_launch_record import record_from_spec

_SECRET_VALUE = "super-secret-value-8f3a"  # noqa: S105  # nosec B105
_CACHE_ENV = {"UV_CACHE_DIR": "uv", "npm_config_cache": "npm"}


def _names(entries: tuple[EnvEntry, ...], category: str) -> set[str]:
    return {entry.name for entry in entries if entry.category == category}


@pytest.fixture
def no_active_cache_env() -> Iterator[None]:
    token = claude_cli._active_cache_env_var.set(None)  # noqa: SLF001
    try:
        yield
    finally:
        claude_cli._active_cache_env_var.reset(token)  # noqa: SLF001


@pytest.fixture
def secret_env(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    names = sorted(registered_secret_env_names() | {CONTROL_TOKEN_ENV})
    for name in names:
        monkeypatch.setenv(name, _SECRET_VALUE)
    return names


def test_fixed_override_names_are_present_in_the_worker_env() -> None:
    policy = claude_cli._worker_env_policy()  # noqa: SLF001
    env = claude_cli._worker_env()  # noqa: SLF001
    fixed = _names(policy, "fixed_override")
    assert fixed and fixed <= set(env)
    assert env[claude_cli._SUBPROCESS_ENV_SCRUB_ENV] == "1"  # noqa: SLF001


def test_secret_removed_names_are_absent_from_the_worker_env(secret_env: list[str]) -> None:
    policy = claude_cli._worker_env_policy()  # noqa: SLF001
    env = claude_cli._worker_env()  # noqa: SLF001
    removed = _names(policy, "secret_removed")
    assert removed == set(secret_env)
    assert not removed & set(env)
    assert default_profile_mod.SONAR_TOKEN_ENV in removed


def test_sandbox_redirect_names_are_all_redirected_by_the_worker_env() -> None:
    policy = claude_cli._worker_env_policy()  # noqa: SLF001
    env = claude_cli._worker_env()  # noqa: SLF001
    redirected = _names(policy, "sandbox_redirect")
    assert redirected == set(claude_cli._WORKER_SANDBOX_ROOT_ENVS)  # noqa: SLF001
    assert redirected <= set(env)


@pytest.mark.usefixtures("no_active_cache_env")
def test_cache_redirect_names_follow_the_cache_env_in_force() -> None:
    explicit = claude_cli._worker_env_policy(_CACHE_ENV)  # noqa: SLF001
    assert _names(explicit, "cache_redirect") == set(_CACHE_ENV)
    assert _names(claude_cli._worker_env_policy(), "cache_redirect") == set()  # noqa: SLF001


def test_cache_redirect_falls_back_to_the_active_cache_env() -> None:
    claude_cli.set_active_cache_env(_CACHE_ENV)
    try:
        active = claude_cli._worker_env_policy()  # noqa: SLF001
    finally:
        claude_cli.set_active_cache_env(None)
    assert _names(active, "cache_redirect") == set(_CACHE_ENV)


def test_fleet_ceiling_names_match_what_the_fleet_env_sets() -> None:
    policy = claude_cli._worker_env_policy(fleet=True)  # noqa: SLF001
    fleet_env = claude_cli._worker_env(fleet=True)  # noqa: SLF001
    ceilings = _names(policy, "fleet_ceiling")
    assert ceilings == set(fleet_load.fleet_env({}))
    assert ceilings <= set(fleet_env)


def test_the_policy_carries_names_and_categories_but_never_values(secret_env: list[str]) -> None:
    spec_policy = claude_cli._process_spec(None, 1.0, 2.0, fleet=True)  # noqa: SLF001
    dumped = json.dumps([[entry.name, entry.category] for entry in spec_policy.env])
    assert _SECRET_VALUE not in dumped
    assert all(set(vars(entry)) == {"name", "category"} for entry in spec_policy.env)
    assert secret_env


def test_the_run_record_environment_never_contains_a_secret_value(secret_env: list[str]) -> None:
    spec = claude_cli.streaming_spec("prompt", mode=claude_cli.ProcessMode(fleet=True))
    record = record_from_spec(spec)
    assert _SECRET_VALUE not in json.dumps(record)
    removed = {e["name"] for e in record["env"] if e["category"] == "secret_removed"}
    assert removed == set(secret_env)


def test_driver_loop_env_policy_lists_only_allowlisted_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", _SECRET_VALUE)
    policy = driver_loop_cmd.env_policy()
    assert {entry.category for entry in policy} == {"allowlisted"}
    assert {entry.name for entry in policy} == set(driver_loop_cmd._allowlisted_env_candidates())  # noqa: SLF001
    assert _SECRET_VALUE not in json.dumps([entry.name for entry in policy])


def test_driver_loop_env_policy_excludes_forbidden_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        driver_loop_cmd, "_forbidden_env_names", lambda: frozenset({"PATH", "SHELL"})
    )
    names = {entry.name for entry in driver_loop_cmd.env_policy()}
    assert "PATH" not in names
    assert "SHELL" not in names
    assert "HOME" in names
