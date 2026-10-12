from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import deploy_restart_decision as rd  # type: ignore[import-not-found]  # noqa: E402

from alan import deploy_restart as deploy_restart_mod


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    return repo


def _commit(repo: Path, paths: list[str], message: str) -> str:
    for rel in paths:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _write_marker(tmp_path: Path, sha: str) -> Path:
    marker = tmp_path / "restarted_sha"
    marker.write_text(sha, encoding="utf-8")
    return marker


def test_script_reuses_alan_deploy_restart_objects() -> None:
    assert rd.changed_paths is deploy_restart_mod.changed_paths
    assert rd.decide is deploy_restart_mod.decide
    assert rd.is_safe_path is deploy_restart_mod.is_safe_path
    assert rd.marker_reachable is deploy_restart_mod.marker_reachable


def test_module_imports_cleanly_with_repo_root_absent_from_sys_path(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, ["README.md"], "base")
    marker = _write_marker(tmp_path, base_sha)
    script_path = Path(__file__).resolve().parent.parent / "scripts" / "deploy_restart_decision.py"
    result = subprocess.run(
        [
            sys.executable, "-S", str(script_path),
            "--repo", str(repo), "--marker", str(marker), "--service-loaded", "true",
        ],
        cwd=str(tmp_path),
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError" not in result.stderr


def test_main_service_loaded_false_restarts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, ["README.md"], "base")
    marker = tmp_path / "restarted_sha"
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    rc = rd._main(["--repo", str(repo), "--marker", str(marker), "--service-loaded", "false"])
    assert rc == 0
    assert output.read_text(encoding="utf-8") == "restart_required=true\n"


def test_main_service_loaded_omitted_defaults_to_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, ["README.md"], "base")
    marker = tmp_path / "restarted_sha"
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    rc = rd._main(["--repo", str(repo), "--marker", str(marker)])
    assert rc == 0
    assert output.read_text(encoding="utf-8") == "restart_required=true\n"


def test_main_internal_exception_yields_restart_true_and_returns_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("boom")

    monkeypatch.setattr(rd, "decide", _boom)
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    rc = rd._main(["--service-loaded", "true"])
    assert rc == 0
    assert output.read_text(encoding="utf-8") == "restart_required=true\n"


def test_main_github_output_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, ["README.md"], "base")
    marker = _write_marker(tmp_path, base_sha)
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    rc = rd._main(["--repo", str(repo), "--marker", str(marker), "--service-loaded", "true"])
    assert rc == 0
    assert output.read_text(encoding="utf-8") == "restart_required=false\n"


def test_main_github_output_unset_still_returns_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, ["README.md"], "base")
    marker = _write_marker(tmp_path, base_sha)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    rc = rd._main(["--repo", str(repo), "--marker", str(marker), "--service-loaded", "true"])
    assert rc == 0
