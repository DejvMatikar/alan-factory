"""Hotspot report: rank files by (commits in window) * (current CCN).

Run via `gh workflow run hotspots.yml -f days=180 -f top=20`. Useful for
deciding what to refactor next — high churn + high complexity is where
agents and humans both make the most mistakes.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _churn(repo: Path, days: int) -> dict[str, int]:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "log",
            f"--since={days}.days.ago",
            "--name-only",
            "--pretty=format:",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    counts: dict[str, int] = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.endswith(".py") and not line.startswith("alan/web/"):
            counts[line] = counts.get(line, 0) + 1
    return counts


def _ccn_for(path: Path) -> int:
    if not path.is_file():
        return 0
    completed = subprocess.run(
        ["uv", "run", "lizard", "--CCN", "0", "--languages", "python", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    total = 0
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 6 and parts[0].isdigit() and parts[1].isdigit():
            try:
                total += int(parts[1])
            except ValueError:
                continue
    return total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--repo", default=".")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    churn = _churn(repo, args.days)
    rows = []
    for filename, commits in churn.items():
        path = repo / filename
        ccn = _ccn_for(path)
        rows.append((commits * ccn, commits, ccn, filename))
    rows.sort(reverse=True)

    print(f"# Hotspots (last {args.days} days, top {args.top})\n")
    print("| Score | Commits | Total CCN | File |")
    print("| ---: | ---: | ---: | --- |")
    for score, commits, ccn, filename in rows[: args.top]:
        print(f"| {score} | {commits} | {ccn} | `{filename}` |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
