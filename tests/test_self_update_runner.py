from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNNER = REPO_ROOT / "deploy" / "update_runner.sh"


def _sandbox(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    script = repo / "deploy" / "update_runner.sh"
    script.write_text(RUNNER.read_text(encoding="utf-8"), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return repo


def _fake_python(repo: Path, capture: Path) -> None:
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(
        "#!/bin/bash\nprintf '%s\\n' \"$@\" > \"" + str(capture) + "\"\n",
        encoding="utf-8",
    )
    python.chmod(python.stat().st_mode | stat.S_IEXEC)


def _run(repo: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["bash", str(repo / "deploy" / "update_runner.sh")],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_an_unset_home_is_refused_before_any_other_check(tmp_path: Path) -> None:
    repo = _sandbox(tmp_path)
    result = _run(repo, {"PATH": os.environ["PATH"]})
    assert result.returncode == 1
    assert "HOME is unset" in result.stderr


def test_no_installed_profile_exits_cleanly_even_with_a_broken_venv(tmp_path: Path) -> None:
    repo = _sandbox(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    engines = tmp_path / "engines"
    engines.mkdir()
    result = _run(
        repo, {"PATH": os.environ["PATH"], "HOME": str(home), "ALAN_ENGINES_ROOT": str(engines)}
    )
    assert result.returncode == 0
    assert result.stdout == ""


def test_an_installed_profile_invokes_the_venv_python_as_a_scheduled_apply(
    tmp_path: Path,
) -> None:
    repo = _sandbox(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    engines = tmp_path / "engines"
    engines.mkdir()
    (engines / "default_profile.yaml").write_text("owner: x\n", encoding="utf-8")
    capture = tmp_path / "captured.txt"
    _fake_python(repo, capture)
    result = _run(
        repo, {"PATH": os.environ["PATH"], "HOME": str(home), "ALAN_ENGINES_ROOT": str(engines)}
    )
    assert result.returncode == 0
    argv = capture.read_text(encoding="utf-8").splitlines()
    assert argv == [
        "-m", "alan.self_update", "apply", "--scheduled",
        "--plist-path", f"{home}/Library/LaunchAgents/ai.alan.plist",
        "--queue-db-path", f"{home}/.alan/queue.db",
    ]


def test_a_profile_only_in_the_owner_config_directory_is_not_enough(tmp_path: Path) -> None:
    repo = _sandbox(tmp_path)
    (repo / "config" / "owner").mkdir(parents=True)
    (repo / "config" / "owner" / "default_profile.yaml").write_text(
        "owner: x\n", encoding="utf-8"
    )
    home = tmp_path / "home"
    home.mkdir()
    engines = tmp_path / "engines"
    engines.mkdir()
    result = _run(
        repo, {"PATH": os.environ["PATH"], "HOME": str(home), "ALAN_ENGINES_ROOT": str(engines)}
    )
    assert result.returncode == 0
    assert result.stdout == ""
