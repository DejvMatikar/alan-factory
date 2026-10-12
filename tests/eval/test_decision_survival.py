from __future__ import annotations

import math
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan.compression import load_cross_team_prior_outputs
from alan.handover import Decision, Handover, save_handover
from alan.workspace import create


def _chain(
    depth: int, tmp_path: Path, *, decisions_only: bool = False
) -> tuple[list[str], str]:
    ws = create(f"chain-{depth}", "eval workspace", root=tmp_path)
    ids: list[str] = []
    for hop in range(depth):
        d1_id = f"d{hop}a"
        d2_id = f"d{hop}b"
        ids.extend([d1_id, d2_id])
        handover = Handover(
            handover_id=f"handover_{ws.task_id}_hop{hop}",
            handover_type="forward",
            task_id=ws.task_id,
            from_team=f"team_{hop}",
            from_invocation_id="inv_001",
            to_team_suggestions=["next_team"],
            iteration=hop + 1,
            timestamp_utc=datetime.now(UTC),
            decisions=[
                Decision(
                    id=d1_id,
                    statement=f"[{d1_id}] pick option A",
                    rationale="eval fixture",
                    confidence=0.9,
                    reversibility="high",
                ),
                Decision(
                    id=d2_id,
                    statement=f"[{d2_id}] pick option B",
                    rationale="eval fixture",
                    confidence=0.8,
                    reversibility="medium",
                ),
            ],
            full_synthesis=f"synthesis hop {hop} must skip on decisions_only",
        )
        save_handover(ws, handover)
    return ids, load_cross_team_prior_outputs(
        ws, "next_team", decisions_only=decisions_only
    )


def _chain_llm(depth: int, ws_root: Path) -> tuple[list[str], str]:
    ws = create(f"chain-llm-{depth}", "eval workspace llm", root=ws_root)
    ids: list[str] = []
    for hop in range(depth):
        d1_id = f"d{hop}a"
        d2_id = f"d{hop}b"
        ids.extend([d1_id, d2_id])
        synthesis = f"Hop {hop} synthesis detail: " + "x" * 1600
        handover = Handover(
            handover_id=f"handover_llm_{ws.task_id}_hop{hop}",
            handover_type="forward",
            task_id=ws.task_id,
            from_team=f"team_{hop}",
            from_invocation_id="inv_001",
            to_team_suggestions=["next_team"],
            iteration=hop + 1,
            timestamp_utc=datetime.now(UTC),
            decisions=[
                Decision(
                    id=d1_id,
                    statement=f"[{d1_id}] pick option A",
                    rationale="eval fixture",
                    confidence=0.9,
                    reversibility="high",
                ),
                Decision(
                    id=d2_id,
                    statement=f"[{d2_id}] pick option B",
                    rationale="eval fixture",
                    confidence=0.8,
                    reversibility="medium",
                ),
            ],
            full_synthesis=synthesis,
        )
        save_handover(ws, handover)
    return ids, load_cross_team_prior_outputs(ws, "next_team")


@pytest.mark.eval
def test_decision_survival_rate(tmp_path: Path) -> None:
    for depth in (2, 4, 6):
        source_ids, output = _chain(depth, tmp_path)
        surviving = sum(1 for did in source_ids if did in output)
        rate = surviving / len(source_ids)
        label = f"{depth}hop"
        print(f"EVAL_METRIC decision_survival_rate_{label}={rate:.3f}")
        assert math.isfinite(rate) and 0.0 <= rate <= 1.0, (
            f"decision_survival_rate_{label}={rate} out of [0.0, 1.0]"
        )


@pytest.mark.eval
def test_decision_survival_rate_decisions_only(tmp_path: Path) -> None:
    for depth in (2, 4, 6):
        source_ids, output = _chain(depth, tmp_path, decisions_only=True)
        surviving = sum(1 for did in source_ids if did in output)
        rate = surviving / len(source_ids)
        label = f"{depth}hop"
        print(f"EVAL_METRIC decision_survival_rate_decisions_only_{label}={rate:.3f}")
        assert rate == 1.0, (
            f"decision_survival_rate_decisions_only_{label}={rate} != 1.0"
        )
        assert "synthesis hop" not in output


@pytest.mark.eval
def test_decision_survival_rate_llm_compact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "claude"
    stub_script = (
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "sys.stdin.read()\n"
        "print(json.dumps("
        "{'type': 'result', 'subtype': 'success', 'result': 'Compacted.'})"
        ", flush=True)\n"
    )
    stub.write_text(stub_script, encoding="utf-8")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{stub_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    ws_root = tmp_path / "workspaces"
    for depth in (2, 4, 6):
        source_ids, output = _chain_llm(depth, ws_root)
        assert "Summary: Compacted." in output, (
            f"LLM compact path not exercised at depth {depth}"
        )
        surviving = sum(1 for did in source_ids if did in output)
        rate = surviving / len(source_ids)
        label = f"{depth}hop"
        print(f"EVAL_METRIC decision_survival_rate_llm_compact_{label}={rate:.3f}")
        assert rate >= 0.95, (
            f"decision_survival_rate_llm_compact_{label}={rate:.3f} < 0.95"
        )
