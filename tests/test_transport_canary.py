from __future__ import annotations

import ast
import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from alan import forge_gh, process_runner, secrets_store
from alan import slot_pool as sp
from alan.engine_config import ForgeTarget, RepoIdentity
from alan.secrets_store import (
    ASKPASS_TOKEN_ENV,
    GIT_CRED_RESET_ARGS,
    MissingSecretError,
    transport_env,
)
from alan.teams import _pr_feedback

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import alan_forge_contract_check  # type: ignore[import-not-found]  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ASKPASS_SCRIPT = _REPO_ROOT / "scripts" / "git-askpass-alan.sh"
_SLOT_POOL_SRC_PATH = _REPO_ROOT / "alan" / "slot_pool.py"
_PR_BRANCH_SRC_PATH = _REPO_ROOT / "alan" / "teams" / "_pr_branch.py"
_FORGE_GH_SRC_PATH = _REPO_ROOT / "alan" / "forge_gh.py"
_PROCESS_RUNNER_SRC_PATH = _REPO_ROOT / "alan" / "process_runner.py"
_PR_PUSH_SRC_PATH = _REPO_ROOT / "alan" / "teams" / "_pr_push.py"
_DEPLOY_RESTART_PROBE_SRC_PATH = _REPO_ROOT / "alan" / "teams" / "_deploy_restart_probe.py"
_PR_THREADS_SRC_PATH = _REPO_ROOT / "alan" / "teams" / "_pr_threads.py"
_PR_POST_SRC_PATH = _REPO_ROOT / "alan" / "teams" / "_pr_post.py"
_PR_READY_SRC_PATH = _REPO_ROOT / "alan" / "teams" / "_pr_ready.py"
_VERDICT_SRC_PATH = _REPO_ROOT / "alan" / "teams" / "_verdict.py"
_CLAUSE_AUDIT_SRC_PATH = _REPO_ROOT / "alan" / "teams" / "_clause_audit.py"
_INLINE_FINDINGS_SRC_PATH = _REPO_ROOT / "alan" / "teams" / "_inline_findings.py"
_DISPATCHER_BASE_SRC_PATH = _REPO_ROOT / "alan" / "_dispatcher_base.py"
_DISPATCHER_USAGE_LIMIT_SRC_PATH = _REPO_ROOT / "alan" / "_dispatcher_usage_limit.py"
_DISPATCHER_SELFHEAL_SRC_PATH = _REPO_ROOT / "alan" / "_dispatcher_selfheal.py"
_DISPATCHER_RECONCILE_SRC_PATH = _REPO_ROOT / "alan" / "_dispatcher_reconcile.py"
_DISPATCHER_LOOP_SRC_PATH = _REPO_ROOT / "alan" / "_dispatcher_loop.py"

_TARGET = ForgeTarget(
    repo=RepoIdentity(owner="canary-owner", repo="canary-repo"),
    credential_env_name="CANARY_GH_TOKEN",
)


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_SECRETS_STORE_ROOT", str(tmp_path / "secrets"))
    monkeypatch.delenv("CANARY_GH_TOKEN", raising=False)
    monkeypatch.delenv("ALAN_VERIFIER_GH_TOKEN", raising=False)


def test_transport_env_raises_when_neither_store_nor_env_resolves() -> None:
    with pytest.raises(MissingSecretError, match="CANARY_GH_TOKEN"):
        transport_env(_TARGET)


def test_transport_env_uses_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CANARY_GH_TOKEN", "env-token")
    env = transport_env(_TARGET)
    assert env[ASKPASS_TOKEN_ENV] == "env-token"


def test_transport_env_prefers_store_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CANARY_GH_TOKEN", "env-token")
    secrets_store.write_secret(_TARGET.repo.slug, secrets_store.TRANSPORT_PURPOSE, "store-token")
    env = transport_env(_TARGET)
    assert env[ASKPASS_TOKEN_ENV] == "store-token"


def test_transport_env_shape_is_minimal_and_secret_free_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CANARY_GH_TOKEN", "tok")
    env = transport_env(_TARGET)
    assert env == {
        "GIT_ASKPASS": str(_ASKPASS_SCRIPT),
        ASKPASS_TOKEN_ENV: "tok",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",
    }
    other_values = {k: v for k, v in env.items() if k != ASKPASS_TOKEN_ENV}
    assert "tok" not in json.dumps(other_values)


def test_git_cred_reset_args_shape() -> None:
    assert GIT_CRED_RESET_ARGS == ("-c", "credential.helper=")


def test_askpass_script_is_executable() -> None:
    mode = stat.S_IMODE(_ASKPASS_SCRIPT.stat().st_mode)
    assert mode == 0o755


def test_askpass_script_echoes_token_from_env() -> None:
    result = subprocess.run(  # noqa: S603
        [str(_ASKPASS_SCRIPT), "Username for 'https://github.com': "],
        env={ASKPASS_TOKEN_ENV: "the-token", "PATH": os.environ.get("PATH", "")},
        capture_output=True, text=True, timeout=5, check=False,
    )
    assert result.returncode == 0
    assert result.stdout == "the-token\n"


def test_askpass_script_never_echoes_the_prompt_argv() -> None:
    result = subprocess.run(  # noqa: S603
        [str(_ASKPASS_SCRIPT), "Password for 'https://x-access-token@github.com': "],
        env={ASKPASS_TOKEN_ENV: "secret-value", "PATH": os.environ.get("PATH", "")},
        capture_output=True, text=True, timeout=5, check=False,
    )
    assert result.stdout.strip() == "secret-value"


def test_askpass_script_fails_loud_when_token_env_unset() -> None:
    result = subprocess.run(  # noqa: S603
        [str(_ASKPASS_SCRIPT), "Username: "],
        env={"PATH": os.environ.get("PATH", "")},
        capture_output=True, text=True, timeout=5, check=False,
    )
    assert result.returncode != 0
    assert result.stdout == ""


def test_askpass_script_source_has_no_hardcoded_secret() -> None:
    text = _ASKPASS_SCRIPT.read_text(encoding="utf-8")
    assert "ghp_" not in text
    assert ASKPASS_TOKEN_ENV in text


_NETWORK_SUBCOMMANDS = frozenset({"clone", "fetch", "push"})

_EXPECTED_SLOT_POOL_NETWORK_FUNCTIONS = frozenset(
    {"_fetch_base", "_fetch_existing_branch", "_clone_env_and_argv", "_refresh_base_best_effort"}
)
_EXPECTED_PR_BRANCH_NETWORK_FUNCTIONS: frozenset[str] = frozenset()
_EXPECTED_FORGE_GH_NETWORK_FUNCTIONS: frozenset[str] = frozenset()
_EXPECTED_PR_PUSH_NETWORK_FUNCTIONS = frozenset(
    {"push_branch", "_try_push_with_lease", "_fetch_for_reconcile", "_repush_reconciled"}
)
_EXPECTED_DEPLOY_RESTART_PROBE_NETWORK_FUNCTIONS = frozenset({"_fetch_commit"})


def _qualified_function_sources(path: Path) -> dict[str, str]:
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    tree = ast.parse(source, filename=str(path))
    sources: dict[str, str] = {}

    def visit(node: ast.AST, class_stack: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, (*class_stack, child.name))
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                end = child.end_lineno or child.lineno
                text = "".join(lines[child.lineno - 1 : end])
                sources[".".join((*class_stack, child.name))] = textwrap.dedent(text)
                visit(child, class_stack)

    visit(tree, ())
    return sources


def _string_literals(src: str) -> set[str]:
    return {
        n.value for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }


def _network_functions(path: Path) -> dict[str, str]:
    return {
        name: src
        for name, src in _qualified_function_sources(path).items()
        if _string_literals(src) & _NETWORK_SUBCOMMANDS
    }


def test_slot_pool_network_call_sites_are_the_expected_enumerated_set() -> None:
    assert set(_network_functions(_SLOT_POOL_SRC_PATH)) == _EXPECTED_SLOT_POOL_NETWORK_FUNCTIONS


def test_slot_pool_network_call_sites_route_through_transport_env() -> None:
    for name, src in _network_functions(_SLOT_POOL_SRC_PATH).items():
        assert "transport_env(" in src, (
            f"alan/slot_pool.py:{name} builds a git clone/fetch/push argv "
            "without routing through secrets_store.transport_env"
        )


def test_pr_branch_network_call_sites_are_the_expected_enumerated_set() -> None:
    assert set(_network_functions(_PR_BRANCH_SRC_PATH)) == _EXPECTED_PR_BRANCH_NETWORK_FUNCTIONS


def test_pr_branch_network_call_sites_route_through_transport_env() -> None:
    for name, src in _network_functions(_PR_BRANCH_SRC_PATH).items():
        assert "secrets_store.transport_env(" in src, (
            f"alan/teams/_pr_branch.py:{name} builds a git clone/fetch/push argv "
            "without routing through secrets_store.transport_env"
        )


def test_forge_gh_network_call_sites_are_the_expected_enumerated_set() -> None:
    assert set(_network_functions(_FORGE_GH_SRC_PATH)) == _EXPECTED_FORGE_GH_NETWORK_FUNCTIONS


def test_forge_gh_network_call_sites_route_through_transport_env() -> None:
    for name, src in _network_functions(_FORGE_GH_SRC_PATH).items():
        assert "secrets_store.transport_env(" in src, (
            f"alan/forge_gh.py:{name} builds a git clone/fetch/push argv "
            "without routing through secrets_store.transport_env"
        )


def test_pr_push_network_call_sites_are_the_expected_enumerated_set() -> None:
    assert set(_network_functions(_PR_PUSH_SRC_PATH)) == _EXPECTED_PR_PUSH_NETWORK_FUNCTIONS


def test_pr_push_network_call_sites_route_through_transport_env() -> None:
    for name, src in _network_functions(_PR_PUSH_SRC_PATH).items():
        assert "secrets_store.transport_env(" in src, (
            f"alan/teams/_pr_push.py:{name} builds a git clone/fetch/push argv "
            "without routing through secrets_store.transport_env"
        )


def test_deploy_restart_probe_network_call_sites_are_the_expected_enumerated_set() -> None:
    assert (
        set(_network_functions(_DEPLOY_RESTART_PROBE_SRC_PATH))
        == _EXPECTED_DEPLOY_RESTART_PROBE_NETWORK_FUNCTIONS
    )


def test_deploy_restart_probe_network_call_sites_route_through_transport_env() -> None:
    for name, src in _network_functions(_DEPLOY_RESTART_PROBE_SRC_PATH).items():
        assert "secrets_store.transport_env(" in src, (
            f"alan/teams/_deploy_restart_probe.py:{name} builds a git clone/fetch/push argv "
            "without routing through secrets_store.transport_env"
        )


_CANARY_MODULES: tuple[tuple[Path, tuple[str, ...], str], ...] = (
    (_FORGE_GH_SRC_PATH, ("alan", "forge_gh"), "alan.process_runner"),
    (_FORGE_GH_SRC_PATH, ("alan", "forge_gh"), "alan.teams._pr_post"),
    (_FORGE_GH_SRC_PATH, ("alan", "forge_gh"), "alan.secrets_store"),
    (_PR_PUSH_SRC_PATH, ("alan", "teams", "_pr_push"), "alan.teams._pr_branch"),
    (_PR_PUSH_SRC_PATH, ("alan", "teams", "_pr_push"), "alan.process_runner"),
    (_PR_THREADS_SRC_PATH, ("alan", "teams", "_pr_threads"), "alan.teams._pr_branch"),
    (_PR_POST_SRC_PATH, ("alan", "teams", "_pr_post"), "alan.teams._pr_branch"),
    (_PR_POST_SRC_PATH, ("alan", "teams", "_pr_post"), "alan.secrets_store"),
    (_PR_READY_SRC_PATH, ("alan", "teams", "_pr_ready"), "alan.teams._pr_branch"),
    (
        _DEPLOY_RESTART_PROBE_SRC_PATH,
        ("alan", "teams", "_deploy_restart_probe"),
        "alan.process_runner",
    ),
    (_VERDICT_SRC_PATH, ("alan", "teams", "_verdict"), "alan.teams.verification"),
    (_CLAUSE_AUDIT_SRC_PATH, ("alan", "teams", "_clause_audit"), "alan.teams.verification"),
    (_CLAUSE_AUDIT_SRC_PATH, ("alan", "teams", "_clause_audit"), "alan.teams._pr_branch"),
    (_INLINE_FINDINGS_SRC_PATH, ("alan", "teams", "_inline_findings"), "alan.teams.verification"),
    (_DISPATCHER_BASE_SRC_PATH, ("alan", "_dispatcher_base"), "alan.dispatcher"),
    (_DISPATCHER_USAGE_LIMIT_SRC_PATH, ("alan", "_dispatcher_usage_limit"), "alan.dispatcher"),
    (_DISPATCHER_SELFHEAL_SRC_PATH, ("alan", "_dispatcher_selfheal"), "alan.dispatcher"),
    (_DISPATCHER_RECONCILE_SRC_PATH, ("alan", "_dispatcher_reconcile"), "alan.dispatcher"),
    (_DISPATCHER_LOOP_SRC_PATH, ("alan", "_dispatcher_loop"), "alan.dispatcher"),
)


def _resolved_import_from_module(
    node: ast.ImportFrom, module_parts: tuple[str, ...]
) -> str | None:
    if node.level == 0:
        return node.module
    base_parts = module_parts[: len(module_parts) - node.level]
    if node.module:
        return ".".join((*base_parts, node.module))
    return ".".join(base_parts) if base_parts else None


@pytest.mark.parametrize(
    ("src_path", "module_parts", "forbidden_parent"),
    _CANARY_MODULES,
    ids=[
        "forge_gh-process_runner", "forge_gh-pr_post", "forge_gh-secrets_store",
        "_pr_push", "_pr_push-process_runner", "_pr_threads", "_pr_post", "_pr_post-secrets_store",
        "_pr_ready", "_deploy_restart_probe", "_verdict",
        "_clause_audit-verification", "_clause_audit-pr_branch", "_inline_findings",
        "_dispatcher_base", "_dispatcher_usage_limit", "_dispatcher_selfheal",
        "_dispatcher_reconcile", "_dispatcher_loop",
    ],
)
def test_module_does_not_from_import_its_parent(
    src_path: Path, module_parts: tuple[str, ...], forbidden_parent: str
) -> None:
    tree = ast.parse(src_path.read_text(encoding="utf-8"), filename=str(src_path))
    offending = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and _resolved_import_from_module(node, module_parts) == forbidden_parent
    ]
    assert not offending, f"{src_path.name} must not from-import {forbidden_parent}"


def _every_imported_module(tree: ast.AST, module_parts: tuple[str, ...]) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolved_import_from_module(node, module_parts)
            if resolved:
                imported.add(resolved)
                imported.update(f"{resolved}.{alias.name}" for alias in node.names)
    return imported


def test_forge_gh_does_not_import_the_pr_branch_module() -> None:
    tree = ast.parse(_FORGE_GH_SRC_PATH.read_text(encoding="utf-8"))
    assert "alan.teams._pr_branch" not in _every_imported_module(tree, ("alan", "forge_gh"))


def test_process_runner_imports_no_application_module() -> None:
    tree = ast.parse(_PROCESS_RUNNER_SRC_PATH.read_text(encoding="utf-8"))
    imported = _every_imported_module(tree, ("alan", "process_runner"))
    assert {name for name in imported if name.split(".")[0] == "alan"} == set()


def test_no_long_lived_git_subprocess_or_fsmonitor() -> None:
    for path in (
        _SLOT_POOL_SRC_PATH, _PR_BRANCH_SRC_PATH, _PR_PUSH_SRC_PATH, _PROCESS_RUNNER_SRC_PATH,
    ):
        src = path.read_text(encoding="utf-8")
        assert "fsmonitor" not in src, f"{path} references fsmonitor — re-verify OQ2 by hand"
        assert "Popen" not in src, f"{path} spawns git via Popen, not subprocess.run"


_CREDENTIAL_FILL_INPUT = "protocol=https\nhost=github.com\n\n"
_AMBIENT_SECRET = "fake-ambient-secret"  # noqa: S105  # nosec B105


def _write_fake_ambient_credential_helper(tmp_path: Path) -> Path:
    script = tmp_path / "fake-credential-helper.sh"
    script.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "get" ]; then\n'
        f'  printf "username=x-access-token\\npassword={_AMBIENT_SECRET}\\n"\n'
        "fi\n"
    )
    script.chmod(0o755)
    return script


def _write_system_gitconfig(tmp_path: Path, helper: Path) -> Path:
    cfg = tmp_path / "gitconfig-system"
    cfg.write_text(f"[credential]\n\thelper = {helper}\n")
    return cfg


def _credential_fill(
    tmp_path: Path, system_cfg: Path, extra_env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "GIT_CONFIG_SYSTEM": str(system_cfg),
        "GIT_TERMINAL_PROMPT": "0",
        **extra_env,
    }
    return subprocess.run(  # noqa: S603,S607
        ["git", "credential", "fill"],
        input=_CREDENTIAL_FILL_INPUT,
        env=env, capture_output=True, text=True, timeout=10, check=False,
    )


def test_ambient_system_helper_leaks_without_transport_env_reset(tmp_path: Path) -> None:
    helper = _write_fake_ambient_credential_helper(tmp_path)
    system_cfg = _write_system_gitconfig(tmp_path, helper)
    result = _credential_fill(tmp_path, system_cfg, {})
    assert result.returncode == 0
    assert f"password={_AMBIENT_SECRET}" in result.stdout


def test_transport_env_reset_prevents_osxkeychain_fallthrough(tmp_path: Path) -> None:
    helper = _write_fake_ambient_credential_helper(tmp_path)
    system_cfg = _write_system_gitconfig(tmp_path, helper)
    reset_env = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",
    }
    result = _credential_fill(tmp_path, system_cfg, reset_env)
    assert result.returncode != 0
    assert _AMBIENT_SECRET not in result.stdout


def _init_bare_source_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for cmd in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@t.com"],
        ["git", "config", "user.name", "T"],
    ):
        subprocess.run(cmd, cwd=str(path), check=True, capture_output=True)  # noqa: S603,S607
    (path / "README.md").write_text("hi\n")
    subprocess.run(  # noqa: S603,S607
        ["git", "add", "README.md"], cwd=str(path), check=True, capture_output=True
    )
    subprocess.run(  # noqa: S603,S607
        ["git", "commit", "-q", "-m", "init"], cwd=str(path), check=True, capture_output=True
    )


def test_pooled_git_config_has_no_credential_helper_or_embedded_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CANARY_GH_TOKEN", "ghp_super_secret_value_should_never_land_on_disk")
    source = tmp_path / "source-repo"
    _init_bare_source_repo(source)
    slot = tmp_path / "slot"
    sp._init_slot(slot, str(source), [], {}, _TARGET)  # noqa: SLF001
    config_text = (slot / ".git" / "config").read_text(encoding="utf-8")
    assert "credential.helper" not in config_text
    assert "ghp_super_secret_value_should_never_land_on_disk" not in config_text
    assert "@" not in config_text


_FORGE_CONTRACT_SRC_PATH = _REPO_ROOT / "scripts" / "alan_forge_contract_check.py"

_UNDEFINED_FIELD_ERROR = {
    "type": "undefinedField",
    "path": ["query", "repository", "pullRequest", "reviewThreads", "nodes", "author"],
    "locations": [{"line": 1, "column": 123}],
    "message": "Field 'author' doesn't exist on type 'PullRequestReviewThread'",
}


def _author_node(login: str, typename: str) -> dict[str, object]:
    return {"login": login, "__typename": typename}


def _feedback_payload_with_thread_authors(authors: list[tuple[str, str]]) -> str:
    nodes = [
        {
            "id": f"T{i}",
            "isResolved": False,
            "comments": {"nodes": [{"body": "x", "databaseId": i, "author": _author_node(*a)}]},
        }
        for i, a in enumerate(authors)
    ]
    return json.dumps({"data": {"repository": {"pullRequest": {
        "reviewThreads": {"totalCount": len(nodes), "nodes": nodes},
        "comments": {"totalCount": 0, "nodes": []},
        "reviews": {"totalCount": 0, "nodes": []},
    }}}})


def _forge_contract_imports_from(module: str) -> set[str]:
    tree = ast.parse(_FORGE_CONTRACT_SRC_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            imported.update(alias.name for alias in node.names)
    return imported


def test_forge_contract_script_imports_feedback_query_from_the_adapter() -> None:
    assert "_FEEDBACK_QUERY" in _forge_contract_imports_from("alan.forge_gh")


def test_forge_contract_script_imports_login_pin_from_the_feedback_module() -> None:
    assert {"_COPILOT_GRAPHQL_LOGIN", "GraphQLActorLogin"} <= _forge_contract_imports_from(
        "alan.teams._pr_feedback"
    )


def test_forge_contract_script_does_not_duplicate_the_query_or_login_literal() -> None:
    script_source = _FORGE_CONTRACT_SRC_PATH.read_text(encoding="utf-8")
    assert forge_gh._FEEDBACK_QUERY not in script_source  # noqa: SLF001
    assert _pr_feedback._COPILOT_GRAPHQL_LOGIN.login not in script_source  # noqa: SLF001


def test_forge_contract_script_binds_the_same_query_and_login_objects() -> None:
    assert alan_forge_contract_check._FEEDBACK_QUERY is forge_gh._FEEDBACK_QUERY  # noqa: SLF001
    assert (
        alan_forge_contract_check._COPILOT_GRAPHQL_LOGIN  # noqa: SLF001
        is _pr_feedback._COPILOT_GRAPHQL_LOGIN  # noqa: SLF001
    )


def test_fetch_feedback_raw_reports_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        raise subprocess.TimeoutExpired(cmd, 60.0)

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    stdout, reason = alan_forge_contract_check.fetch_feedback_raw(1017)
    assert stdout == ""
    assert "timed out" in reason


def test_fetch_feedback_raw_reports_missing_gh_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del cmd, kwargs
        raise FileNotFoundError("gh not found")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    stdout, reason = alan_forge_contract_check.fetch_feedback_raw(1017)
    assert stdout == ""
    assert "raised" in reason


def test_fetch_feedback_raw_preserves_stdout_on_nonzero_returncode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(cmd, 1, stdout='{"errors": []}', stderr="boom")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    stdout, reason = alan_forge_contract_check.fetch_feedback_raw(1017)
    assert stdout == '{"errors": []}'
    assert "rc=1" in reason
    assert "boom" in reason


def test_fetch_feedback_raw_returns_stdout_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout='{"data": {}}', stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    stdout, reason = alan_forge_contract_check.fetch_feedback_raw(1017)
    assert stdout == '{"data": {}}'
    assert reason == ""


def test_check_query_shape_reports_undefined_field() -> None:
    body = json.dumps({"data": None, "errors": [_UNDEFINED_FIELD_ERROR]})
    verdict = alan_forge_contract_check.check_query_shape(body)
    assert verdict.outcome is alan_forge_contract_check.Outcome.DRIFT
    assert "reviewThreads.nodes.author" in verdict.reason


def test_check_query_shape_falls_back_to_message_when_path_missing() -> None:
    body = json.dumps({"errors": [{"message": "something broke"}]})
    verdict = alan_forge_contract_check.check_query_shape(body)
    assert verdict.outcome is alan_forge_contract_check.Outcome.DRIFT
    assert "something broke" in verdict.reason


def test_check_query_shape_falls_back_to_unnamed_when_neither_present() -> None:
    body = json.dumps({"errors": [{}]})
    verdict = alan_forge_contract_check.check_query_shape(body)
    assert verdict.outcome is alan_forge_contract_check.Outcome.DRIFT
    assert "<unnamed field>" in verdict.reason


def test_check_query_shape_ignores_non_dict_error_entries() -> None:
    body = json.dumps({"errors": ["not-a-dict"]})
    verdict = alan_forge_contract_check.check_query_shape(body)
    assert verdict.outcome is alan_forge_contract_check.Outcome.DRIFT
    assert "offending field(s): " in verdict.reason


_VALID_PULL_REQUEST_BODY = json.dumps({"data": {"repository": {"pullRequest": {}}}})


def test_check_query_shape_passes_when_no_errors() -> None:
    verdict = alan_forge_contract_check.check_query_shape(_VALID_PULL_REQUEST_BODY)
    assert verdict == alan_forge_contract_check.Verdict(
        alan_forge_contract_check.Outcome.VERIFIED, ""
    )


def test_check_query_shape_passes_when_errors_key_is_empty_list() -> None:
    body = json.dumps({"errors": [], "data": {"repository": {"pullRequest": {}}}})
    verdict = alan_forge_contract_check.check_query_shape(body)
    assert verdict.outcome is alan_forge_contract_check.Outcome.VERIFIED


def test_check_query_shape_reports_missing_pull_request_when_data_lacks_it() -> None:
    verdict = alan_forge_contract_check.check_query_shape(json.dumps({"data": {}}))
    assert verdict.outcome is alan_forge_contract_check.Outcome.UNVERIFIABLE
    assert "data.repository.pullRequest" in verdict.reason


def test_check_query_shape_reports_null_pull_request() -> None:
    body = json.dumps({"data": {"repository": {"pullRequest": None}}})
    verdict = alan_forge_contract_check.check_query_shape(body)
    assert verdict.outcome is alan_forge_contract_check.Outcome.UNVERIFIABLE
    assert "data.repository.pullRequest" in verdict.reason


def test_check_query_shape_rejects_unparseable_body() -> None:
    verdict = alan_forge_contract_check.check_query_shape("not json")
    assert verdict.outcome is alan_forge_contract_check.Outcome.UNVERIFIABLE
    assert "did not parse" in verdict.reason


def test_check_query_shape_rejects_non_object_body() -> None:
    verdict = alan_forge_contract_check.check_query_shape("[1, 2]")
    assert verdict.outcome is alan_forge_contract_check.Outcome.UNVERIFIABLE
    assert "did not parse" in verdict.reason


def test_check_pinned_identity_passes_when_login_resolves_to_a_bot() -> None:
    payload = _feedback_payload_with_thread_authors(
        [("copilot-pull-request-reviewer", "Bot"), ("a-human", "User")]
    )
    reason = alan_forge_contract_check.check_pinned_identity(
        payload, alan_forge_contract_check._COPILOT_GRAPHQL_LOGIN  # noqa: SLF001
    )
    assert reason == ""


def test_check_pinned_identity_fails_when_login_resolves_to_wrong_type() -> None:
    payload = _feedback_payload_with_thread_authors([("copilot-pull-request-reviewer", "User")])
    reason = alan_forge_contract_check.check_pinned_identity(
        payload, alan_forge_contract_check._COPILOT_GRAPHQL_LOGIN  # noqa: SLF001
    )
    assert "copilot-pull-request-reviewer" in reason
    assert "User" in reason


def test_check_pinned_identity_fails_when_login_absent_entirely() -> None:
    payload = _feedback_payload_with_thread_authors([("some-other-bot", "Bot")])
    reason = alan_forge_contract_check.check_pinned_identity(
        payload, alan_forge_contract_check._COPILOT_GRAPHQL_LOGIN  # noqa: SLF001
    )
    assert "possible upstream rename" in reason


def test_check_pinned_identity_walks_comments_and_reviews_too() -> None:
    pinned = alan_forge_contract_check._COPILOT_GRAPHQL_LOGIN  # noqa: SLF001
    payload = json.dumps({"data": {"repository": {"pullRequest": {
        "reviewThreads": {"totalCount": 0, "nodes": []},
        "comments": {"totalCount": 1, "nodes": [
            {"databaseId": 1, "body": "x", "author": _author_node(pinned.login, "Bot")}
        ]},
        "reviews": {"totalCount": 0, "nodes": []},
    }}}})
    assert alan_forge_contract_check.check_pinned_identity(payload, pinned) == ""


def test_run_check_skips_identity_check_but_not_shape_check_on_fetch_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        alan_forge_contract_check, "fetch_feedback_raw", lambda pr: ("", "network exploded")
    )

    def _boom(*args: object, **kwargs: object) -> str:
        raise AssertionError("must not be called once fetch has already failed")

    monkeypatch.setattr(alan_forge_contract_check, "check_pinned_identity", _boom)
    verdict = alan_forge_contract_check.run_check(1017)
    assert verdict.outcome is alan_forge_contract_check.Outcome.UNVERIFIABLE
    assert verdict.reason == "network exploded"


def test_run_check_short_circuits_on_shape_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(alan_forge_contract_check, "fetch_feedback_raw", lambda pr: ("{}", ""))
    monkeypatch.setattr(
        alan_forge_contract_check,
        "check_query_shape",
        lambda stdout: alan_forge_contract_check.Verdict(
            alan_forge_contract_check.Outcome.UNVERIFIABLE, "shape broke"
        ),
    )

    def _boom(*args: object, **kwargs: object) -> str:
        raise AssertionError("must not be called once shape check has already failed")

    monkeypatch.setattr(alan_forge_contract_check, "check_pinned_identity", _boom)
    verdict = alan_forge_contract_check.run_check(1017)
    assert verdict.outcome is alan_forge_contract_check.Outcome.UNVERIFIABLE
    assert verdict.reason == "shape broke"


def test_run_check_returns_identity_result_when_fetch_and_shape_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(alan_forge_contract_check, "fetch_feedback_raw", lambda pr: ("{}", ""))
    monkeypatch.setattr(
        alan_forge_contract_check,
        "check_query_shape",
        lambda stdout: alan_forge_contract_check.Verdict(
            alan_forge_contract_check.Outcome.VERIFIED, ""
        ),
    )
    monkeypatch.setattr(
        alan_forge_contract_check, "check_pinned_identity", lambda stdout, pinned: ""
    )
    verdict = alan_forge_contract_check.run_check(1017)
    assert verdict == alan_forge_contract_check.Verdict(
        alan_forge_contract_check.Outcome.VERIFIED, ""
    )


def test_main_returns_1_and_prints_reason_on_drift(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr(
        alan_forge_contract_check,
        "run_check",
        lambda pr_number: alan_forge_contract_check.Verdict(
            alan_forge_contract_check.Outcome.DRIFT, "it broke"
        ),
    )
    rc = alan_forge_contract_check.main()
    assert rc == 1
    captured = capsys.readouterr()
    assert "it broke" in captured.err


def test_main_returns_0_and_prints_pinned_login_on_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr(
        alan_forge_contract_check,
        "run_check",
        lambda pr_number: alan_forge_contract_check.Verdict(
            alan_forge_contract_check.Outcome.VERIFIED, ""
        ),
    )
    rc = alan_forge_contract_check.main()
    assert rc == 0
    captured = capsys.readouterr()
    assert "OK" in captured.out
    assert alan_forge_contract_check._COPILOT_GRAPHQL_LOGIN.login in captured.out  # noqa: SLF001
