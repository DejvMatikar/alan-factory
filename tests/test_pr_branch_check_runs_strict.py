from __future__ import annotations

import json
from typing import Any

import pytest

from alan import process_runner
from alan.teams import _pr_branch
from tests._pr_branch_support import _WIDGET_FORGE, _fake_gh


def _capturing_on_fail() -> tuple[list[tuple[int, str]], Any]:
    failures: list[tuple[int, str]] = []

    def _on_fail(pr: int, reason: str) -> None:
        failures.append((pr, reason))

    return failures, _on_fail


def _iter(*, strict: bool, on_fail: Any) -> list[dict[str, Any]] | None:
    return _pr_branch._iter_check_runs(  # noqa: SLF001
        _WIDGET_FORGE, "sha-1", 7, on_fail=on_fail, strict=strict
    )


@pytest.mark.parametrize(
    "payload",
    [{"total_count": 0}, {"check_runs": "nope"}, {"check_runs": None}],
    ids=["field_missing", "field_is_string", "field_is_null"],
)
def test_strict_probe_reports_a_payload_without_a_check_run_list_as_unavailable(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]
) -> None:
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh(json.dumps(payload)))
    failures, on_fail = _capturing_on_fail()
    assert _iter(strict=True, on_fail=on_fail) is None
    assert failures == [(7, "check_runs_field_missing")]


def test_strict_probe_reads_an_empty_check_run_list_as_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh(json.dumps({"check_runs": []})))
    failures, on_fail = _capturing_on_fail()
    assert _iter(strict=True, on_fail=on_fail) == []
    assert failures == []


def test_strict_probe_returns_the_check_runs_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs = [{"name": "ci", "status": "queued", "conclusion": None}]
    monkeypatch.setattr(
        process_runner, "run_subprocess", _fake_gh(json.dumps({"check_runs": runs}))
    )
    failures, on_fail = _capturing_on_fail()
    assert _iter(strict=True, on_fail=on_fail) == runs
    assert failures == []


def test_lenient_default_still_reads_a_missing_check_run_list_as_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh(json.dumps({"total_count": 0})))
    failures, on_fail = _capturing_on_fail()
    assert _iter(strict=False, on_fail=on_fail) == []
    assert failures == []


def test_strict_probe_still_reports_a_non_object_payload_as_malformed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh("[1, 2, 3]"))
    failures, on_fail = _capturing_on_fail()
    assert _iter(strict=True, on_fail=on_fail) is None
    assert failures == [(7, "check_runs_json_malformed")]
