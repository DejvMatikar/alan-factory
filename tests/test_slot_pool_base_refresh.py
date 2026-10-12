from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest

from alan import slot_pool as sp
from alan.engine_config import ForgeTarget, RepoIdentity
from alan.secrets_store import MissingSecretError

_TARGET = ForgeTarget(
    repo=RepoIdentity(owner="acme", repo="widget"),
    credential_env_name="ACME_GH_TOKEN",  # noqa: S106  # nosec B106
)


def _ok(args: list[str], _cwd: Path, **_kw: object) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, 0, stdout="", stderr="")


def test_prepare_existing_branch_refreshes_base_after_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sp, "_fetch_existing_branch", lambda p, b, _ft: f"origin/{b}")
    monkeypatch.setattr(sp, "_clean_slot", lambda p: None)
    monkeypatch.setattr(sp, "_head_sha", lambda p: "c" * 40)
    calls: list[str] = []

    def _fake_run_git(
        args: list[str], _cwd: Path, *, extra_env: Mapping[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        del extra_env
        calls.append(args[0])
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(sp, "_run_git", _fake_run_git)
    sp._prepare_existing_branch(tmp_path, "pr-branch", None)
    assert calls == ["checkout", "fetch"]


def test_prepare_existing_branch_refresh_uses_transport_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sp, "_fetch_existing_branch", lambda p, b, _ft: f"origin/{b}")
    monkeypatch.setattr(sp, "_clean_slot", lambda p: None)
    monkeypatch.setattr(sp, "_head_sha", lambda p: "c" * 40)
    monkeypatch.setattr(sp, "transport_env", lambda _ft: {"GIT_ASKPASS": "x"})
    captured: dict[str, object] = {}

    def _fake_run_git(
        args: list[str], _cwd: Path, *, extra_env: Mapping[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        if args[0] == "fetch":
            captured["extra_env"] = extra_env
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(sp, "_run_git", _fake_run_git)
    sp._prepare_existing_branch(tmp_path, "pr-branch", _TARGET)
    assert captured["extra_env"] == {"GIT_ASKPASS": "x"}


def test_prepare_existing_branch_refresh_failure_does_not_raise(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sp, "_fetch_existing_branch", lambda p, b, _ft: f"origin/{b}")
    monkeypatch.setattr(sp, "_clean_slot", lambda p: None)
    monkeypatch.setattr(sp, "_head_sha", lambda p: "c" * 40)

    def _fake_run_git(
        args: list[str], _cwd: Path, *, extra_env: Mapping[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        del extra_env
        if args[0] == "fetch":
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="boom")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(sp, "_run_git", _fake_run_git)
    base, branch = sp._prepare_existing_branch(tmp_path, "pr-branch", None)
    assert base == "c" * 40
    assert branch == "pr-branch"


def test_refresh_base_best_effort_swallows_missing_git_binary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _raise(*_a: object, **_kw: object) -> object:
        raise FileNotFoundError("git")

    monkeypatch.setattr(sp, "_run_git", _raise)
    sp._refresh_base_best_effort(tmp_path, None)


def test_refresh_base_best_effort_swallows_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _raise(*_a: object, **_kw: object) -> object:
        raise subprocess.TimeoutExpired(cmd="git", timeout=30)

    monkeypatch.setattr(sp, "_run_git", _raise)
    sp._refresh_base_best_effort(tmp_path, None)


def test_refresh_base_best_effort_no_forge_target_skips_transport_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _boom(*_a: object, **_kw: object) -> object:
        raise AssertionError("transport_env must not be called with no forge target")

    monkeypatch.setattr(sp, "transport_env", _boom)
    captured: dict[str, object] = {}

    def _fake_run_git(
        args: list[str], _cwd: Path, *, extra_env: Mapping[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        captured["extra_env"] = extra_env
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(sp, "_run_git", _fake_run_git)
    sp._refresh_base_best_effort(tmp_path, None)
    assert captured["extra_env"] is None


def test_refresh_base_best_effort_swallows_missing_secret_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _raise(*_a: object, **_kw: object) -> object:
        raise MissingSecretError("no transport credential")

    monkeypatch.setattr(sp, "transport_env", _raise)
    sp._refresh_base_best_effort(tmp_path, _TARGET)


def test_refresh_base_best_effort_logs_on_nonzero_returncode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def _fake_run_git(
        args: list[str], _cwd: Path, *, extra_env: Mapping[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        del extra_env
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="boom")

    monkeypatch.setattr(sp, "_run_git", _fake_run_git)
    with caplog.at_level("WARNING"):
        sp._refresh_base_best_effort(tmp_path, None)
    assert "slot base refresh failed" in caplog.text
