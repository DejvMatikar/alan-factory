from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import alan.unblock_ledger as unblock_ledger_mod
from alan import secrets_store
from alan.digest_sweep import DIGEST_LAST_DATE_META_KEY
from alan.health_repo_metrics import BLOCK_CORRECTION_AUDIT_FILE, classify_build_workspaces
from alan.queue import Queue
from alan.roots import logs_root
from alan.routes import infra as infra_mod
from alan.workspace import Requirement, Status, Task, TaskDoDClause, TaskStatus, transition
from alan.workspace import Workspace as _Workspace
from alan.workspace import create as create_workspace
from alan.workspace import read as read_workspace


def test_run_job_reconciler_passes_replay_routing_to_reconcile_on_startup(
    db_path: Path,
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan import reconciler as reconciler_mod
    from alan.dispatcher import PubSub
    from alan.queue import Queue

    captured: list[Any] = []

    def spy_reconcile(
        *, queue: Any, workspaces_root: Any, pubsub: Any, replay_routing: Any = None
    ) -> reconciler_mod.ReconciliationReport:
        captured.append(replay_routing)
        return reconciler_mod.ReconciliationReport()

    monkeypatch.setattr(reconciler_mod, "reconcile_on_startup", spy_reconcile)
    monkeypatch.setattr(reconciler_mod, "reconcile_stalled_dags", lambda **_: None)

    q = Queue(db_path=db_path)
    sentinel = lambda job, handover: None  # noqa: E731
    infra_mod._run_job_reconciler(q, ws_root, PubSub(), sentinel)  # noqa: SLF001
    q.close()

    assert captured == [sentinel]


def test_run_job_reconciler_passes_none_routing_by_default(
    db_path: Path,
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan import reconciler as reconciler_mod
    from alan.dispatcher import PubSub
    from alan.queue import Queue

    captured: list[Any] = []

    def spy_reconcile(
        *, queue: Any, workspaces_root: Any, pubsub: Any, replay_routing: Any = None
    ) -> reconciler_mod.ReconciliationReport:
        captured.append(replay_routing)
        return reconciler_mod.ReconciliationReport()

    monkeypatch.setattr(reconciler_mod, "reconcile_on_startup", spy_reconcile)
    monkeypatch.setattr(reconciler_mod, "reconcile_stalled_dags", lambda **_: None)

    q = Queue(db_path=db_path)
    infra_mod._run_job_reconciler(q, ws_root, PubSub())  # noqa: SLF001
    q.close()

    assert captured == [None]


def test_log_job_reconcile_report_logs_replayed(caplog: pytest.LogCaptureFixture) -> None:
    from alan import reconciler as reconciler_mod

    report = reconciler_mod.ReconciliationReport(replayed=[42])
    with caplog.at_level("INFO", logger="alan.server"):
        infra_mod._log_job_reconcile_report(report)  # noqa: SLF001
    assert any("replayed" in m for m in caplog.messages)


def test_log_job_reconcile_report_logs_rerun(caplog: pytest.LogCaptureFixture) -> None:
    from alan import reconciler as reconciler_mod

    report = reconciler_mod.ReconciliationReport(rerun=[7])
    with caplog.at_level("INFO", logger="alan.server"):
        infra_mod._log_job_reconcile_report(report)  # noqa: SLF001
    assert any("re-ran" in m for m in caplog.messages)


def test_lm_float_or_none_returns_none_for_none() -> None:
    assert infra_mod._lm_float_or_none(None) is None  # noqa: SLF001


def test_lm_float_or_none_returns_none_for_bool() -> None:
    assert infra_mod._lm_float_or_none(True) is None  # noqa: SLF001
    assert infra_mod._lm_float_or_none(False) is None  # noqa: SLF001


def test_lm_float_or_none_returns_float_for_numeric() -> None:
    assert infra_mod._lm_float_or_none(0.75) == 0.75  # noqa: SLF001
    assert infra_mod._lm_float_or_none(1) == 1.0  # noqa: SLF001


def test_lm_float_or_none_returns_none_for_string() -> None:
    assert infra_mod._lm_float_or_none("0.5") is None  # noqa: SLF001


def _build_task(*, team: str = "implementation", evidenced: bool = True) -> Task:
    clause = TaskDoDClause(text="Feature merged", evidence="PR #1" if evidenced else None)
    return Task(id="t1", team=team, status=TaskStatus.COMPLETED, dod_clauses=[clause])


def _make_ws(
    ws_root: Path,
    slug: str,
    status: Status,
    *,
    team: str = "implementation",
    evidenced: bool = True,
) -> _Workspace:
    ws = create_workspace(
        slug, "d", root=ws_root, tasks=[_build_task(team=team, evidenced=evidenced)]
    )
    transition(ws, status)
    return read_workspace(ws.task_id, root=ws_root)


def test_compute_build_success_rate_empty_list() -> None:
    rate, in_flight = infra_mod._compute_build_success_rate([])  # noqa: SLF001
    assert rate is None
    assert in_flight == 0


def test_compute_build_success_rate_counts_evidence_gap_as_failure(ws_root: Path) -> None:
    failed_ws = _make_ws(ws_root, "build-fail", Status.FAILED, evidenced=False)
    rate, _ = infra_mod._compute_build_success_rate([failed_ws])  # noqa: SLF001
    assert rate == pytest.approx(0.0)


def test_compute_build_success_rate_partial_and_escalated_are_terminal(ws_root: Path) -> None:
    partial_ws = _make_ws(ws_root, "build-partial", Status.PARTIAL, evidenced=False)
    escalated_ws = _make_ws(ws_root, "build-escalated", Status.ESCALATED, evidenced=False)
    rate, in_flight = infra_mod._compute_build_success_rate(  # noqa: SLF001
        [partial_ws, escalated_ws]
    )
    assert rate == pytest.approx(0.0)
    assert in_flight == 0


def test_compute_build_success_rate_mixed_success_and_failure(ws_root: Path) -> None:
    success_ws = _make_ws(ws_root, "build-ok", Status.COMPLETED)
    failure_ws = _make_ws(ws_root, "build-bad", Status.FAILED, evidenced=False)
    rate, _ = infra_mod._compute_build_success_rate([success_ws, failure_ws])  # noqa: SLF001
    assert rate == pytest.approx(0.5)


def _make_ws_no_dod_clauses(
    ws_root: Path, slug: str, ws_status: Status, task_status: TaskStatus
) -> _Workspace:
    task = Task(id="t1", team="implementation", status=task_status)
    ws = create_workspace(slug, "d", root=ws_root, tasks=[task])
    transition(ws, ws_status)
    return read_workspace(ws.task_id, root=ws_root)


def test_compute_build_success_rate_empty_dod_clauses_failed_task_is_not_vacuous_success(
    ws_root: Path,
) -> None:
    failed_ws = _make_ws_no_dod_clauses(
        ws_root, "build-crash-fail", Status.FAILED, TaskStatus.FAILED
    )
    terminal, in_flight = classify_build_workspaces([failed_ws])
    assert len(terminal) == 1
    assert in_flight == 0
    rate, _ = infra_mod._compute_build_success_rate([failed_ws])  # noqa: SLF001
    assert rate == pytest.approx(0.0)


def test_compute_build_success_rate_empty_dod_clauses_escalated_task_is_not_vacuous_success(
    ws_root: Path,
) -> None:
    escalated_ws = _make_ws_no_dod_clauses(
        ws_root, "build-crash-escalated", Status.ESCALATED, TaskStatus.AWAITING_INPUT
    )
    terminal, _ = classify_build_workspaces([escalated_ws])
    assert len(terminal) == 1
    rate, _ = infra_mod._compute_build_success_rate([escalated_ws])  # noqa: SLF001
    assert rate == pytest.approx(0.0)


def test_compute_build_success_rate_empty_dod_clauses_completed_task_is_success(
    ws_root: Path,
) -> None:
    completed_ws = _make_ws_no_dod_clauses(
        ws_root, "build-clean-success", Status.COMPLETED, TaskStatus.COMPLETED
    )
    rate, _ = infra_mod._compute_build_success_rate([completed_ws])  # noqa: SLF001
    assert rate == pytest.approx(1.0)


def test_compute_build_success_rate_no_tasks_is_not_vacuous_success(ws_root: Path) -> None:
    from alan.workspace import increment_revisit_count

    ws = create_workspace("build-no-tasks", "d", root=ws_root)
    ws = increment_revisit_count(ws, "implementation", "chain1")
    transition(ws, Status.FAILED)
    failed_ws = read_workspace(ws.task_id, root=ws_root)
    terminal, _ = classify_build_workspaces([failed_ws])
    assert len(terminal) == 1
    rate, _ = infra_mod._compute_build_success_rate([failed_ws])  # noqa: SLF001
    assert rate == pytest.approx(0.0)


def test_build_quality_metrics_no_lm_blob(db_path: Path, ws_root: Path) -> None:
    from alan.queue import Queue

    q = Queue(db_path=db_path)
    try:
        result = infra_mod._build_quality_metrics(q, ws_root)  # noqa: SLF001
    finally:
        q.close()
    assert result.build_success_rate is None
    assert result.in_flight_build_count == 0
    assert result.unproven_clause_count == 0
    assert result.first_pass_approval_rate is None
    assert result.mean_fix_cycles_to_merge is None
    assert result.sample_size is None
    assert result.computed_at is None
    assert result.rescue_success_rate is None
    assert result.rescue_success_rate_sample == 0
    assert result.rescue_success_rate_low_data is True
    assert result.rescue_load_rate is None
    assert result.rescue_load_rate_sample == 0
    assert result.rescue_load_rate_low_data is True


def test_build_quality_metrics_unproven_clause_count_counts_terminal_workspace(
    db_path: Path, ws_root: Path
) -> None:
    ws = create_workspace(
        "build-unproven-infra",
        "d",
        root=ws_root,
        tasks=[_build_task()],
        requirements=[Requirement(text="unbound clause")],
    )
    transition(ws, Status.COMPLETED)
    q = Queue(db_path=db_path)
    try:
        result = infra_mod._build_quality_metrics(q, ws_root)  # noqa: SLF001
    finally:
        q.close()
    assert result.unproven_clause_count == 1


def test_build_quality_metrics_with_valid_blob(
    db_path: Path, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime

    from alan.health import save_learning_metrics
    from alan.learning_metrics import LearningMetrics
    from alan.queue import Queue

    q = Queue(db_path=db_path)
    now = datetime.now(UTC)
    metrics = LearningMetrics(
        first_pass_approval_rate=0.8, mean_fix_cycles_to_merge=1.5, sample_size=10
    )
    save_learning_metrics(q, metrics, now)
    try:
        result = infra_mod._build_quality_metrics(q, ws_root)  # noqa: SLF001
    finally:
        q.close()
    assert result.first_pass_approval_rate == pytest.approx(0.8)
    assert result.mean_fix_cycles_to_merge is None
    assert result.sample_size == 10
    assert result.computed_at == now.isoformat()


def test_build_quality_metrics_hides_mean_fix_cycles_regardless_of_blob_value(
    db_path: Path, ws_root: Path
) -> None:
    from datetime import UTC, datetime

    from alan.health import save_learning_metrics
    from alan.learning_metrics import LearningMetrics
    from alan.queue import Queue

    q = Queue(db_path=db_path)
    metrics = LearningMetrics(
        first_pass_approval_rate=None, mean_fix_cycles_to_merge=9.9, sample_size=3
    )
    save_learning_metrics(q, metrics, datetime.now(UTC))
    try:
        result = infra_mod._build_quality_metrics(q, ws_root)  # noqa: SLF001
    finally:
        q.close()
    assert result.mean_fix_cycles_to_merge is None


def test_build_quality_metrics_sample_size_not_int(db_path: Path, ws_root: Path) -> None:
    from alan.queue import Queue

    q = Queue(db_path=db_path)
    q.set_meta("learning_metrics_v1", '{"sample_size": "ten", "computed_at": null}')
    try:
        result = infra_mod._build_quality_metrics(q, ws_root)  # noqa: SLF001
    finally:
        q.close()
    assert result.sample_size is None


def test_build_quality_metrics_computed_at_none(db_path: Path, ws_root: Path) -> None:
    from alan.queue import Queue

    q = Queue(db_path=db_path)
    q.set_meta("learning_metrics_v1", '{"sample_size": 5, "computed_at": null}')
    try:
        result = infra_mod._build_quality_metrics(q, ws_root)  # noqa: SLF001
    finally:
        q.close()
    assert result.computed_at is None
    assert result.sample_size == 5


def test_build_quality_metrics_sample_size_bool(db_path: Path, ws_root: Path) -> None:
    from alan.queue import Queue

    q = Queue(db_path=db_path)
    q.set_meta("learning_metrics_v1", '{"sample_size": true, "computed_at": null}')
    try:
        result = infra_mod._build_quality_metrics(q, ws_root)  # noqa: SLF001
    finally:
        q.close()
    assert result.sample_size is None


def test_build_quality_metrics_computed_at_non_string(
    db_path: Path, ws_root: Path
) -> None:
    from alan.queue import Queue

    q = Queue(db_path=db_path)
    q.set_meta("learning_metrics_v1", '{"sample_size": 5, "computed_at": 12345}')
    try:
        result = infra_mod._build_quality_metrics(q, ws_root)  # noqa: SLF001
    finally:
        q.close()
    assert result.computed_at is None
    assert result.sample_size == 5


def test_build_quality_metrics_list_workspaces_exception(
    db_path: Path, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.queue import Queue

    def _boom(*_a: object, **_kw: object) -> object:
        raise RuntimeError("disk error")

    monkeypatch.setattr("alan.routes.infra.workspace_mod.list_workspaces", _boom)
    q = Queue(db_path=db_path)
    try:
        result = infra_mod._build_quality_metrics(q, ws_root)  # noqa: SLF001
    finally:
        q.close()
    assert result.build_success_rate is None
    assert result.in_flight_build_count == 0
    assert result.unproven_clause_count == 0


def _write_block_correction_rows(logs_dir: Path, rows: list[dict[str, Any]]) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / BLOCK_CORRECTION_AUDIT_FILE
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def test_build_quality_metrics_format_correction_not_measured_on_empty_sample(
    db_path: Path, ws_root: Path
) -> None:
    q = Queue(db_path=db_path)
    try:
        result = infra_mod._build_quality_metrics(q, ws_root)  # noqa: SLF001
    finally:
        q.close()
    assert result.format_error_rate is None
    assert result.format_error_sample == 0
    assert result.format_error_low_data is True
    assert result.recovery_rate is None
    assert result.recovery_sample == 0
    assert result.recovery_low_data is True


def test_build_quality_metrics_format_correction_computed_from_audit_rows(
    db_path: Path, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_dir = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_dir))
    _write_block_correction_rows(
        logs_dir,
        [
            {"site": "s", "block": "b", "role": "planner", "outcome": "clean", "attempts": 1},
            {"site": "s", "block": "b", "role": "planner", "outcome": "malformed", "attempts": 1},
            {"site": "s", "block": "b", "role": "planner", "outcome": "corrected", "attempts": 2},
            {"site": "s", "block": "b", "role": "planner", "outcome": "exhausted", "attempts": 2},
        ],
    )
    q = Queue(db_path=db_path)
    try:
        result = infra_mod._build_quality_metrics(q, ws_root)  # noqa: SLF001
    finally:
        q.close()
    assert result.format_error_rate == pytest.approx(0.5)
    assert result.format_error_sample == 2
    assert result.format_error_low_data is False
    assert result.recovery_rate == pytest.approx(0.5)
    assert result.recovery_sample == 2
    assert result.recovery_low_data is False


def test_health_endpoint_surfaces_format_correction_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from alan import server

    logs_dir = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_dir))
    _write_block_correction_rows(
        logs_dir,
        [{"site": "s", "block": "b", "role": "planner", "outcome": "corrected", "attempts": 2}],
    )
    monkeypatch.setattr(infra_mod, "_QUALITY_METRICS_CACHE", {})
    with TestClient(server.app) as client:
        res = client.get("/api/health")
    quality = res.json()["quality_metrics"]
    assert quality["recovery_rate"] == pytest.approx(1.0)
    assert quality["recovery_sample"] == 1
    assert quality["recovery_low_data"] is False
    assert quality["format_error_rate"] is None
    assert quality["format_error_sample"] == 0
    assert quality["format_error_low_data"] is True


def test_health_load_learning_metrics_none_when_not_set(db_path: Path) -> None:
    from alan.health import load_learning_metrics
    from alan.queue import Queue

    q = Queue(db_path=db_path)
    try:
        assert load_learning_metrics(q) is None
    finally:
        q.close()


def test_health_load_learning_metrics_invalid_json(db_path: Path) -> None:
    from alan.health import load_learning_metrics
    from alan.queue import Queue

    q = Queue(db_path=db_path)
    q.set_meta("learning_metrics_v1", "not-json{")
    try:
        assert load_learning_metrics(q) is None
    finally:
        q.close()


def test_health_load_learning_metrics_non_dict_json(db_path: Path) -> None:
    from alan.health import load_learning_metrics
    from alan.queue import Queue

    q = Queue(db_path=db_path)
    q.set_meta("learning_metrics_v1", "[1, 2, 3]")
    try:
        assert load_learning_metrics(q) is None
    finally:
        q.close()


def test_health_save_and_load_learning_metrics_roundtrip(db_path: Path) -> None:
    from datetime import UTC, datetime

    from alan.health import load_learning_metrics, save_learning_metrics
    from alan.learning_metrics import LearningMetrics
    from alan.queue import Queue

    q = Queue(db_path=db_path)
    now = datetime.now(UTC)
    metrics = LearningMetrics(
        first_pass_approval_rate=0.6, mean_fix_cycles_to_merge=2.0, sample_size=7
    )
    save_learning_metrics(q, metrics, now)
    try:
        blob = load_learning_metrics(q)
    finally:
        q.close()
    assert blob is not None
    assert blob["first_pass_approval_rate"] == pytest.approx(0.6)
    assert blob["mean_fix_cycles_to_merge"] == pytest.approx(2.0)
    assert blob["sample_size"] == 7
    assert blob["computed_at"] == now.isoformat()


def test_import_legacy_directory_once_missing_dir_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan.engine_config as engine_config_mod

    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "store"))
    assert engine_config_mod.import_legacy_directory_once(tmp_path / "nonexistent") == []


def test_import_legacy_directory_once_persists_without_in_memory_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan.engine_config as engine_config_mod
    import alan.engine_registry_store as engine_registry_store_mod

    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "store"))
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    (legacy_dir / "sprocket.yaml").write_text(
        "repo:\n  owner: sprocket-co\n  repo: sprocket\nposture: propose\n"
    )

    loaded = engine_config_mod.import_legacy_directory_once(legacy_dir)

    assert [cfg.repo.slug for cfg in loaded] == ["sprocket-co/sprocket"]
    assert engine_config_mod.lookup_by_slug("sprocket-co/sprocket") is None
    persisted_slugs = {
        f"{p['repo']['owner']}/{p['repo']['repo']}"  # type: ignore[index]
        for p in engine_registry_store_mod.list_engines()
    }
    assert "sprocket-co/sprocket" in persisted_slugs


def test_import_legacy_directory_once_then_load_persisted_registers_in_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan.engine_config as engine_config_mod

    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "store"))
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    (legacy_dir / "sprocket.yaml").write_text(
        "repo:\n  owner: sprocket-co\n  repo: sprocket\nposture: propose\n"
    )

    engine_config_mod.import_legacy_directory_once(legacy_dir)
    engine_config_mod.load_persisted_engines()

    cfg = engine_config_mod.lookup_by_slug("sprocket-co/sprocket")
    assert cfg is not None
    assert cfg.posture == engine_config_mod.Posture.propose


def test_import_legacy_directory_once_store_nonempty_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan.engine_config as engine_config_mod

    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "store"))
    seed = engine_config_mod.EngineConfig(
        repo=engine_config_mod.RepoIdentity(owner="acme", repo="seed")
    )
    engine_config_mod.register_and_persist(seed, validate_env=False)

    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    (legacy_dir / "sprocket.yaml").write_text(
        "repo:\n  owner: sprocket-co\n  repo: sprocket\n"
    )

    assert engine_config_mod.import_legacy_directory_once(legacy_dir) == []
    assert engine_config_mod.lookup_by_slug("sprocket-co/sprocket") is None


def test_import_legacy_directory_once_duplicate_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan.engine_config as engine_config_mod

    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "store"))
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    (legacy_dir / "a.yaml").write_text("repo:\n  owner: sprocket-co\n  repo: sprocket\n")
    (legacy_dir / "b.yaml").write_text("repo:\n  owner: sprocket-co\n  repo: sprocket\n")

    with pytest.raises(engine_config_mod.DuplicateEngineError):
        engine_config_mod.import_legacy_directory_once(legacy_dir)


def test_import_legacy_engines_once_noop_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import alan.engine_config as engine_config_mod

    monkeypatch.delenv("ALAN_ENGINES_DIR", raising=False)
    infra_mod._import_legacy_engines_once()  # noqa: SLF001
    assert engine_config_mod.get_registered() is None
    assert engine_config_mod.lookup_by_slug("anything/here") is None


def test_import_legacy_engines_once_persists_from_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan.engine_registry_store as engine_registry_store_mod

    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "store"))
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    (legacy_dir / "second.yaml").write_text(
        "repo:\n  owner: sprocket-co\n  repo: sprocket\n"
        "posture: propose\n"
        "gate_commands:\n  - [cargo, test]\n"
        "warmup_commands:\n  - [cargo, fetch]\n"
    )
    monkeypatch.setenv("ALAN_ENGINES_DIR", str(legacy_dir))

    infra_mod._import_legacy_engines_once()  # noqa: SLF001

    persisted = engine_registry_store_mod.list_engines()
    assert len(persisted) == 1
    assert persisted[0]["posture"] == "propose"
    assert persisted[0]["gate_commands"] == [["cargo", "test"]]


def test_import_legacy_engines_once_dir_set_but_no_configs_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan.engine_config as engine_config_mod

    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "store"))
    monkeypatch.setenv("ALAN_ENGINES_DIR", str(tmp_path / "legacy"))

    infra_mod._import_legacy_engines_once()  # noqa: SLF001

    assert engine_config_mod.get_registered() is None


def test_import_legacy_engines_once_duplicate_is_caught_and_warned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "store"))
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    (legacy_dir / "a.yaml").write_text("repo:\n  owner: sprocket-co\n  repo: sprocket\n")
    (legacy_dir / "b.yaml").write_text("repo:\n  owner: sprocket-co\n  repo: sprocket\n")
    monkeypatch.setenv("ALAN_ENGINES_DIR", str(legacy_dir))

    with caplog.at_level("WARNING", logger="alan.server"):
        infra_mod._import_legacy_engines_once()  # noqa: SLF001

    assert any("duplicate" in m for m in caplog.messages)


def test_import_legacy_engines_once_logs_imported_slugs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "store"))
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    (legacy_dir / "second.yaml").write_text("repo:\n  owner: sprocket-co\n  repo: sprocket\n")
    monkeypatch.setenv("ALAN_ENGINES_DIR", str(legacy_dir))

    with caplog.at_level("INFO", logger="alan.server"):
        infra_mod._import_legacy_engines_once()  # noqa: SLF001

    assert any("sprocket-co/sprocket" in m for m in caplog.messages)


def test_health_no_completed_fraction_key() -> None:
    from fastapi.testclient import TestClient

    from alan import server

    with TestClient(server.app) as client:
        res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert "completed_fraction" not in data
    assert "completed_fraction" not in data["quality_metrics"]


def test_digest_overdue_flag_true_past_deadline_with_no_record(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 7, 5, 9, 30, tzinfo=UTC)
    deadline = datetime(2026, 7, 5, 9, 0, tzinfo=UTC)

    monkeypatch.setattr(infra_mod, "resolve_digest_deadline", lambda _now: deadline)
    assert infra_mod._digest_overdue_flag(queue, now) is True  # noqa: SLF001


def test_digest_overdue_flag_false_when_today_already_recorded(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 7, 5, 9, 30, tzinfo=UTC)
    deadline = datetime(2026, 7, 5, 9, 0, tzinfo=UTC)
    queue.set_meta(DIGEST_LAST_DATE_META_KEY, "2026-07-05")

    monkeypatch.setattr(infra_mod, "resolve_digest_deadline", lambda _now: deadline)
    assert infra_mod._digest_overdue_flag(queue, now) is False  # noqa: SLF001


def test_digest_overdue_flag_false_before_deadline(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 7, 5, 8, 0, tzinfo=UTC)
    deadline = datetime(2026, 7, 5, 9, 0, tzinfo=UTC)

    monkeypatch.setattr(infra_mod, "resolve_digest_deadline", lambda _now: deadline)
    assert infra_mod._digest_overdue_flag(queue, now) is False  # noqa: SLF001


def test_health_endpoint_surfaces_digest_overdue_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from alan import server

    monkeypatch.setattr(
        infra_mod, "resolve_digest_deadline", lambda now: now - timedelta(hours=1)
    )
    with TestClient(server.app) as client:
        res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["digest_overdue"] is True


def test_health_endpoint_digest_overdue_false_when_recorded_today(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from alan import server

    monkeypatch.setattr(
        infra_mod, "resolve_digest_deadline", lambda now: now - timedelta(hours=1)
    )
    today_key = (datetime.now(UTC) - timedelta(hours=1)).date().isoformat()
    with TestClient(server.app) as client:
        server.app.state.queue.set_meta(DIGEST_LAST_DATE_META_KEY, today_key)
        res = client.get("/api/health")
    assert res.json()["digest_overdue"] is False


def _register_sprocket_engine() -> None:
    from alan.engine_config import EngineConfig, RepoIdentity, register

    register(EngineConfig(repo=RepoIdentity(owner="sprocket-co", repo="sprocket")))


def _save_two_findings_health_state(tmp_path: Path, ws_project_id: str | None = None) -> str:
    from alan import server
    from alan.health import HealthFinding, HealthState, save_health_state

    ws = create_workspace("Health Filter WS", "d", root=tmp_path, project_id=ws_project_id)
    state = HealthState(
        verdict="degraded",
        blocked_count=2,
        in_flight_count=0,
        findings=[
            HealthFinding(
                finding_id="f-alan",
                kind="build_task",
                severity="high",
                description="d",
                workspace_id=None,
                pr_number=None,
                blocker_flags=[],
                action="dispatch_remediation",
                engine_key=None,
                repo="o/r",
            ),
            HealthFinding(
                finding_id="f-sprocket",
                kind="build_task",
                severity="high",
                description="d",
                workspace_id=ws.task_id,
                pr_number=None,
                blocker_flags=[],
                action="dispatch_remediation",
                engine_key="sprocket-co/sprocket",
            ),
        ],
        computed_at=datetime.now(UTC).isoformat(),
    )
    save_health_state(server.app.state.queue, state)
    return ws.task_id


def test_health_endpoint_no_filter_returns_all_findings(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from alan import server

    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: tmp_path
    try:
        with TestClient(server.app) as client:
            _save_two_findings_health_state(tmp_path)
            res = client.get("/api/health")
    finally:
        server.app.dependency_overrides.clear()
    findings = res.json()["health_state"]["findings"]
    assert {f["finding_id"] for f in findings} == {"f-alan", "f-sprocket"}


def test_health_endpoint_engine_filter_returns_only_matching_finding(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from alan import server

    _register_sprocket_engine()
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: tmp_path
    try:
        with TestClient(server.app) as client:
            _save_two_findings_health_state(tmp_path)
            res = client.get("/api/health", params={"engine": "sprocket-co/sprocket"})
    finally:
        server.app.dependency_overrides.clear()
    findings = res.json()["health_state"]["findings"]
    assert [f["finding_id"] for f in findings] == ["f-sprocket"]


def test_health_endpoint_explicit_default_engine_filter_matches_none_keyed_finding(
    tmp_path: Path,
) -> None:
    from fastapi.testclient import TestClient

    from alan import server

    _register_sprocket_engine()
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: tmp_path
    try:
        with TestClient(server.app) as client:
            _save_two_findings_health_state(tmp_path)
            res = client.get("/api/health", params={"engine": "DejvMatikar/alan"})
    finally:
        server.app.dependency_overrides.clear()
    findings = res.json()["health_state"]["findings"]
    assert [f["finding_id"] for f in findings] == ["f-alan"]


def test_health_endpoint_project_id_filter_returns_only_matching_finding(
    tmp_path: Path,
) -> None:
    from fastapi.testclient import TestClient

    from alan import server

    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: tmp_path
    try:
        with TestClient(server.app) as client:
            _save_two_findings_health_state(tmp_path, ws_project_id="proj-1")
            res = client.get("/api/health", params={"project_id": "proj-1"})
    finally:
        server.app.dependency_overrides.clear()
    findings = res.json()["health_state"]["findings"]
    assert [f["finding_id"] for f in findings] == ["f-sprocket"]


def test_health_endpoint_findings_include_repo_and_engine_key(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from alan import server

    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: tmp_path
    try:
        with TestClient(server.app) as client:
            _save_two_findings_health_state(tmp_path)
            res = client.get("/api/health")
    finally:
        server.app.dependency_overrides.clear()
    findings = {f["finding_id"]: f for f in res.json()["health_state"]["findings"]}
    assert findings["f-alan"]["repo"] == "o/r"
    assert findings["f-sprocket"]["engine_key"] == "sprocket-co/sprocket"


def test_finding_project_id_filter_cache_avoids_repeat_workspace_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan.workspace as workspace_mod
    from alan.health import HealthFinding

    ws = create_workspace("Health Cache WS", "d", root=tmp_path, project_id="proj-1")
    read_calls: list[str] = []
    real_read = read_workspace

    def _counting_read(task_id: str, *, root: Path | None = None) -> _Workspace:
        read_calls.append(task_id)
        return real_read(task_id, root=root)

    monkeypatch.setattr(workspace_mod, "read", _counting_read)
    finding_a = HealthFinding(
        finding_id="a", kind="build_task", severity="high", description="d",
        workspace_id=ws.task_id, pr_number=None, blocker_flags=[], action="dispatch_remediation",
    )
    finding_b = HealthFinding(
        finding_id="b", kind="build_task", severity="high", description="d",
        workspace_id=ws.task_id, pr_number=None, blocker_flags=[], action="dispatch_remediation",
    )
    result = infra_mod._filter_findings(  # noqa: SLF001
        [finding_a, finding_b], engine=None, project_id="proj-1", workspaces_root=tmp_path
    )
    assert [f.finding_id for f in result] == ["a", "b"]
    assert read_calls == [ws.task_id]


def test_finding_project_id_returns_none_when_workspace_missing(tmp_path: Path) -> None:
    from alan.health import HealthFinding

    finding = HealthFinding(
        finding_id="a", kind="build_task", severity="high", description="d",
        workspace_id="nonexistent-ws", pr_number=None, blocker_flags=[],
        action="dispatch_remediation",
    )
    result = infra_mod._filter_findings(  # noqa: SLF001
        [finding], engine=None, project_id="proj-1", workspaces_root=tmp_path
    )
    assert result == []


def test_build_pool_factory_returns_none_when_no_pool_root() -> None:
    assert infra_mod._build_pool_factory(None) is None


def test_build_pool_factory_builds_pool_sized_by_default_constant(tmp_path: Path) -> None:
    from alan.engine_config import EngineConfig, RepoIdentity

    factory = infra_mod._build_pool_factory(tmp_path)
    assert factory is not None
    cfg = EngineConfig(repo=RepoIdentity(owner="sprocket-co", repo="sprocket"))
    pool = factory(cfg)
    assert pool._size == infra_mod._DEFAULT_SECONDARY_POOL_SIZE  # noqa: SLF001


def test_build_pool_factory_honors_cfg_pool_size(tmp_path: Path) -> None:
    from alan.engine_config import EngineConfig, RepoIdentity

    factory = infra_mod._build_pool_factory(tmp_path)
    assert factory is not None
    cfg = EngineConfig(repo=RepoIdentity(owner="sprocket-co", repo="sprocket"), pool_size=7)
    pool = factory(cfg)
    assert pool._size == 7  # noqa: SLF001


def test_build_pool_factory_threads_cache_env_from_config(tmp_path: Path) -> None:
    from alan.engine_config import EngineConfig, RepoIdentity

    factory = infra_mod._build_pool_factory(tmp_path)
    assert factory is not None
    cfg = EngineConfig(
        repo=RepoIdentity(owner="sprocket-co", repo="sprocket"),
        cache_env={"NUGET_PACKAGES": "nuget"},
    )
    pool = factory(cfg)
    assert pool._cache_env == {"NUGET_PACKAGES": "nuget"}  # noqa: SLF001


def test_build_pool_factory_wires_on_warmup_measured_to_registry_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import engine_registry_store
    from alan.engine_config import EngineConfig, RepoIdentity

    captured: list[tuple[str, float]] = []
    monkeypatch.setattr(
        engine_registry_store,
        "write_warmup_seconds",
        lambda slug, seconds: captured.append((slug, seconds)),
    )
    factory = infra_mod._build_pool_factory(tmp_path)
    assert factory is not None
    cfg = EngineConfig(repo=RepoIdentity(owner="sprocket-co", repo="sprocket"))
    pool = factory(cfg)
    pool._on_warmup_measured(3.5)  # type: ignore[misc]  # noqa: SLF001
    assert captured == [("sprocket-co/sprocket", 3.5)]


def test_secondary_pool_size_defaults_when_unset() -> None:
    from alan.engine_config import EngineConfig, RepoIdentity

    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    assert infra_mod._secondary_pool_size(cfg) == infra_mod._DEFAULT_SECONDARY_POOL_SIZE


def test_secondary_pool_size_honors_explicit_value() -> None:
    from alan.engine_config import EngineConfig, RepoIdentity

    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"), pool_size=9)
    assert infra_mod._secondary_pool_size(cfg) == 9


def test_cached_quality_metrics_hit_then_ttl_expiry(
    db_path: Path, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    q = Queue(db_path=db_path)
    calls: list[int] = []
    original = infra_mod._build_quality_metrics

    def spy(queue: Any, root: Any) -> infra_mod.QualityMetrics:
        calls.append(1)
        return original(queue, root)

    monkeypatch.setattr(infra_mod, "_QUALITY_METRICS_CACHE", {})
    monkeypatch.setattr(infra_mod, "_build_quality_metrics", spy)
    first = infra_mod._cached_quality_metrics(q, ws_root)
    second = infra_mod._cached_quality_metrics(q, ws_root)
    assert len(calls) == 1
    assert first == second
    monkeypatch.setattr(infra_mod, "_QUALITY_METRICS_TTL_S", 0.0)
    infra_mod._cached_quality_metrics(q, ws_root)
    assert len(calls) == 2
    q.close()


def test_run_build_chat_reconciler_reaps_and_logs_when_nonempty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from alan import build_chat as build_chat_mod

    root = tmp_path / "bc"
    s = build_chat_mod.create_session(root=root)
    build_chat_mod.set_repo_provision(
        s.session_id, build_chat_mod.RepoProvisionStatus.READY, root=root
    )
    target = build_chat_mod.session_dir(s.session_id, root=root) / "session.json"
    raw = json.loads(target.read_text())
    raw["created_at"] = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    target.write_text(json.dumps(raw), encoding="utf-8")

    with caplog.at_level("INFO", logger="alan.server"):
        infra_mod._run_build_chat_reconciler(root)  # noqa: SLF001

    assert any("reaped" in r.message for r in caplog.records)


def test_run_build_chat_reconciler_no_reap_log_when_nothing_stale(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "bc"
    root.mkdir(parents=True)
    with caplog.at_level("INFO", logger="alan.server"):
        infra_mod._run_build_chat_reconciler(root)  # noqa: SLF001
    assert not any("reaped" in r.message for r in caplog.records)


def test_audit_dir_defaults_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_LOGS_ROOT", raising=False)
    assert logs_root() == Path.home() / ".alan" / "logs"


def test_audit_dir_honors_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    logs_dir = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_dir))
    assert logs_root() == logs_dir


def test_check_credential_expiry_writes_audit_row_for_due_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_SECRETS_STORE_ROOT", str(tmp_path / "secrets"))
    logs_dir = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_dir))

    now = datetime.now(UTC)
    expires_at = (now.date() + timedelta(days=3)).isoformat()
    secrets_store.write_secret("engine-a", "transport", "v", expires_at=expires_at)

    infra_mod._check_credential_expiry(now)  # noqa: SLF001

    audit_path = logs_dir / secrets_store.CREDENTIAL_EXPIRY_AUDIT_FILE
    rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["name"] == "engine-a:transport"
    assert rows[0]["expires_at"] == expires_at


def test_check_credential_expiry_writes_no_rows_when_nothing_due(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_SECRETS_STORE_ROOT", str(tmp_path / "secrets"))
    logs_dir = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_dir))

    infra_mod._check_credential_expiry(datetime.now(UTC))  # noqa: SLF001

    assert not (logs_dir / secrets_store.CREDENTIAL_EXPIRY_AUDIT_FILE).exists()


def test_rescue_metrics_default_empty(db_path: Path) -> None:
    q = Queue(db_path=db_path)
    try:
        result = infra_mod._rescue_metrics(q, [])  # noqa: SLF001
    finally:
        q.close()
    assert result == {
        "rescue_success_rate": None,
        "rescue_success_rate_sample": 0,
        "rescue_success_rate_low_data": True,
        "rescue_load_rate": None,
        "rescue_load_rate_sample": 0,
        "rescue_load_rate_low_data": True,
    }


def test_rescue_metrics_reflects_a_cleared_engaged_workspace(
    db_path: Path, ws_root: Path
) -> None:
    ws = create_workspace("rescued", "d", root=ws_root)
    transition(ws, Status.COMPLETED)
    ws = read_workspace(ws.task_id, root=ws_root)
    q = Queue(db_path=db_path)
    try:
        unblock_ledger_mod.ensure_schema(q)
        unblock_ledger_mod.record(
            q,
            unblock_ledger_mod.Entry(
                workspace_key=ws.task_id,
                scope_ref=f"ws:{ws.task_id}",
                shape="crash_park",
                fingerprint="fp-1",
                kind=unblock_ledger_mod.KIND_ATTEMPT,
                action="dispatch:verification",
                outcome=unblock_ledger_mod.OUTCOME_ACTED,
            ),
        )
        result = infra_mod._rescue_metrics(q, [ws])  # noqa: SLF001
    finally:
        q.close()
    assert result["rescue_success_rate"] == pytest.approx(1.0)
    assert result["rescue_success_rate_sample"] == 1
    assert result["rescue_success_rate_low_data"] is True
    assert result["rescue_load_rate"] == pytest.approx(1.0)
    assert result["rescue_load_rate_sample"] == 1
