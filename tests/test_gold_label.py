from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import gold_label
from alan import project as project_mod
from alan import workspace as workspace_mod
from alan.handover import Handover, OpenQuestion, QuestionClass
from alan.roots import logs_root as roots_logs_root


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def logs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(p))
    return p


def _question(
    *,
    question_id: str = "q1",
    recommended: str | None = "a",
    question_class: QuestionClass | None = "fix_escalation",
) -> OpenQuestion:
    return OpenQuestion(
        id=question_id,
        question="which?",
        proposed_answer_space=["a", "b"],
        resolver_role="human",
        blocking=True,
        recommended=recommended,
        question_class=question_class,
    )


def _handover(*, timestamp_utc: datetime | None = None) -> Handover:
    return Handover(
        handover_id="h1",
        handover_type="escalation",
        task_id="t1",
        from_team="verification",
        from_invocation_id="inv_001",
        iteration=1,
        timestamp_utc=timestamp_utc or datetime(2020, 1, 1, tzinfo=UTC),
        full_synthesis="synthesis",
        open_questions=[],
    )


def test_audit_dir_reads_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path))
    assert roots_logs_root().resolve() == tmp_path.resolve()


def test_audit_dir_falls_back_to_home_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_LOGS_ROOT", raising=False)
    assert roots_logs_root().resolve() == (Path.home() / ".alan" / "logs").resolve()


def test_truncate_to_cap_missing_file_is_a_noop(tmp_path: Path) -> None:
    path = tmp_path / "nofile.jsonl"
    gold_label._truncate_to_cap(path, root=tmp_path)  # pyright: ignore[reportPrivateUsage]
    assert not path.exists()


def test_truncate_to_cap_path_outside_root_is_a_noop(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    other_root = tmp_path / "root"
    other_root.mkdir()
    path = outside / "rows.jsonl"
    path.write_text("a\nb\n", encoding="utf-8")

    gold_label._truncate_to_cap(path, root=other_root)  # pyright: ignore[reportPrivateUsage]

    assert path.read_text(encoding="utf-8") == "a\nb\n"


def test_truncate_to_cap_read_failure_is_a_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "rows.jsonl"
    path.write_text("a\nb\n", encoding="utf-8")

    def boom(*_a: object, **_kw: object) -> str:
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_text", boom)
    gold_label._truncate_to_cap(path, root=tmp_path)  # pyright: ignore[reportPrivateUsage]
    assert path.read_bytes() == b"a\nb\n"


def test_truncate_to_cap_under_cap_is_a_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gold_label, "_ROW_CAP", 5)
    path = tmp_path / "rows.jsonl"
    path.write_text("a\nb\n", encoding="utf-8")
    gold_label._truncate_to_cap(path, root=tmp_path)  # pyright: ignore[reportPrivateUsage]
    assert path.read_text(encoding="utf-8") == "a\nb\n"


def test_truncate_to_cap_over_cap_keeps_last_n(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gold_label, "_ROW_CAP", 3)
    path = tmp_path / "rows.jsonl"
    path.write_text("\n".join(str(i) for i in range(5)) + "\n", encoding="utf-8")
    gold_label._truncate_to_cap(path, root=tmp_path)  # pyright: ignore[reportPrivateUsage]
    assert path.read_text(encoding="utf-8").splitlines() == ["2", "3", "4"]


def test_truncate_to_cap_write_failure_is_logged_and_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(gold_label, "_ROW_CAP", 1)
    path = tmp_path / "rows.jsonl"
    path.write_text("a\nb\nc\n", encoding="utf-8")

    def boom(*_a: object, **_kw: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", boom)
    with caplog.at_level(logging.WARNING, logger="alan.gold_label"):
        gold_label._truncate_to_cap(path, root=tmp_path)  # pyright: ignore[reportPrivateUsage]
    assert "truncate failed" in caplog.text


def test_record_gold_label_writes_agreeing_row(
    root: Path, logs_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(projects_root))
    project = project_mod.create("Gold Label Project", root=projects_root)
    ws = workspace_mod.create("gl-ws", "d", root=root, project_id=project.id)
    blocking = _handover()
    question = _question(recommended="a")

    gold_label.record_gold_label(ws=ws, blocking=blocking, question=question, answer="a")

    path = logs_root / gold_label.GOLD_LABEL_FILE
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["task_id"] == ws.task_id
    assert row["workspace_id"] == ws.task_id
    assert row["project_id"] == project.id
    assert row["question_id"] == "q1"
    assert row["question_class"] == "fix_escalation"
    assert row["options"] == ["a", "b"]
    assert row["recommended"] == "a"
    assert row["chosen"] == "a"
    assert row["agreed_with_recommendation"] is True
    assert row["time_to_answer_seconds"] > 0
    assert "timestamp_utc" in row


def test_record_gold_label_writes_disagreeing_row(root: Path, logs_root: Path) -> None:
    ws = workspace_mod.create("gl-ws-2", "d", root=root)
    blocking = _handover()
    question = _question(recommended="a")

    gold_label.record_gold_label(ws=ws, blocking=blocking, question=question, answer="b")

    path = logs_root / gold_label.GOLD_LABEL_FILE
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert row["project_id"] is None
    assert row["chosen"] == "b"
    assert row["agreed_with_recommendation"] is False


def test_record_gold_label_no_recommendation_is_not_agreement(
    root: Path, logs_root: Path
) -> None:
    ws = workspace_mod.create("gl-ws-3", "d", root=root)
    blocking = _handover()
    question = _question(recommended=None)

    gold_label.record_gold_label(ws=ws, blocking=blocking, question=question, answer="a")

    path = logs_root / gold_label.GOLD_LABEL_FILE
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert row["recommended"] is None
    assert row["agreed_with_recommendation"] is False


def test_record_gold_label_swallows_unexpected_failure(
    root: Path, logs_root: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = workspace_mod.create("gl-ws-boom", "d", root=root)
    blocking = _handover()

    def boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(gold_label, "append_jsonl_audit", boom)
    with caplog.at_level(logging.ERROR, logger="alan.gold_label"):
        gold_label.record_gold_label(
            ws=ws, blocking=blocking, question=_question(), answer="a"
        )

    assert "record failed" in caplog.text
    assert not (logs_root / gold_label.GOLD_LABEL_FILE).exists()


def test_record_gold_label_enforces_row_cap(
    root: Path, logs_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gold_label, "_ROW_CAP", 2)
    ws = workspace_mod.create("gl-ws-cap", "d", root=root)
    blocking = _handover()

    for i in range(3):
        gold_label.record_gold_label(
            ws=ws, blocking=blocking, question=_question(question_id=f"q{i}"), answer="a"
        )

    path = logs_root / gold_label.GOLD_LABEL_FILE
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert [r["question_id"] for r in rows] == ["q1", "q2"]


def test_load_gold_labels_missing_file_returns_empty(tmp_path: Path) -> None:
    assert gold_label.load_gold_labels(tmp_path / "nofile.jsonl") == []


def test_load_gold_labels_defaults_to_audit_dir(logs_root: Path) -> None:
    logs_root.mkdir(parents=True, exist_ok=True)
    path = logs_root / gold_label.GOLD_LABEL_FILE
    path.write_text(json.dumps({"question_class": "fix_escalation"}) + "\n", encoding="utf-8")

    rows = gold_label.load_gold_labels()

    assert rows == [{"question_class": "fix_escalation"}]


def test_load_gold_labels_skips_blank_malformed_and_non_dict_lines(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    path.write_text(
        "\n".join(
            [
                "",
                "not json {{{",
                json.dumps(["a", "b"]),
                json.dumps({"question_class": "fix_escalation"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    rows = gold_label.load_gold_labels(path)

    assert rows == [{"question_class": "fix_escalation"}]


def test_agreement_rate_by_class_computes_per_class_rate() -> None:
    rows: list[dict[str, object]] = [
        {
            "question_class": "fix_escalation",
            "recommended": "a",
            "agreed_with_recommendation": True,
        },
        {
            "question_class": "fix_escalation",
            "recommended": "a",
            "agreed_with_recommendation": False,
        },
        {
            "question_class": "verification_escalation",
            "recommended": "a",
            "agreed_with_recommendation": True,
        },
    ]

    rates = gold_label.agreement_rate_by_class(rows)

    assert rates == {"fix_escalation": 0.5, "verification_escalation": 1.0}


def test_agreement_rate_by_class_skips_rows_missing_or_malformed_fields() -> None:
    rows: list[dict[str, object]] = [
        {"question_class": None, "recommended": "a", "agreed_with_recommendation": True},
        {
            "question_class": "fix_escalation",
            "recommended": "a",
            "agreed_with_recommendation": "yes",
        },
        {"question_class": "fix_escalation", "recommended": "a"},
    ]

    rates = gold_label.agreement_rate_by_class(rows)

    assert rates == {}


def test_agreement_rate_by_class_excludes_rows_with_no_recommendation() -> None:
    rows: list[dict[str, object]] = [
        {
            "question_class": "fix_escalation",
            "recommended": None,
            "agreed_with_recommendation": False,
        },
        {
            "question_class": "fix_escalation",
            "recommended": "a",
            "agreed_with_recommendation": True,
        },
    ]

    rates = gold_label.agreement_rate_by_class(rows)

    assert rates == {"fix_escalation": 1.0}


def test_latency_stats_by_class_computes_mean_p50_and_count() -> None:
    rows: list[dict[str, object]] = [
        {"question_class": "verification_escalation", "time_to_answer_seconds": 5},
        {"question_class": "fix_escalation", "time_to_answer_seconds": 10.0},
        {"question_class": "fix_escalation", "time_to_answer_seconds": 300.0},
        {"question_class": "fix_escalation", "time_to_answer_seconds": 20.0},
        {"question_class": "orchestrated_completeness", "time_to_answer_seconds": 10.0},
        {"question_class": "orchestrated_completeness", "time_to_answer_seconds": 20.0},
        {"question_class": "orchestrated_completeness", "time_to_answer_seconds": 30.0},
        {"question_class": "orchestrated_completeness", "time_to_answer_seconds": 1000.0},
    ]

    stats = gold_label.latency_stats_by_class(rows)

    assert stats["verification_escalation"] == {
        "mean_seconds": 5.0, "p50_seconds": 5.0, "count": 1,
    }
    assert stats["fix_escalation"] == {
        "mean_seconds": 110.0, "p50_seconds": 20.0, "count": 3,
    }
    assert stats["orchestrated_completeness"] == {
        "mean_seconds": 265.0, "p50_seconds": 25.0, "count": 4,
    }


def test_latency_stats_by_class_skips_invalid_rows() -> None:
    rows: list[dict[str, object]] = [
        {"question_class": None, "time_to_answer_seconds": 10.0},
        {"question_class": "fix_escalation", "time_to_answer_seconds": True},
        {"question_class": "fix_escalation", "time_to_answer_seconds": "slow"},
        {"question_class": "fix_escalation"},
    ]

    stats = gold_label.latency_stats_by_class(rows)

    assert stats == {}
