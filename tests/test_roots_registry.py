from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from alan import claude_cli, engine_config, roots  # pyright: ignore[reportPrivateUsage]
from alan.engine_registry_store import engines_root as store_engines_root
from alan.roots import ALL_KNOWN_ROOTS, CI_ISOLATION_ROOTS, STATE_ISOLATION_ROOTS
from alan.teams import _pr_audit_log
from tests._deploy_render_support import EXAMPLE_ORIGIN, render_scratch

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ROOT_TOKEN_RE = re.compile(r"ALAN_[A-Z0-9_]+_ROOT")

CI_WORKFLOWS: tuple[Path, ...] = (_REPO_ROOT / ".github" / "workflows" / "tests.yml",)

SERVER_ENV_TS_PATH = _REPO_ROOT / "alan" / "web" / "e2e" / "fixtures" / "server-env.ts"
PLIST_PATH = _REPO_ROOT / "deploy" / "ai.alan.plist"
_ALAN_SRC_ROOT = _REPO_ROOT / "alan"


def _tokens_in(path: Path) -> frozenset[str]:
    return frozenset(_ROOT_TOKEN_RE.findall(path.read_text(encoding="utf-8")))


def _assert_carries(path: Path, expected: frozenset[str]) -> None:
    present = _tokens_in(path)
    missing = expected - present
    assert not missing, (
        f"{path} is missing isolation root(s) {sorted(missing)} registered in "
        f"alan/roots.py; tokens present: {sorted(present)}"
    )


@pytest.mark.parametrize("workflow", CI_WORKFLOWS, ids=lambda p: p.name)
def test_ci_workflow_carries_every_isolation_root(workflow: Path) -> None:
    _assert_carries(workflow, CI_ISOLATION_ROOTS)


def _state_root_env_is_defended(token: str, env: Mapping[str, str], shared_dir: Path) -> bool:
    value = env.get(token, "")
    if not value:
        return False
    return Path(value).is_relative_to(shared_dir)


def test_conftest_defends_every_state_root_subdir_token(tmp_path: Path) -> None:
    import tests.conftest as conftest_mod  # pyright: ignore[reportPrivateUsage]

    shared_dir = conftest_mod._SHARED_TEST_DIR  # pyright: ignore[reportPrivateUsage]
    undefended = {
        root_env_var
        for root_env_var in roots.STATE_ROOT_SUBDIRS
        if root_env_var != "ALAN_LOGS_ROOT"
        and not _state_root_env_is_defended(root_env_var, os.environ, shared_dir)
    }
    assert not undefended, f"tests/conftest.py does not defend root(s) {sorted(undefended)}"
    assert os.environ.get("ALAN_LOGS_ROOT") == str(tmp_path / "logs"), (
        "ALAN_LOGS_ROOT must be redirected per-test by the autouse "
        "_redirect_alan_logs_root fixture"
    )


def test_state_root_env_is_defended_flags_a_synthetic_undefended_token(tmp_path: Path) -> None:
    fake_root_var = "ALAN_SYNTHETIC_UNDEFENDED_ROOT"
    sibling_dir = tmp_path.parent / f"{tmp_path.name}-foreign"
    leaked_env = {fake_root_var: "/leaked/elsewhere"}
    sibling_env = {fake_root_var: str(sibling_dir / "workspaces")}
    missing_env: dict[str, str] = {}
    defended_env = {fake_root_var: str(tmp_path / "sub")}
    assert _state_root_env_is_defended(fake_root_var, leaked_env, tmp_path) is False
    assert _state_root_env_is_defended(fake_root_var, sibling_env, tmp_path) is False
    assert _state_root_env_is_defended(fake_root_var, missing_env, tmp_path) is False
    assert _state_root_env_is_defended(fake_root_var, defended_env, tmp_path) is True


def test_conftest_overrides_every_poisoned_ambient_state_root_subdir(tmp_path: Path) -> None:
    poisoned_dir = tmp_path / "leaked-ambient-state"
    env = {
        **os.environ,
        **{token: str(poisoned_dir / token) for token in roots.STATE_ROOT_SUBDIRS},
        "TMPDIR": str(tmp_path),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, os, tests.conftest\n"
            "from alan import roots\n"
            "print(json.dumps({t: os.environ.get(t) for t in roots.STATE_ROOT_SUBDIRS}))\n",
        ],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload: dict[str, str | None] = json.loads(result.stdout.strip().splitlines()[-1])
    poisoned = str(poisoned_dir)
    leaked = {
        token: value
        for token, value in payload.items()
        if value is None or value.startswith(poisoned)
    }
    assert not leaked, f"poisoned ambient root(s) survived conftest import: {leaked}"


def test_server_env_ts_carries_every_ci_isolation_root() -> None:
    _assert_carries(SERVER_ENV_TS_PATH, CI_ISOLATION_ROOTS)


def test_claude_cli_worker_env_carries_every_ci_isolation_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for token in CI_ISOLATION_ROOTS:
        monkeypatch.delenv(token, raising=False)
    env = claude_cli._worker_env()  # pyright: ignore[reportPrivateUsage]
    missing = CI_ISOLATION_ROOTS - set(env)
    assert not missing, (
        f"_worker_env() is missing isolation root(s) {sorted(missing)} even after every "
        f"CI_ISOLATION_ROOTS token was deleted from the process environment first"
    )
    assert "ALAN_SLOT_POOL_ROOT" in env


def test_engine_config_reserved_roots_is_registry_identity() -> None:
    assert (
        engine_config._RESERVED_SANDBOX_ROOT_ENVS  # pyright: ignore[reportPrivateUsage]
        is roots.STATE_ISOLATION_ROOTS
    )


def test_worker_sandbox_root_envs_is_registry_identity() -> None:
    assert (
        claude_cli._WORKER_SANDBOX_ROOT_ENVS  # pyright: ignore[reportPrivateUsage]
        is roots.STATE_ROOT_SUBDIRS
    )


def test_state_root_subdirs_excludes_retired_chat_sessions_root() -> None:
    assert "ALAN_CHAT_SESSIONS_ROOT" not in roots.STATE_ROOT_SUBDIRS


def test_state_root_subdirs_excludes_retired_workspace_chat_sessions_root() -> None:
    assert "ALAN_WORKSPACE_CHAT_SESSIONS_ROOT" not in roots.STATE_ROOT_SUBDIRS


def test_assert_carries_reports_missing_tokens(tmp_path: Path) -> None:
    surface = tmp_path / "surface.txt"
    surface.write_text("only ALAN_LOGS_ROOT is here", encoding="utf-8")
    with pytest.raises(AssertionError, match="ALAN_STATE_ROOT"):
        _assert_carries(surface, frozenset({"ALAN_LOGS_ROOT", "ALAN_STATE_ROOT"}))


def test_assert_carries_passes_when_every_token_present(tmp_path: Path) -> None:
    surface = tmp_path / "surface.txt"
    surface.write_text("ALAN_LOGS_ROOT and ALAN_STATE_ROOT both appear here", encoding="utf-8")
    _assert_carries(surface, frozenset({"ALAN_LOGS_ROOT", "ALAN_STATE_ROOT"}))


def test_assert_carries_rejects_a_surface_missing_the_slot_pool_root(tmp_path: Path) -> None:
    surface = tmp_path / "surface.txt"
    surface.write_text(
        " ".join(sorted(CI_ISOLATION_ROOTS - {"ALAN_SLOT_POOL_ROOT"})), encoding="utf-8"
    )
    with pytest.raises(AssertionError, match="ALAN_SLOT_POOL_ROOT"):
        _assert_carries(surface, CI_ISOLATION_ROOTS)


def _conftest_import_probe(tmp_path: Path, leaked_pool: Path, leaked_repo: Path) -> dict[str, str]:
    env = {
        **os.environ,
        "ALAN_SLOT_POOL_ROOT": str(leaked_pool),
        "ALAN_WORKTREE_REPO_ROOT": str(leaked_repo),
        "TMPDIR": str(tmp_path),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, tests.conftest\n"
            "from alan import roots\n"
            "print(json.dumps({\n"
            "    'slot_pool_root': str(roots.slot_pool_root()),\n"
            "    'worktree_repo_root': str(roots.worktree_repo_root()),\n"
            "}))\n",
        ],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload: dict[str, str] = json.loads(result.stdout.strip().splitlines()[-1])
    return payload


def test_conftest_neutralizes_a_leaked_ambient_slot_pool_root(tmp_path: Path) -> None:
    leaked_pool = tmp_path / "leaked-prod-slots"
    leaked_repo = tmp_path / "leaked-worktree-repo"
    payload = _conftest_import_probe(tmp_path, leaked_pool, leaked_repo)
    assert payload["slot_pool_root"] == "None"
    assert payload["slot_pool_root"] != str(leaked_pool)


def test_conftest_overrides_a_leaked_ambient_worktree_repo_root(tmp_path: Path) -> None:
    leaked_pool = tmp_path / "leaked-prod-slots"
    leaked_repo = tmp_path / "leaked-worktree-repo"
    payload = _conftest_import_probe(tmp_path, leaked_pool, leaked_repo)
    assert payload["worktree_repo_root"] != "None"
    assert payload["worktree_repo_root"] != str(leaked_repo)
    assert payload["worktree_repo_root"].endswith("worktree-repo")


@pytest.fixture(scope="session")
def rendered_plist_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    rendered = render_scratch(tmp_path_factory.mktemp("render"), EXAMPLE_ORIGIN)
    assert rendered.result.returncode == 0, rendered.result.stderr
    return rendered.target


@pytest.fixture(params=["template", "rendered"])
def plist_variant_path(request: pytest.FixtureRequest, rendered_plist_path: Path) -> Path:
    if request.param == "template":
        return PLIST_PATH
    return rendered_plist_path


def test_plist_excludes_state_isolation_roots(plist_variant_path: Path) -> None:
    present = _tokens_in(plist_variant_path)
    leaked = present & STATE_ISOLATION_ROOTS
    assert not leaked, (
        f"{plist_variant_path} must not carry state-isolation root(s) {sorted(leaked)} — "
        "the production plist deploys unredirected by design; see "
        "docs/architecture/ci_isolation.md."
    )


def test_rendered_plist_retains_slot_infra_roots(rendered_plist_path: Path) -> None:
    present = _tokens_in(rendered_plist_path)
    assert {"ALAN_SLOT_POOL_ROOT", "ALAN_SLOT_REPO_ROOT"} <= present


def test_no_undocumented_root_env_in_source() -> None:
    unregistered: dict[str, list[str]] = {}
    for path in sorted(_ALAN_SRC_ROOT.rglob("*.py")):
        extra = _tokens_in(path) - ALL_KNOWN_ROOTS
        if extra:
            unregistered[str(path.relative_to(_REPO_ROOT))] = sorted(extra)
    assert not unregistered, (
        "source references ALAN_*_ROOT token(s) missing from "
        f"alan.roots.ALL_KNOWN_ROOTS: {unregistered}"
    )


_TIER_A_CASES: tuple[tuple[str, str, str, Path], ...] = (
    ("workspaces_root", "ALAN_WORKSPACES_ROOT", "workspaces", Path.home() / ".alan" / "workspaces"),
    ("logs_root", "ALAN_LOGS_ROOT", "logs", Path.home() / ".alan" / "logs"),
    ("state_root", "ALAN_STATE_ROOT", "state", Path.home() / ".alan" / "state"),
    (
        "build_chat_sessions_root",
        "ALAN_BUILD_CHAT_SESSIONS_ROOT",
        "build-chat-sessions",
        Path.home() / ".alan" / "build-chat-sessions",
    ),
    (
        "explore_chat_sessions_root",
        "ALAN_EXPLORE_CHAT_SESSIONS_ROOT",
        "explore-chat-sessions",
        Path.home() / ".alan" / "explore-chat-sessions",
    ),
    ("projects_root", "ALAN_PROJECTS_ROOT", "projects", Path.home() / ".alan" / "projects"),
    ("secrets_store_root", "ALAN_SECRETS_STORE_ROOT", "secrets", Path.home() / ".alan" / "secrets"),
    ("engines_root", "ALAN_ENGINES_ROOT", "engines", Path.home() / ".alan" / "engines"),
    ("plans_root", "ALAN_PLANS_ROOT", "plans", Path.home() / ".alan" / "plans"),
    (
        "driver_loop_state_root",
        "ALAN_DRIVER_LOOP_STATE_ROOT",
        ".",
        Path.home() / ".alan",
    ),
)
_TIER_A_IDS = [case[0] for case in _TIER_A_CASES]


@pytest.mark.parametrize(
    ("resolver_name", "token", "subdir", "legacy_default"), _TIER_A_CASES, ids=_TIER_A_IDS
)
def test_tier_a_resolver_default_matches_legacy_constant(
    monkeypatch: pytest.MonkeyPatch,
    resolver_name: str,
    token: str,
    subdir: str,
    legacy_default: Path,
) -> None:
    monkeypatch.delenv(token, raising=False)
    resolver = getattr(roots, resolver_name)
    assert resolver() == legacy_default
    assert resolver() == Path.home() / ".alan" / subdir


@pytest.mark.parametrize(
    ("resolver_name", "token", "subdir", "legacy_default"), _TIER_A_CASES, ids=_TIER_A_IDS
)
def test_tier_a_resolver_treats_empty_string_as_unset(
    monkeypatch: pytest.MonkeyPatch,
    resolver_name: str,
    token: str,
    subdir: str,
    legacy_default: Path,
) -> None:
    del legacy_default
    monkeypatch.setenv(token, "")
    resolver = getattr(roots, resolver_name)
    assert resolver() == Path.home() / ".alan" / subdir


@pytest.mark.parametrize(
    ("resolver_name", "token", "subdir", "legacy_default"), _TIER_A_CASES, ids=_TIER_A_IDS
)
def test_tier_a_resolver_honors_env_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    resolver_name: str,
    token: str,
    subdir: str,
    legacy_default: Path,
) -> None:
    del subdir, legacy_default
    monkeypatch.setenv(token, str(tmp_path))
    resolver = getattr(roots, resolver_name)
    assert resolver() == tmp_path


@pytest.mark.parametrize(
    ("resolver_name", "token", "subdir", "legacy_default"), _TIER_A_CASES, ids=_TIER_A_IDS
)
def test_tier_a_resolver_strips_whitespace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    resolver_name: str,
    token: str,
    subdir: str,
    legacy_default: Path,
) -> None:
    del subdir, legacy_default
    monkeypatch.setenv(token, f"  {tmp_path}  ")
    resolver = getattr(roots, resolver_name)
    assert resolver() == tmp_path


@pytest.mark.parametrize(
    ("resolver_name", "token", "subdir", "legacy_default"), _TIER_A_CASES, ids=_TIER_A_IDS
)
def test_tier_a_resolver_resolves_at_call_time_not_import_time(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    resolver_name: str,
    token: str,
    subdir: str,
    legacy_default: Path,
) -> None:
    del legacy_default
    monkeypatch.delenv(token, raising=False)
    fake_home = tmp_path / "fake-home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    resolver = getattr(roots, resolver_name)
    assert resolver() == fake_home / ".alan" / subdir


def test_logs_root_resolves_at_call_time_not_import_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("ALAN_LOGS_ROOT", raising=False)
    fake_home = tmp_path / "fake-home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    assert roots.logs_root() == fake_home / ".alan" / "logs"
    assert _pr_audit_log.audit_dir() == fake_home / ".alan" / "logs"


def test_cache_root_default_is_sibling_of_default_state_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_STATE_ROOT", raising=False)
    assert roots.cache_root() == Path.home() / ".alan" / "cache"


def test_cache_root_honors_state_root_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "custom-state"))
    assert roots.cache_root() == tmp_path / "cache"


def test_cache_root_is_derived_as_state_root_parent_sibling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "a" / "b" / "state"))
    assert roots.cache_root() == tmp_path / "a" / "b" / "cache"


def test_cache_root_ignores_hypothetical_alan_cache_root_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("ALAN_STATE_ROOT", raising=False)
    monkeypatch.setenv("ALAN_CACHE_ROOT", str(tmp_path / "should-be-ignored"))
    assert roots.cache_root() == Path.home() / ".alan" / "cache"


def test_deploy_marker_root_default_is_sibling_of_default_state_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_STATE_ROOT", raising=False)
    assert roots.deploy_marker_root() == Path.home() / ".alan" / "deploy"


def test_deploy_marker_root_honors_state_root_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "custom-state"))
    assert roots.deploy_marker_root() == tmp_path / "deploy"


def test_deploy_marker_root_is_derived_as_state_root_parent_sibling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "a" / "b" / "state"))
    assert roots.deploy_marker_root() == tmp_path / "a" / "b" / "deploy"


def test_deploy_marker_root_has_no_dedicated_env_var() -> None:
    assert "ALAN_DEPLOY_MARKER_ROOT" not in roots.STATE_ROOT_SUBDIRS
    assert "ALAN_DEPLOY_MARKER_ROOT" not in roots.ALL_KNOWN_ROOTS


def test_state_root_subdirs_shape_and_order() -> None:
    assert list(roots.STATE_ROOT_SUBDIRS.items()) == [
        ("ALAN_WORKSPACES_ROOT", "workspaces"),
        ("ALAN_LOGS_ROOT", "logs"),
        ("ALAN_STATE_ROOT", "state"),
        ("ALAN_BUILD_CHAT_SESSIONS_ROOT", "build-chat-sessions"),
        ("ALAN_EXPLORE_CHAT_SESSIONS_ROOT", "explore-chat-sessions"),
        ("ALAN_PROJECTS_ROOT", "projects"),
        ("ALAN_SECRETS_STORE_ROOT", "secrets"),
        ("ALAN_ENGINES_ROOT", "engines"),
        ("ALAN_PLANS_ROOT", "plans"),
        ("ALAN_DRIVER_LOOP_STATE_ROOT", "."),
    ]


def test_state_isolation_roots_is_derived_from_subdirs() -> None:
    assert STATE_ISOLATION_ROOTS == frozenset(roots.STATE_ROOT_SUBDIRS)


def test_engine_registry_store_engines_root_delegates_to_roots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_ENGINES_ROOT", raising=False)
    assert store_engines_root() == roots.engines_root() == Path.home() / ".alan" / "engines"


def test_slot_pool_root_returns_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_SLOT_POOL_ROOT", raising=False)
    assert roots.slot_pool_root() is None


def test_slot_pool_root_returns_none_when_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_SLOT_POOL_ROOT", "")
    assert roots.slot_pool_root() is None


def test_slot_pool_root_returns_path_when_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_SLOT_POOL_ROOT", str(tmp_path))
    assert roots.slot_pool_root() == tmp_path


def test_slot_pool_root_strips_whitespace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_SLOT_POOL_ROOT", f"  {tmp_path}  ")
    assert roots.slot_pool_root() == tmp_path


def test_slot_repo_root_returns_none_when_unset_and_does_not_shell_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_SLOT_REPO_ROOT", raising=False)
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append((a, k)))
    assert roots.slot_repo_root() is None
    assert calls == []


def test_slot_repo_root_returns_str_when_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_SLOT_REPO_ROOT", str(tmp_path))
    assert roots.slot_repo_root() == str(tmp_path)


def test_slot_repo_root_strips_whitespace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_SLOT_REPO_ROOT", f"  {tmp_path}  ")
    assert roots.slot_repo_root() == str(tmp_path)


def test_slot_repo_root_returns_url_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://github.com/DejvMatikar/alan.git"
    monkeypatch.setenv("ALAN_SLOT_REPO_ROOT", url)
    assert roots.slot_repo_root() == url
