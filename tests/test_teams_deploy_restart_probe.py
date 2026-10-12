from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from alan import deploy_restart, process_runner, roots, secrets_store
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _deploy_restart_probe
from tests.fakes.forge import FakeForge
from tests.forge_stubs import always, pr_read


@pytest.fixture(autouse=True)
def _isolated_state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))


def _marker_path() -> Path:
    return roots.deploy_marker_root() / "restarted_sha"


def _write_marker(sha: str = "b" * 40) -> Path:
    marker = _marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(sha, encoding="utf-8")
    return marker


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "a@b.c"],
        ["git", "config", "user.name", "test"],
    ):
        subprocess.run(cmd, cwd=repo, check=True)


def _commit(repo: Path, rel_path: str, message: str) -> str:
    p = repo / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _boom(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("must not shell out for this scenario")


_FETCH_OK = subprocess.CompletedProcess([], 0, stdout="", stderr="")


def _fake_git_fetch(
    outcome: subprocess.CompletedProcess[str] | BaseException,
) -> object:
    def _fake_run_subprocess(
        cmd: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        assert cmd[0] == "git"
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return _fake_run_subprocess


_TransportEnvCall = tuple[tuple[object, ...], dict[str, object]]


def _stub_merge_and_fetch(
    monkeypatch: pytest.MonkeyPatch,
    forge: FakeForge,
    sha: str,
    fetch: subprocess.CompletedProcess[str] | BaseException = _FETCH_OK,
) -> list[_TransportEnvCall]:
    transport_env_calls: list[_TransportEnvCall] = []

    def _record_transport_env(*a: object, **k: object) -> dict[str, str]:
        transport_env_calls.append((a, k))
        return {}

    always(forge, "read_pr", pr_read({"mergeCommit": {"oid": sha}}))
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_git_fetch(fetch))
    monkeypatch.setattr(secrets_store, "transport_env", _record_transport_env)
    return transport_env_calls


def test_missing_marker_short_circuits_before_any_subprocess(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    assert not _marker_path().is_file()
    monkeypatch.setattr(process_runner, "run_subprocess", _boom)
    monkeypatch.setattr(subprocess, "run", _boom)
    assert _deploy_restart_probe.classify_merge(42) == (
        False, deploy_restart.REASON_MISSING_MARKER, None,
    )
    assert _active_fake_forge.calls == []


@pytest.mark.parametrize(
    "unusable_read",
    [
        pr_read(returncode=1, stderr="boom"),
        pr_read({}),
        pr_read({"mergeCommit": None}),
        pr_read({"mergeCommit": {"oid": ""}}),
        subprocess.TimeoutExpired(cmd="gh", timeout=60.0),
        FileNotFoundError("gh"),
    ],
    ids=["nonzero_returncode", "missing_key", "null_merge_commit", "empty_oid", "timeout",
         "missing_binary"],
)
def test_unusable_merge_commit_read_defers_before_any_fetch(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge, unusable_read: object,
) -> None:
    _write_marker()
    always(_active_fake_forge, "read_pr", unusable_read)
    monkeypatch.setattr(process_runner, "run_subprocess", _boom)
    assert _deploy_restart_probe.classify_merge(42) == (False, "merge_sha_unavailable", None)


def test_merge_commit_read_targets_alans_own_repo_with_the_sixty_second_bound(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    sha = "a" * 40
    _write_marker()
    _stub_merge_and_fetch(
        monkeypatch, _active_fake_forge, sha,
        subprocess.CompletedProcess([], 1, stdout="", stderr="failed"),
    )
    assert _deploy_restart_probe.classify_merge(42) == (False, "fetch_unavailable", sha)
    assert _active_fake_forge.calls == [
        (
            "read_pr",
            (42, ("mergeCommit",)),
            {"forge_target": DEFAULT_FORGE_TARGET, "timeout": 60.0},
        ),
    ]


def test_fetch_nonzero_returncode_defers(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    _write_marker()
    sha = "c" * 40
    _stub_merge_and_fetch(
        monkeypatch, _active_fake_forge, sha,
        subprocess.CompletedProcess([], 1, stdout="", stderr="failed"),
    )
    assert _deploy_restart_probe.classify_merge(42) == (False, "fetch_unavailable", sha)


def test_fetch_resolves_transport_env_for_the_default_forge_target(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    _write_marker()
    sha = "c" * 40
    transport_env_calls = _stub_merge_and_fetch(
        monkeypatch, _active_fake_forge, sha,
        subprocess.CompletedProcess([], 1, stdout="", stderr="failed"),
    )
    assert _deploy_restart_probe.classify_merge(42) == (False, "fetch_unavailable", sha)
    assert transport_env_calls == [((), {"forge_target": DEFAULT_FORGE_TARGET})]


def test_fetch_missing_secret_defers(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    _write_marker()
    sha = "c" * 40
    _stub_merge_and_fetch(monkeypatch, _active_fake_forge, sha)

    def _raise_missing(*_a: object, **_k: object) -> dict[str, str]:
        raise secrets_store.MissingSecretError("no transport credential")

    monkeypatch.setattr(secrets_store, "transport_env", _raise_missing)
    assert _deploy_restart_probe.classify_merge(42) == (False, "fetch_unavailable", sha)


def test_fetch_timeout_defers(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    _write_marker()
    sha = "c" * 40
    _stub_merge_and_fetch(
        monkeypatch, _active_fake_forge, sha, subprocess.TimeoutExpired(cmd="git", timeout=30.0),
    )
    assert _deploy_restart_probe.classify_merge(42) == (False, "fetch_unavailable", sha)


def test_service_loaded_nonzero_returncode_yields_restart_required_outcome(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    sha = "d" * 40
    _write_marker(sha)
    _stub_merge_and_fetch(monkeypatch, _active_fake_forge, sha)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 1))
    assert _deploy_restart_probe.classify_merge(42) == (False, "service_not_loaded", sha)


def test_service_loaded_probe_oserror_yields_restart_required_outcome(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    sha = "e" * 40
    _write_marker(sha)
    _stub_merge_and_fetch(monkeypatch, _active_fake_forge, sha)

    def _raise_oserror(*_a: object, **_k: object) -> object:
        raise OSError("launchctl not found")

    monkeypatch.setattr(subprocess, "run", _raise_oserror)
    assert _deploy_restart_probe.classify_merge(42) == (False, "service_not_loaded", sha)


def test_service_loaded_probe_timeout_yields_restart_required_outcome(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    sha = "f" * 40
    _write_marker(sha)
    _stub_merge_and_fetch(monkeypatch, _active_fake_forge, sha)

    def _raise_timeout(*_a: object, **_k: object) -> object:
        raise subprocess.TimeoutExpired(cmd="launchctl", timeout=2.0)

    monkeypatch.setattr(subprocess, "run", _raise_timeout)
    assert _deploy_restart_probe.classify_merge(42) == (False, "service_not_loaded", sha)


_REAL_SUBPROCESS_RUN = subprocess.run


def _fake_launchctl_ok(cmd: Any, *args: Any, **kwargs: Any) -> Any:
    if cmd and cmd[0] == "launchctl":
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    return _REAL_SUBPROCESS_RUN(cmd, *args, **kwargs)


def _wire_probe_to_local_repo(
    monkeypatch: pytest.MonkeyPatch, forge: FakeForge, repo: Path, head_sha: str
) -> None:
    _stub_merge_and_fetch(monkeypatch, forge, head_sha)
    monkeypatch.setattr(subprocess, "run", _fake_launchctl_ok)
    monkeypatch.setattr(_deploy_restart_probe, "_repo_root", lambda: repo)


def test_safe_paths_only_yields_restart_free_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    base_sha = _commit(repo, "README.md", "base")
    _write_marker(base_sha)
    head_sha = _commit(repo, "docs/x.md", "safe change")
    _wire_probe_to_local_repo(monkeypatch, _active_fake_forge, repo, head_sha)
    assert _deploy_restart_probe.classify_merge(42) == (True, "safe_paths_only", head_sha)


def test_hot_paths_yields_restart_free_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    base_sha = _commit(repo, "README.md", "base")
    _write_marker(base_sha)
    head_sha = _commit(repo, "alan/server.py", "hot change")
    _wire_probe_to_local_repo(monkeypatch, _active_fake_forge, repo, head_sha)
    assert _deploy_restart_probe.classify_merge(42) == (False, "hot_paths", head_sha)


def test_unexpected_exception_yields_classifier_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _write_marker()

    def _boom_marker_root() -> Path:
        raise RuntimeError("boom")

    monkeypatch.setattr(roots, "deploy_marker_root", _boom_marker_root)
    assert _deploy_restart_probe.classify_merge(42) == (False, "classifier_error", None)
