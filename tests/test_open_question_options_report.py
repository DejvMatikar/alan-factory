from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import open_question_options_report as report_mod  # type: ignore[import-not-found]  # noqa: E402


def _write_handover(
    root: Path,
    task_id: str,
    seq: str,
    *,
    from_team: str = "pr_merge",
    open_questions: list[dict[str, object]] | None = None,
    nested: bool = False,
) -> Path:
    if nested:
        handovers_dir = root / task_id / "tasks" / task_id / "handovers"
    else:
        handovers_dir = root / task_id / "handovers"
    handovers_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "handover_id": f"handover_{task_id}_{seq}",
        "from_team": from_team,
        "open_questions": open_questions if open_questions is not None else [],
    }
    target = handovers_dir / f"handover_{task_id}_{seq}.yaml"
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return target


def _blocking_question(
    *, options: list[str] | None = None, key_present: bool = True
) -> dict[str, object]:
    q: dict[str, object] = {
        "id": "q1", "question": "x", "resolver_role": "human", "blocking": True,
    }
    if key_present:
        q["proposed_answer_space"] = options if options is not None else []
    return q


def test_scan_handovers_missing_root_returns_empty(tmp_path: Path) -> None:
    assert report_mod.scan_handovers(tmp_path / "nope") == {}


def test_scan_handovers_no_handover_files_returns_empty(tmp_path: Path) -> None:
    tmp_path.mkdir(exist_ok=True)
    assert report_mod.scan_handovers(tmp_path) == {}


def test_scan_handovers_counts_key_presence_and_population(tmp_path: Path) -> None:
    _write_handover(
        tmp_path, "t1", "001", from_team="pr_merge",
        open_questions=[_blocking_question(options=["a", "b"])],
    )
    _write_handover(
        tmp_path, "t1", "002", from_team="pr_merge",
        open_questions=[_blocking_question(key_present=False)],
    )
    stats = report_mod.scan_handovers(tmp_path)
    s = stats["pr_merge"]
    assert s.total_questions == 2
    assert s.key_present == 1
    assert s.populated == 1


def test_scan_handovers_key_present_but_empty_not_populated(tmp_path: Path) -> None:
    _write_handover(
        tmp_path, "t1", "001", from_team="verification",
        open_questions=[_blocking_question(options=[])],
    )
    stats = report_mod.scan_handovers(tmp_path)
    s = stats["verification"]
    assert s.total_questions == 1
    assert s.key_present == 1
    assert s.populated == 0


def test_scan_handovers_ignores_non_blocking_questions(tmp_path: Path) -> None:
    q = _blocking_question(options=["a", "b"])
    q["blocking"] = False
    _write_handover(tmp_path, "t1", "001", open_questions=[q])
    assert report_mod.scan_handovers(tmp_path) == {}


def test_scan_handovers_buckets_by_from_team(tmp_path: Path) -> None:
    _write_handover(
        tmp_path, "t1", "001", from_team="pr_merge",
        open_questions=[_blocking_question(options=["a", "b"])],
    )
    _write_handover(
        tmp_path, "t2", "001", from_team="implementation_fix",
        open_questions=[_blocking_question(options=["a", "b"])],
    )
    stats = report_mod.scan_handovers(tmp_path)
    assert set(stats) == {"pr_merge", "implementation_fix"}


def test_scan_handovers_unknown_team_when_from_team_missing(tmp_path: Path) -> None:
    handovers_dir = tmp_path / "t1" / "handovers"
    handovers_dir.mkdir(parents=True)
    payload = {"open_questions": [_blocking_question(options=["a", "b"])]}
    (handovers_dir / "handover_t1_001.yaml").write_text(
        yaml.safe_dump(payload), encoding="utf-8"
    )
    stats = report_mod.scan_handovers(tmp_path)
    assert "unknown" in stats


def test_scan_handovers_matches_nested_tasks_layout(tmp_path: Path) -> None:
    _write_handover(
        tmp_path, "t1", "001", from_team="pr_merge",
        open_questions=[_blocking_question(options=["a", "b"])],
        nested=True,
    )
    stats = report_mod.scan_handovers(tmp_path)
    assert stats["pr_merge"].total_questions == 1


def test_scan_handovers_skips_invalid_yaml(tmp_path: Path) -> None:
    handovers_dir = tmp_path / "t1" / "handovers"
    handovers_dir.mkdir(parents=True)
    (handovers_dir / "handover_t1_001.yaml").write_text(
        "not: valid: yaml: [", encoding="utf-8"
    )
    assert report_mod.scan_handovers(tmp_path) == {}


def test_scan_handovers_skips_non_mapping_root(tmp_path: Path) -> None:
    handovers_dir = tmp_path / "t1" / "handovers"
    handovers_dir.mkdir(parents=True)
    (handovers_dir / "handover_t1_001.yaml").write_text(
        yaml.safe_dump(["a", "b"]), encoding="utf-8"
    )
    assert report_mod.scan_handovers(tmp_path) == {}


def test_scan_handovers_skips_non_list_open_questions(tmp_path: Path) -> None:
    handovers_dir = tmp_path / "t1" / "handovers"
    handovers_dir.mkdir(parents=True)
    payload = {"from_team": "pr_merge", "open_questions": "not-a-list"}
    (handovers_dir / "handover_t1_001.yaml").write_text(
        yaml.safe_dump(payload), encoding="utf-8"
    )
    assert report_mod.scan_handovers(tmp_path) == {}


def test_scan_handovers_skips_non_dict_question_items(tmp_path: Path) -> None:
    handovers_dir = tmp_path / "t1" / "handovers"
    handovers_dir.mkdir(parents=True)
    payload = {"from_team": "pr_merge", "open_questions": ["not-a-dict", 42]}
    (handovers_dir / "handover_t1_001.yaml").write_text(
        yaml.safe_dump(payload), encoding="utf-8"
    )
    assert report_mod.scan_handovers(tmp_path) == {}


def test_render_report_empty_stats() -> None:
    assert "No blocking OpenQuestions" in report_mod.render_report({})


def test_render_report_formats_counts_and_totals() -> None:
    stats = {
        "pr_merge": report_mod.TeamStats(total_questions=2, key_present=2, populated=2),
        "verification": report_mod.TeamStats(total_questions=1, key_present=0, populated=0),
    }
    report = report_mod.render_report(stats)
    assert "pr_merge" in report
    assert "verification" in report
    assert "TOTAL" in report
    assert "100.0%" in report


def test_default_workspaces_root_uses_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_WORKSPACES_ROOT", str(tmp_path))
    assert report_mod._default_workspaces_root() == tmp_path  # noqa: SLF001


def test_default_workspaces_root_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_WORKSPACES_ROOT", raising=False)
    assert (
        report_mod._default_workspaces_root()  # noqa: SLF001
        == report_mod._WORKSPACES_DIR_DEFAULT  # noqa: SLF001
    )


def test_main_prints_report_and_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_handover(
        tmp_path, "t1", "001", from_team="pr_merge",
        open_questions=[_blocking_question(options=["a", "b"])],
    )
    monkeypatch.setattr(sys, "argv", ["open_question_options_report.py", "--root", str(tmp_path)])
    assert report_mod.main() == 0
    out = capsys.readouterr().out
    assert "pr_merge" in out
