from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from alan import roots
from alan.teams import brainstorming, opportunity_scout
from alan.teams._orchestrated import _FIELD_LINE_RE
from alan.teams.implementation_plan import (
    ImplementationPlanError,
    _parse_decisions_block,
    _parse_test_scenarios,
)
from alan.teams.research import TEAM as _research_team
from alan.workspace import Status, list_workspaces

_ORCHESTRATED_TEAMS = (
    brainstorming.TEAM,
    opportunity_scout.TEAM,
    _research_team,
)

_LAYER3_SAMPLE_FLOOR = 3
_LAYER3_THRESHOLD = 0.95
_LAYER3_COHORT_SINCE = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _has_all_four_markers(prompt: str) -> bool:
    return all(rx.search(prompt) is not None for rx in _FIELD_LINE_RE.values())


def _is_implementation_plan_inv(result_path: Path) -> bool:
    wf_path = result_path.parent.parent / "workflow.yaml"
    if not wf_path.is_file():
        return False
    try:
        raw = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return False
    return isinstance(raw, dict) and raw.get("team") == "implementation_plan"


def _load_result_text(result_path: Path) -> str | None:
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    text = data.get("result_text")
    return text if isinstance(text, str) else None


def _is_recent_enough(path: Path) -> bool:
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return False
    return mtime >= _LAYER3_COHORT_SINCE


def _collect_synthesizer_texts() -> list[str]:
    workspaces_dir = roots.workspaces_root()
    if not workspaces_dir.is_dir():
        return []
    texts: list[str] = []
    for path in workspaces_dir.glob("**/invocations/*/agents/synthesizer.result.json"):
        if not _is_recent_enough(path):
            continue
        if not _is_implementation_plan_inv(path):
            continue
        text = _load_result_text(path)
        if text is not None:
            texts.append(text)
    return texts


def _parses_successfully(text: str) -> bool:
    try:
        _parse_decisions_block(text)
        _parse_test_scenarios(text)
    except ImplementationPlanError:
        return False
    return True


def test_layer2_marker_parse_rate() -> None:
    matched = sum(
        1
        for team in _ORCHESTRATED_TEAMS
        if _has_all_four_markers(team.orchestrator_prompt)
    )
    total = len(_ORCHESTRATED_TEAMS)
    rate = matched / total
    assert rate > 0.95, f"Layer-2 marker parse rate {rate:.3f} below threshold 0.95"


@pytest.mark.eval
def test_layer3_decisions_parse_rate() -> None:
    texts = _collect_synthesizer_texts()
    if not texts:
        print("EVAL_METRIC layer3_decisions_parse_rate=N/A (no synthesizer outputs found)")
        pytest.skip("no implementation_plan synthesizer outputs on disk")
    passed = sum(1 for t in texts if _parses_successfully(t))
    total = len(texts)
    rate = passed / total
    print(f"EVAL_METRIC layer3_decisions_parse_rate={rate:.3f} ({passed}/{total})")
    if total >= _LAYER3_SAMPLE_FLOOR:
        assert rate > _LAYER3_THRESHOLD, (
            f"Layer-3 decisions parse rate {rate:.3f} below threshold "
            f"{_LAYER3_THRESHOLD} (n={total}); check synthesizer output format"
        )


def test_layer3_threshold_is_recalibrated() -> None:
    assert _LAYER3_THRESHOLD == 0.95


def test_is_recent_enough_treats_missing_file_as_not_recent(tmp_path: Path) -> None:
    assert _is_recent_enough(tmp_path / "does-not-exist.result.json") is False


def test_layer3_cohort_excludes_pre_cutoff_samples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tests.eval.test_structural_metrics as self_mod

    monkeypatch.setenv("ALAN_WORKSPACES_ROOT", str(tmp_path))

    def _seed(inv_id: str, mtime: datetime) -> None:
        agents_dir = tmp_path / "ws1" / "invocations" / inv_id / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        result_path = agents_dir / "synthesizer.result.json"
        result_path.write_text(json.dumps({"result_text": f"text-{inv_id}"}))
        (agents_dir.parent / "workflow.yaml").write_text(
            "team: implementation_plan\n"
        )
        ts = mtime.timestamp()
        os.utime(result_path, (ts, ts))

    _seed("inv_old", datetime(2026, 5, 15, tzinfo=timezone.utc))
    _seed("inv_new", datetime(2026, 6, 15, tzinfo=timezone.utc))

    texts = self_mod._collect_synthesizer_texts()
    assert texts == ["text-inv_new"]


@pytest.mark.eval
def test_completed_fraction() -> None:
    workspaces = list_workspaces()
    non_archived = [ws for ws in workspaces if ws.meta.status != Status.ARCHIVED]
    if not non_archived:
        print("EVAL_METRIC completed_fraction=N/A (no non-archived workspaces)")
        pytest.skip("no non-archived workspaces")
    completed = sum(1 for ws in non_archived if ws.meta.status == Status.COMPLETED)
    rate = completed / len(non_archived)
    print(f"EVAL_METRIC completed_fraction={rate:.3f} ({completed}/{len(non_archived)})")
