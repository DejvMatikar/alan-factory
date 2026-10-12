from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(_REPO_ROOT / "scripts"))
import alan_forge_contract_check  # type: ignore[import-not-found]  # noqa: E402
import alan_forge_contract_staleness  # type: ignore[import-not-found]  # noqa: E402

from alan import process_runner  # noqa: E402

_UNDEFINED_FIELD_ERROR = {
    "type": "undefinedField",
    "path": ["query", "repository", "pullRequest", "reviewThreads", "nodes", "author"],
    "locations": [{"line": 1, "column": 123}],
    "message": "Field 'author' doesn't exist on type 'PullRequestReviewThread'",
}


def _author_node(login: str, typename: str) -> dict[str, object]:
    return {"login": login, "__typename": typename}


def _feedback_payload_with_thread_authors(authors: list[tuple[str, str]]) -> str:
    nodes = [
        {
            "id": f"T{i}",
            "isResolved": False,
            "comments": {"nodes": [{"body": "x", "databaseId": i, "author": _author_node(*a)}]},
        }
        for i, a in enumerate(authors)
    ]
    return json.dumps({"data": {"repository": {"pullRequest": {
        "reviewThreads": {"totalCount": len(nodes), "nodes": nodes},
        "comments": {"totalCount": 0, "nodes": []},
        "reviews": {"totalCount": 0, "nodes": []},
    }}}})


def test_run_check_verified_when_fetch_shape_and_identity_all_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _feedback_payload_with_thread_authors([("copilot-pull-request-reviewer", "Bot")])

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout=body, stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    verdict = alan_forge_contract_check.run_check(1017)
    assert verdict.outcome is alan_forge_contract_check.Outcome.VERIFIED


def test_run_check_drift_on_undefined_field_query_error(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps({"data": None, "errors": [_UNDEFINED_FIELD_ERROR]})

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout=body, stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    verdict = alan_forge_contract_check.run_check(1017)
    assert verdict.outcome is alan_forge_contract_check.Outcome.DRIFT
    assert "reviewThreads.nodes.author" in verdict.reason


def test_run_check_drift_on_undefined_field_query_error_via_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = json.dumps({"data": None, "errors": [_UNDEFINED_FIELD_ERROR]})

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(
            cmd, 1, stdout=body, stderr="graphql: validation failed"
        )

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    verdict = alan_forge_contract_check.run_check(1017)
    assert verdict.outcome is alan_forge_contract_check.Outcome.DRIFT
    assert "reviewThreads.nodes.author" in verdict.reason


def test_run_check_drift_when_pinned_identity_resolves_to_wrong_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _feedback_payload_with_thread_authors([("copilot-pull-request-reviewer", "User")])

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout=body, stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    verdict = alan_forge_contract_check.run_check(1017)
    assert verdict.outcome is alan_forge_contract_check.Outcome.DRIFT
    assert "copilot-pull-request-reviewer" in verdict.reason


def test_run_check_unverifiable_on_missing_gh_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del cmd, kwargs
        raise FileNotFoundError("gh not found")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    verdict = alan_forge_contract_check.run_check(1017)
    assert verdict.outcome is alan_forge_contract_check.Outcome.UNVERIFIABLE
    assert "raised" in verdict.reason


def test_run_check_unverifiable_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        raise subprocess.TimeoutExpired(cmd, 60.0)

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    verdict = alan_forge_contract_check.run_check(1017)
    assert verdict.outcome is alan_forge_contract_check.Outcome.UNVERIFIABLE
    assert "timed out" in verdict.reason


def test_run_check_unverifiable_on_nonzero_exit_with_unparseable_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="rate limited")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    verdict = alan_forge_contract_check.run_check(1017)
    assert verdict.outcome is alan_forge_contract_check.Outcome.UNVERIFIABLE
    assert "rate limited" in verdict.reason


def test_main_writes_status_to_github_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(
        alan_forge_contract_check,
        "run_check",
        lambda pr_number: alan_forge_contract_check.Verdict(
            alan_forge_contract_check.Outcome.DRIFT, "boom"
        ),
    )
    alan_forge_contract_check.main()
    assert output.read_text(encoding="utf-8") == "status=drift\n"


def test_main_returns_0_on_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr(
        alan_forge_contract_check,
        "run_check",
        lambda pr_number: alan_forge_contract_check.Verdict(
            alan_forge_contract_check.Outcome.VERIFIED, ""
        ),
    )
    assert alan_forge_contract_check.main() == 0


def test_main_returns_1_on_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr(
        alan_forge_contract_check,
        "run_check",
        lambda pr_number: alan_forge_contract_check.Verdict(
            alan_forge_contract_check.Outcome.DRIFT, "boom"
        ),
    )
    assert alan_forge_contract_check.main() == 1


def test_main_returns_2_on_unverifiable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr(
        alan_forge_contract_check,
        "run_check",
        lambda pr_number: alan_forge_contract_check.Verdict(
            alan_forge_contract_check.Outcome.UNVERIFIABLE, "no gh"
        ),
    )
    assert alan_forge_contract_check.main() == 2


def test_main_classifies_unexpected_exception_as_unverifiable_and_writes_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _boom(pr_number: int) -> alan_forge_contract_check.Verdict:
        raise PermissionError("quarantined gh binary")

    monkeypatch.setattr(alan_forge_contract_check, "run_check", _boom)
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert alan_forge_contract_check.main() == 2
    assert output.read_text(encoding="utf-8") == "status=unverifiable\n"


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_fetch_last_success_returns_started_at_from_first_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = json.dumps({"workflow_runs": [{"run_started_at": "2026-08-17T06:23:00Z"}]}).encode()
    monkeypatch.setattr(
        alan_forge_contract_staleness.urllib.request,
        "urlopen",
        lambda *a, **kw: _FakeResponse(body),
    )
    result = alan_forge_contract_staleness.fetch_last_success(
        "https://api.github.com", "DejvMatikar/alan", "tok"
    )
    assert result == "2026-08-17T06:23:00Z"


def test_fetch_last_success_returns_none_on_empty_run_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = json.dumps({"workflow_runs": []}).encode()
    monkeypatch.setattr(
        alan_forge_contract_staleness.urllib.request,
        "urlopen",
        lambda *a, **kw: _FakeResponse(body),
    )
    result = alan_forge_contract_staleness.fetch_last_success(
        "https://api.github.com", "DejvMatikar/alan", "tok"
    )
    assert result is None


def test_fetch_last_success_returns_none_when_run_started_at_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = json.dumps({"workflow_runs": [{"id": 1}]}).encode()
    monkeypatch.setattr(
        alan_forge_contract_staleness.urllib.request,
        "urlopen",
        lambda *a, **kw: _FakeResponse(body),
    )
    result = alan_forge_contract_staleness.fetch_last_success(
        "https://api.github.com", "DejvMatikar/alan", "tok"
    )
    assert result is None


def test_fetch_last_success_raises_on_non_dict_response(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps([1, 2, 3]).encode()
    monkeypatch.setattr(
        alan_forge_contract_staleness.urllib.request,
        "urlopen",
        lambda *a, **kw: _FakeResponse(body),
    )
    with pytest.raises(ValueError, match="not a JSON object"):
        alan_forge_contract_staleness.fetch_last_success(
            "https://api.github.com", "DejvMatikar/alan", "tok"
        )


def test_fetch_last_success_raises_on_missing_workflow_runs_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = json.dumps({"unexpected": True}).encode()
    monkeypatch.setattr(
        alan_forge_contract_staleness.urllib.request,
        "urlopen",
        lambda *a, **kw: _FakeResponse(body),
    )
    with pytest.raises(ValueError, match="workflow_runs"):
        alan_forge_contract_staleness.fetch_last_success(
            "https://api.github.com", "DejvMatikar/alan", "tok"
        )


def test_fetch_last_success_raises_on_non_dict_run_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps({"workflow_runs": ["not-a-dict"]}).encode()
    monkeypatch.setattr(
        alan_forge_contract_staleness.urllib.request,
        "urlopen",
        lambda *a, **kw: _FakeResponse(body),
    )
    with pytest.raises(ValueError, match="not a JSON object"):
        alan_forge_contract_staleness.fetch_last_success(
            "https://api.github.com", "DejvMatikar/alan", "tok"
        )


_NOW = datetime(2026, 8, 18, 6, 30, tzinfo=UTC)


def test_is_stale_true_on_empty_run_history() -> None:
    assert alan_forge_contract_staleness.is_stale(None, now=_NOW) is True


def test_is_stale_false_within_threshold() -> None:
    assert alan_forge_contract_staleness.is_stale("2026-08-17T06:23:00+00:00", now=_NOW) is False


def test_is_stale_true_beyond_threshold() -> None:
    assert alan_forge_contract_staleness.is_stale("2026-08-14T05:00:00+00:00", now=_NOW) is True


def test_is_stale_false_exactly_at_threshold() -> None:
    threshold_s = 3 * 24 * 3600 + 3600
    started = datetime.fromtimestamp(_NOW.timestamp() - threshold_s, tz=UTC)
    assert alan_forge_contract_staleness.is_stale(started.isoformat(), now=_NOW) is False


def test_main_stale_true_writes_never_verified_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(alan_forge_contract_staleness, "fetch_last_success", lambda *a: None)
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    body_path = tmp_path / "body.md"
    rc = alan_forge_contract_staleness.main(["--body-path", str(body_path)], now=_NOW)
    assert rc == 0
    assert "never completed a verification" in body_path.read_text(encoding="utf-8")
    output_text = output.read_text(encoding="utf-8")
    assert "stale=true" in output_text
    assert "last_success=never" in output_text


def test_main_stale_false_names_last_success_date_in_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        alan_forge_contract_staleness,
        "fetch_last_success",
        lambda *a: "2026-08-17T06:23:00+00:00",
    )
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    body_path = tmp_path / "body.md"
    rc = alan_forge_contract_staleness.main(["--body-path", str(body_path)], now=_NOW)
    assert rc == 0
    assert "2026-08-17T06:23:00+00:00" in body_path.read_text(encoding="utf-8")
    assert "stale=false" in output.read_text(encoding="utf-8")


def test_main_api_failure_yields_stale_false_and_exit_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _boom(*args: object) -> str | None:
        raise URLError("network exploded")

    monkeypatch.setattr(alan_forge_contract_staleness, "fetch_last_success", _boom)
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    body_path = tmp_path / "body.md"
    rc = alan_forge_contract_staleness.main(["--body-path", str(body_path)], now=_NOW)
    assert rc == 0
    assert output.read_text(encoding="utf-8") == "stale=false\n"
    assert not body_path.exists()
