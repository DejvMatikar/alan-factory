from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from tests.self_update_fixtures import checks_envelope, gate_check, git, push_run, runs_envelope

REPO_ROOT = Path(__file__).resolve().parent.parent
_ALAN_SRC = REPO_ROOT / "alan"
_SUPPORT_FILES = (
    "self_update_github.py",
    "deploy_refresh.py",
    "deploy_restart.py",
    "roots.py",
    "store.py",
    "process_runner.py",
)
_EXCLUDED = ("alan/web/dist", "alan/web/dist.*", ".venv/", "node_modules/", "__pycache__")
ORIGIN_URL = "git@github.com:example-owner/example-repo.git"
PASSING_RENDERER = "#!/bin/sh\nexit 0\n"

_LYING_ROLLBACK_SOURCE = (
    "from __future__ import annotations\n\n"
    "from pathlib import Path\n\n"
    "from alan.self_update import _journal\n\n\n"
    "def perform(repo: Path, journal, *, plist_path, health_url) -> bool:\n"
    "    _journal.write(journal.with_phase(_journal.Phase.ROLLED_BACK))\n"
    "    return True\n"
)


@dataclass(frozen=True)
class SabotageRepo:
    bare: Path
    clone: Path
    good_sha: str


def good_rollback_source() -> str:
    return (_ALAN_SRC / "self_update" / "_rollback.py").read_text(encoding="utf-8")


def _init_repo(path: Path, *, bare: bool) -> None:
    args = ["git", "init", "-q", "-b", "main"]
    if bare:
        args.append("--bare")
    args.append(str(path))
    subprocess.run(args, check=True, capture_output=True)  # noqa: S603


def seed_alan_package(repo: Path) -> None:
    dest = repo / "alan"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "__init__.py").write_text("", encoding="utf-8")
    for name in _SUPPORT_FILES:
        (dest / name).write_text((_ALAN_SRC / name).read_text(encoding="utf-8"), encoding="utf-8")
    shutil.copytree(_ALAN_SRC / "self_update", dest / "self_update")


def seed_scripts_and_deploy(repo: Path, render_script: str = PASSING_RENDERER) -> None:
    scripts = repo / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    swap = REPO_ROOT / "scripts" / "swap_web_dist.py"
    (scripts / "swap_web_dist.py").write_text(swap.read_text(encoding="utf-8"), encoding="utf-8")
    deploy = repo / "deploy"
    deploy.mkdir(parents=True, exist_ok=True)
    (deploy / "render_plist.sh").write_text(render_script, encoding="utf-8")


def seed_web(repo: Path, head: str) -> None:
    web = repo / "alan" / "web"
    web.mkdir(parents=True, exist_ok=True)
    (web / "dist.a").mkdir()
    (web / "dist.a" / "index.html").write_text("<html>old</html>\n", encoding="utf-8")
    (web / "dist.a" / ".deploy_sha").write_text(head, encoding="utf-8")
    os.symlink("dist.a", web / "dist")


def exclude_build_output(repo: Path) -> None:
    exclude = repo / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text("\n".join(_EXCLUDED) + "\n", encoding="utf-8")


def build_good_repo(tmp_path: Path, *, render_script: str = PASSING_RENDERER) -> SabotageRepo:
    bare = tmp_path / "origin.git"
    clone = tmp_path / "clone"
    _init_repo(bare, bare=True)
    _init_repo(clone, bare=False)
    seed_alan_package(clone)
    seed_scripts_and_deploy(clone, render_script)
    git(clone, "add", "-A")
    git(clone, "commit", "-q", "-m", "seed")
    git(clone, "push", "-q", str(bare), "main")
    git(clone, "remote", "add", "origin", ORIGIN_URL)
    exclude_build_output(clone)
    head = git(clone, "rev-parse", "HEAD")
    seed_web(clone, head)
    return SabotageRepo(bare, clone, head)


def push_sabotaged_commit(repo: SabotageRepo, tmp_path: Path, *, render_fails: bool) -> str:
    other = tmp_path / "sabotage-clone"
    subprocess.run(  # noqa: S603
        ["git", "clone", "-q", str(repo.bare), str(other)], check=True, capture_output=True
    )
    (other / "alan" / "self_update" / "_rollback.py").write_text(
        _LYING_ROLLBACK_SOURCE, encoding="utf-8"
    )
    if render_fails:
        (other / "deploy" / "render_plist.sh").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", "sabotage")
    git(other, "push", "-q", "origin", "main")
    return git(other, "rev-parse", "HEAD")


def write_gh_envelopes(tmp_path: Path, sha: str) -> tuple[Path, Path]:
    runs_file = tmp_path / "gh_runs.json"
    checks_file = tmp_path / "gh_checks.json"
    runs_file.write_text(json.dumps(runs_envelope([push_run(sha)])), encoding="utf-8")
    checks_file.write_text(json.dumps(checks_envelope([gate_check(sha)])), encoding="utf-8")
    return runs_file, checks_file


def write_marker(state_root: Path, sha: str) -> Path:
    path = state_root.parent / "deploy" / "restarted_sha"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{sha}\n", encoding="utf-8")
    return path


def write_journal(state_root: Path, payload: dict[str, object]) -> Path:
    path = state_root.parent / "deploy" / "self_update_journal.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def read_rejected(state_root: Path) -> frozenset[str]:
    path = state_root.parent / "deploy" / "self_update_rejected"
    if not path.is_file():
        return frozenset()
    lines = path.read_text(encoding="utf-8").splitlines()
    return frozenset(line.strip() for line in lines if line.strip())
