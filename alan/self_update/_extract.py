from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from alan import roots

_KIT_PREFIX = "alan-self-update-kit-"
_KIT_ROOT_NAME = "kits"
_ARCHIVE_TIMEOUT_S = 60.0
_ARCHIVED_PATH = "alan"
_SHA_RE = re.compile(r"\A[0-9a-f]{40}\Z")
_ALLOWED_ARGV_FLAGS = frozenset({
    "-S", "-m", "--kit-repo", "--plist-path", "--health-url", "--scheduled",
    "--sha", "--force", "--queue-db-path",
})


def _kit_root() -> Path:
    root = roots.deploy_marker_root() / _KIT_ROOT_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cleanup_stale_kits() -> None:
    for entry in _kit_root().glob(f"{_KIT_PREFIX}*"):
        shutil.rmtree(entry, ignore_errors=True)


def _member_is_safe(member: tarfile.TarInfo, dest: Path) -> bool:
    if member.issym() or member.islnk() or member.isdev():
        return False
    root = dest.resolve()
    resolved = (dest / member.name).resolve()
    return resolved == root or root in resolved.parents


def build_kit(repo: Path, commit: str) -> Path:
    if not _SHA_RE.fullmatch(commit):
        raise ValueError(f"refusing to archive a non-commit-sha ref: {commit!r}")
    result = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), "archive", "--format=tar", commit, "--", _ARCHIVED_PATH],
        capture_output=True,
        timeout=_ARCHIVE_TIMEOUT_S,
        check=True,
    )
    kit = Path(tempfile.mkdtemp(prefix=_KIT_PREFIX, dir=_kit_root()))
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
        members = [member for member in archive.getmembers() if _member_is_safe(member, kit)]
        archive.extractall(kit, members=members, filter="data")
    return kit


def _exec(python: str, argv: list[str]) -> None:
    if not os.path.isabs(python):
        raise ValueError(f"refusing to exec a non-absolute interpreter path: {python!r}")
    for arg in argv:
        if arg.startswith("-") and arg not in _ALLOWED_ARGV_FLAGS:
            raise ValueError(f"refusing to exec with an unrecognized option: {arg!r}")
    os.execv(python, [python, *argv])  # noqa: S606  # nosec B606


def extract_and_exec(repo: Path, commit: str, argv: list[str]) -> None:
    _cleanup_stale_kits()
    kit = build_kit(repo, commit)
    python = os.path.realpath(sys.executable)
    os.chdir(kit)
    _exec(python, ["-S", "-m", "alan.self_update", *argv])
