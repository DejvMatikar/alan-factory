from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEPLOY_ASSETS = ("render_plist.sh", "ai.alan.plist")

EXAMPLE_ORIGIN = "https://github.com/example-owner/example-repo.git"
EXAMPLE_SLOT_URL = EXAMPLE_ORIGIN
NODE22_ENTRY = "/opt/homebrew/opt/node@22/bin"
REFUSED_EXIT = 78
FIX_HINT = "remote set-url origin https://github.com/<owner>/<repo>.git"
SLOT_URL_KEY = "ALAN_SLOT_REPO_ROOT"


@dataclass(frozen=True)
class Rendered:
    result: subprocess.CompletedProcess[str]
    repo: Path
    home: Path

    @property
    def target(self) -> Path:
        return rendered_target(self.home)

    @property
    def content(self) -> str:
        return self.target.read_text(encoding="utf-8")


def _git_env(base: Path) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(base),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }


def _run_git(base: Path, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=_git_env(base),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def scratch_checkout(
    base: Path, origin: str | None, *, git: bool = True, name: str = "repo"
) -> Path:
    base = base.resolve()
    probe = _run_git(base, base, "rev-parse", "--is-inside-work-tree")
    assert probe.returncode != 0, f"scratch base {base} sits inside a git checkout"
    repo = base / name
    deploy_dir = repo / "deploy"
    deploy_dir.mkdir(parents=True)
    for asset in _DEPLOY_ASSETS:
        shutil.copyfile(_REPO_ROOT / "deploy" / asset, deploy_dir / asset)
    if git:
        init = _run_git(base, repo, "init", "-q")
        assert init.returncode == 0, init.stderr
        if origin is not None:
            added = _run_git(base, repo, "remote", "add", "origin", origin)
            assert added.returncode == 0, added.stderr
    return repo


def scratch_home(base: Path, name: str = "home") -> Path:
    home = base.resolve() / name
    home.mkdir()
    return home


def run_render(
    repo: Path,
    home: Path | None,
    *,
    cwd: Path | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {"PATH": os.environ.get("PATH", ""), "GIT_CONFIG_NOSYSTEM": "1"}
    if home is not None:
        env["HOME"] = str(home)
    env.update(extra_env or {})
    return subprocess.run(
        ["/bin/bash", str(repo / "deploy" / "render_plist.sh")],
        cwd=cwd if cwd is not None else repo.parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def render_scratch(base: Path, origin: str | None, *, git: bool = True) -> Rendered:
    repo = scratch_checkout(base, origin, git=git)
    home = scratch_home(base)
    return Rendered(run_render(repo, home), repo, home)


def rendered_target(home: Path) -> Path:
    return home / "Library" / "LaunchAgents" / "ai.alan.plist"


def plist_string_value(content: str, key: str) -> str:
    match = re.search(rf"<key>{re.escape(key)}</key>\s*<string>([^<]*)</string>", content)
    assert match is not None, f"no <string> value follows key {key}"
    return match.group(1)
