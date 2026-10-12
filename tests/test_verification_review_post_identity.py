from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import cast, get_args

import pytest

from alan import secrets_store
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _pr_branch, _pr_post, _verdict
from alan.teams import _verification_review_post as review_post_mod
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, api_result

_TARGET = DEFAULT_FORGE_TARGET
_INLINE_JUDGE_OUTPUT = (
    "## Verdict\n\nVERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 2\n\n"
    "## INLINE_FINDINGS_YAML\n```yaml\n"
    "- path: alan/foo.py\n  line: 10\n  body: first finding\n"
    "- path: alan/bar.py\n  line: 20\n  body: second finding\n"
    "```\n"
)
_FILE_NOT_FOUND = FileNotFoundError("gh")
_TIMEOUT = subprocess.TimeoutExpired(cmd="gh", timeout=1.0)
_CREDENTIALS = [
    pytest.param({"GH_TOKEN": "tok"}, True, id="credential-resolved"),
    pytest.param({}, False, id="empty-credential"),
    pytest.param(None, False, id="no-credential"),
]
_TRANSPORT_FAILURES = pytest.mark.parametrize(
    "error", [_FILE_NOT_FOUND, _TIMEOUT], ids=["missing-gh", "timeout"],
)
_WITH_CREDENTIAL = pytest.mark.parametrize(("credential", "requested"), _CREDENTIALS)


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.delenv("ALAN_VERIFIER_GH_TOKEN", raising=False)
    monkeypatch.setenv("ALAN_REVIEW_POST_RETRY_MAX", "0")
    monkeypatch.setattr(_pr_post, "dismiss_stale_verifier_reviews", lambda _p, _t, **_kw: False)


@pytest.fixture(autouse=True)
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []
    monkeypatch.setattr(time, "sleep", recorded.append)
    return recorded


def _resolve(monkeypatch: pytest.MonkeyPatch, credential: dict[str, str] | None) -> None:
    monkeypatch.setattr(secrets_store, "verifier_gh_env", lambda _t: credential)


def _post(
    verdict: _verdict.Verdict = "APPROVE", body: str = "body",
) -> tuple[_pr_post.ReviewPostOutcome, review_post_mod.ReviewPostFailureClass | None, int]:
    return review_post_mod._post_review_with_retries(  # noqa: SLF001
        42, verdict, body, "task-x", verdict, "inv_1", forge_target=_TARGET,
    )


def _entries(tmp_path: Path) -> list[dict[str, object]]:
    path = tmp_path / "logs" / "verification_audit.jsonl"
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def _actions(tmp_path: Path, *names: str) -> list[dict[str, object]]:
    return [e for e in _entries(tmp_path) if e["action"] in names]


def _requested(forge: ForgeScript) -> list[object]:
    return [kwargs["as_verifier"] for _, kwargs in forge.calls("post_review")]


def _overrides(entries: list[dict[str, object]]) -> list[object]:
    return [e["gh_user_override"] for e in entries if "gh_user_override" in e]


def _comment_counts(forge: ForgeScript) -> list[int]:
    payloads = [cast("dict[str, list[object]]", args[1]) for args, _ in forge.calls("post_review")]
    return [len(payload["comments"]) for payload in payloads]


@_WITH_CREDENTIAL
def test_a_successful_plain_post_requests_and_audits_the_resolved_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
    credential: dict[str, str] | None, requested: bool,
) -> None:
    _resolve(monkeypatch, credential)
    forge.queue("post_review", api_result("{}"))
    outcome, failure_class, attempts = _post()
    assert outcome.ok is True
    assert (failure_class, attempts) == (None, 1)
    assert _requested(forge) == [requested]
    posted = _actions(tmp_path, "review_ok")
    assert [e["gh_user_override"] for e in posted] == [requested]
    assert posted[0]["inv_id"] == "inv_1"


@_WITH_CREDENTIAL
def test_a_nonzero_plain_post_requests_and_audits_the_resolved_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
    caplog: pytest.LogCaptureFixture,
    credential: dict[str, str] | None, requested: bool,
) -> None:
    _resolve(monkeypatch, credential)
    caplog.set_level("WARNING")
    forge.queue("post_review", api_result(returncode=1, stderr="boom"))
    outcome, _, _ = _post()
    assert outcome.ok is False
    assert _requested(forge) == [requested]
    failed = _actions(tmp_path, "review_failed")
    assert [e["gh_user_override"] for e in failed] == [requested]
    assert failed[0]["returncode"] == 1
    assert failed[0]["stderr"] == "boom"
    assert "inv_id" not in failed[0]
    assert "error" not in failed[0]
    assert any("review post failed (rc=1)" in r.message for r in caplog.records)


@_WITH_CREDENTIAL
@_TRANSPORT_FAILURES
def test_a_plain_post_transport_failure_is_reported_not_raised_and_audits_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
    credential: dict[str, str] | None, requested: bool, error: BaseException,
) -> None:
    _resolve(monkeypatch, credential)
    forge.queue("post_review", error)
    outcome, _, _ = _post()
    assert outcome.ok is False
    assert outcome.returncode is None
    assert outcome.error == str(error)
    failed = _actions(tmp_path, "review_failed")
    assert [e["gh_user_override"] for e in failed] == [requested]
    assert failed[0]["error"] == str(error)
    assert "returncode" not in failed[0]


@_WITH_CREDENTIAL
def test_a_successful_inline_post_requests_and_audits_the_resolved_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
    credential: dict[str, str] | None, requested: bool,
) -> None:
    _resolve(monkeypatch, credential)
    forge.queue("post_review", api_result("{}"))
    outcome, _, _ = _post("REQUEST_CHANGES", _INLINE_JUDGE_OUTPUT)
    assert outcome.ok is True
    assert _requested(forge) == [requested]
    assert _comment_counts(forge) == [2]
    assert [e["gh_user_override"] for e in _actions(tmp_path, "review_ok_inline")] == [requested]


@_WITH_CREDENTIAL
def test_a_failed_inline_post_falls_back_to_a_plain_one_under_the_same_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
    credential: dict[str, str] | None, requested: bool,
) -> None:
    _resolve(monkeypatch, credential)
    forge.queue(
        "post_review",
        api_result(returncode=1, stderr="inline failed"), api_result(returncode=1, stderr="boom"),
    )
    outcome, _, _ = _post("REQUEST_CHANGES", _INLINE_JUDGE_OUTPUT)
    assert outcome.ok is False
    assert _requested(forge) == [requested, requested]
    assert _comment_counts(forge) == [2, 0]
    entries = _actions(tmp_path, "review_failed_inline", "review_failed")
    assert [e["action"] for e in entries] == ["review_failed_inline", "review_failed"]
    assert _overrides(entries) == [requested, requested]


@_WITH_CREDENTIAL
@_TRANSPORT_FAILURES
def test_an_inline_transport_failure_is_audited_with_the_error_and_the_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
    credential: dict[str, str] | None, requested: bool, error: BaseException,
) -> None:
    _resolve(monkeypatch, credential)
    forge.queue("post_review", error, api_result("{}"))
    outcome, _, _ = _post("REQUEST_CHANGES", _INLINE_JUDGE_OUTPUT)
    assert outcome.ok is True
    failed = _actions(tmp_path, "review_failed_inline")
    assert [e["error"] for e in failed] == [str(error)]
    assert "returncode" not in failed[0]
    entries = _actions(tmp_path, "review_failed_inline", "review_ok")
    assert _overrides(entries) == [requested, requested]


def test_every_retry_attempt_reuses_the_identity_resolved_once_for_the_post(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_REVIEW_POST_RETRY_MAX", "2")
    resolutions = iter([{"GH_TOKEN": "tok"}, {}, {}, {}, {}])
    monkeypatch.setattr(secrets_store, "verifier_gh_env", lambda _t: next(resolutions))
    forge.queue("post_review", *[api_result(returncode=1, stderr="boom")] * 3)
    outcome, failure_class, attempts = _post()
    assert outcome.ok is False
    assert (failure_class, attempts) == ("transient", 3)
    assert _requested(forge) == [True, True, True]
    assert _overrides(_actions(tmp_path, "review_failed")) == [True, True, True]


def test_retries_keep_the_default_backoff_between_attempts(
    monkeypatch: pytest.MonkeyPatch, forge: ForgeScript, sleeps: list[float],
) -> None:
    monkeypatch.setenv("ALAN_REVIEW_POST_RETRY_MAX", "2")
    monkeypatch.delenv("ALAN_REVIEW_POST_RETRY_INTERVAL_S", raising=False)
    _resolve(monkeypatch, {"GH_TOKEN": "tok"})
    forge.queue("post_review", *[api_result(returncode=1, stderr="boom")] * 3)
    _, _, attempts = _post()
    assert attempts == 3
    assert sleeps == [5.0, 5.0]


@_WITH_CREDENTIAL
@pytest.mark.parametrize(
    ("result", "returncode", "stderr"),
    [
        (api_result(returncode=7, stderr="HTTP 401: denied"), 7, "HTTP 401: denied"),
        (_FILE_NOT_FOUND, None, None),
    ],
    ids=["nonzero", "transport"],
)
def test_the_classifier_reads_the_same_identity_fact_and_the_forge_result_unchanged(
    monkeypatch: pytest.MonkeyPatch, forge: ForgeScript,
    credential: dict[str, str] | None, requested: bool,
    result: object, returncode: int | None, stderr: str | None,
) -> None:
    _resolve(monkeypatch, credential)
    seen: list[dict[str, object]] = []

    def classify(**kwargs: object) -> str:
        seen.append(kwargs)
        return "expired_token"

    monkeypatch.setattr(_pr_branch, "classify_credential_failure", classify)
    forge.queue("post_review", result)
    _, failure_class, attempts = _post()
    assert seen == [{"token_present": requested, "returncode": returncode, "stderr": stderr}]
    assert _requested(forge) == [requested]
    assert (failure_class, attempts) == ("expired_token", 1)


@pytest.mark.parametrize("event", get_args(_pr_post.ReviewEvent))
def test_the_plain_post_sends_the_event_with_an_empty_comments_array(
    forge: ForgeScript, event: _pr_post.ReviewEvent,
) -> None:
    forge.queue("post_review", api_result("{}"))
    review_post_mod._apply_legacy_pr_review(  # noqa: SLF001
        42, "APPROVE", "the body", "task-x", event, "inv_1",
        forge_target=_TARGET, as_verifier=False,
    )
    assert forge.calls("post_review") == [
        (
            (42, {"body": "the body", "event": event, "comments": []}),
            {"forge_target": _TARGET, "as_verifier": False},
        ),
    ]


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [("x" * 500, "x" * 500), ("x" * 501, "x" * 500), (None, ""), ("", "")],
    ids=["at-cap", "over-cap", "none", "empty"],
)
def test_failure_detail_records_the_returncode_and_a_capped_stderr(
    stderr: str | None, expected: str,
) -> None:
    entry: dict[str, object] = {}
    review_post_mod._add_failure_detail(  # noqa: SLF001
        entry, _pr_post.ReviewPostOutcome(ok=False, returncode=1, stderr=stderr, error=None),
    )
    assert entry == {"returncode": 1, "stderr": expected}


def test_failure_detail_records_only_the_error_for_a_transport_failure() -> None:
    entry: dict[str, object] = {}
    review_post_mod._add_failure_detail(  # noqa: SLF001
        entry, _pr_post.ReviewPostOutcome(ok=False, returncode=None, stderr=None, error="gh"),
    )
    assert entry == {"error": "gh"}
