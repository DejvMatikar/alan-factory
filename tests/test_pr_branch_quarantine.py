from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from alan import process_runner
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _pr_branch, _pr_flake_ledger, _pr_ready
from tests import _pr_branch_support as support
from tests._pr_branch_support import _WIDGET_FORGE, _audit_entries, _fake_gh
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, pr_read

_BASE_RUNS: list[dict[str, object]] = [
    {"name": "ci/test", "conclusion": "FAILURE", "status": "COMPLETED"},
    {"name": "ci/lint", "conclusion": "SUCCESS", "status": "COMPLETED"},
    {"name": "ci/slow", "conclusion": "", "status": "IN_PROGRESS"},
    {"name": "ci/queued", "conclusion": "QUEUED", "status": ""},
    {"name": "ci/empty", "conclusion": "", "status": ""},
]

@pytest.fixture(autouse=True)
def _default_transport_token(monkeypatch: pytest.MonkeyPatch) -> None:
    support.set_verifier_transport_token(monkeypatch)


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


def test_known_flake_registry_and_allowlist_locked() -> None:
    assert _pr_ready._KNOWN_FLAKE_REGISTRY == {}  # noqa: SLF001
    assert _pr_flake_ledger.SHARED_FLAKY_ALLOWLIST == frozenset(
        {
            "Frontend E2E (Playwright, blocking)",
            "Backend tests + coverage",
            "Frontend tests + coverage (100/100/100/100)",
        }
    )


def test_fetch_pr_base_sha_ok(forge: ForgeScript) -> None:
    sha = "a" * 40
    forge.queue("read_pr", pr_read({"baseRefOid": sha + "\n"}))
    assert _pr_ready._fetch_pr_base_sha(42, forge_target=DEFAULT_FORGE_TARGET) == sha


def test_fetch_pr_base_sha_empty_returns_none(forge: ForgeScript) -> None:
    forge.queue("read_pr", pr_read({"baseRefOid": "\n"}))
    assert _pr_ready._fetch_pr_base_sha(42, forge_target=DEFAULT_FORGE_TARGET) is None


@pytest.mark.parametrize("fields", [{}, {"baseRefOid": None}, {"baseRefOid": 7}])
def test_fetch_pr_base_sha_missing_or_non_str_field_returns_none(
    forge: ForgeScript, fields: dict[str, object],
) -> None:
    forge.queue("read_pr", pr_read(fields))
    assert _pr_ready._fetch_pr_base_sha(42, forge_target=DEFAULT_FORGE_TARGET) is None


def test_fetch_pr_base_sha_nonzero_rc_returns_none(forge: ForgeScript) -> None:
    forge.queue("read_pr", pr_read({"baseRefOid": "a" * 40}, returncode=1))
    assert _pr_ready._fetch_pr_base_sha(42, forge_target=DEFAULT_FORGE_TARGET) is None


def test_fetch_pr_base_sha_exception_returns_none(forge: ForgeScript) -> None:
    forge.queue("read_pr", FileNotFoundError("gh"))
    assert _pr_ready._fetch_pr_base_sha(42, forge_target=DEFAULT_FORGE_TARGET) is None


def test_fetch_pr_base_sha_timeout_returns_none(forge: ForgeScript) -> None:
    forge.queue("read_pr", subprocess.TimeoutExpired(cmd="gh", timeout=60.0))
    assert _pr_ready._fetch_pr_base_sha(42, forge_target=DEFAULT_FORGE_TARGET) is None


def test_fetch_diff_files_returns_the_file_set_the_port_reports(forge: ForgeScript) -> None:
    forge.queue("changed_files", frozenset({"alan/foo.py", "alan/bar.py"}))
    assert _pr_branch._fetch_diff_files(
        42, forge_target=DEFAULT_FORGE_TARGET,
    ) == frozenset({"alan/foo.py", "alan/bar.py"})


def test_fetch_diff_files_returns_none_when_the_port_reports_none(forge: ForgeScript) -> None:
    forge.queue("changed_files", None)
    assert _pr_branch._fetch_diff_files(42, forge_target=DEFAULT_FORGE_TARGET) is None


def test_fetch_diff_files_asks_the_port_about_the_forge_target(forge: ForgeScript) -> None:
    forge.queue("changed_files", frozenset())
    _pr_branch._fetch_diff_files(42, forge_target=_WIDGET_FORGE)
    assert forge.calls("changed_files") == [((42,), {"forge_target": _WIDGET_FORGE})]


def test_fetch_diff_files_public_name_is_the_same_function() -> None:
    assert _pr_branch.fetch_diff_files is _pr_branch._fetch_diff_files


def test_is_pending_state_true_by_status() -> None:
    assert _pr_ready._is_pending_state("", "IN_PROGRESS") is True


def test_is_pending_state_true_by_state() -> None:
    assert _pr_ready._is_pending_state("QUEUED", "") is True


def test_is_pending_state_false() -> None:
    assert _pr_ready._is_pending_state("SUCCESS", "COMPLETED") is False


def test_check_run_result_for_name_fail() -> None:
    assert _pr_ready._check_run_result_for_name(_BASE_RUNS, "ci/test") == "fail"


def test_check_run_result_for_name_pass() -> None:
    assert _pr_ready._check_run_result_for_name(_BASE_RUNS, "ci/lint") == "pass"


def test_check_run_result_for_name_pending_by_status() -> None:
    assert _pr_ready._check_run_result_for_name(_BASE_RUNS, "ci/slow") == "pending"


def test_check_run_result_for_name_pending_by_state() -> None:
    assert _pr_ready._check_run_result_for_name(_BASE_RUNS, "ci/queued") == "pending"


def test_check_run_result_for_name_unknown_no_state() -> None:
    assert _pr_ready._check_run_result_for_name(_BASE_RUNS, "ci/empty") == "unknown"


def test_check_run_result_for_name_not_found_returns_unknown() -> None:
    assert _pr_ready._check_run_result_for_name(_BASE_RUNS, "missing") == "unknown"


def test_is_known_flake_quarantined_not_in_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert not _pr_ready._KNOWN_FLAKE_REGISTRY
    assert _pr_ready._is_known_flake_quarantined("ci/test", frozenset()) is False


def test_is_known_flake_quarantined_empty_owned_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(_pr_ready._KNOWN_FLAKE_REGISTRY, "ci/test", frozenset())
    assert _pr_ready._is_known_flake_quarantined("ci/test", frozenset({"alan/foo.py"})) is False


def test_is_known_flake_quarantined_diff_none_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(_pr_ready._KNOWN_FLAKE_REGISTRY, "ci/test", frozenset({"alan/foo.py"}))
    assert _pr_ready._is_known_flake_quarantined("ci/test", None) is False


def test_is_known_flake_quarantined_disjoint_returns_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(_pr_ready._KNOWN_FLAKE_REGISTRY, "ci/test", frozenset({"alan/foo.py"}))
    assert _pr_ready._is_known_flake_quarantined("ci/test", frozenset({"alan/bar.py"})) is True


def test_is_known_flake_quarantined_overlapping_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(_pr_ready._KNOWN_FLAKE_REGISTRY, "ci/test", frozenset({"alan/foo.py"}))
    assert _pr_ready._is_known_flake_quarantined("ci/test", frozenset({"alan/foo.py"})) is False


def test_is_known_flake_quarantined_glob_match_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(_pr_ready._KNOWN_FLAKE_REGISTRY, "ci/test", frozenset({"alan/foo/*"}))
    diff = frozenset({"alan/foo/bar.py"})
    assert _pr_ready._is_known_flake_quarantined("ci/test", diff) is False


def test_is_known_flake_quarantined_glob_no_match_returns_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(_pr_ready._KNOWN_FLAKE_REGISTRY, "ci/test", frozenset({"alan/foo/*"}))
    diff = frozenset({"alan/bar/baz.py"})
    assert _pr_ready._is_known_flake_quarantined("ci/test", diff) is True


def test_classify_one_check_pre_existing() -> None:
    runs: list[dict[str, object]] = [
        {"name": "ci/test", "conclusion": "FAILURE", "status": "COMPLETED"}
    ]
    ev = _pr_ready._classify_one_check("ci/test", runs, None)
    assert ev.reason == "pre_existing"
    assert ev.base_result == "fail"


def test_classify_one_check_introduced_by_diff() -> None:
    runs: list[dict[str, object]] = [
        {"name": "ci/test", "conclusion": "SUCCESS", "status": "COMPLETED"}
    ]
    ev = _pr_ready._classify_one_check("ci/test", runs, None)
    assert ev.reason == "blocking"
    assert ev.base_result == "pass"


def test_classify_one_check_uncertain_no_base(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not _pr_ready._KNOWN_FLAKE_REGISTRY
    ev = _pr_ready._classify_one_check("ci/test", None, None)
    assert ev.reason == "blocking"
    assert ev.base_result == "unknown"


def test_classify_one_check_known_flake_when_base_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(_pr_ready._KNOWN_FLAKE_REGISTRY, "ci/test", frozenset({"alan/x.py"}))
    runs: list[dict[str, object]] = [
        {"name": "ci/test", "conclusion": "SUCCESS", "status": "COMPLETED"}
    ]
    ev = _pr_ready._classify_one_check("ci/test", runs, frozenset({"alan/y.py"}))
    assert ev.reason == "known_flake"


def test_classify_one_check_known_flake_when_base_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(_pr_ready._KNOWN_FLAKE_REGISTRY, "ci/test", frozenset({"alan/x.py"}))
    ev = _pr_ready._classify_one_check("ci/test", None, frozenset({"alan/y.py"}))
    assert ev.reason == "known_flake"
    assert ev.base_result == "unknown"


def test_gather_quarantine_inputs_base_sha_none_skips_check_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(_pr_ready, "_fetch_pr_base_sha", lambda _p, **_kw: None)
    iter_called: list[bool] = [False]

    def fake_iter(
        _t: object, _s: str, _p: int, **_kw: object
    ) -> list[dict[str, object]]:
        iter_called[0] = True
        return []

    monkeypatch.setattr(_pr_branch, "_iter_check_runs", fake_iter)
    quarantined, evidence = _pr_ready.classify_flake_quarantine(
        42, ("ci/test",), (),
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert iter_called[0] is False
    assert quarantined == ()
    assert evidence[0].reason == "blocking"


def test_gather_quarantine_inputs_audits_check_runs_query_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(_pr_ready, "_fetch_pr_base_sha", lambda _p, **_kw: "a" * 40)
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh("", returncode=1))
    quarantined, evidence = _pr_ready.classify_flake_quarantine(
        42, ("ci/test",), (),
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert quarantined == ()
    assert evidence[0].reason == "blocking"
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "quarantine_check_runs_query_failed"
        and "returncode=1" in str(e.get("reason"))
        for e in entries
    )
    assert evidence[0].base_result == "unknown"


def test_classify_flake_quarantine_pending_checks_blocks_all(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    quarantined, evidence = _pr_ready.classify_flake_quarantine(
        42, ("ci/test",), ("ci/slow",),
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert quarantined == ()
    assert evidence == []


def test_classify_flake_quarantine_no_failing_returns_empty() -> None:
    quarantined, evidence = _pr_ready.classify_flake_quarantine(
        42, (), (),
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert quarantined == ()
    assert evidence == []


def test_classify_flake_quarantine_pre_existing_quarantined(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(_pr_ready, "_fetch_pr_base_sha", lambda _p, **_kw: "a" * 40)
    monkeypatch.setattr(
        _pr_branch, "_iter_check_runs",
        lambda _t, _s, _p, **_kw: [
            {"name": "ci/test", "conclusion": "FAILURE", "status": "COMPLETED"}
        ],
    )
    quarantined, evidence = _pr_ready.classify_flake_quarantine(
        42, ("ci/test",), (),
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert "ci/test" in quarantined
    assert evidence[0].reason == "pre_existing"
    assert evidence[0].base_result == "fail"
    assert evidence[0].head_result == "fail"


def test_classify_flake_quarantine_introduced_blocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(_pr_ready, "_fetch_pr_base_sha", lambda _p, **_kw: "a" * 40)
    monkeypatch.setattr(
        _pr_branch, "_iter_check_runs",
        lambda _t, _s, _p, **_kw: [
            {"name": "ci/test", "conclusion": "SUCCESS", "status": "COMPLETED"}
        ],
    )
    quarantined, evidence = _pr_ready.classify_flake_quarantine(
        42, ("ci/test",), (),
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert quarantined == ()
    assert evidence[0].reason == "blocking"


def test_classify_flake_quarantine_uncertain_repo_none_blocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(_pr_ready, "_fetch_pr_base_sha", lambda _p, **_kw: None)
    quarantined, evidence = _pr_ready.classify_flake_quarantine(
        42, ("ci/test",), (),
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert quarantined == ()
    assert evidence[0].reason == "blocking"


def test_classify_flake_quarantine_base_check_runs_none_blocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(_pr_ready, "_fetch_pr_base_sha", lambda _p, **_kw: "a" * 40)
    monkeypatch.setattr(_pr_branch, "_iter_check_runs", lambda _t, _s, _p, **_kw: None)
    quarantined, evidence = _pr_ready.classify_flake_quarantine(
        42, ("ci/test",), (),
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert quarantined == ()
    assert evidence[0].reason == "blocking"
    assert evidence[0].base_result == "unknown"


def test_classify_flake_quarantine_known_flake_disjoint_quarantined(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(_pr_ready, "_fetch_pr_base_sha", lambda _p, **_kw: None)
    monkeypatch.setitem(
        _pr_ready._KNOWN_FLAKE_REGISTRY, "flaky/test", frozenset({"alan/foo.py"})
    )
    forge.queue("changed_files", frozenset({"alan/bar.py"}))
    quarantined, evidence = _pr_ready.classify_flake_quarantine(
        42, ("flaky/test",), (),
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert "flaky/test" in quarantined
    assert evidence[0].reason == "known_flake"
    assert [args for args, _kwargs in forge.calls("changed_files")] == [(42,)]


def test_classify_flake_quarantine_known_flake_touched_blocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(_pr_ready, "_fetch_pr_base_sha", lambda _p, **_kw: None)
    monkeypatch.setitem(
        _pr_ready._KNOWN_FLAKE_REGISTRY, "flaky/test", frozenset({"alan/foo.py"})
    )
    forge.queue("changed_files", frozenset({"alan/foo.py"}))
    quarantined, evidence = _pr_ready.classify_flake_quarantine(
        42, ("flaky/test",), (),
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert quarantined == ()
    assert evidence[0].reason == "blocking"


def test_classify_flake_quarantine_known_flake_diff_unfetchable_blocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(_pr_ready, "_fetch_pr_base_sha", lambda _p, **_kw: None)
    monkeypatch.setitem(_pr_ready._KNOWN_FLAKE_REGISTRY, "flaky/test", frozenset({"alan/foo.py"}))
    forge.queue("changed_files", None)
    quarantined, evidence = _pr_ready.classify_flake_quarantine(
        42, ("flaky/test",), (),
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert quarantined == ()
    assert evidence[0].reason == "blocking"


def test_classify_flake_quarantine_mixed_some_quarantined_some_not(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(_pr_ready, "_fetch_pr_base_sha", lambda _p, **_kw: "a" * 40)
    monkeypatch.setattr(
        _pr_branch, "_iter_check_runs",
        lambda _t, _s, _p, **_kw: [
            {"name": "ci/flaky", "conclusion": "FAILURE", "status": "COMPLETED"},
            {"name": "ci/real", "conclusion": "SUCCESS", "status": "COMPLETED"},
        ],
    )
    quarantined, evidence = _pr_ready.classify_flake_quarantine(
        42, ("ci/flaky", "ci/real"), (),
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert "ci/flaky" in quarantined
    assert "ci/real" not in quarantined
    assert len(evidence) == 2
    reasons = {ev.check_name: ev.reason for ev in evidence}
    assert reasons["ci/flaky"] == "pre_existing"
    assert reasons["ci/real"] == "blocking"


def test_classify_flake_quarantine_audit_entries_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(_pr_ready, "_fetch_pr_base_sha", lambda _p, **_kw: "a" * 40)
    monkeypatch.setattr(
        _pr_branch, "_iter_check_runs",
        lambda _t, _s, _p, **_kw: [
            {"name": "ci/test", "conclusion": "FAILURE", "status": "COMPLETED"}
        ],
    )
    _pr_ready._audit_quarantine(
        "task-x", 42,
        [_pr_ready.QuarantineEvidence("ci/test", "fail", "fail", "pre_existing")],
    )
    entries = _audit_entries(tmp_path)
    qe = next(e for e in entries if e.get("action") == "flake_quarantine_decision")
    assert qe["check_name"] == "ci/test"
    assert qe["base_result"] == "fail"
    assert qe["reason"] == "pre_existing"
    assert qe["quarantined"] is True
