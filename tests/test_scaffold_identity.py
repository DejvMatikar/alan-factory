from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from alan import engine_config as engine_config_mod
from alan import repo_scaffold, secrets_store
from alan.engine_config import EngineConfig, ForgeConfig, RepoIdentity
from alan.scaffold_templates import AUTHORITATIVE_CHECKS, STACKS, get_stack_template
from tests.fakes.forge import FakeForge
from tests.forge_stubs import api_result, rows_read


@pytest.fixture(autouse=True)
def _state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))


@pytest.fixture(autouse=True)
def _engines_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "engines"))


def _completed(
    *, rc: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)


def _cfg_for_stack(stack: str) -> EngineConfig:
    repo = RepoIdentity(owner="acme", repo=f"{stack}-repo")
    return EngineConfig(
        repo=repo,
        forge=ForgeConfig(repo=repo, credential_env_name="ACME_GH_TOKEN"),
        preset=stack,
    )


@pytest.mark.parametrize("stack", STACKS)
def test_ci_job_name_equals_authoritative_checks(stack: str) -> None:
    template = get_stack_template(stack)
    assert template is not None
    parsed = yaml.safe_load(template.ci_yml)
    job_name = parsed["jobs"]["test"]["name"]
    assert frozenset({job_name}) == AUTHORITATIVE_CHECKS[stack]


def _fake_scaffold_subprocess_run(
    cmd: list[str], **kwargs: Any
) -> subprocess.CompletedProcess[str]:
    del kwargs
    if cmd[0] == "git":
        if "status" in cmd and "--porcelain" in cmd:
            return _completed(stdout=" M CONVENTIONS.md\n")
        return _completed()
    raise AssertionError(f"unexpected command: {cmd}")


@pytest.mark.parametrize("stack", STACKS)
def test_scaffold_unions_authoritative_checks_into_forge_config(
    stack: str, monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "tok")  # noqa: ARG005
    monkeypatch.setattr(subprocess, "run", _fake_scaffold_subprocess_run)
    _active_fake_forge.stub("prs_by_head", lambda *_a, **_k: rows_read(rows=()))
    _active_fake_forge.stub(
        "create_pr", lambda *_a, **_k: api_result(stdout="https://github.com/o/r/pull/1")
    )
    cfg = _cfg_for_stack(stack)
    engine_config_mod.register_and_persist(cfg, validate_env=False)

    repo_scaffold.scaffold(cfg, project_title="Widget", project_doc="")

    persisted = engine_config_mod.lookup_by_repo(cfg.repo)
    assert persisted is not None
    assert persisted.forge is not None
    assert AUTHORITATIVE_CHECKS[stack] <= persisted.forge.authoritative_checks


def _fake_protect_subprocess_run_factory(
    captured_put_body: dict[str, Any],
) -> Any:
    def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["gh", "api"]:
            if "-X" in cmd:
                method = cmd[cmd.index("-X") + 1]
            else:
                method = "GET"
            if method == "GET":
                return _completed(stdout=json.dumps({"default_branch": "main"}))
            if method == "PATCH":
                return _completed(stdout="{}")
            if method == "PUT":
                captured_put_body.update(json.loads(kwargs.get("input", "{}")))
                return _completed(stdout="{}")
        raise AssertionError(f"unexpected command: {cmd}")

    return _run


@pytest.mark.parametrize("stack", STACKS)
def test_protect_sends_authoritative_checks_as_required_status_checks(
    stack: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "tok")  # noqa: ARG005
    checks = AUTHORITATIVE_CHECKS[stack]
    cfg = _cfg_for_stack(stack)
    assert cfg.forge is not None
    cfg = cfg.model_copy(
        update={"forge": cfg.forge.model_copy(update={"authoritative_checks": checks})}
    )
    engine_config_mod.register_and_persist(cfg, validate_env=False)

    captured_put_body: dict[str, Any] = {}
    monkeypatch.setattr(
        subprocess, "run", _fake_protect_subprocess_run_factory(captured_put_body)
    )

    repo_scaffold.protect(cfg)

    assert set(captured_put_body["required_status_checks"]["contexts"]) == checks
