"""Measure OpenQuestion.proposed_answer_space key-presence vs population.

Walks every `handovers/*.yaml` file under a workspaces root (any nesting
depth, to cover both the flat and `tasks/<id>/` layouts) via raw
`yaml.safe_load` — NOT `load_handover` — so it counts documents that fail
current-schema validation too. For every blocking OpenQuestion found,
buckets by `from_team` and reports how many carry the
`proposed_answer_space` key and how many have it populated (non-empty).

Usage:
    uv run python scripts/open_question_options_report.py [--root DIR]

Baseline measured 2026-07-20 (Track L, item L0.1) against
production state: 527 handover files, 18 blocking OpenQuestions carried the
proposed_answer_space key, 0 were populated.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_WORKSPACES_ROOT_ENV = "ALAN_WORKSPACES_ROOT"
_WORKSPACES_DIR_DEFAULT = Path.home() / ".alan" / "workspaces"
_UNKNOWN_TEAM = "unknown"


@dataclass
class TeamStats:
    total_questions: int = 0
    key_present: int = 0
    populated: int = 0


def _default_workspaces_root() -> Path:
    override = os.environ.get(_WORKSPACES_ROOT_ENV, "").strip()
    return Path(override) if override else _WORKSPACES_DIR_DEFAULT


def _iter_handover_files(root: Path) -> Iterator[Path]:
    if not root.is_dir():
        return
    yield from sorted(root.glob("**/handovers/*.yaml"))


def _load_raw(path: Path) -> dict[str, Any] | None:
    try:
        data: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


def _record_question(
    stats: dict[str, TeamStats], from_team: str, question: object
) -> None:
    if not isinstance(question, dict):
        return
    if question.get("blocking") is not True:
        return
    team_stats = stats[from_team]
    team_stats.total_questions += 1
    if "proposed_answer_space" not in question:
        return
    team_stats.key_present += 1
    value = question.get("proposed_answer_space")
    if isinstance(value, list) and value:
        team_stats.populated += 1


def scan_handovers(root: Path) -> dict[str, TeamStats]:
    stats: dict[str, TeamStats] = defaultdict(TeamStats)
    for path in _iter_handover_files(root):
        data = _load_raw(path)
        if data is None:
            continue
        from_team = str(data.get("from_team") or _UNKNOWN_TEAM)
        questions = data.get("open_questions")
        if not isinstance(questions, list):
            continue
        for question in questions:
            _record_question(stats, from_team, question)
    return dict(stats)


def _stats_row(label: str, s: TeamStats) -> str:
    rate = s.populated / s.total_questions if s.total_questions else 0.0
    return f"{label:<24} {s.total_questions:>8} {s.key_present:>6} {s.populated:>10} {rate:>7.1%}"


def _totals(stats: dict[str, TeamStats]) -> TeamStats:
    total = TeamStats()
    for s in stats.values():
        total.total_questions += s.total_questions
        total.key_present += s.key_present
        total.populated += s.populated
    return total


def render_report(stats: dict[str, TeamStats]) -> str:
    if not stats:
        return "No blocking OpenQuestions found."
    header = f"{'team':<24} {'blocking':>8} {'key':>6} {'populated':>10} {'rate':>8}"
    lines = [header, "-" * len(header)]
    lines.extend(_stats_row(team, stats[team]) for team in sorted(stats))
    lines.append("-" * len(header))
    lines.append(_stats_row("TOTAL", _totals(stats)))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=_default_workspaces_root())
    args = parser.parse_args()
    print(render_report(scan_handovers(args.root)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
