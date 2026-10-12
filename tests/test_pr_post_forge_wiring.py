from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from alan import secrets_store
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _pr_branch, _pr_post, _pr_threads
from tests._pr_branch_support import (
    _WIDGET_FORGE,
    _audit_entries,
    _thread_node,
    _threads_payload,
)
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, api_result

_TARGET = DEFAULT_FORGE_TARGET
_FILE_NOT_FOUND = FileNotFoundError("gh")
_TIMEOUT = subprocess.TimeoutExpired(cmd="gh", timeout=1.0)


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


@pytest.fixture(autouse=True)
def _logs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))


def _impl_audit(tmp_path: Path) -> list[dict[str, object]]:
    path = tmp_path / "logs" / "impl_audit.jsonl"
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def _review_line(review_id: int, *, state: str = "CHANGES_REQUESTED", **extra: object) -> str:
    return json.dumps({
        "state": state, "user": {"login": "alan-verifier"}, "id": review_id, **extra,
    })


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (api_result("{}"), True),
        (api_result(returncode=1, stderr="boom"), False),
        (_FILE_NOT_FOUND, False),
        (_TIMEOUT, False),
    ],
    ids=["ok", "nonzero", "missing-gh", "timeout"],
)
def test_resolve_review_thread_reports_the_forge_outcome(
    forge: ForgeScript, result: object, expected: bool,
) -> None:
    forge.queue("resolve_thread", result)
    assert _pr_threads.resolve_review_thread("PRTI_abc") is expected


def test_resolve_review_thread_resolves_as_the_default_identity_on_the_default_repository(
    forge: ForgeScript,
) -> None:
    forge.queue("resolve_thread", api_result("{}"))
    _pr_threads.resolve_review_thread("PRTI_abc")
    assert forge.calls("resolve_thread") == [
        (("PRTI_abc",), {"forge_target": _TARGET, "as_verifier": False}),
    ]


def test_post_pr_review_with_inline_comments_posts_the_built_payload(
    forge: ForgeScript,
) -> None:
    forge.queue("post_review", api_result("{}"))
    findings = [
        _pr_post.InlineFinding(path="alan/x.py", line=10, body="bad thing"),
        _pr_post.InlineFinding(path="alan/y.py", line=20, body="also bad"),
    ]
    outcome = _pr_post.post_pr_review_with_inline_comments(
        42, "verdict body", "REQUEST_CHANGES", findings,
        forge_target=_TARGET,
    )
    assert outcome == _pr_post.ReviewPostOutcome(ok=True, returncode=0, stderr="", error=None)
    assert forge.calls("post_review") == [
        (
            (42, {
                "body": "verdict body",
                "event": "REQUEST_CHANGES",
                "comments": [
                    {"path": "alan/x.py", "line": 10, "side": "RIGHT", "body": "bad thing"},
                    {"path": "alan/y.py", "line": 20, "side": "RIGHT", "body": "also bad"},
                ],
            }),
            {"forge_target": _TARGET, "as_verifier": False},
        ),
    ]


def test_post_pr_review_with_inline_comments_reports_a_nonzero_post(
    forge: ForgeScript,
) -> None:
    forge.queue("post_review", api_result(returncode=1, stderr="boom"))
    outcome = _pr_post.post_pr_review_with_inline_comments(
        42, "body", "REQUEST_CHANGES", [], forge_target=_TARGET
    )
    assert outcome == _pr_post.ReviewPostOutcome(
        ok=False, returncode=1, stderr="boom", error=None,
    )


@pytest.mark.parametrize("error", [_FILE_NOT_FOUND, _TIMEOUT], ids=["missing-gh", "timeout"])
def test_post_pr_review_with_inline_comments_reports_a_transport_failure(
    forge: ForgeScript, caplog: pytest.LogCaptureFixture, error: BaseException,
) -> None:
    forge.queue("post_review", error)
    caplog.set_level("WARNING")
    outcome = _pr_post.post_pr_review_with_inline_comments(
        42, "body", "REQUEST_CHANGES", [], forge_target=_TARGET
    )
    assert outcome.ok is False
    assert "gh" in (outcome.error or "")
    assert outcome.returncode is None
    assert outcome.stderr is None
    assert any("gh api review post raised" in r.message for r in caplog.records)


def test_post_pr_review_with_inline_comments_returns_untruncated_stderr(
    forge: ForgeScript,
) -> None:
    forge.queue("post_review", api_result(returncode=1, stderr="x" * 600))
    outcome = _pr_post.post_pr_review_with_inline_comments(
        42, "body", "REQUEST_CHANGES", [], forge_target=_TARGET
    )
    assert len(outcome.stderr or "") == 600


def test_post_pr_review_with_inline_comments_passes_the_verifier_flag_and_target(
    forge: ForgeScript,
) -> None:
    forge.queue("post_review", api_result("{}"))
    outcome = _pr_post.post_pr_review_with_inline_comments(
        42, "body", "APPROVE", [], as_verifier=True, forge_target=_WIDGET_FORGE,
    )
    assert outcome.ok is True
    assert forge.calls("post_review")[0][1] == {
        "forge_target": _WIDGET_FORGE, "as_verifier": True,
    }


def test_post_pr_review_with_inline_comments_refuses_a_positional_verifier_flag() -> None:
    with pytest.raises(
        TypeError, match=r"positional arguments? but \d+ positional arguments?.*were given",
    ):
        _pr_post.post_pr_review_with_inline_comments(  # type: ignore[call-arg]
            42, "body", "APPROVE", [], True,  # pyright: ignore[reportCallIssue]
            forge_target=_TARGET,
        )


def test_list_unresolved_threads_coerces_unresolved_threads_as_the_default_identity(
    forge: ForgeScript,
) -> None:
    payload = _threads_payload(
        _thread_node(thread_id="T1", path="alan/x.py", line=42, body="bug"),
        _thread_node(thread_id="T2", resolved=True, body="done"),
        _thread_node(thread_id="T3", outdated=True, path="alan/y.py", line=7, body="stale"),
    )
    forge.queue("unresolved_threads", api_result(payload))
    threads = _pr_threads.list_unresolved_threads(42, forge_target=_TARGET)
    assert threads == (
        _pr_threads.ReviewThread(
            thread_id="T1", author_login="copilot-pull-request-reviewer[bot]",
            body="bug", path="alan/x.py", line=42, is_outdated=False,
        ),
        _pr_threads.ReviewThread(
            thread_id="T3", author_login="copilot-pull-request-reviewer[bot]",
            body="stale", path="alan/y.py", line=7, is_outdated=True,
        ),
    )
    assert forge.calls("unresolved_threads") == [
        ((42,), {"forge_target": _TARGET, "as_verifier": False}),
    ]


@pytest.mark.parametrize("error", [_FILE_NOT_FOUND, _TIMEOUT], ids=["missing-gh", "timeout"])
def test_list_unresolved_threads_audits_a_transport_failure(
    forge: ForgeScript, tmp_path: Path, error: BaseException,
) -> None:
    forge.queue("unresolved_threads", error)
    assert _pr_threads.list_unresolved_threads(42, forge_target=_TARGET) == ()
    assert any(
        e.get("action") == "list_unresolved_threads_failed"
        and "gh subprocess error" in str(e.get("reason"))
        for e in _audit_entries(tmp_path)
    )


def test_list_unresolved_threads_audits_a_nonzero_read(
    forge: ForgeScript, tmp_path: Path,
) -> None:
    forge.queue("unresolved_threads", api_result(returncode=1, stderr="boom"))
    assert _pr_threads.list_unresolved_threads(42, forge_target=_TARGET) == ()
    assert any(
        e.get("action") == "list_unresolved_threads_failed"
        and "returncode=1" in str(e.get("reason"))
        for e in _audit_entries(tmp_path)
    )


def test_list_unresolved_threads_returns_nothing_for_an_empty_payload(
    forge: ForgeScript,
) -> None:
    forge.queue("unresolved_threads", api_result(""))
    assert _pr_threads.list_unresolved_threads(42, forge_target=_TARGET) == ()


def test_list_unresolved_threads_handles_malformed_nodes(forge: ForgeScript) -> None:
    payload = json.dumps({"data": {"repository": {"pullRequest": {
        "reviewThreads": {"nodes": [
            "not-a-dict",
            {"id": "T-noauthor", "isResolved": False, "path": "p", "line": 1,
             "comments": {"nodes": [{"body": "x"}]}},
            {"id": "T-nocomments", "isResolved": False, "path": "p", "line": 1,
             "comments": None},
            {"id": "T-emptynodes", "isResolved": False, "path": "p", "line": 1,
             "comments": {"nodes": []}},
            {"id": "T-nondict-comment", "isResolved": False,
             "path": "p", "line": 1, "comments": {"nodes": ["str"]}},
        ]}
    }}}})
    forge.queue("unresolved_threads", api_result(payload))
    threads = _pr_threads.list_unresolved_threads(42, forge_target=_TARGET)
    assert [t.thread_id for t in threads] == [
        "T-noauthor", "T-nocomments", "T-emptynodes", "T-nondict-comment",
    ]
    assert (threads[0].body, threads[0].author_login) == ("x", "")
    assert (threads[1].body, threads[1].author_login) == ("", "")


def test_fetch_review_stream_returns_the_forge_stream(forge: ForgeScript) -> None:
    forge.queue("reviews", "line1\nline2\n")
    assert _pr_post.fetch_review_stream(42, forge_target=_WIDGET_FORGE) == "line1\nline2\n"
    assert forge.calls("reviews") == [((42,), {"forge_target": _WIDGET_FORGE})]


def test_fetch_review_stream_returns_none_when_the_forge_has_no_stream(
    forge: ForgeScript,
) -> None:
    forge.queue("reviews", None)
    assert _pr_post.fetch_review_stream(42, forge_target=_TARGET) is None


def test_fetch_changes_requested_ids_is_empty_when_the_stream_is_unavailable(
    forge: ForgeScript,
) -> None:
    forge.queue("reviews", None)
    assert _pr_post._fetch_changes_requested_ids(1, _TARGET, "sha-head") == ()  # noqa: SLF001


def test_fetch_changes_requested_ids_reads_the_verifier_reviews_on_the_target(
    forge: ForgeScript, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_VERIFIER_LOGIN", "alan-verifier")
    forge.queue("reviews", _review_line(55))
    result = _pr_post._fetch_changes_requested_ids(42, _WIDGET_FORGE, "sha-head")  # noqa: SLF001
    assert result == (55,)
    assert forge.calls("reviews") == [((42,), {"forge_target": _WIDGET_FORGE})]


@pytest.mark.parametrize(
    ("result", "expected"),
    [(True, True), (False, False)],
)
def test_dismiss_one_review_reports_the_forge_outcome(
    forge: ForgeScript, result: bool, expected: bool,
) -> None:
    forge.queue("dismiss_review", result)
    assert _pr_post._dismiss_one_review(  # noqa: SLF001
        1, 7, forge_target=_TARGET,
    ) is expected


def test_dismiss_one_review_uses_the_default_message(forge: ForgeScript) -> None:
    forge.queue("dismiss_review", True)
    _pr_post._dismiss_one_review(1, 7, forge_target=_TARGET)  # noqa: SLF001
    assert forge.calls("dismiss_review") == [
        ((1, 7), {"forge_target": _TARGET, "message": "superseded by re-review"}),
    ]


def test_dismiss_one_review_uses_a_custom_message(forge: ForgeScript) -> None:
    forge.queue("dismiss_review", True)
    _pr_post._dismiss_one_review(  # noqa: SLF001
        1, 7, forge_target=_WIDGET_FORGE, message="custom message here",
    )
    assert forge.calls("dismiss_review") == [
        ((1, 7), {"forge_target": _WIDGET_FORGE, "message": "custom message here"}),
    ]


def test_dismiss_stale_verifier_reviews_audits_a_missing_head_sha(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(_pr_branch, "_fetch_pr_head_sha", lambda _n, **_kw: None)
    assert _pr_post.dismiss_stale_verifier_reviews(42, "task-x", forge_target=_TARGET) is False
    assert any(
        e["action"] == "dismiss_stale_reviews_failed"
        and e.get("reason") == "head_sha_unavailable"
        for e in _impl_audit(tmp_path)
    )


def test_dismiss_stale_verifier_reviews_skips_when_no_review_is_stale(
    forge: ForgeScript, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(_pr_branch, "_fetch_pr_head_sha", lambda _n, **_kw: "sha-abc")
    forge.queue("reviews", None)
    assert _pr_post.dismiss_stale_verifier_reviews(42, "task-x", forge_target=_TARGET) is False
    assert any(e["action"] == "dismiss_stale_reviews_skipped" for e in _impl_audit(tmp_path))


def test_dismiss_stale_verifier_reviews_dismisses_every_stale_review_as_the_verifier(
    forge: ForgeScript, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(_pr_branch, "_fetch_pr_head_sha", lambda _n, **_kw: "sha-abc")
    monkeypatch.setenv("ALAN_VERIFIER_LOGIN", "alan-verifier")
    forge.queue("reviews", "\n".join([_review_line(101), _review_line(102)]))
    forge.queue("dismiss_review", True, True)
    assert _pr_post.dismiss_stale_verifier_reviews(
        42, "task-x", forge_target=_WIDGET_FORGE,
    ) is True
    assert [args for args, _ in forge.calls("dismiss_review")] == [(42, 101), (42, 102)]
    assert all(
        kwargs["forge_target"] == _WIDGET_FORGE for _, kwargs in forge.calls("dismiss_review")
    )
    dismissed = [e for e in _impl_audit(tmp_path) if e["action"] == "review_dismissed"]
    assert len(dismissed) == 2


def test_dismiss_stale_verifier_reviews_keeps_dismissing_after_a_failure(
    forge: ForgeScript, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(_pr_branch, "_fetch_pr_head_sha", lambda _n, **_kw: "sha-abc")
    monkeypatch.setenv("ALAN_VERIFIER_LOGIN", "alan-verifier")
    forge.queue("reviews", "\n".join([_review_line(101), _review_line(102)]))
    forge.queue("dismiss_review", True, False)
    assert _pr_post.dismiss_stale_verifier_reviews(42, "task-x", forge_target=_TARGET) is False
    assert len(forge.calls("dismiss_review")) == 2
    assert any(e["action"] == "review_dismiss_failed" for e in _impl_audit(tmp_path))


@pytest.mark.parametrize(
    ("credential", "expected"),
    [({"GH_TOKEN": "tok"}, True), ({}, False), (None, False)],
    ids=["credential", "empty-mapping", "none"],
)
def test_verifier_identity_is_available_only_when_a_credential_resolves(
    monkeypatch: pytest.MonkeyPatch, credential: dict[str, str] | None, expected: bool,
) -> None:
    monkeypatch.setattr(secrets_store, "verifier_gh_env", lambda _t: credential)
    available = _pr_post.verifier_identity_available(_WIDGET_FORGE)
    assert available is expected
