from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan import secrets_store
from alan.engine_config import ForgeTarget, RepoIdentity
from alan.teams import _pr_feedback, _pr_forge, _pr_threads, _verdict
from alan.teams import _verifier_thread_resolution as vtr
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, api_result, login_read
from tests.thread_forge_fake import (
    resolve_mutation_stdout,
    review_threads_stdout,
    thread_node,
)

_FORGE = ForgeTarget(
    repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
    credential_env_name="ACME_WIDGET_GH_TOKEN",  # noqa: S106  # nosec B106
)

_REPORTED = _verdict.CriticalCountReport(reviewer=0, judge_reported=True)


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


def set_audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(root))
    return root


def audit_rows(root: Path) -> list[dict[str, object]]:
    path = root / "verification_audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def stub_verifier_env(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str] | None,
) -> list[ForgeTarget]:
    calls: list[ForgeTarget] = []

    def fake(forge_target: ForgeTarget) -> dict[str, str]:
        calls.append(forge_target)
        return dict(env) if env is not None else {}

    monkeypatch.setattr(secrets_store, "verifier_gh_env", fake)
    return calls


def make_thread(
    reply_count: int | None, author_login: str = "alan-verifier",
) -> _pr_threads.ReviewThread:
    return _pr_threads.ReviewThread(
        thread_id="t1", author_login=author_login, body="finding",
        path="alan/foo.py", line=1, is_outdated=False, reply_count=reply_count,
    )


def test_resolve_after_approval_delegates_floor_reason_to_verdict_module(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _active_fake_forge: FakeForge,
) -> None:
    root = set_audit_root(tmp_path, monkeypatch)
    calls: list[tuple[int, _verdict.CriticalCountReport | None]] = []

    def fake_floor_reason(
        critical: int, counts: _verdict.CriticalCountReport | None,
    ) -> str | None:
        calls.append((critical, counts))
        return "reviewer_nonzero"

    monkeypatch.setattr(_verdict, "count_floor_reason", fake_floor_reason)
    vtr.resolve_after_approval(
        "APPROVE", 0, 42, task_id="t1", inv_id="inv1", forge_target=_FORGE, counts=_REPORTED,
    )
    assert calls == [(0, _REPORTED)]
    assert _active_fake_forge.calls == []
    rows = audit_rows(root)
    assert rows[0]["reason"] == "reviewer_nonzero"


def test_confirm_identity_ok_when_login_matches(forge: ForgeScript) -> None:
    forge.queue("viewer_login", login_read("alan-verifier"))
    result = vtr._confirm_identity(_FORGE)  # noqa: SLF001
    assert result.reason is None
    assert result.observed_login == "alan-verifier"


def test_confirm_identity_mismatch(forge: ForgeScript) -> None:
    forge.queue("viewer_login", login_read("someone-else"))
    result = vtr._confirm_identity(_FORGE)  # noqa: SLF001
    assert result.reason == "login_mismatch"
    assert result.observed_login == "someone-else"


def test_confirm_identity_empty_login_reports_empty_login(forge: ForgeScript) -> None:
    forge.queue("viewer_login", login_read(None, outcome="failed"))
    result = vtr._confirm_identity(_FORGE)  # noqa: SLF001
    assert result.reason == "identity_check_failed"
    assert result.error == "empty login"


def test_confirm_identity_failed_call_truncates_stderr(forge: ForgeScript) -> None:
    forge.queue(
        "viewer_login", login_read(None, outcome="failed", returncode=1, stderr="d" * 600),
    )
    result = vtr._confirm_identity(_FORGE)  # noqa: SLF001
    assert result.reason == "identity_check_failed"
    assert result.error == "d" * vtr.ERROR_TEXT_CAP


def test_confirm_identity_raised_carries_exception_text(forge: ForgeScript) -> None:
    forge.queue(
        "viewer_login",
        login_read(None, outcome="raised", returncode=-1, stderr="Command 'gh' timed out"),
    )
    result = vtr._confirm_identity(_FORGE)  # noqa: SLF001
    assert result.reason == "identity_check_failed"
    assert result.error == "Command 'gh' timed out"


def test_confirm_identity_no_credential_race_holds_with_empty_error(
    forge: ForgeScript,
) -> None:
    forge.queue("viewer_login", login_read(None, outcome="no_credential", returncode=-1))
    result = vtr._confirm_identity(_FORGE)  # noqa: SLF001
    assert result.reason == "identity_check_failed"
    assert result.error == ""


def test_confirm_identity_reads_viewer_login_as_verifier_for_the_target(
    forge: ForgeScript,
) -> None:
    forge.queue("viewer_login", login_read("alan-verifier"))
    vtr._confirm_identity(_FORGE)  # noqa: SLF001
    assert forge.calls("viewer_login") == [
        ((), {"forge_target": _FORGE, "as_verifier": True, "timeout": _pr_forge.GH_TIMEOUT_S}),
    ]


@pytest.mark.parametrize(
    ("reply_count", "expected"),
    [
        (None, "reply_count_unreadable"),
        (1, "has_replies"),
        (0, None),
    ],
)
def test_refusal_reason(reply_count: int | None, expected: str | None) -> None:
    assert vtr._refusal_reason(make_thread(reply_count)) == expected  # noqa: SLF001


def test_resolve_after_approval_noop_when_not_approve(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _active_fake_forge: FakeForge,
) -> None:
    root = set_audit_root(tmp_path, monkeypatch)
    vtr.resolve_after_approval(
        "REQUEST_CHANGES", 0, 42, task_id="t1", inv_id="inv1",
        forge_target=_FORGE, counts=_REPORTED,
    )
    assert _active_fake_forge.calls == []
    assert audit_rows(root) == []


def test_fallback_supplied_count_holds_resolution_with_no_forge_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _active_fake_forge: FakeForge,
) -> None:
    root = set_audit_root(tmp_path, monkeypatch)
    vtr.resolve_after_approval(
        "APPROVE", 0, 42, task_id="t1", inv_id="inv1", forge_target=_FORGE,
        counts=_verdict.CriticalCountReport(reviewer=None, judge_reported=False),
    )
    assert _active_fake_forge.calls == []
    rows = audit_rows(root)
    assert len(rows) == 1
    assert rows[0]["action"] == vtr.HELD_ACTION
    assert rows[0]["reason"] == "count_not_reported"
    assert rows[0]["pr_number"] == 42


def test_reviewer_nonzero_count_holds_resolution_despite_judge_approval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _active_fake_forge: FakeForge,
) -> None:
    root = set_audit_root(tmp_path, monkeypatch)
    vtr.resolve_after_approval(
        "APPROVE", 0, 42, task_id="t1", inv_id="inv1", forge_target=_FORGE,
        counts=_verdict.CriticalCountReport(reviewer=2, judge_reported=True),
    )
    assert _active_fake_forge.calls == []
    rows = audit_rows(root)
    assert rows[0]["reason"] == "reviewer_nonzero"


def test_resolve_after_approval_holds_when_no_reviewer_credential(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _active_fake_forge: FakeForge,
) -> None:
    stub_verifier_env(monkeypatch, None)
    root = set_audit_root(tmp_path, monkeypatch)
    vtr.resolve_after_approval(
        "APPROVE", 0, 42, task_id="t1", inv_id="inv1", forge_target=_FORGE, counts=_REPORTED,
    )
    assert _active_fake_forge.calls == []
    rows = audit_rows(root)
    assert rows[0]["reason"] == "no_reviewer_credential"


def test_resolve_after_approval_holds_on_identity_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    forge.queue("viewer_login", login_read("someone-else"))
    stub_verifier_env(monkeypatch, {"GH_TOKEN": "verifier-tok"})
    root = set_audit_root(tmp_path, monkeypatch)
    vtr.resolve_after_approval(
        "APPROVE", 0, 42, task_id="t1", inv_id="inv1", forge_target=_FORGE, counts=_REPORTED,
    )
    rows = audit_rows(root)
    assert rows[0]["reason"] == "login_mismatch"
    assert rows[0]["observed_login"] == "someone-else"
    assert forge.calls("unresolved_threads") == []


def test_resolve_after_approval_holds_when_identity_read_is_not_ok(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    forge.queue("viewer_login", login_read(None, outcome="failed"))
    stub_verifier_env(monkeypatch, {"GH_TOKEN": "verifier-tok"})
    root = set_audit_root(tmp_path, monkeypatch)
    vtr.resolve_after_approval(
        "APPROVE", 0, 42, task_id="t1", inv_id="inv1", forge_target=_FORGE, counts=_REPORTED,
    )
    rows = audit_rows(root)
    assert rows[0]["reason"] == "identity_check_failed"
    assert rows[0]["error"] == "empty login"
    assert forge.calls("unresolved_threads") == []


def test_resolve_after_approval_holds_when_thread_read_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    forge.queue("viewer_login", login_read("alan-verifier"))
    forge.queue("unresolved_threads", api_result(returncode=1, stderr="boom"))
    stub_verifier_env(monkeypatch, {"GH_TOKEN": "verifier-tok"})
    root = set_audit_root(tmp_path, monkeypatch)
    vtr.resolve_after_approval(
        "APPROVE", 0, 42, task_id="t1", inv_id="inv1", forge_target=_FORGE, counts=_REPORTED,
    )
    rows = audit_rows(root)
    assert rows[0]["reason"] == "thread_read_failed"


def test_approve_with_reported_zero_count_resolves_only_reply_free_verifier_threads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    nodes = [
        thread_node("t-verifier-clean", author_login="alan-verifier", total_count=1),
        thread_node("t-verifier-replied", author_login="alan-verifier", total_count=2),
        thread_node("t-human", author_login="DejvMatikar", total_count=1),
        thread_node("t-copilot", author_login="copilot-pull-request-reviewer", total_count=1),
    ]
    forge.queue("viewer_login", login_read("alan-verifier"))
    forge.queue("unresolved_threads", api_result(review_threads_stdout(nodes)))
    forge.queue("resolve_thread", api_result(resolve_mutation_stdout(True)))
    stub_verifier_env(monkeypatch, {"GH_TOKEN": "verifier-tok"})
    root = set_audit_root(tmp_path, monkeypatch)
    vtr.resolve_after_approval(
        "APPROVE", 0, 42, task_id="t1", inv_id="inv1", forge_target=_FORGE, counts=_REPORTED,
    )
    rows = audit_rows(root)
    resolved = [r for r in rows if r["action"] == vtr.RESOLVED_ACTION]
    refused = [r for r in rows if r["action"] == vtr.REFUSED_ACTION]
    assert [r["thread_id"] for r in resolved] == ["t-verifier-clean"]
    assert [r["thread_id"] for r in refused] == ["t-verifier-replied"]
    assert refused[0]["reason"] == "has_replies"


def test_resolve_after_approval_stops_after_first_forge_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    nodes = [
        thread_node("t1", author_login="alan-verifier", total_count=1),
        thread_node("t2", author_login="alan-verifier", total_count=1),
    ]
    forge.queue("viewer_login", login_read("alan-verifier"))
    forge.queue("unresolved_threads", api_result(review_threads_stdout(nodes)))
    forge.queue("resolve_thread", api_result(returncode=1, stderr="forge down"))
    stub_verifier_env(monkeypatch, {"GH_TOKEN": "verifier-tok"})
    root = set_audit_root(tmp_path, monkeypatch)
    vtr.resolve_after_approval(
        "APPROVE", 0, 42, task_id="t1", inv_id="inv1", forge_target=_FORGE, counts=_REPORTED,
    )
    rows = audit_rows(root)
    refused = [r for r in rows if r["action"] == vtr.REFUSED_ACTION]
    assert [r["thread_id"] for r in refused] == ["t1", "t2"]
    assert refused[0]["reason"] == "forge_refused"
    assert refused[0]["error"] == "forge down"
    assert refused[1]["reason"] == "stopped_after_failure"


def test_secondary_engine_read_identity_and_resolve_use_its_own_credential_and_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    nodes = [thread_node("t1", author_login="alan-verifier", total_count=1)]
    forge.queue("viewer_login", login_read("alan-verifier"))
    forge.queue("unresolved_threads", api_result(review_threads_stdout(nodes)))
    forge.queue("resolve_thread", api_result(resolve_mutation_stdout(True)))
    calls = stub_verifier_env(monkeypatch, {"GH_TOKEN": "secondary-tok"})
    root = set_audit_root(tmp_path, monkeypatch)
    vtr.resolve_after_approval(
        "APPROVE", 0, 99, task_id="t1", inv_id="inv1", forge_target=_FORGE, counts=_REPORTED,
    )
    assert calls == [_FORGE]
    assert forge.calls("unresolved_threads") == [
        ((99,), {"forge_target": _FORGE, "as_verifier": True}),
    ]
    assert forge.calls("resolve_thread") == [
        (("t1",), {"forge_target": _FORGE, "as_verifier": True}),
    ]
    assert [kwargs["forge_target"] for _, kwargs in forge.calls("viewer_login")] == [_FORGE]
    rows = audit_rows(root)
    assert rows[-1]["repo"] == "acme-corp/widget-app"


def test_secondary_engine_without_reviewer_credential_never_falls_back_to_default_login(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _active_fake_forge: FakeForge,
) -> None:
    calls = stub_verifier_env(monkeypatch, None)
    root = set_audit_root(tmp_path, monkeypatch)
    vtr.resolve_after_approval(
        "APPROVE", 0, 99, task_id="t1", inv_id="inv1", forge_target=_FORGE, counts=_REPORTED,
    )
    assert calls == [_FORGE]
    assert _active_fake_forge.calls == []
    rows = audit_rows(root)
    assert rows[0]["reason"] == "no_reviewer_credential"
    assert rows[0]["repo"] == _FORGE.repo.slug


def test_parse_thread_info_still_counts_verifier_authored_threads_as_human() -> None:
    stdout = json.dumps({
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "totalCount": 1,
                        "nodes": [
                            {
                                "id": "th1",
                                "isResolved": False,
                                "comments": {
                                    "nodes": [
                                        {
                                            "body": "finding",
                                            "databaseId": 1,
                                            "author": {
                                                "login": "alan-verifier",
                                                "__typename": "User",
                                            },
                                        }
                                    ]
                                },
                            }
                        ],
                    }
                }
            }
        }
    })
    info = _pr_feedback.parse_thread_info(stdout)
    assert info.human_unresolved == 1
    assert info.copilot_threads == ()
