"""Regenerate the trajectory store from durable audit logs.

Usage:
    uv run python scripts/derive_trajectories.py [--out PATH] [--logs-root DIR] [--git-log FILE]

Reads:
    <logs-root>/node_turns_audit.jsonl
    <logs-root>/verification_audit.jsonl
    <logs-root>/pr_merge_audit.jsonl

Writes one JSON line per TrajectoryRecord to --out (default: trajectory_store.jsonl).
Fully re-derivable: delete the output and re-run to reproduce the same store.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

_LOGS_ROOT_DEFAULT = Path.home() / ".alan" / "logs"
_STORE_DEFAULT = "trajectory_store.jsonl"
_NODE_TURNS_FILE = "node_turns_audit.jsonl"
_VERIFICATION_FILE = "verification_audit.jsonl"
_MERGE_FILE = "pr_merge_audit.jsonl"

_GIT_LOG_CMD = [
    "git", "log", "--format=%x1e%aI%x00%s%x00%b", "--all",
]


def _read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _read_git_log(git_log_path: Path | None) -> str:
    if git_log_path is not None:
        return _read_file(git_log_path)
    try:
        result = subprocess.run(
            _GIT_LOG_CMD,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout if result.returncode == 0 else ""
    except FileNotFoundError:
        return ""


def main(argv: list[str] | None = None) -> int:
    from alan.trajectory import derive_trajectories

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=_STORE_DEFAULT, help="Output JSONL path")
    parser.add_argument(
        "--logs-root",
        default=str(_LOGS_ROOT_DEFAULT),
        help="Directory containing audit JSONL files",
    )
    parser.add_argument(
        "--git-log",
        default=None,
        help="Path to a pre-captured git log file (optional; defaults to running git)",
    )
    args = parser.parse_args(argv)

    logs_root = Path(args.logs_root)
    node_turns_text = _read_file(logs_root / _NODE_TURNS_FILE)
    verification_text = _read_file(logs_root / _VERIFICATION_FILE)
    merge_audit_text = _read_file(logs_root / _MERGE_FILE)
    git_log_path = Path(args.git_log) if args.git_log else None
    git_log_text = _read_git_log(git_log_path)

    records = derive_trajectories(
        node_turns_text, verification_text, merge_audit_text, git_log_text
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")

    print(f"Wrote {len(records)} trajectory record(s) to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
