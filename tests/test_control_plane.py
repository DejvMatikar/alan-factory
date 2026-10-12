from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from alan import claude_cli, control_plane, control_token_store


@pytest.fixture(autouse=True)
def _reset_store_cache() -> Iterator[None]:
    control_token_store.reset_cached_meta()
    yield
    control_token_store.reset_cached_meta()


def test_bootstrap_control_token_writes_owner_only_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(control_plane.CONTROL_TOKEN_ENV, raising=False)
    token = control_plane.bootstrap_control_token(tmp_path)
    path = tmp_path / control_token_store.CONTROL_TOKEN_FILENAME
    assert path.read_text(encoding="utf-8") == token
    assert (path.stat().st_mode & 0o777) == 0o600
    control_plane.clear_control_token()


def test_bootstrap_control_token_adopts_pre_existing_file_instead_of_regenerating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(control_plane.CONTROL_TOKEN_ENV, raising=False)
    path = tmp_path / control_token_store.CONTROL_TOKEN_FILENAME
    path.write_text("stale", encoding="utf-8")
    path.chmod(0o644)
    token = control_plane.bootstrap_control_token(tmp_path)
    assert token == "stale"  # noqa: S105
    assert (path.stat().st_mode & 0o777) == 0o600
    assert path.read_text(encoding="utf-8") == "stale"
    control_plane.clear_control_token()


def test_bootstrap_control_token_sets_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(control_plane.CONTROL_TOKEN_ENV, raising=False)
    token = control_plane.bootstrap_control_token(tmp_path)
    assert os.environ[control_plane.CONTROL_TOKEN_ENV] == token
    control_plane.clear_control_token()


def test_bootstrap_control_token_reuses_the_same_token_across_restarts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(control_plane.CONTROL_TOKEN_ENV, raising=False)
    first = control_plane.bootstrap_control_token(tmp_path)
    control_plane.clear_control_token()
    second = control_plane.bootstrap_control_token(tmp_path)
    assert first == second
    control_plane.clear_control_token()


def test_bootstrap_control_token_unsets_env_when_tombstoned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(control_plane.CONTROL_TOKEN_ENV, raising=False)
    control_plane.bootstrap_control_token(tmp_path)
    control_token_store.revoke_token(tmp_path)

    token = control_plane.bootstrap_control_token(tmp_path)

    assert token is None
    assert control_plane.CONTROL_TOKEN_ENV not in os.environ
    control_plane.clear_control_token()


def test_clear_control_token_removes_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(control_plane.CONTROL_TOKEN_ENV, "x")
    control_plane.clear_control_token()
    assert control_plane.CONTROL_TOKEN_ENV not in os.environ


def test_clear_control_token_is_a_noop_when_already_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(control_plane.CONTROL_TOKEN_ENV, raising=False)
    control_plane.clear_control_token()
    assert control_plane.CONTROL_TOKEN_ENV not in os.environ


def test_clear_control_token_resets_the_store_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(control_plane.CONTROL_TOKEN_ENV, raising=False)
    control_plane.bootstrap_control_token(tmp_path)
    assert control_token_store.current_meta().state == "active"
    control_plane.clear_control_token()
    assert control_token_store.current_meta().token is None


def test_same_uid_subprocess_cannot_recover_control_token_via_ps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "residual-risk-probe-control-token-value"  # noqa: S105
    monkeypatch.setenv(control_plane.CONTROL_TOKEN_ENV, token)
    worker_env = claude_cli._worker_env()  # noqa: SLF001
    assert control_plane.CONTROL_TOKEN_ENV not in worker_env

    result = subprocess.run(
        ["ps", "eww", "-p", str(os.getpid())],
        capture_output=True,
        text=True,
        env=worker_env,
        timeout=10,
    )

    assert result.returncode == 0
    assert token not in result.stdout
