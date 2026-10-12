from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from alan import engine_config as engine_config_mod
from alan import engine_preflight, github_repos, repo_scaffold, secrets_store
from alan.branch_protection_compat import ApplyOutcome, Finding
from alan.engine_config import EngineConfig, ForgeConfig, RepoIdentity, default_engine_config
from alan.forge import LoginRead
from tests.fakes.forge import FakeForge
from tests.forge_stubs import always, api_result, login_read, rows_read

_CREDENTIAL_ENV = "ACME_GH_TOKEN"  # noqa: S105


@pytest.fixture(autouse=True)
def _state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))


@pytest.fixture(autouse=True)
def _engines_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "engines"))


def _cfg(**overrides: Any) -> EngineConfig:
    repo = RepoIdentity(owner="acme", repo="widget")
    base = default_engine_config().model_copy(update={
        "repo": repo,
        "forge": ForgeConfig(repo=repo, credential_env_name=_CREDENTIAL_ENV),
        "preset": "python",
    })
    return base.model_copy(update=overrides)


def _no_forge_cfg() -> EngineConfig:
    return EngineConfig(repo=RepoIdentity(owner="acme", repo="bare"))


def _completed(
    *, rc: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)


_WORKFLOW_SCOPE_STDOUT = (
    'HTTP/2.0 200 OK\r\nX-OAuth-Scopes: repo, workflow\r\n\r\n{"login":"stub"}\n'
)


def _fake_subprocess_run(
    *, ls_remote_stdout: str = "abc123\tHEAD\n",
    status_stdout: str = " M CONVENTIONS.md\n",
    gh_api_user_stdout: str = _WORKFLOW_SCOPE_STDOUT,
) -> Callable[..., subprocess.CompletedProcess[str]]:
    def _run(cmd: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        if cmd[0] == "gh" and cmd[1:4] == ["api", "-i", "user"]:
            return _completed(stdout=gh_api_user_stdout)
        if cmd[0] == "git":
            if "ls-remote" in cmd:
                return _completed(stdout=ls_remote_stdout)
            if "status" in cmd and "--porcelain" in cmd:
                return _completed(stdout=status_stdout)
            return _completed()
        raise AssertionError(f"unexpected command: {cmd}")

    return _run


def _with_token(monkeypatch: pytest.MonkeyPatch, token: str | None = "tok-123") -> None:  # noqa: S107
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: token)  # noqa: ARG005


def test_onboarding_status_defaults_when_never_persisted() -> None:
    cfg = _cfg()
    state = repo_scaffold.onboarding_status(cfg)
    assert state.slug == "acme/widget"
    assert state.scaffold_pr_number is None
    assert state.protection_deferred == ()


def test_persist_and_load_state_roundtrip() -> None:
    cfg = _cfg()
    forge = repo_scaffold._forge_target(cfg)  # noqa: SLF001
    state = repo_scaffold.OnboardingState(
        slug="acme/widget", stack="python", scaffold_pr_number=3,
        scaffold_pr_url="https://github.com/acme/widget/pull/3",
        authoritative_checks=frozenset({"python-ci"}),
    )
    repo_scaffold._persist_state(forge, state)  # noqa: SLF001
    loaded = repo_scaffold._load_state(forge)  # noqa: SLF001
    assert loaded == state


def test_load_state_missing_file_returns_none() -> None:
    cfg = _cfg()
    forge = repo_scaffold._forge_target(cfg)  # noqa: SLF001
    assert repo_scaffold._load_state(forge) is None  # noqa: SLF001


def test_load_state_malformed_json_returns_none(tmp_path: Path) -> None:
    cfg = _cfg()
    forge = repo_scaffold._forge_target(cfg)  # noqa: SLF001
    path = repo_scaffold._state_path(forge)  # noqa: SLF001
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json")
    assert repo_scaffold._load_state(forge) is None  # noqa: SLF001


def test_persist_state_swallows_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg()
    forge = repo_scaffold._forge_target(cfg)  # noqa: SLF001

    def _boom(*_a: Any, **_kw: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", _boom)
    repo_scaffold._persist_state(forge, repo_scaffold.OnboardingState(slug="acme/widget"))  # noqa: SLF001


def test_require_forge_raises_when_forge_none() -> None:
    with pytest.raises(repo_scaffold.ScaffoldError, match="no forge configured"):
        repo_scaffold._require_forge(_no_forge_cfg())  # noqa: SLF001


def test_forge_target_derives_from_forge_config() -> None:
    forge = repo_scaffold._forge_target(_cfg())  # noqa: SLF001
    assert forge.repo.slug == "acme/widget"
    assert forge.credential_env_name == _CREDENTIAL_ENV


def test_scaffold_raises_when_no_forge() -> None:
    with pytest.raises(repo_scaffold.ScaffoldError, match="no forge configured"):
        repo_scaffold.scaffold(_no_forge_cfg(), project_title="", project_doc="")


def test_scaffold_raises_when_no_preset(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg(preset=None)
    with pytest.raises(repo_scaffold.ScaffoldError, match="no scaffold template"):
        repo_scaffold.scaffold(cfg, project_title="", project_doc="")


def test_scaffold_raises_when_preset_unknown() -> None:
    cfg = _cfg(preset="rust")
    with pytest.raises(repo_scaffold.ScaffoldError, match="no scaffold template"):
        repo_scaffold.scaffold(cfg, project_title="", project_doc="")


def test_scaffold_raises_when_no_credential(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    _with_token(monkeypatch, None)
    cfg = _cfg()
    with pytest.raises(repo_scaffold.ScaffoldError, match="no transport credential"):
        repo_scaffold.scaffold(cfg, project_title="", project_doc="")
    assert _active_fake_forge.calls == []


def test_parse_pr_row_none_for_empty_list() -> None:
    assert repo_scaffold._parse_pr_row([]) is None  # noqa: SLF001


def test_parse_pr_row_none_for_non_list() -> None:
    assert repo_scaffold._parse_pr_row({"number": 1}) is None  # noqa: SLF001


def test_parse_pr_row_none_when_row_not_dict() -> None:
    assert repo_scaffold._parse_pr_row(["not-a-dict"]) is None  # noqa: SLF001


def test_parse_pr_row_none_when_fields_wrong_type() -> None:
    assert repo_scaffold._parse_pr_row([{"number": "7", "url": 1}]) is None  # noqa: SLF001


def test_parse_pr_row_returns_opened_pr_for_valid_row() -> None:
    row = repo_scaffold._parse_pr_row(  # noqa: SLF001
        [{"number": 7, "url": "https://github.com/acme/widget/pull/7"}]
    )
    assert row == repo_scaffold._OpenedPr(  # noqa: SLF001
        number=7, url="https://github.com/acme/widget/pull/7"
    )


def test_find_existing_pr_none_on_raise(_active_fake_forge: FakeForge) -> None:
    _active_fake_forge.stub(
        "prs_by_head", lambda *_a, **_k: rows_read(outcome="raised", rows=None)
    )
    forge = repo_scaffold._forge_target(_cfg())  # noqa: SLF001
    assert repo_scaffold._find_existing_pr(forge, "branch") is None  # noqa: SLF001


def test_find_existing_pr_none_on_nonzero_returncode(_active_fake_forge: FakeForge) -> None:
    _active_fake_forge.stub(
        "prs_by_head", lambda *_a, **_k: rows_read(outcome="failed", rows=None)
    )
    forge = repo_scaffold._forge_target(_cfg())  # noqa: SLF001
    assert repo_scaffold._find_existing_pr(forge, "branch") is None  # noqa: SLF001


def test_find_existing_pr_none_on_invalid_json(_active_fake_forge: FakeForge) -> None:
    _active_fake_forge.stub("prs_by_head", lambda *_a, **_k: rows_read(rows=None))
    forge = repo_scaffold._forge_target(_cfg())  # noqa: SLF001
    assert repo_scaffold._find_existing_pr(forge, "branch") is None  # noqa: SLF001


def test_find_existing_pr_ok_with_empty_rows_means_no_pr(_active_fake_forge: FakeForge) -> None:
    _active_fake_forge.stub("prs_by_head", lambda *_a, **_k: rows_read(rows=()))
    forge = repo_scaffold._forge_target(_cfg())  # noqa: SLF001
    assert repo_scaffold._find_existing_pr(forge, "branch") is None  # noqa: SLF001


def test_find_existing_pr_one_row_returns_it_without_clone_or_create(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    _with_token(monkeypatch)
    _active_fake_forge.stub(
        "prs_by_head",
        lambda *_a, **_k: rows_read(
            rows=({"number": 11, "url": "https://github.com/acme/widget/pull/11"},)
        ),
    )
    cfg = _cfg()

    def _boom(*_a: Any, **_kw: Any) -> subprocess.CompletedProcess[str]:
        raise AssertionError("must not run a subprocess when an existing PR is found")

    monkeypatch.setattr(subprocess, "run", _boom)

    state = repo_scaffold.scaffold(cfg, project_title="Widget", project_doc="")

    assert state.scaffold_pr_number == 11
    assert not any(name == "create_pr" for name, _, _ in _active_fake_forge.calls)


def test_parse_pr_row_accepts_tuple_rows() -> None:
    row = repo_scaffold._parse_pr_row(  # noqa: SLF001
        ({"number": 7, "url": "https://github.com/acme/widget/pull/7"},)
    )
    assert row == repo_scaffold._OpenedPr(  # noqa: SLF001
        number=7, url="https://github.com/acme/widget/pull/7"
    )


def test_union_authoritative_checks_no_op_when_already_present() -> None:
    cfg = _cfg()
    forge_cfg = cfg.forge
    assert forge_cfg is not None
    forge_cfg = forge_cfg.model_copy(update={"authoritative_checks": frozenset({"python-ci"})})

    def _boom(*_a: Any, **_kw: Any) -> None:
        raise AssertionError("must not persist when checks already present")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(engine_config_mod, "update_and_persist", _boom)
        result = repo_scaffold._union_authoritative_checks(  # noqa: SLF001
            cfg, forge_cfg, frozenset({"python-ci"})
        )
    assert result == frozenset({"python-ci"})


def test_union_authoritative_checks_swallows_engine_not_found() -> None:
    cfg = _cfg()
    forge_cfg = cfg.forge
    assert forge_cfg is not None
    result = repo_scaffold._union_authoritative_checks(  # noqa: SLF001
        cfg, forge_cfg, frozenset({"python-ci"})
    )
    assert result == frozenset({"python-ci"})


def test_union_authoritative_checks_persists_when_engine_registered() -> None:
    cfg = _cfg()
    engine_config_mod.register_and_persist(cfg, validate_env=False)
    forge_cfg = cfg.forge
    assert forge_cfg is not None
    repo_scaffold._union_authoritative_checks(cfg, forge_cfg, frozenset({"python-ci"}))  # noqa: SLF001
    persisted = engine_config_mod.lookup_by_repo(cfg.repo)
    assert persisted is not None
    assert persisted.forge is not None
    assert persisted.forge.authoritative_checks == frozenset({"python-ci"})


def test_scaffold_idempotent_returns_existing_state_without_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg()
    forge = repo_scaffold._forge_target(cfg)  # noqa: SLF001
    existing = repo_scaffold.OnboardingState(
        slug="acme/widget", stack="python", scaffold_pr_number=9,
        scaffold_pr_url="https://github.com/acme/widget/pull/9",
    )
    repo_scaffold._persist_state(forge, existing)  # noqa: SLF001

    def _boom(*_a: Any, **_kw: Any) -> None:
        raise AssertionError("subprocess must not run for an idempotent scaffold call")

    monkeypatch.setattr(subprocess, "run", _boom)
    result = repo_scaffold.scaffold(cfg, project_title="Widget", project_doc="")
    assert result == existing


def test_scaffold_adopts_live_pr_found_via_gh_pr_list_when_state_lost(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    _with_token(monkeypatch)
    cfg = _cfg()
    _active_fake_forge.stub(
        "prs_by_head",
        lambda *_a, **_k: rows_read(
            rows=({"number": 11, "url": "https://github.com/acme/widget/pull/11"},)
        ),
    )
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run())

    state = repo_scaffold.scaffold(cfg, project_title="Widget", project_doc="")

    assert state.scaffold_pr_number == 11
    assert state.scaffold_pr_url == "https://github.com/acme/widget/pull/11"
    persisted = repo_scaffold.onboarding_status(cfg)
    assert persisted.scaffold_pr_number == 11


def test_scaffold_happy_path_writes_files_opens_pr_and_unions_checks(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    _with_token(monkeypatch)
    cfg = _cfg()
    engine_config_mod.register_and_persist(cfg, validate_env=False)
    _active_fake_forge.stub("prs_by_head", lambda *_a, **_k: rows_read(rows=()))
    _active_fake_forge.stub(
        "create_pr", lambda *_a, **_k: api_result(stdout="https://github.com/acme/widget/pull/7\n")
    )
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run())

    captured: dict[str, str] = {}
    real_write = repo_scaffold._write_scaffold_files

    def _spy_write(repo_path: Path, template: Any, project_title: str, project_doc: str) -> None:
        real_write(repo_path, template, project_title, project_doc)
        captured["ci"] = (repo_path / ".github" / "workflows" / "ci.yml").read_text()
        captured["dependabot"] = (repo_path / ".github" / "dependabot.yml").read_text()
        captured["conventions"] = (repo_path / "CONVENTIONS.md").read_text()

    monkeypatch.setattr(repo_scaffold, "_write_scaffold_files", _spy_write)

    state = repo_scaffold.scaffold(cfg, project_title="Widget Co", project_doc="Vision: ship it")

    assert state.scaffold_pr_number == 7
    assert state.scaffold_pr_url == "https://github.com/acme/widget/pull/7"
    assert state.stack == "python"
    assert state.authoritative_checks == frozenset({"python-ci"})
    assert "python-ci" in captured["ci"]
    assert "package-ecosystem" in captured["dependabot"]
    assert "Widget Co" in captured["conventions"]
    assert "Vision: ship it" in captured["conventions"]

    persisted = engine_config_mod.lookup_by_repo(cfg.repo)
    assert persisted is not None
    assert persisted.forge is not None
    assert persisted.forge.authoritative_checks == frozenset({"python-ci"})

    reloaded = repo_scaffold.onboarding_status(cfg)
    assert reloaded == state


def test_scaffold_second_call_after_persisted_state_does_not_reopen_pr(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    _with_token(monkeypatch)
    cfg = _cfg()
    engine_config_mod.register_and_persist(cfg, validate_env=False)
    _active_fake_forge.stub("prs_by_head", lambda *_a, **_k: rows_read(rows=()))
    _active_fake_forge.stub(
        "create_pr", lambda *_a, **_k: api_result(stdout="https://github.com/acme/widget/pull/7\n")
    )
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run())

    first = repo_scaffold.scaffold(cfg, project_title="Widget Co", project_doc="")

    def _boom(*_a: Any, **_kw: Any) -> None:
        raise AssertionError("must not reopen a PR on a second scaffold call")

    monkeypatch.setattr(subprocess, "run", _boom)
    second = repo_scaffold.scaffold(cfg, project_title="Widget Co", project_doc="")
    assert second == first


def test_scaffold_preserves_prior_protection_state_on_idempotent_reuse(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    _with_token(monkeypatch)
    cfg = _cfg()
    engine_config_mod.register_and_persist(cfg, validate_env=False)
    _active_fake_forge.stub("prs_by_head", lambda *_a, **_k: rows_read(rows=()))
    _active_fake_forge.stub(
        "create_pr", lambda *_a, **_k: api_result(stdout="https://github.com/acme/widget/pull/7\n")
    )
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run())
    forge = repo_scaffold._forge_target(cfg)  # noqa: SLF001

    prior = repo_scaffold.OnboardingState(
        slug="acme/widget", protection_applied_keys=frozenset({"squash_merge_enabled"}),
        protection_deferred=(Finding(check="required_checks", status="fail", message="x"),),
    )
    repo_scaffold._persist_state(forge, prior)  # noqa: SLF001

    result = repo_scaffold.scaffold(cfg, project_title="Widget Co", project_doc="")

    assert result.protection_applied_keys == frozenset({"squash_merge_enabled"})
    assert result.protection_deferred == prior.protection_deferred


def test_scaffold_raises_when_no_file_changes(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    _with_token(monkeypatch)
    cfg = _cfg()
    _active_fake_forge.stub("prs_by_head", lambda *_a, **_k: rows_read(rows=()))
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run(status_stdout=""))
    with pytest.raises(repo_scaffold.ScaffoldError, match="no file changes"):
        repo_scaffold.scaffold(cfg, project_title="", project_doc="")


def test_scaffold_raises_when_clone_fails(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    _with_token(monkeypatch)
    cfg = _cfg()
    _active_fake_forge.stub("prs_by_head", lambda *_a, **_k: rows_read(rows=()))

    def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        if cmd[0] == "git" and "clone" in cmd:
            return _completed(rc=1, stderr="clone failed")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", _run)
    with pytest.raises(repo_scaffold.ScaffoldError, match="git clone failed"):
        repo_scaffold.scaffold(cfg, project_title="", project_doc="")


def test_scaffold_raises_when_checkout_fails(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    _with_token(monkeypatch)
    cfg = _cfg()
    _active_fake_forge.stub("prs_by_head", lambda *_a, **_k: rows_read(rows=()))

    def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        if cmd[0] == "git" and "checkout" in cmd:
            return _completed(rc=1, stderr="checkout failed")
        if cmd[0] == "git":
            return _completed()
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", _run)
    with pytest.raises(repo_scaffold.ScaffoldError, match="git checkout -b failed"):
        repo_scaffold.scaffold(cfg, project_title="", project_doc="")


def test_scaffold_raises_when_git_add_fails(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    _with_token(monkeypatch)
    cfg = _cfg()
    _active_fake_forge.stub("prs_by_head", lambda *_a, **_k: rows_read(rows=()))

    def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        if cmd[0] == "git" and "add" in cmd:
            return _completed(rc=1, stderr="add failed")
        if cmd[0] == "git":
            return _completed()
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", _run)
    with pytest.raises(repo_scaffold.ScaffoldError, match="git add failed"):
        repo_scaffold.scaffold(cfg, project_title="", project_doc="")


def test_scaffold_raises_when_git_status_fails(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    _with_token(monkeypatch)
    cfg = _cfg()
    _active_fake_forge.stub("prs_by_head", lambda *_a, **_k: rows_read(rows=()))

    def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        if cmd[0] == "git" and "status" in cmd and "--porcelain" in cmd:
            return _completed(rc=1, stderr="status failed")
        if cmd[0] == "git":
            return _completed()
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", _run)
    with pytest.raises(repo_scaffold.ScaffoldError, match="git status failed"):
        repo_scaffold.scaffold(cfg, project_title="", project_doc="")


def test_scaffold_raises_when_commit_fails(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    _with_token(monkeypatch)
    cfg = _cfg()
    _active_fake_forge.stub("prs_by_head", lambda *_a, **_k: rows_read(rows=()))

    def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        if cmd[0] == "git" and "commit" in cmd:
            return _completed(rc=1, stderr="commit failed")
        if cmd[0] == "git" and "status" in cmd:
            return _completed(stdout=" M x\n")
        if cmd[0] == "git":
            return _completed()
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", _run)
    with pytest.raises(repo_scaffold.ScaffoldError, match="git commit failed"):
        repo_scaffold.scaffold(cfg, project_title="", project_doc="")


def test_scaffold_raises_when_push_fails(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    _with_token(monkeypatch)
    cfg = _cfg()
    _active_fake_forge.stub("prs_by_head", lambda *_a, **_k: rows_read(rows=()))

    def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        if cmd[0] == "git" and "push" in cmd:
            return _completed(rc=1, stderr="push failed")
        if cmd[0] == "git" and "status" in cmd:
            return _completed(stdout=" M x\n")
        if cmd[0] == "git":
            return _completed()
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", _run)
    with pytest.raises(repo_scaffold.ScaffoldError) as exc_info:
        repo_scaffold.scaffold(cfg, project_title="", project_doc="")
    assert "git push failed: no further detail from git" in str(exc_info.value)
    assert "PushOutcome" not in str(exc_info.value)


def test_scaffold_push_failure_prefers_the_forges_own_reason_over_the_outcome_name(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    _with_token(monkeypatch)
    cfg = _cfg()
    _active_fake_forge.stub("prs_by_head", lambda *_a, **_k: rows_read(rows=()))
    remote_reason = (
        "refusing to allow a Personal Access Token to create or update workflow "
        "`.github/workflows/ci.yml` without `workflow` scope"
    )

    def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        if cmd[0] == "git" and "push" in cmd:
            return _completed(
                rc=1,
                stderr=(
                    "To https://github.com/acme/widget.git\n"
                    " ! [remote rejected] alan/repo-scaffold -> alan/repo-scaffold\n"
                    f"remote: {remote_reason}\n"
                    "error: failed to push some refs\n"
                ),
            )
        if cmd[0] == "git" and "status" in cmd:
            return _completed(stdout=" M x\n")
        if cmd[0] == "git":
            return _completed()
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", _run)
    with pytest.raises(repo_scaffold.ScaffoldError) as exc_info:
        repo_scaffold.scaffold(cfg, project_title="", project_doc="")
    assert remote_reason in str(exc_info.value)
    assert "PushOutcome" not in str(exc_info.value)


def test_scaffold_raises_when_pr_create_fails(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    _with_token(monkeypatch)
    cfg = _cfg()
    _active_fake_forge.stub("prs_by_head", lambda *_a, **_k: rows_read(rows=()))
    _active_fake_forge.stub(
        "create_pr", lambda *_a, **_k: api_result(returncode=1, stderr="pr create failed")
    )
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run(status_stdout=" M x\n"))
    with pytest.raises(repo_scaffold.ScaffoldError, match="gh pr create failed"):
        repo_scaffold.scaffold(cfg, project_title="", project_doc="")


def test_scaffold_raises_when_pr_create_output_unparseable(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    _with_token(monkeypatch)
    cfg = _cfg()
    _active_fake_forge.stub("prs_by_head", lambda *_a, **_k: rows_read(rows=()))
    _active_fake_forge.stub("create_pr", lambda *_a, **_k: api_result(stdout="no url here"))
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run(status_stdout=" M x\n"))
    with pytest.raises(repo_scaffold.ScaffoldError, match="gh pr create failed"):
        repo_scaffold.scaffold(cfg, project_title="", project_doc="")


def test_create_scaffold_pr_propagates_a_transport_fault(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    _with_token(monkeypatch)
    cfg = _cfg()
    _active_fake_forge.stub("prs_by_head", lambda *_a, **_k: rows_read(rows=()))
    always(_active_fake_forge, "create_pr", FileNotFoundError("gh"))
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run(status_stdout=" M x\n"))
    with pytest.raises(FileNotFoundError):
        repo_scaffold.scaffold(cfg, project_title="", project_doc="")


def test_protect_raises_when_no_forge() -> None:
    with pytest.raises(repo_scaffold.ScaffoldError, match="no forge configured"):
        repo_scaffold.protect(_no_forge_cfg())


def test_protect_persists_applied_and_deferred_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg()
    outcomes = (
        ApplyOutcome(key="squash_merge_enabled", status="applied"),
        ApplyOutcome(
            key="required_checks", status="deferred",
            finding=Finding(
                check="required_checks", status="fail", message="nope", remedy="fix it"
            ),
        ),
    )
    monkeypatch.setattr(repo_scaffold, "apply_branch_protection", lambda forge: outcomes)  # noqa: ARG005

    state = repo_scaffold.protect(cfg)

    assert state.protection_applied_keys == frozenset({"squash_merge_enabled"})
    assert len(state.protection_deferred) == 1
    assert state.protection_deferred[0].check == "required_checks"

    reloaded = repo_scaffold.onboarding_status(cfg)
    assert reloaded == state


def test_protect_preserves_scaffold_pr_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg()
    forge = repo_scaffold._forge_target(cfg)  # noqa: SLF001
    repo_scaffold._persist_state(  # noqa: SLF001
        forge,
        repo_scaffold.OnboardingState(
            slug="acme/widget", scaffold_pr_number=4,
            scaffold_pr_url="https://github.com/acme/widget/pull/4",
        ),
    )
    monkeypatch.setattr(repo_scaffold, "apply_branch_protection", lambda forge: ())  # noqa: ARG005

    state = repo_scaffold.protect(cfg)

    assert state.scaffold_pr_number == 4


def test_check_push_repo_unknown_without_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_token(monkeypatch, None)
    forge = repo_scaffold._forge_target(_cfg())  # noqa: SLF001
    finding = repo_scaffold._check_push_repo(forge)  # noqa: SLF001
    assert finding.check == "push_repository"
    assert finding.status == "unknown"


def test_check_push_repo_pass_when_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_token(monkeypatch)
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run())
    forge = repo_scaffold._forge_target(_cfg())  # noqa: SLF001
    finding = repo_scaffold._check_push_repo(forge)  # noqa: SLF001
    assert finding.status == "pass"


def test_check_push_repo_fail_when_no_head(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_token(monkeypatch)
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run(ls_remote_stdout=""))
    forge = repo_scaffold._forge_target(_cfg())  # noqa: SLF001
    finding = repo_scaffold._check_push_repo(forge)  # noqa: SLF001
    assert finding.status == "fail"


def test_check_push_repo_unknown_on_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_token(monkeypatch)

    def _raise(*_a: Any, **_kw: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["git"], timeout=5)

    monkeypatch.setattr(subprocess, "run", _raise)
    forge = repo_scaffold._forge_target(_cfg())  # noqa: SLF001
    finding = repo_scaffold._check_push_repo(forge)  # noqa: SLF001
    assert finding.status == "unknown"


def test_check_credentials_fail_without_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_token(monkeypatch, None)
    forge = repo_scaffold._forge_target(_cfg())  # noqa: SLF001
    finding = repo_scaffold._check_credentials(forge)  # noqa: SLF001
    assert finding.status == "fail"
    assert finding.check == "mint_credentials"


def test_check_credentials_fail_without_reviewer(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _resolve(_engine: str, purpose: str, *, env_fallback: str | None) -> str | None:
        del env_fallback
        calls.append(purpose)
        return "tok" if purpose == secrets_store.TRANSPORT_PURPOSE else None

    monkeypatch.setattr(secrets_store, "resolve_secret", _resolve)
    forge = repo_scaffold._forge_target(_cfg())  # noqa: SLF001
    finding = repo_scaffold._check_credentials(forge)  # noqa: SLF001
    assert finding.status == "fail"
    assert "reviewer" in finding.message


def test_check_credentials_pass_when_both_present(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_token(monkeypatch)
    forge = repo_scaffold._forge_target(_cfg())  # noqa: SLF001
    finding = repo_scaffold._check_credentials(forge)  # noqa: SLF001
    assert finding.status == "pass"


def test_check_workflow_write_capability_unknown_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_token(monkeypatch, None)
    forge = repo_scaffold._forge_target(_cfg())  # noqa: SLF001
    finding = repo_scaffold._check_workflow_write_capability(forge)  # noqa: SLF001
    assert finding.check == "workflow_write"
    assert finding.status == "unknown"
    assert "no transport credential" in finding.message


def test_check_workflow_write_capability_pass_when_scope_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_token(monkeypatch)
    monkeypatch.setattr(github_repos, "probe_workflow_scope", lambda token: "present")  # noqa: ARG005
    forge = repo_scaffold._forge_target(_cfg())  # noqa: SLF001
    finding = repo_scaffold._check_workflow_write_capability(forge)  # noqa: SLF001
    assert finding.status == "pass"


def test_check_workflow_write_capability_fail_when_scope_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_token(monkeypatch)
    monkeypatch.setattr(github_repos, "probe_workflow_scope", lambda token: "absent")  # noqa: ARG005
    forge = repo_scaffold._forge_target(_cfg())  # noqa: SLF001
    finding = repo_scaffold._check_workflow_write_capability(forge)  # noqa: SLF001
    assert finding.status == "fail"
    assert "workflow' scope" in finding.message
    assert "workflow' checkbox" in finding.remedy


def test_check_workflow_write_capability_unknown_when_unverifiable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_token(monkeypatch)
    monkeypatch.setattr(github_repos, "probe_workflow_scope", lambda token: "unverifiable")  # noqa: ARG005
    forge = repo_scaffold._forge_target(_cfg())  # noqa: SLF001
    finding = repo_scaffold._check_workflow_write_capability(forge)  # noqa: SLF001
    assert finding.status == "unknown"
    assert "fine-grained" in finding.remedy


def test_check_collaborator_access_always_unknown() -> None:
    finding = repo_scaffold._check_collaborator_access()  # noqa: SLF001
    assert finding.status == "unknown"
    assert finding.check == "grant_reviewer_collaborator_access"
    assert finding.remedy


def test_check_scaffold_pr_merged_fail_when_not_run_yet() -> None:
    forge = repo_scaffold._forge_target(_cfg())  # noqa: SLF001
    finding = repo_scaffold._check_scaffold_pr_merged(  # noqa: SLF001
        forge, repo_scaffold.OnboardingState(slug="acme/widget")
    )
    assert finding.status == "fail"
    assert finding.check == "review_and_merge_scaffold_pr"


def test_check_scaffold_pr_merged_unknown_without_token(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    _with_token(monkeypatch, None)
    forge = repo_scaffold._forge_target(_cfg())  # noqa: SLF001
    state = repo_scaffold.OnboardingState(
        slug="acme/widget", scaffold_pr_number=1, scaffold_pr_url="url"
    )
    finding = repo_scaffold._check_scaffold_pr_merged(forge, state)  # noqa: SLF001
    assert finding.status == "unknown"
    assert _active_fake_forge.calls == []


def test_check_scaffold_pr_merged_pass_when_merged(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    _with_token(monkeypatch)
    _active_fake_forge.stub("pr_state", lambda *_a, **_k: api_result(stdout="MERGED"))
    forge = repo_scaffold._forge_target(_cfg())  # noqa: SLF001
    state = repo_scaffold.OnboardingState(
        slug="acme/widget", scaffold_pr_number=1,
        scaffold_pr_url="https://github.com/acme/widget/pull/1",
    )
    finding = repo_scaffold._check_scaffold_pr_merged(forge, state)  # noqa: SLF001
    assert finding.status == "pass"
    assert finding.remedy_url == state.scaffold_pr_url


def test_check_scaffold_pr_merged_fail_when_open(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    _with_token(monkeypatch)
    _active_fake_forge.stub("pr_state", lambda *_a, **_k: api_result(stdout="OPEN"))
    forge = repo_scaffold._forge_target(_cfg())  # noqa: SLF001
    state = repo_scaffold.OnboardingState(slug="acme/widget", scaffold_pr_number=1)
    finding = repo_scaffold._check_scaffold_pr_merged(forge, state)  # noqa: SLF001
    assert finding.status == "fail"
    assert "not yet merged" in finding.message


def test_check_scaffold_pr_merged_unknown_on_raise(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    _with_token(monkeypatch)
    always(_active_fake_forge, "pr_state", subprocess.TimeoutExpired(cmd=["gh"], timeout=5))
    forge = repo_scaffold._forge_target(_cfg())  # noqa: SLF001
    state = repo_scaffold.OnboardingState(slug="acme/widget", scaffold_pr_number=1)
    finding = repo_scaffold._check_scaffold_pr_merged(forge, state)  # noqa: SLF001
    assert finding.status == "unknown"


def test_check_scaffold_pr_merged_unknown_on_nonzero_returncode(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    _with_token(monkeypatch)
    _active_fake_forge.stub(
        "pr_state", lambda *_a, **_k: api_result(returncode=1, stderr="boom")
    )
    forge = repo_scaffold._forge_target(_cfg())  # noqa: SLF001
    state = repo_scaffold.OnboardingState(slug="acme/widget", scaffold_pr_number=1)
    finding = repo_scaffold._check_scaffold_pr_merged(forge, state)  # noqa: SLF001
    assert finding.status == "unknown"


def test_scaffold_pr_state_unknown_when_transport_raises(
    _active_fake_forge: FakeForge,
) -> None:
    always(_active_fake_forge, "pr_state", subprocess.TimeoutExpired(cmd=["gh"], timeout=5))
    forge = repo_scaffold._forge_target(_cfg())  # noqa: SLF001
    state, error = repo_scaffold._view_scaffold_pr_state(forge, 1)  # noqa: SLF001
    assert state is None
    assert error == "gh pr view raised"


def test_scaffold_pr_state_nonzero_uses_stderr_or_default(
    _active_fake_forge: FakeForge,
) -> None:
    _active_fake_forge.stub("pr_state", lambda *_a, **_k: api_result(returncode=1, stderr=""))
    forge = repo_scaffold._forge_target(_cfg())  # noqa: SLF001
    state, error = repo_scaffold._view_scaffold_pr_state(forge, 1)  # noqa: SLF001
    assert state is None
    assert error == "gh pr view failed"


def test_build_checklist_never_shorter_than_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_token(monkeypatch, None)
    cfg = _cfg()
    state = repo_scaffold.onboarding_status(cfg)
    checklist = repo_scaffold.build_checklist(cfg, state)
    checks = {f.check for f in checklist}
    assert checks == {
        "push_repository", "mint_credentials", "workflow_write",
        "grant_reviewer_collaborator_access", "review_and_merge_scaffold_pr",
    }


def test_build_checklist_includes_workflow_write_before_scaffold_pr_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_token(monkeypatch, None)
    cfg = _cfg()
    state = repo_scaffold.onboarding_status(cfg)
    checklist = repo_scaffold.build_checklist(cfg, state)
    checks = [f.check for f in checklist]
    assert "workflow_write" in checks
    assert checks.index("workflow_write") < checks.index("review_and_merge_scaffold_pr")


def test_build_checklist_appends_deferred_protection_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_token(monkeypatch)
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run())
    cfg = _cfg()
    deferred = Finding(check="required_checks", status="fail", message="nope", remedy="fix")
    state = repo_scaffold.OnboardingState(slug="acme/widget", protection_deferred=(deferred,))
    checklist = repo_scaffold.build_checklist(cfg, state)
    assert checklist[-1] == deferred
    assert len(checklist) == 6


def test_overall_status_empty_is_pass() -> None:
    assert repo_scaffold.overall_status(()) == "pass"


def test_overall_status_all_pass() -> None:
    checklist = (Finding(check="a", status="pass", message="ok"),)
    assert repo_scaffold.overall_status(checklist) == "pass"


def test_overall_status_unknown_beats_pass() -> None:
    checklist = (
        Finding(check="a", status="pass", message="ok"),
        Finding(check="b", status="unknown", message="?"),
    )
    assert repo_scaffold.overall_status(checklist) == "unknown"


def test_overall_status_fail_beats_all() -> None:
    checklist = (
        Finding(check="a", status="fail", message="no"),
        Finding(check="b", status="unknown", message="?"),
    )
    assert repo_scaffold.overall_status(checklist) == "fail"


def test_engine_preflight_passes_after_onboarding_and_manual_steps(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    checks = frozenset({"python-ci"})
    cfg = _cfg()
    forge_cfg = cfg.forge
    assert forge_cfg is not None
    cfg = cfg.model_copy(
        update={"forge": forge_cfg.model_copy(update={"authoritative_checks": checks})}
    )

    def _resolve(_engine: str, purpose: str, *, env_fallback: str | None) -> str | None:
        del env_fallback
        return "tok-author" if purpose == secrets_store.TRANSPORT_PURPOSE else "tok-reviewer"

    monkeypatch.setattr(secrets_store, "resolve_secret", _resolve)

    def _login(*, forge_target: Any, as_verifier: bool, timeout: float) -> LoginRead:
        del forge_target, timeout
        return login_read("reviewer-bot" if as_verifier else "author-bot")

    _active_fake_forge.stub("viewer_login", _login)

    repo_json = {
        "default_branch": "main", "allow_squash_merge": True, "delete_branch_on_merge": True,
    }
    protection_json = {
        "required_conversation_resolution": {"enabled": True},
        "required_pull_request_reviews": {"required_approving_review_count": 0},
        "required_status_checks": {"contexts": sorted(checks), "checks": []},
    }

    def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        if cmd[:2] == ["git", "ls-remote"]:
            return _completed(stdout="abc123\tHEAD\n")
        if cmd[:3] == ["gh", "api", f"repos/{cfg.repo.slug}"]:
            return _completed(stdout=json.dumps(repo_json))
        if cmd[:3] == ["gh", "api", f"repos/{cfg.repo.slug}/branches/main/protection"]:
            return _completed(stdout=json.dumps(protection_json))
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", _run)

    result = engine_preflight.run_preflight(cfg)

    assert result.overall == "pass"
    assert all(f.status == "pass" for f in result.findings)
