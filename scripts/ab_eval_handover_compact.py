"""A/B measurement: lexical vs LLM compaction of handover full_synthesis.

Reads handover YAMLs from ~/.alan/workspaces/*/handovers/ and
~/.alan/workspaces/*/tasks/<id>/handovers/ (new layout), filters to eligible
handovers (iteration > 1, len(full_synthesis) > _LLM_COMPACT_THRESHOLD), and
reports mean synthesis length vs mean LLM summary length.

Run with:
    uv run python scripts/ab_eval_handover_compact.py

Leave ALAN_LLM_COMPACT_DISABLE unset to enable LLM compaction.
Set ALAN_LLM_COMPACT_DISABLE=1 (or "true" / "yes") to measure only the
lexical baseline.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path
from typing import Any

from alan.compression import (
    _LLM_COMPACT_THRESHOLD,
    DEFAULT_MAX_CHARS,
    summarize_handover,
)
from alan.handover import HandoverError, load_handover
from alan.workspace import Workspace, WorkspaceError, read

_WORKSPACE_ROOT = Path.home() / ".alan" / "workspaces"


def _collect_handovers_for_workspace(ws_dir: Path) -> list[Path]:
    result: list[Path] = []
    legacy = ws_dir / "handovers"
    if legacy.is_dir():
        result.extend(sorted(legacy.glob("handover_*.yaml")))
    tasks_dir = ws_dir / "tasks"
    if tasks_dir.is_dir():
        for task_dir in sorted(tasks_dir.iterdir()):
            if task_dir.is_dir():
                task_h = task_dir / "handovers"
                if task_h.is_dir():
                    result.extend(sorted(task_h.glob("handover_*.yaml")))
    return result


def _iter_handover_paths(ws_root: Path) -> list[Path]:
    result: list[Path] = []
    for ws_dir in sorted(ws_root.iterdir()):
        if ws_dir.is_dir():
            result.extend(_collect_handovers_for_workspace(ws_dir))
    return result


def _workspace_from_handover_path(path: Path) -> Workspace | None:
    try:
        is_new = path.parents[2].name == "tasks"
        outer = path.parents[3] if is_new else path.parents[1]
        return read(outer.name, root=outer.parent)
    except (WorkspaceError, IndexError):
        return None


def _measure(path: Path) -> dict[str, Any] | None:
    try:
        h = load_handover(path)
    except HandoverError:
        return None
    if h.iteration <= 1:
        return None
    if len(h.full_synthesis) <= _LLM_COMPACT_THRESHOLD:
        return None
    ws = _workspace_from_handover_path(path)
    summary = summarize_handover(h, max_chars=DEFAULT_MAX_CHARS, workspace=ws)
    return {
        "handover_id": h.handover_id,
        "synthesis_len": len(h.full_synthesis),
        "summary_len": len(summary),
    }


def main() -> int:
    if not _WORKSPACE_ROOT.is_dir():
        print(f"No workspaces at {_WORKSPACE_ROOT}")
        return 0

    paths = _iter_handover_paths(_WORKSPACE_ROOT)
    records = [r for p in paths if (r := _measure(p)) is not None]

    if not records:
        print("No eligible handovers (iteration > 1, synthesis > threshold).")
        return 0

    avg_syn = statistics.mean(r["synthesis_len"] for r in records)
    avg_sum = statistics.mean(r["summary_len"] for r in records)
    ratio = avg_sum / avg_syn if avg_syn > 0 else 0.0

    print(json.dumps({
        "eligible_handovers": len(records),
        "mean_synthesis_chars": round(avg_syn, 1),
        "mean_summary_chars": round(avg_sum, 1),
        "compression_ratio": round(ratio, 3),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
