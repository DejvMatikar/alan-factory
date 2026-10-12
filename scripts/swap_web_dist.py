"""Atomically swap the built frontend into serving position.

Two fixed slots (dist.a / dist.b) let a new build land in a temporary
location and get promoted into place with os.rename over a symlink named
`dist` — never `mv` (BSD mv stat()s the destination through a symlink and
would move the source inside dist.a/ instead of replacing the link, and
`mv -T` does not exist on macOS) and never a force-relink shell one-liner
(same portability gap). This is what lets alan/web/dist survive a
`git clean -fdx` checkout reset
and is what removes the window, present on every deploy before this change,
where the service serves a missing or partially written frontend.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

_SLOTS = ("dist.a", "dist.b")
_DEPLOY_SHA_FILENAME = ".deploy_sha"


def next_slot(web_dir: Path) -> str:
    dist = web_dir / "dist"
    if not dist.is_symlink():
        return _SLOTS[0]
    target = os.readlink(dist)
    return _SLOTS[1] if target == _SLOTS[0] else _SLOTS[0]


def activate(web_dir: Path, slot: str, sha: str) -> tuple[str, str]:
    slot_dir = web_dir / slot
    if not (slot_dir / "index.html").is_file():
        raise SystemExit(f"refusing to activate {slot}: no index.html found (build failed?)")

    (slot_dir / _DEPLOY_SHA_FILENAME).write_text(sha, encoding="utf-8")

    dist = web_dir / "dist"
    tmp = web_dir / "dist.tmp"
    tmp.unlink(missing_ok=True)
    os.symlink(slot, tmp)

    old_target = os.readlink(dist) if dist.is_symlink() else "(none)"
    if dist.exists() and not dist.is_symlink():
        legacy = web_dir / "dist.legacy"
        os.rename(dist, legacy)
        os.rename(tmp, dist)
        shutil.rmtree(legacy)
    else:
        os.rename(tmp, dist)

    return old_target, slot


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="swap_web_dist",
        description="Compute the next frontend build slot, or activate a built slot.",
    )
    parser.add_argument("--web-dir", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("next-slot")
    activate_parser = subparsers.add_parser("activate")
    activate_parser.add_argument("--slot", required=True)
    activate_parser.add_argument("--sha", required=True)

    args = parser.parse_args(argv)
    if args.command == "next-slot":
        print(next_slot(args.web_dir))
        return 0

    old_target, new_target = activate(args.web_dir, args.slot, args.sha)
    print(f"dist: {old_target} -> {new_target}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
