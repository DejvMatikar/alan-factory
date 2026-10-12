from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from alan import engine_preflight as ep
from alan import secrets_store
from alan.branch_protection_compat import Finding
from alan.engine_config import (
    EngineConfig,
    ForgeConfig,
    ForgeTarget,
    RepoIdentity,
    default_engine_config,
)
from alan.engine_config_validation import FlowGraphPolicy
from alan.engine_presets import dotnet_preset, ios_preset, list_presets
from alan.forge import LoginRead
from alan.teams._base import BASE_ALLOWED_TOOLS, render_allowed_tools
from tests.fakes.forge import FakeForge
from tests.forge_stubs import file_read, login_read
from tests.readiness_support import (
    CODE_QUALITY,
    REVIEWER,
    REVIEWER_FINGERPRINT,
    REVIEWER_TOKEN,
    Install,
)

_CREDENTIAL_ENV = "ACME_GH_TOKEN"  # noqa: S105

_FORGE_TARGET = ForgeTarget(
    repo=RepoIdentity(owner="acme", repo="widget"),
    credential_env_name=_CREDENTIAL_ENV,
    authoritative_checks=frozenset({"SonarCloud Code Analysis"}),
)


def _stub_viewer_login(
    forge: FakeForge, *, author: LoginRead | None = None, reviewer: LoginRead | None = None,
) -> None:
    resolved_author = author if author is not None else login_read("author-bot")
    resolved_reviewer = reviewer if reviewer is not None else login_read("reviewer-bot")

    def _login(*, forge_target: ForgeTarget, as_verifier: bool, timeout: float) -> LoginRead:
        del forge_target, timeout
        return resolved_reviewer if as_verifier else resolved_author

    forge.stub("viewer_login", _login)

_BAD_FLOW_POLICY = FlowGraphPolicy(
    verifier_teams=frozenset({"verification"}),
    merge_teams=frozenset({"pr_merge"}),
    terminal_teams=frozenset({"deploy_verify"}),
    internal_only_teams=frozenset(),
    restricted_predecessors={},
    revisit_cap=3,
)
_BAD_TRANSITION_GRAPH = {"implementation": ["pr_merge"], "pr_merge": []}


def _cfg(**overrides: Any) -> EngineConfig:
    base = default_engine_config()
    updates: dict[str, Any] = {
        "repo": RepoIdentity(owner="acme", repo="widget"),
        "forge": ForgeConfig(
            repo=RepoIdentity(owner="acme", repo="widget"),
            credential_env_name=_CREDENTIAL_ENV,
            authoritative_checks=frozenset({"SonarCloud Code Analysis"}),
        ),
    }
    updates.update(overrides)
    return base.model_copy(update=updates)


def _completed(
    *, rc: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)


def test_worst_precedence() -> None:
    assert ep._worst([]) == "pass"  # noqa: SLF001
    assert ep._worst(["pass", "pass"]) == "pass"  # noqa: SLF001
    assert ep._worst(["pass", "unknown"]) == "unknown"  # noqa: SLF001
    assert ep._worst(["pass", "unknown", "fail"]) == "fail"  # noqa: SLF001
    assert ep._worst(["fail", "pass"]) == "fail"  # noqa: SLF001


def test_check_author_token_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "tok")  # noqa: ARG005
    finding = ep._check_author_token(_FORGE_TARGET)  # noqa: SLF001
    assert finding.status == "pass"
    assert finding.check == "author_token_valid"


def test_check_author_token_fail_when_no_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: None)  # noqa: ARG005
    monkeypatch.delenv(_CREDENTIAL_ENV, raising=False)
    finding = ep._check_author_token(_FORGE_TARGET)  # noqa: SLF001
    assert finding.status == "fail"
    assert finding.remedy


def test_reviewer_distinctness_fail_when_author_equals_reviewer(
    _active_fake_forge: FakeForge,
) -> None:
    _stub_viewer_login(
        _active_fake_forge, author=login_read("same-user"), reviewer=login_read("same-user"),
    )
    finding = ep._check_reviewer_distinctness(_FORGE_TARGET)  # noqa: SLF001
    assert finding.status == "fail"
    assert "same-user" in finding.message


def test_reviewer_distinctness_pass_when_logins_differ(_active_fake_forge: FakeForge) -> None:
    _stub_viewer_login(
        _active_fake_forge, author=login_read("author-bot"), reviewer=login_read("reviewer-bot"),
    )
    finding = ep._check_reviewer_distinctness(_FORGE_TARGET)  # noqa: SLF001
    assert finding.status == "pass"
    assert "author-bot" in finding.message
    assert "reviewer-bot" in finding.message


def test_reviewer_distinctness_unknown_when_reviewer_credential_missing(
    _active_fake_forge: FakeForge,
) -> None:
    _stub_viewer_login(
        _active_fake_forge,
        author=login_read("author-bot"),
        reviewer=login_read(None, outcome="no_credential"),
    )
    finding = ep._check_reviewer_distinctness(_FORGE_TARGET)  # noqa: SLF001
    assert finding.status == "unknown"


def test_reviewer_distinctness_remedy_names_two_credential_model(
    _active_fake_forge: FakeForge,
) -> None:
    _stub_viewer_login(
        _active_fake_forge,
        author=login_read(None, outcome="no_credential"),
        reviewer=login_read(None, outcome="no_credential"),
    )
    finding_unknown = ep._check_reviewer_distinctness(_FORGE_TARGET)  # noqa: SLF001
    assert finding_unknown.status == "unknown"
    assert finding_unknown.remedy is not None
    assert "reviewer" in finding_unknown.remedy
    assert "store" in finding_unknown.remedy

    _stub_viewer_login(
        _active_fake_forge, author=login_read("same-user"), reviewer=login_read("same-user"),
    )
    finding_fail = ep._check_reviewer_distinctness(_FORGE_TARGET)  # noqa: SLF001
    assert finding_fail.status == "fail"
    assert finding_fail.remedy is not None
    assert "reviewer" in finding_fail.remedy
    assert "transport" in finding_fail.remedy


def test_author_login_returns_the_login_on_ok_outcome(_active_fake_forge: FakeForge) -> None:
    _active_fake_forge.stub("viewer_login", lambda **_k: login_read("author-bot"))
    assert ep._author_login(_FORGE_TARGET) == "author-bot"  # noqa: SLF001


def test_author_login_returns_none_on_no_credential(_active_fake_forge: FakeForge) -> None:
    _active_fake_forge.stub(
        "viewer_login", lambda **_k: login_read(None, outcome="no_credential"),
    )
    assert ep._author_login(_FORGE_TARGET) is None  # noqa: SLF001


def test_author_login_returns_none_on_failed(_active_fake_forge: FakeForge) -> None:
    _active_fake_forge.stub("viewer_login", lambda **_k: login_read(None, outcome="failed"))
    assert ep._author_login(_FORGE_TARGET) is None  # noqa: SLF001


def test_author_login_returns_none_on_raised(_active_fake_forge: FakeForge) -> None:
    _active_fake_forge.stub("viewer_login", lambda **_k: login_read(None, outcome="raised"))
    assert ep._author_login(_FORGE_TARGET) is None  # noqa: SLF001


def test_author_login_requests_the_transport_credential_not_the_verifier_one(
    _active_fake_forge: FakeForge,
) -> None:
    captured: dict[str, object] = {}

    def _login(*, forge_target: ForgeTarget, as_verifier: bool, timeout: float) -> LoginRead:
        del forge_target, timeout
        captured["as_verifier"] = as_verifier
        return login_read("author-bot")

    _active_fake_forge.stub("viewer_login", _login)
    ep._author_login(_FORGE_TARGET)  # noqa: SLF001
    assert captured["as_verifier"] is False


def test_reviewer_login_requests_the_verifier_credential_not_the_transport_one(
    _active_fake_forge: FakeForge,
) -> None:
    captured: dict[str, object] = {}

    def _login(*, forge_target: ForgeTarget, as_verifier: bool, timeout: float) -> LoginRead:
        del forge_target, timeout
        captured["as_verifier"] = as_verifier
        return login_read("reviewer-bot")

    _active_fake_forge.stub("viewer_login", _login)
    assert ep._reviewer_login(_FORGE_TARGET) == "reviewer-bot"  # noqa: SLF001
    assert captured["as_verifier"] is True


def test_repo_clonable_fail_when_no_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: None)  # noqa: ARG005
    monkeypatch.delenv(_CREDENTIAL_ENV, raising=False)

    def _boom(*_a: Any, **_kw: Any) -> subprocess.CompletedProcess[str]:
        raise AssertionError("git ls-remote must not run without a credential")

    monkeypatch.setattr(subprocess, "run", _boom)
    finding = ep._check_repo_clonable(_FORGE_TARGET)  # noqa: SLF001
    assert finding.status == "fail"


def test_repo_clonable_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "tok")  # noqa: ARG005
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed())  # noqa: ARG005
    finding = ep._check_repo_clonable(_FORGE_TARGET)  # noqa: SLF001
    assert finding.status == "pass"


def test_repo_clonable_fail_on_nonzero_rc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "tok")  # noqa: ARG005
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: _completed(rc=128, stderr="fatal: not found")  # noqa: ARG005
    )
    finding = ep._check_repo_clonable(_FORGE_TARGET)  # noqa: SLF001
    assert finding.status == "fail"


def test_repo_clonable_unknown_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "tok")  # noqa: ARG005

    def _raise(*_a: Any, **_kw: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["git"], timeout=5)

    monkeypatch.setattr(subprocess, "run", _raise)
    finding = ep._check_repo_clonable(_FORGE_TARGET)  # noqa: SLF001
    assert finding.status == "unknown"


def test_check_repo_clonable_still_drives_subprocess_run_not_the_forge_port(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "tok")  # noqa: ARG005
    captured: dict[str, Any] = {}

    def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        return _completed()

    monkeypatch.setattr(subprocess, "run", _run)
    finding = ep._check_repo_clonable(_FORGE_TARGET)  # noqa: SLF001
    assert finding.status == "pass"
    assert captured["cmd"][:2] == ["git", "ls-remote"]
    assert _active_fake_forge.calls == []


def test_command_reachable_prefix_match_semantics() -> None:
    prefixes = ep._bash_allowlist_prefixes(("Bash(uv run:*)",))  # noqa: SLF001
    assert ep._command_reachable(["uv", "run", "--no-active", "pytest"], prefixes)  # noqa: SLF001
    assert not ep._command_reachable(["uv", "publish"], prefixes)  # noqa: SLF001


def test_bash_allowlist_prefixes_bare_bash_matches_everything() -> None:
    prefixes = ep._bash_allowlist_prefixes(("Bash",))  # noqa: SLF001
    assert prefixes == [()]
    assert ep._command_reachable(["anything", "at", "all"], prefixes)  # noqa: SLF001


def test_tools_reachable_pass_for_default_engine_config() -> None:
    finding = ep._check_tools_reachable(_cfg())  # noqa: SLF001
    assert finding.status == "pass"


def test_tools_reachable_fails_against_engine_allowlist_not_base_allowed_tools() -> None:
    assert "Bash(git log:*)" in BASE_ALLOWED_TOOLS
    cfg = _cfg(
        base_tools=["Read"],
        team_tools={},
        gate_commands=[["git", "log", "--oneline"]],
        warmup_commands=[],
    )
    finding = ep._check_tools_reachable(cfg)  # noqa: SLF001
    assert finding.status == "fail"
    assert "git log --oneline" in finding.message


def test_tools_reachable_fails_on_empty_command() -> None:
    cfg = _cfg(gate_commands=[[]], warmup_commands=[])
    finding = ep._check_tools_reachable(cfg)  # noqa: SLF001
    assert finding.status == "fail"
    assert "empty" in finding.message


def test_flow_graph_pass_for_default_engine_config() -> None:
    finding = ep._check_flow_graph(_cfg())  # noqa: SLF001
    assert finding.status == "pass"


def test_flow_graph_fail_on_invalid_graph() -> None:
    cfg = _cfg(transition_graph=_BAD_TRANSITION_GRAPH, flow_policy=_BAD_FLOW_POLICY)
    finding = ep._check_flow_graph(cfg)  # noqa: SLF001
    assert finding.status == "fail"
    assert finding.remedy


def test_flow_graph_pass_when_flow_policy_absent() -> None:
    cfg = _cfg(flow_policy=None, transition_graph={})
    finding = ep._check_flow_graph(cfg)  # noqa: SLF001
    assert finding.status == "pass"


def test_check_flow_graph_is_the_same_validator_engine_config_proposal_reuses() -> None:
    from alan import engine_config_proposal

    assert engine_config_proposal.validate_draft(_cfg()) == ep._check_flow_graph(_cfg())  # noqa: SLF001

    bad_cfg = _cfg(transition_graph=_BAD_TRANSITION_GRAPH, flow_policy=_BAD_FLOW_POLICY)
    assert engine_config_proposal.validate_draft(bad_cfg) == ep._check_flow_graph(bad_cfg)  # noqa: SLF001


def test_tools_reachable_pass_for_every_preset() -> None:
    for name, cfg in list_presets():
        finding = ep._check_tools_reachable(cfg)  # noqa: SLF001
        assert finding.status == "pass", name


def test_ios_gate_and_warmup_commands_reachable() -> None:
    cfg = ios_preset()
    prefixes = ep._bash_allowlist_prefixes(render_allowed_tools(cfg, "implementation"))  # noqa: SLF001
    for cmd in [*cfg.gate_commands, *cfg.warmup_commands]:
        assert ep._command_reachable(cmd, prefixes), cmd  # noqa: SLF001


def test_flow_graph_pass_for_every_preset() -> None:
    for name, cfg in list_presets():
        finding = ep._check_flow_graph(cfg)  # noqa: SLF001
        assert finding.status == "pass", name


_DOTNET_REPO = RepoIdentity(owner="acme", repo="dotnet-svc")
_DOTNET_CREDENTIAL_ENV = "ACME_DOTNET_GH_TOKEN"  # noqa: S105

_DOTNET_REPO_JSON = {
    "default_branch": "main",
    "allow_squash_merge": True,
    "delete_branch_on_merge": True,
}
_DOTNET_PROTECTION_JSON = {
    "required_conversation_resolution": {"enabled": True},
    "required_pull_request_reviews": {"required_approving_review_count": 0},
    "required_status_checks": {"contexts": ["SonarCloud Code Analysis"], "checks": []},
}


def test_dotnet_preset_wizard_simulation_clears_preflight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    cfg = dotnet_preset().model_copy(update={
        "repo": _DOTNET_REPO,
        "forge": ForgeConfig(
            repo=_DOTNET_REPO,
            credential_env_name=_DOTNET_CREDENTIAL_ENV,
            authoritative_checks=frozenset({"SonarCloud Code Analysis"}),
        ),
    })

    def _resolve(engine: str, purpose: str, *, env_fallback: str | None = None) -> str:
        del engine, env_fallback
        return "reviewer-token" if purpose == secrets_store.REVIEWER_PURPOSE else "author-token"

    monkeypatch.setattr(secrets_store, "resolve_secret", _resolve)
    monkeypatch.delenv(_DOTNET_CREDENTIAL_ENV, raising=False)
    _stub_viewer_login(
        _active_fake_forge, author=login_read("author-bot"), reviewer=login_read("reviewer-bot"),
    )

    def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["git", "ls-remote"]:
            return _completed(rc=0)
        if cmd[:2] == ["gh", "api"] and str(cmd[2]).endswith("/protection"):
            return _completed(stdout=json.dumps(_DOTNET_PROTECTION_JSON))
        if cmd[:2] == ["gh", "api"]:
            return _completed(stdout=json.dumps(_DOTNET_REPO_JSON))
        raise AssertionError(f"unexpected subprocess call: {cmd}")

    monkeypatch.setattr(subprocess, "run", _run)

    result = ep.run_preflight(cfg)

    by_check = {f.check: f for f in result.findings}
    assert by_check["gate_warmup_tools_reachable"].status == "pass"
    assert by_check["flow_graph_and_posture_valid"].status == "pass"
    assert result.overall == "pass"


def test_dotnet_preset_raw_forge_none_falls_back_to_default_forge() -> None:
    from alan.engine_config import forge_target_for_workspace

    cfg = dotnet_preset()
    assert cfg.forge is None
    resolved = forge_target_for_workspace(None, cfg)
    assert resolved.repo.slug == "DejvMatikar/alan"


def test_branch_protection_admin_unreadable_surfaces_as_finding_not_crash(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    admin_finding = Finding(
        check="branch_protection_readable",
        status="fail",
        message="cannot read branch protection — token lacks admin scope",
        remedy="grant the transport token admin access",
    )
    monkeypatch.setattr(ep, "compare_branch_protection", lambda forge: (admin_finding,))  # noqa: ARG005
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "tok")  # noqa: ARG005
    monkeypatch.setenv(_CREDENTIAL_ENV, "tok")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(stdout="bot\n"))  # noqa: ARG005
    _stub_viewer_login(_active_fake_forge)

    result = ep.run_preflight(_cfg())

    assert result.overall == "fail"
    by_check = {f.check: f for f in result.findings}
    assert by_check["branch_protection_readable"].status == "fail"
    assert "admin scope" in by_check["branch_protection_readable"].message


def test_run_preflight_reviewer_distinctness_fail_coexists_with_other_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))

    def _resolve(engine: str, purpose: str, *, env_fallback: str | None = None) -> str:
        del engine, purpose, env_fallback
        return "shared-token"

    monkeypatch.setattr(secrets_store, "resolve_secret", _resolve)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed())  # noqa: ARG005
    _stub_viewer_login(
        _active_fake_forge, author=login_read("same-user"), reviewer=login_read("same-user"),
    )

    result = ep.run_preflight(_cfg())

    by_check = {f.check: f for f in result.findings}
    assert by_check["reviewer_distinct_from_author"].status == "fail"
    assert by_check["author_token_valid"].status == "pass"
    assert by_check["repo_clonable"].status == "pass"
    assert result.overall == "fail"


def test_load_last_preflight_returns_none_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert ep.load_last_preflight(_FORGE_TARGET) is None


def test_load_last_preflight_returns_none_on_malformed_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    path = ep._preflight_path(_FORGE_TARGET)  # noqa: SLF001
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json", encoding="utf-8")
    assert ep.load_last_preflight(_FORGE_TARGET) is None


def test_run_preflight_persists_and_round_trips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "tok")  # noqa: ARG005
    monkeypatch.setenv(_CREDENTIAL_ENV, "tok")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(stdout="bot\n"))  # noqa: ARG005
    _stub_viewer_login(_active_fake_forge)

    result = ep.run_preflight(_cfg())
    loaded = ep.load_last_preflight(_FORGE_TARGET)

    assert loaded is not None
    assert loaded == result


def _break_transport_env(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_a: Any, **_kw: Any) -> dict[str, str]:
        raise secrets_store.MissingSecretError("boom")

    monkeypatch.setattr(secrets_store, "transport_env", _raise)


def _break_subprocess_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "tok")  # noqa: ARG005
    monkeypatch.setenv(_CREDENTIAL_ENV, "tok")

    def _raise(*_a: Any, **_kw: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["gh"], timeout=1)

    monkeypatch.setattr(subprocess, "run", _raise)


def _break_subprocess_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "tok")  # noqa: ARG005
    monkeypatch.setenv(_CREDENTIAL_ENV, "tok")

    def _raise(*_a: Any, **_kw: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("no gh binary")

    monkeypatch.setattr(subprocess, "run", _raise)


_NEVER_RAISES_SCENARIOS = [
    ("transport_env_missing_secret", _break_transport_env, "fail"),
    ("subprocess_timeout", _break_subprocess_timeout, "unknown"),
    ("subprocess_oserror", _break_subprocess_oserror, "unknown"),
]


@pytest.mark.parametrize(
    "setup,expected_overall",
    [(s, o) for _, s, o in _NEVER_RAISES_SCENARIOS],
    ids=[n for n, _, _ in _NEVER_RAISES_SCENARIOS],
)
def test_run_preflight_never_raises_when_a_single_dependency_throws(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _active_fake_forge: FakeForge,
    setup: Any,
    expected_overall: str,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _stub_viewer_login(_active_fake_forge)
    setup(monkeypatch)
    result = ep.run_preflight(_cfg())
    assert result.overall == expected_overall


def test_run_preflight_never_raises_when_persist_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "tok")  # noqa: ARG005
    monkeypatch.setenv(_CREDENTIAL_ENV, "tok")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(stdout="bot\n"))  # noqa: ARG005
    _stub_viewer_login(_active_fake_forge)

    def _raise(*_a: Any, **_kw: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", _raise)

    result = ep.run_preflight(_cfg())

    assert result is not None
    assert ep.load_last_preflight(_FORGE_TARGET) is None


def test_check_dependabot_config_pass_when_file_present(_active_fake_forge: FakeForge) -> None:
    _active_fake_forge.stub("repo_file", lambda *_a, **_k: file_read("", outcome="ok"))
    finding = ep._check_dependabot_config(_FORGE_TARGET)  # noqa: SLF001
    assert finding.status == "pass"
    assert finding.check == "dependabot_config_present"


def test_check_dependabot_config_fail_when_missing(_active_fake_forge: FakeForge) -> None:
    _active_fake_forge.stub(
        "repo_file", lambda *_a, **_k: file_read(None, outcome="failed", returncode=1),
    )
    finding = ep._check_dependabot_config(_FORGE_TARGET)  # noqa: SLF001
    assert finding.status == "fail"
    assert "dependabot.yml" in finding.message
    assert finding.remedy
    assert "dependabot.yml" in finding.remedy


def test_check_dependabot_config_absent_reports_the_same_fail_verdict_as_failed(
    _active_fake_forge: FakeForge,
) -> None:
    _active_fake_forge.stub(
        "repo_file", lambda *_a, **_k: file_read(None, outcome="absent", returncode=1),
    )
    absent_finding = ep._check_dependabot_config(_FORGE_TARGET)  # noqa: SLF001
    _active_fake_forge.stub(
        "repo_file", lambda *_a, **_k: file_read(None, outcome="failed", returncode=1),
    )
    failed_finding = ep._check_dependabot_config(_FORGE_TARGET)  # noqa: SLF001
    assert absent_finding.status == failed_finding.status == "fail"
    assert absent_finding.message == failed_finding.message


def test_check_dependabot_config_unknown_on_raised(_active_fake_forge: FakeForge) -> None:
    _active_fake_forge.stub(
        "repo_file", lambda *_a, **_k: file_read(None, outcome="raised", stderr="timed out"),
    )
    finding = ep._check_dependabot_config(_FORGE_TARGET)  # noqa: SLF001
    assert finding.status == "unknown"


def test_check_dependabot_config_calls_repo_file_with_the_dependabot_path_and_timeout(
    _active_fake_forge: FakeForge,
) -> None:
    _active_fake_forge.stub("repo_file", lambda *_a, **_k: file_read("", outcome="ok"))
    ep._check_dependabot_config(_FORGE_TARGET)  # noqa: SLF001
    (call,) = _active_fake_forge.calls
    assert call[0] == "repo_file"
    assert call[1] == (ep._DEPENDABOT_CONFIG_PATH,)  # noqa: SLF001
    assert call[2]["forge_target"] == _FORGE_TARGET
    assert call[2]["timeout"] == ep._DEPENDABOT_CHECK_TIMEOUT_S  # noqa: SLF001


def test_check_dependency_sweep_roster_pass_when_empty_graph() -> None:
    cfg = _cfg(transition_graph={})
    finding = ep._check_dependency_sweep_roster(cfg)  # noqa: SLF001
    assert finding.status == "pass"


_ROSTER_COMPLETE_GRAPH = {
    "implementation": ["verification"],
    "implementation_fix": ["verification"],
    "verification": ["implementation_fix", "pr_merge"],
    "pr_merge": ["implementation_fix"],
    "dependency_sweep": [],
}


def test_check_dependency_sweep_roster_pass_when_graph_declares_all_required_teams() -> None:
    cfg = _cfg(transition_graph=_ROSTER_COMPLETE_GRAPH, flow_policy=None)
    finding = ep._check_dependency_sweep_roster(cfg)  # noqa: SLF001
    assert finding.status == "pass"


def test_check_dependency_sweep_roster_fail_when_missing_required_teams() -> None:
    cfg = _cfg(transition_graph=_BAD_TRANSITION_GRAPH, flow_policy=None)
    finding = ep._check_dependency_sweep_roster(cfg)  # noqa: SLF001
    assert finding.status == "fail"
    assert "implementation_fix" in finding.message
    assert "dependency_sweep" not in finding.message
    assert finding.remedy


_BOTH_REQUIRED_TEAMS_MISSING_GRAPH = {"research": ["architecture"], "architecture": []}


def test_check_dependency_sweep_roster_fail_when_missing_both_required_teams() -> None:
    cfg = _cfg(transition_graph=_BOTH_REQUIRED_TEAMS_MISSING_GRAPH, flow_policy=None)
    finding = ep._check_dependency_sweep_roster(cfg)  # noqa: SLF001
    assert finding.status == "fail"
    assert "pr_merge" in finding.message
    assert "implementation_fix" in finding.message
    assert "dependency_sweep" not in finding.message


def test_check_dependency_sweep_roster_pass_for_default_engine_config_real_graph() -> None:
    finding = ep._check_dependency_sweep_roster(default_engine_config())  # noqa: SLF001
    assert finding.status == "pass"


def test_check_dependency_sweep_enable_returns_none_when_both_pass(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "tok")  # noqa: ARG005
    _active_fake_forge.stub("repo_file", lambda *_a, **_k: file_read("", outcome="ok"))
    cfg = _cfg(transition_graph=_ROSTER_COMPLETE_GRAPH, flow_policy=None)
    assert ep.check_dependency_sweep_enable(cfg) is None


def test_check_dependency_sweep_enable_returns_none_for_empty_transition_graph(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "tok")  # noqa: ARG005
    _active_fake_forge.stub("repo_file", lambda *_a, **_k: file_read("", outcome="ok"))
    cfg = _cfg(transition_graph={}, flow_policy=None)
    assert ep.check_dependency_sweep_enable(cfg) is None


def test_check_dependency_sweep_enable_returns_dependabot_finding_first(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "tok")  # noqa: ARG005
    _active_fake_forge.stub(
        "repo_file", lambda *_a, **_k: file_read(None, outcome="failed", returncode=1),
    )
    cfg = _cfg(transition_graph=_ROSTER_COMPLETE_GRAPH, flow_policy=None)
    finding = ep.check_dependency_sweep_enable(cfg)
    assert finding is not None
    assert finding.check == "dependabot_config_present"


def test_check_dependency_sweep_enable_returns_roster_finding_when_dependabot_ok(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "tok")  # noqa: ARG005
    _active_fake_forge.stub("repo_file", lambda *_a, **_k: file_read("", outcome="ok"))
    cfg = _cfg(transition_graph=_BAD_TRANSITION_GRAPH, flow_policy=None)
    finding = ep.check_dependency_sweep_enable(cfg)
    assert finding is not None
    assert finding.check == "dependency_sweep_roster_present"


def test_check_dependency_sweep_enable_returns_none_for_default_engine_config(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "tok")  # noqa: ARG005
    _active_fake_forge.stub("repo_file", lambda *_a, **_k: file_read("", outcome="ok"))
    assert ep.check_dependency_sweep_enable(default_engine_config()) is None


def _prepare_run(
    install: Install, monkeypatch: pytest.MonkeyPatch, forge: FakeForge, **logins: LoginRead
) -> None:
    monkeypatch.setenv(_CREDENTIAL_ENV, "tok")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(stdout="bot\n"))  # noqa: ARG005
    _stub_viewer_login(forge, **logins)


@pytest.fixture
def install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Install:
    return Install.isolated(tmp_path, monkeypatch)


def _readiness_row(result: ep.PreflightResult) -> Finding:
    assert result.findings[-1].check == "dispatch_readiness"
    return result.findings[-1]


def test_run_preflight_readiness_row_passes_for_an_install_with_no_requirements(
    install: Install, monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    install.require()
    _prepare_run(install, monkeypatch, _active_fake_forge)
    result = ep.run_preflight(_cfg())
    row = _readiness_row(result)
    assert row.status == "pass"
    assert row.remedy == ""


def test_run_preflight_readiness_row_never_moves_overall(
    install: Install, monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    install.require(REVIEWER, CODE_QUALITY)
    _prepare_run(install, monkeypatch, _active_fake_forge)
    result = ep.run_preflight(_cfg())
    row = _readiness_row(result)
    assert row.status == "fail"
    assert result.overall == ep._worst(f.status for f in result.findings[:-1])  # noqa: SLF001


def test_run_preflight_readiness_row_names_each_failing_requirement_and_its_fix(
    install: Install, monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    install.require(REVIEWER, CODE_QUALITY)
    _prepare_run(install, monkeypatch, _active_fake_forge)
    row = _readiness_row(ep.run_preflight(_cfg()))
    assert "reviewer_credential: no reviewer credential is stored" in row.message
    assert "code_quality: SonarCloud has not reported" in row.message
    assert row.remedy == "./setup.sh"


def test_run_preflight_uses_its_own_fresh_distinctness_answer_for_the_readiness_row(
    install: Install, monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    install.require(REVIEWER)
    install.store_reviewer()
    _prepare_run(install, monkeypatch, _active_fake_forge)
    assert ep.load_last_preflight(_FORGE_TARGET) is None
    result = ep.run_preflight(_cfg())
    assert _readiness_row(result).status == "pass"
    assert result.reviewer_fingerprint == REVIEWER_FINGERPRINT


def test_run_preflight_readiness_row_fails_when_author_and_reviewer_are_the_same_account(
    install: Install, monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    install.require(REVIEWER)
    install.store_reviewer()
    same = login_read("same-user")
    _prepare_run(install, monkeypatch, _active_fake_forge, author=same, reviewer=same)
    result = ep.run_preflight(_cfg())
    row = _readiness_row(result)
    assert row.status == "fail"
    assert "same account" in row.message
    (issue,) = ep.evaluate_readiness(_cfg())
    assert issue.state == "not_ready"


def test_run_preflight_readiness_row_is_unknown_when_the_reviewer_cannot_be_resolved(
    install: Install, monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    install.require(REVIEWER)
    install.store_reviewer()
    _prepare_run(
        install, monkeypatch, _active_fake_forge, reviewer=login_read(None, outcome="no_credential")
    )
    row = _readiness_row(ep.run_preflight(_cfg()))
    assert row.status == "unknown"


def test_run_preflight_persists_the_readiness_row_with_the_result(
    install: Install, monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    install.require(REVIEWER)
    install.store_reviewer()
    _prepare_run(install, monkeypatch, _active_fake_forge)
    result = ep.run_preflight(_cfg())
    loaded = ep.load_last_preflight(_FORGE_TARGET)
    assert loaded == result
    assert ep.evaluate_readiness(_cfg()) == ()


def test_run_preflight_records_no_reviewer_fingerprint_without_a_stored_reviewer(
    install: Install, monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    install.require()
    _prepare_run(install, monkeypatch, _active_fake_forge)
    assert ep.run_preflight(_cfg()).reviewer_fingerprint is None


def test_run_preflight_records_no_reviewer_fingerprint_when_the_record_is_unreadable(
    install: Install, monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    install.require()
    install.store_reviewer()
    path = secrets_store._key_path("acme/widget", secrets_store.REVIEWER_PURPOSE)  # noqa: SLF001
    path.write_text("{", encoding="utf-8")
    _prepare_run(install, monkeypatch, _active_fake_forge)
    assert ep.run_preflight(_cfg()).reviewer_fingerprint is None


def test_a_result_saved_before_the_fingerprint_existed_still_loads() -> None:
    legacy = json.dumps(
        {"slug": "acme/widget", "checked_at": "2026-01-01T00:00:00+00:00", "overall": "pass",
         "findings": []}
    )
    assert ep.PreflightResult.model_validate_json(legacy).reviewer_fingerprint is None


def test_readiness_row_is_unknown_when_only_unknown_issues_remain() -> None:
    issue = ep.ReadinessIssue(
        engine="acme/widget", requirement="reviewer_credential", state="unknown",
        message="m", fix="./setup.sh",
    )
    row = ep._readiness_finding((issue,))  # noqa: SLF001
    assert (row.check, row.status, row.remedy) == ("dispatch_readiness", "unknown", "./setup.sh")


def test_readiness_row_lists_a_shared_fix_once() -> None:
    issues = tuple(
        ep.ReadinessIssue(
            engine="acme/widget", requirement=name, state="not_ready", message="m",
            fix="./setup.sh",
        )
        for name in ("reviewer_credential", "code_quality")
    )
    row = ep._readiness_finding(issues)  # noqa: SLF001
    assert row.status == "fail"
    assert row.remedy == "./setup.sh"


def test_run_preflight_readiness_row_is_pass_when_only_an_expiry_warning_remains(
    install: Install, monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    install.require(REVIEWER)
    expiry = (datetime.now(UTC).date() + timedelta(days=5)).isoformat()
    install.store_reviewer(expires_at=expiry)
    _prepare_run(install, monkeypatch, _active_fake_forge)
    row = _readiness_row(ep.run_preflight(_cfg()))
    assert row.status == "pass"
    assert expiry in row.message
    assert row.remedy == "./setup.sh"


def test_reviewer_token_fixture_is_not_echoed_in_the_readiness_row(
    install: Install, monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    install.require(REVIEWER)
    install.store_reviewer()
    _prepare_run(install, monkeypatch, _active_fake_forge)
    result = ep.run_preflight(_cfg())
    assert REVIEWER_TOKEN not in result.model_dump_json()
