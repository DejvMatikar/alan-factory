from __future__ import annotations

import ast
import os
import subprocess
import tomllib
from pathlib import Path

import pytest

from alan import base_ref

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC_PATH = _REPO_ROOT / "alan" / "base_ref.py"
_PYPROJECT_PATH = _REPO_ROOT / "pyproject.toml"


def _fake_run(rc: int = 0) -> object:
    def run(
        argv: list[str], *, cwd: str, capture_output: bool, text: bool,
        timeout: float, check: bool, env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        del cwd, capture_output, text, timeout, check, env
        return subprocess.CompletedProcess(argv, rc, stdout="", stderr="")

    return run


def test_resolve_returns_resolved_ref_on_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("alan.base_ref.subprocess.run",_fake_run(0))
    assert base_ref.resolve(tmp_path) == base_ref.RESOLVED_BASE_REF


def test_resolve_returns_none_on_nonzero_returncode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("alan.base_ref.subprocess.run",_fake_run(1))
    assert base_ref.resolve(tmp_path) is None


def test_resolve_returns_none_on_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _raise(*_a: object, **_kw: object) -> object:
        raise subprocess.TimeoutExpired(cmd="git", timeout=30)

    monkeypatch.setattr("alan.base_ref.subprocess.run",_raise)
    assert base_ref.resolve(tmp_path) is None


def test_resolve_returns_none_on_missing_git_binary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _raise(*_a: object, **_kw: object) -> object:
        raise FileNotFoundError("git")

    monkeypatch.setattr("alan.base_ref.subprocess.run",_raise)
    assert base_ref.resolve(tmp_path) is None


def test_resolve_passes_timeout_through(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    def _run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr("alan.base_ref.subprocess.run",_run)
    base_ref.resolve(tmp_path, timeout_s=5.0)
    assert captured["timeout"] == 5.0


def test_resolve_env_none_uses_plain_argv_and_preserves_ambient(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HOME", "/srv/tester")
    captured: dict[str, object] = {}

    def _run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr("alan.base_ref.subprocess.run",_run)
    base_ref.resolve(tmp_path)
    assert captured["argv"] == ["git", "fetch", "origin", "main"]
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["PATH"] == "/usr/bin:/bin"
    assert env["HOME"] == "/srv/tester"
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_resolve_env_overlay_applies_cred_reset_args_and_overlay_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    captured: dict[str, object] = {}

    def _run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr("alan.base_ref.subprocess.run",_run)
    base_ref.resolve(tmp_path, env={"GIT_TERMINAL_PROMPT": "1", "CUSTOM": "x"})
    assert captured["argv"] == [
        "git", "-c", "credential.helper=", "fetch", "origin", "main",
    ]
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["PATH"] == "/usr/bin:/bin"
    assert env["GIT_TERMINAL_PROMPT"] == "1"
    assert env["CUSTOM"] == "x"


def test_resolve_default_timeout_is_30s(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    def _run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr("alan.base_ref.subprocess.run",_run)
    base_ref.resolve(tmp_path)
    assert captured["timeout"] == 30.0


def test_resolve_real_subprocess_on_non_git_directory(tmp_path: Path) -> None:
    assert base_ref.resolve(tmp_path, timeout_s=5.0) is None


def test_base_ref_imports_no_alan_modules() -> None:
    tree = ast.parse(_SRC_PATH.read_text(encoding="utf-8"), filename=str(_SRC_PATH))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            assert not node.module.startswith("alan"), (
                f"alan/base_ref.py imports {node.module!r} — must stay a pure leaf"
            )
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("alan"), (
                    f"alan/base_ref.py imports {alias.name!r} — must stay a pure leaf"
                )


def test_base_ref_is_registered_below_its_consumers_in_layers() -> None:
    data = tomllib.loads(_PYPROJECT_PATH.read_text(encoding="utf-8"))
    layers: list[str] = next(
        c["layers"]
        for c in data["tool"]["importlinter"]["contracts"]
        if c.get("name") == "Layered architecture"
    )
    base_ref_idx = layers.index("base_ref")
    for consumer in ("teams", "slot_pool", "drift_gate", "worktree"):
        assert base_ref_idx > layers.index(consumer), (
            f"base_ref must sit below {consumer!r} in the layers list"
        )
    assert base_ref_idx == len(layers) - 3
    assert layers[-2:] == ["block_correction", "process_runner"]


def test_base_ref_all_matches_public_surface() -> None:
    assert set(base_ref.__all__) == {"RESOLVED_BASE_REF", "resolve"}


def test_resolve_os_environ_not_mutated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    before = dict(os.environ)
    monkeypatch.setattr("alan.base_ref.subprocess.run",_fake_run(0))
    base_ref.resolve(tmp_path, env={"EXTRA": "1"})
    assert os.environ == before
