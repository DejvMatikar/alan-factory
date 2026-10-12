from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

import alan.explore_chat_repo as ec_repo
from alan import engine_config as engine_config_mod
from alan import secrets_store
from alan.engine_config import EngineConfig, ForgeConfig, RepoIdentity

_CREDENTIAL_ENV = "ACME_GH_TOKEN"  # noqa: S105


def _cfg(**overrides: Any) -> EngineConfig:
    repo = RepoIdentity(owner="acme", repo="widget")
    base = EngineConfig(
        repo=repo, forge=ForgeConfig(repo=repo, credential_env_name=_CREDENTIAL_ENV)
    )
    return base.model_copy(update=overrides)


def _completed(
    *, rc: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)


def test_repo_provision_status_importable() -> None:
    assert ec_repo.RepoProvisionStatus.UNCHECKED.value == "unchecked"


class TestRepoLabel:
    def test_none_engine_key_returns_alan_literal(self) -> None:
        assert ec_repo.repo_label(None) == "alan (this repository)"

    def test_unregistered_engine_key_returns_alan_literal(self) -> None:
        assert ec_repo.repo_label("ghost/repo") == "alan (this repository)"

    def test_registered_non_alan_slug_returns_the_slug(self) -> None:
        cfg = _cfg()
        engine_config_mod.register(cfg, validate_env=False)
        assert ec_repo.repo_label("acme/widget") == "acme/widget"

    def test_alans_own_registered_slug_returns_its_own_slug(self) -> None:
        default = engine_config_mod.default_engine_config()
        engine_config_mod.register(default, validate_env=False)
        assert ec_repo.repo_label(default.repo.slug) == default.repo.slug


class TestNeedsRepo:
    def test_none_engine_key_returns_none(self) -> None:
        assert ec_repo.needs_repo(None) is None

    def test_unregistered_engine_key_returns_none(self) -> None:
        assert ec_repo.needs_repo("ghost/repo") is None

    def test_malformed_engine_key_returns_none(self) -> None:
        assert ec_repo.needs_repo("not-a-slug") is None

    def test_alan_canonical_equal_engine_returns_none(self) -> None:
        default = engine_config_mod.default_engine_config()
        engine_config_mod.register(default, validate_env=False)
        assert ec_repo.needs_repo(default.repo.slug) is None

    def test_genuinely_non_alan_engine_returns_engine_key(self) -> None:
        cfg = _cfg()
        engine_config_mod.register(cfg, validate_env=False)
        assert ec_repo.needs_repo("acme/widget") == "acme/widget"


class TestProvision:
    def test_unregistered_engine_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ec_repo.RepoProvisionError, match="has no forge configured"):
            ec_repo.provision("acme/ghost", tmp_path / "dest")

    def test_engine_without_forge_raises(self, tmp_path: Path) -> None:
        cfg = EngineConfig(repo=RepoIdentity(owner="acme", repo="bare"))
        engine_config_mod.register(cfg, validate_env=False)
        with pytest.raises(ec_repo.RepoProvisionError, match="has no forge configured"):
            ec_repo.provision("acme/bare", tmp_path / "dest")

    def test_missing_credential_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _cfg()
        engine_config_mod.register(cfg, validate_env=False)
        monkeypatch.delenv(_CREDENTIAL_ENV, raising=False)
        monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: None)  # noqa: ARG005
        with pytest.raises(ec_repo.RepoProvisionError):
            ec_repo.provision("acme/widget", tmp_path / "dest")
        assert not (tmp_path / "dest").exists()

    def test_clone_failure_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = _cfg()
        engine_config_mod.register(cfg, validate_env=False)
        monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "tok")  # noqa: ARG005
        monkeypatch.setattr(
            ec_repo, "run_subprocess", lambda *a, **kw: _completed(rc=1, stderr="boom")  # noqa: ARG005
        )
        with pytest.raises(ec_repo.RepoProvisionError, match="git clone failed"):
            ec_repo.provision("acme/widget", tmp_path / "dest")

    def test_clone_timeout_raises_provision_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _cfg()
        engine_config_mod.register(cfg, validate_env=False)
        monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "tok")  # noqa: ARG005

        def fake_run_subprocess(*a: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(cmd=["git", "clone"], timeout=120.0)

        monkeypatch.setattr(ec_repo, "run_subprocess", fake_run_subprocess)
        dest = tmp_path / "dest"
        with pytest.raises(ec_repo.RepoProvisionError):
            ec_repo.provision("acme/widget", dest)
        assert not dest.exists()

    def test_clone_missing_git_binary_raises_provision_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _cfg()
        engine_config_mod.register(cfg, validate_env=False)
        monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "tok")  # noqa: ARG005

        def fake_run_subprocess(*a: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError("git not found")

        monkeypatch.setattr(ec_repo, "run_subprocess", fake_run_subprocess)
        dest = tmp_path / "dest"
        with pytest.raises(ec_repo.RepoProvisionError):
            ec_repo.provision("acme/widget", dest)
        assert not dest.exists()

    def test_dest_cleaned_up_after_clone_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _cfg()
        engine_config_mod.register(cfg, validate_env=False)
        monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "tok")  # noqa: ARG005
        dest = tmp_path / "dest"

        def fake_run_subprocess(
            cmd: list[str], *, cwd: str | None, timeout: float, env: dict[str, str] | None = None
        ) -> subprocess.CompletedProcess[str]:
            dest.mkdir(parents=True)
            (dest / "partial").write_text("leftover")
            return _completed(rc=1, stderr="boom")

        monkeypatch.setattr(ec_repo, "run_subprocess", fake_run_subprocess)
        with pytest.raises(ec_repo.RepoProvisionError, match="git clone failed"):
            ec_repo.provision("acme/widget", dest)
        assert not dest.exists()

    def test_stale_dest_removed_before_clone(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _cfg()
        engine_config_mod.register(cfg, validate_env=False)
        monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "tok")  # noqa: ARG005
        dest = tmp_path / "dest"
        dest.mkdir(parents=True)
        (dest / "stale").write_text("leftover from a prior failed attempt")
        captured: dict[str, Any] = {}

        def fake_run_subprocess(
            cmd: list[str], *, cwd: str | None, timeout: float, env: dict[str, str] | None = None
        ) -> subprocess.CompletedProcess[str]:
            captured["dest_existed_at_clone_time"] = dest.exists()
            return _completed()

        monkeypatch.setattr(ec_repo, "run_subprocess", fake_run_subprocess)
        ec_repo.provision("acme/widget", dest)
        assert captured["dest_existed_at_clone_time"] is False

    def test_success_clones_with_expected_git_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _cfg()
        engine_config_mod.register(cfg, validate_env=False)
        monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "tok")  # noqa: ARG005
        captured: dict[str, Any] = {}

        def fake_run_subprocess(
            cmd: list[str], *, cwd: str | None, timeout: float, env: dict[str, str] | None = None
        ) -> subprocess.CompletedProcess[str]:
            captured["cmd"] = cmd
            captured["cwd"] = cwd
            captured["env"] = env
            return _completed()

        monkeypatch.setattr(ec_repo, "run_subprocess", fake_run_subprocess)
        dest = tmp_path / "nested" / "repo"
        ec_repo.provision("acme/widget", dest)
        assert captured["cmd"][0] == "git"
        assert "clone" in captured["cmd"]
        assert captured["cmd"][-1] == str(dest)
        assert captured["cwd"] is None
        assert captured["env"] is not None
        assert dest.parent.is_dir()
        assert not dest.exists()
