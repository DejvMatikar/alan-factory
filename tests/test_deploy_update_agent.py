from __future__ import annotations

import plistlib
import stat
import subprocess
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = REPO_ROOT / "deploy" / "ai.alan.update.plist"
RENDER = REPO_ROOT / "deploy" / "render_update_plist.sh"

_PLACEHOLDER_REPO = "__ALAN_REPO_DIR__"
_PLACEHOLDER_HOME = "__ALAN_HOME__"
_LABEL_STRING = "ai.alan.update"
_ALLOWED_LABEL_FILES = frozenset({"ai.alan.update.plist", "render_update_plist.sh"})


def _label_offenders(deploy_dir: Path) -> list[Path]:
    return [
        path
        for path in sorted(deploy_dir.glob("*"))
        if path.is_file()
        and path.name not in _ALLOWED_LABEL_FILES
        and _LABEL_STRING in path.read_text(encoding="utf-8")
    ]


def _rendered_plist(text: str) -> Any:
    filled = text.replace(_PLACEHOLDER_REPO, "/repo").replace(_PLACEHOLDER_HOME, "/fixture-home")
    return plistlib.loads(filled.encode("utf-8"))


def _sandbox(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    (repo / "deploy" / "ai.alan.update.plist").write_text(
        TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    script = repo / "deploy" / "render_update_plist.sh"
    script.write_text(RENDER.read_text(encoding="utf-8"), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return repo


def test_the_template_sets_start_interval_and_run_at_load() -> None:
    data = _rendered_plist(TEMPLATE.read_text(encoding="utf-8"))
    assert data["StartInterval"] == 900
    assert data["RunAtLoad"] is True


def test_the_template_names_only_the_update_runner() -> None:
    data = _rendered_plist(TEMPLATE.read_text(encoding="utf-8"))
    args = data["ProgramArguments"]
    assert args[-1] == "/repo/deploy/update_runner.sh"
    assert "self_update" not in TEMPLATE.read_text(encoding="utf-8")


def test_the_template_path_can_find_uv_node_22_and_git() -> None:
    data = _rendered_plist(TEMPLATE.read_text(encoding="utf-8"))
    entries = data["EnvironmentVariables"]["PATH"].split(":")
    assert any(entry.endswith(".local/bin") or entry.endswith(".venv/bin") for entry in entries)
    assert any("node@22" in entry for entry in entries)
    assert any(entry in ("/usr/bin", "/opt/homebrew/bin") for entry in entries)


def test_render_refuses_an_unset_home(tmp_path: Path) -> None:
    repo = _sandbox(tmp_path)
    result = subprocess.run(  # noqa: S603
        ["bash", str(repo / "deploy" / "render_update_plist.sh")],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        timeout=30,
    )
    assert result.returncode == 1
    assert "HOME is unset" in result.stderr


def test_render_writes_only_under_the_home_launch_agents_dir(tmp_path: Path) -> None:
    repo = _sandbox(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    result = subprocess.run(  # noqa: S603
        ["bash", str(repo / "deploy" / "render_update_plist.sh")],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(home)},
        timeout=30,
    )
    assert result.returncode == 0
    target = home / "Library" / "LaunchAgents" / "ai.alan.update.plist"
    assert target.is_file()
    rendered = plistlib.loads(target.read_bytes())
    assert rendered["ProgramArguments"][-1] == f"{repo}/deploy/update_runner.sh"
    created = {p.relative_to(home) for p in home.rglob("*")}
    assert created == {
        Path("Library"),
        Path("Library/LaunchAgents"),
        Path("Library/LaunchAgents/ai.alan.update.plist"),
    }


def test_render_never_loads_the_agent() -> None:
    assert "launchctl" not in RENDER.read_text(encoding="utf-8")


def test_only_the_template_and_render_script_name_the_update_agent() -> None:
    assert _label_offenders(REPO_ROOT / "deploy") == []


def test_the_label_scan_notices_a_stray_mention(tmp_path: Path) -> None:
    stray = tmp_path / "update_runner.sh"
    stray.write_text(f"# {_LABEL_STRING}\n", encoding="utf-8")
    assert _label_offenders(tmp_path) == [stray]
