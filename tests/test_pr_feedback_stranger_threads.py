from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

import alan.teams._pr_feedback as _pr_feedback
from alan import secrets_store
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.forge import PermissionRead
from alan.teams._pr_feedback import FeedbackSnapshot, HumanThread
from alan.teams._pr_forge import GH_TIMEOUT_S
from tests.fakes.forge import FakeForge
from tests.forge_stubs import always, api_result

PR = 42
REPO = "DejvMatikar/alan"
COPILOT = "copilot-pull-request-reviewer"
_TIMEOUT = subprocess.TimeoutExpired("gh", 1.0)


def thread_node(
    thread_id: str = "T1", login: str | None = "stranger", *,
    total: object = 1, typename: str = "User", resolved: bool = False,
) -> dict[str, object]:
    comment: dict[str, object] = {"body": "b", "databaseId": 1}
    if login is not None:
        comment["author"] = {"login": login, "__typename": typename}
    comments: dict[str, object] = {"nodes": [comment]}
    if total != "absent":
        comments["totalCount"] = total
    return {"id": thread_id, "isResolved": resolved, "comments": comments}


def feedback_payload(
    nodes: list[dict[str, object]], *, comments: list[dict[str, object]] | None = None,
) -> str:
    pr: dict[str, object] = {"reviewThreads": {"totalCount": len(nodes), "nodes": nodes}}
    if comments is not None:
        pr["comments"] = {"totalCount": len(comments), "nodes": comments}
    return json.dumps({"data": {"repository": {"pullRequest": pr}}})


def mutation_payload(key: str, *, resolved: object, count: object = 1) -> str:
    thread: dict[str, object] = {"isResolved": resolved}
    if count != "absent":
        thread["comments"] = {"totalCount": count}
    return json.dumps({"data": {key: {"thread": thread}}})


def resolved_ok(count: object = 1) -> object:
    return api_result(mutation_payload("resolveReviewThread", resolved=True, count=count))


def unresolved_ok(count: object = 2) -> object:
    return api_result(mutation_payload("unresolveReviewThread", resolved=False, count=count))


def scripted(spec: object) -> Callable[..., object]:
    def _call(key: str, **_kwargs: object) -> object:
        if spec is None:
            raise AssertionError(f"unexpected forge call for {key}")
        result = cast("dict[str, object]", spec)[key] if isinstance(spec, dict) else spec
        if isinstance(result, BaseException):
            raise result
        return result

    return _call


def stub_forge(
    fake: FakeForge, monkeypatch: pytest.MonkeyPatch, *, payload: str,
    permissions: object = None, resolve: object = None, unresolve: object = None,
    credential: bool = True,
) -> list[str]:
    always(fake, "feedback", api_result(payload))
    fake.stub("repo_permission", scripted(permissions))
    fake.stub("resolve_thread_checked", scripted(resolve))
    fake.stub("unresolve_thread", scripted(unresolve))
    credential_checks: list[str] = []

    def verifier_env(_target: object) -> dict[str, str]:
        credential_checks.append("checked")
        return {"GH_TOKEN": "reviewer-tok"} if credential else {}

    monkeypatch.setattr(secrets_store, "verifier_gh_env", verifier_env)
    return credential_checks


def call_args(fake: FakeForge, name: str) -> list[object]:
    return [args[0] for called, args, _ in fake.calls if called == name]


def write_calls(fake: FakeForge) -> list[str]:
    writes = ("resolve_thread_checked", "unresolve_thread")
    return [called for called, _, _ in fake.calls if called in writes]


def audit_records(tmp_path: Path) -> list[dict[str, object]]:
    path = tmp_path / "logs" / "impl_audit.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    kept = [row for row in rows if str(row["action"]).startswith("stranger_")]
    return [{k: v for k, v in row.items() if k != "timestamp_utc"} for row in kept]


def thread_record(
    action: str, *, thread_id: str = "T1", login: str = "stranger", permission: str = "read",
    comment_count: int | None = 1, error: str | None = None,
) -> dict[str, object]:
    return {
        "action": action, "pr_number": PR, "repo": REPO, "thread_id": thread_id,
        "login": login, "permission": permission, "comment_count": comment_count,
        "error": error,
    }


def lookup_failed_record(login: str, error: str) -> dict[str, object]:
    return {
        "action": "stranger_permission_lookup_failed", "pr_number": PR, "repo": REPO,
        "login": login, "error": error,
    }


def fetch() -> FeedbackSnapshot:
    return _pr_feedback.fetch_feedback(PR, forge_target=DEFAULT_FORGE_TARGET)


def read() -> FeedbackSnapshot:
    return _pr_feedback.read_feedback(PR, forge_target=DEFAULT_FORGE_TARGET)


def ok_permission(permission: str | None) -> PermissionRead:
    return PermissionRead(outcome="ok", permission=permission, returncode=0, stderr="")


@pytest.mark.parametrize("permission", ["read", "triage", "none"])
def test_single_comment_thread_from_a_non_collaborator_is_resolved_and_uncounted(
    permission: str, _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload([thread_node()]),
        permissions={"stranger": ok_permission(permission)}, resolve=resolved_ok(),
    )
    snap = fetch()
    assert snap.ok is True
    assert snap.thread_info.human_unresolved == 0
    assert snap.thread_info.human_threads == ()
    assert snap.thread_info.total == 1
    assert call_args(_active_fake_forge, "repo_permission") == ["stranger"]
    assert call_args(_active_fake_forge, "resolve_thread_checked") == ["T1"]
    assert call_args(_active_fake_forge, "unresolve_thread") == []
    assert audit_records(tmp_path) == [
        thread_record("stranger_thread_resolved", permission=permission),
    ]


def test_lookup_and_resolve_use_the_engine_forge_target_and_the_gh_timeout(
    _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload([thread_node()]),
        permissions={"stranger": ok_permission("read")}, resolve=resolved_ok(),
    )
    fetch()
    by_name = {called: kwargs for called, _, kwargs in _active_fake_forge.calls}
    assert by_name["repo_permission"] == {
        "forge_target": DEFAULT_FORGE_TARGET, "timeout": GH_TIMEOUT_S,
    }
    assert by_name["resolve_thread_checked"] == {"forge_target": DEFAULT_FORGE_TARGET}


@pytest.mark.parametrize("permission", ["admin", "maintain", "write"])
def test_thread_from_a_collaborator_keeps_blocking_and_is_never_resolved(
    permission: str, _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload([thread_node()]),
        permissions={"stranger": ok_permission(permission)},
    )
    snap = fetch()
    assert snap.thread_info.human_unresolved == 1
    assert snap.thread_info == read().thread_info
    assert write_calls(_active_fake_forge) == []
    assert audit_records(tmp_path) == []


@pytest.mark.parametrize("permission", ["READ", "Write", "", None, "custom", "null", "readonly"])
def test_unrecognised_or_missing_permission_counts_the_thread_and_audits_the_lookup(
    permission: str | None, _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload([thread_node()]),
        permissions={"stranger": ok_permission(permission)},
    )
    snap = fetch()
    assert snap.thread_info.human_unresolved == 1
    assert write_calls(_active_fake_forge) == []
    assert audit_records(tmp_path) == [
        lookup_failed_record("stranger", f"unrecognised permission {permission!r}"),
    ]


@pytest.mark.parametrize(
    ("result", "error"),
    [
        (PermissionRead("failed", None, 1, "HTTP 404: Not Found"), "HTTP 404: Not Found"),
        (PermissionRead("failed", None, 0, ""), ""),
        (PermissionRead("raised", None, -1, "gh vanished"), "gh vanished"),
        (PermissionRead("failed", "write", 1, "rate limited"), "rate limited"),
        (OSError("gh gone"), "gh gone"),
        (_TIMEOUT, str(_TIMEOUT)),
    ],
    ids=["failed", "failed-empty", "raised", "failed-with-value", "oserror", "timeout"],
)
def test_failed_permission_lookup_counts_the_thread_and_is_audited_once(
    result: object, error: str, _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload([thread_node()]),
        permissions={"stranger": result},
    )
    snap = fetch()
    assert snap.thread_info.human_unresolved == 1
    assert write_calls(_active_fake_forge) == []
    assert audit_records(tmp_path) == [lookup_failed_record("stranger", error)]


def test_lookup_failure_text_is_capped_at_five_hundred_characters(
    _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload([thread_node()]),
        permissions={"stranger": PermissionRead("failed", None, 1, "x" * 900)},
    )
    fetch()
    assert audit_records(tmp_path) == [lookup_failed_record("stranger", "x" * 500)]


@pytest.mark.parametrize("login", ["DejvMatikar", "dejvmatikar", "DEJVMATIKAR"])
def test_repository_owner_threads_are_never_looked_up_or_resolved(
    login: str, _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload([thread_node(login=login)]),
    )
    snap = fetch()
    assert snap == read()
    assert snap.thread_info.human_unresolved == 1
    assert call_args(_active_fake_forge, "repo_permission") == []
    assert write_calls(_active_fake_forge) == []
    assert audit_records(tmp_path) == []


@pytest.mark.parametrize("login", ["alan-verifier", "Alan-Verifier", "ALAN-VERIFIER"])
def test_reviewer_account_threads_are_never_looked_up_or_resolved(
    login: str, _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload([thread_node(login=login)]),
    )
    snap = fetch()
    assert snap == read()
    assert call_args(_active_fake_forge, "repo_permission") == []
    assert write_calls(_active_fake_forge) == []


def test_a_configured_reviewer_login_is_skipped_and_the_default_login_is_not(
    _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_VERIFIER_LOGIN", "Custom-Reviewer")
    nodes = [thread_node("T1", "custom-reviewer"), thread_node("T2", "alan-verifier")]
    stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload(nodes),
        permissions={"alan-verifier": ok_permission("write")},
    )
    fetch()
    assert call_args(_active_fake_forge, "repo_permission") == ["alan-verifier"]


@pytest.mark.parametrize(
    "node",
    [
        pytest.param(thread_node(login=None), id="missing-author"),
        pytest.param(thread_node(login=""), id="empty-login"),
        pytest.param(thread_node(thread_id=""), id="empty-thread-id"),
        pytest.param(thread_node(total=2), id="has-replies"),
        pytest.param(thread_node(total=0), id="zero-comments"),
        pytest.param(thread_node(total="absent"), id="count-absent"),
        pytest.param(thread_node(total="1"), id="count-not-int"),
        pytest.param(thread_node(total=True), id="count-bool"),
        pytest.param(thread_node(resolved=True), id="already-resolved"),
        pytest.param(thread_node(login="dependabot", typename="Bot"), id="other-bot"),
        pytest.param(thread_node(login=COPILOT, typename="Bot"), id="copilot"),
    ],
)
def test_threads_failing_a_candidate_filter_reach_no_forge_call(
    node: dict[str, object], _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credential_checks = stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload([node]),
    )
    snap = fetch()
    assert snap == read()
    assert credential_checks == []
    assert call_args(_active_fake_forge, "repo_permission") == []
    assert write_calls(_active_fake_forge) == []
    assert audit_records(tmp_path) == []


def test_a_thread_with_replies_from_a_stranger_still_counts(
    _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload([thread_node(total=3)]),
    )
    assert fetch().thread_info.human_unresolved == 1


def test_without_the_reviewer_credential_nothing_is_looked_up_or_resolved(
    _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    credential_checks = stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload([thread_node()]),
        credential=False,
    )
    snap = fetch()
    assert snap == read()
    assert snap.thread_info.human_unresolved == 1
    assert credential_checks == ["checked"]
    assert call_args(_active_fake_forge, "repo_permission") == []
    assert write_calls(_active_fake_forge) == []
    assert audit_records(tmp_path) == []


def test_the_reviewer_credential_is_checked_once_per_evaluation(
    _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch,
) -> None:
    nodes = [thread_node("T1", "alice"), thread_node("T2", "bob"), thread_node("T3", "cara")]
    credential_checks = stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload(nodes),
        permissions=dict.fromkeys(("alice", "bob", "cara"), ok_permission("none")),
        resolve=resolved_ok(),
    )
    fetch()
    assert credential_checks == ["checked"]


def test_a_payload_without_human_threads_never_checks_the_credential(
    _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch,
) -> None:
    nodes = [thread_node(login="dependabot", typename="Bot")]
    credential_checks = stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload(nodes),
    )
    fetch()
    assert credential_checks == []


def test_each_author_is_looked_up_at_most_once_per_evaluation(
    _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch,
) -> None:
    nodes = [thread_node(f"A{i}", "alice") for i in range(3)]
    nodes += [thread_node(f"B{i}", "bob") for i in range(2)]
    stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload(nodes),
        permissions={"alice": ok_permission("read"), "bob": ok_permission("write")},
        resolve=resolved_ok(),
    )
    snap = fetch()
    assert call_args(_active_fake_forge, "repo_permission") == ["alice", "bob"]
    assert call_args(_active_fake_forge, "resolve_thread_checked") == ["A0", "A1", "A2"]
    assert [t.thread_id for t in snap.thread_info.human_threads] == ["B0", "B1"]
    assert snap.thread_info.human_unresolved == 2


def test_author_lookups_are_shared_across_login_case_variants(
    _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch,
) -> None:
    nodes = [thread_node("T1", "Alice"), thread_node("T2", "alice"), thread_node("T3", "ALICE")]
    stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload(nodes),
        permissions={"Alice": ok_permission("read")},
        resolve=resolved_ok(),
    )
    snap = fetch()
    assert call_args(_active_fake_forge, "repo_permission") == ["Alice"]
    assert snap.thread_info.human_unresolved == 0


def test_a_failed_lookup_is_audited_once_per_author_and_not_retried(
    _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    nodes = [thread_node(f"A{i}", "alice") for i in range(3)]
    nodes += [thread_node("B0", "bob"), thread_node("B1", "bob")]
    failed = PermissionRead("failed", None, 1, "boom")
    stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload(nodes),
        permissions={"alice": failed, "bob": failed},
    )
    snap = fetch()
    assert call_args(_active_fake_forge, "repo_permission") == ["alice", "bob"]
    assert snap.thread_info.human_unresolved == 5
    assert audit_records(tmp_path) == [
        lookup_failed_record("alice", "boom"), lookup_failed_record("bob", "boom"),
    ]


def test_nothing_is_remembered_between_evaluations(
    _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload([thread_node()]),
        permissions={"stranger": ok_permission("write")},
    )
    fetch()
    fetch()
    assert call_args(_active_fake_forge, "repo_permission") == ["stranger", "stranger"]


def test_an_already_resolved_thread_is_no_longer_a_candidate_on_the_next_evaluation(
    _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload([thread_node()]),
        permissions={"stranger": ok_permission("read")}, resolve=resolved_ok(),
    )
    fetch()
    always(
        _active_fake_forge, "feedback",
        api_result(feedback_payload([thread_node(resolved=True)])),
    )
    snap = fetch()
    assert snap.thread_info.human_unresolved == 0
    assert call_args(_active_fake_forge, "repo_permission") == ["stranger"]


_TRANSIENT = "502 Bad Gateway"
_RATE_LIMITED = "API rate limit exceeded for user ID 1."
_RESOLVE_FAILURES = [
    pytest.param(api_result(returncode=1, stderr="refused"), "refused", id="refused"),
    pytest.param(api_result(returncode=1, stderr=_RATE_LIMITED), _RATE_LIMITED, id="rate-limited"),
    pytest.param(api_result(returncode=1, stderr=_TRANSIENT), _TRANSIENT, id="transient"),
    pytest.param(
        api_result(returncode=-1, stderr="no reviewer credential"), "no reviewer credential",
        id="no-credential",
    ),
    pytest.param(OSError("gh gone"), "gh gone", id="oserror"),
    pytest.param(_TIMEOUT, str(_TIMEOUT), id="timeout"),
    pytest.param(api_result("not json"), "unexpected read-back", id="bad-payload"),
    pytest.param(
        api_result(mutation_payload("resolveReviewThread", resolved=False)),
        "unexpected read-back", id="not-resolved",
    ),
    pytest.param(
        api_result(mutation_payload("resolveReviewThread", resolved="true")),
        "unexpected read-back", id="non-bool-resolved",
    ),
    pytest.param(
        api_result(mutation_payload("unresolveReviewThread", resolved=True)),
        "unexpected read-back", id="wrong-mutation-key",
    ),
]


@pytest.mark.parametrize(("outcome", "error"), _RESOLVE_FAILURES)
def test_a_failed_resolve_leaves_the_thread_counted_and_is_audited_once(
    outcome: object, error: str, _active_fake_forge: FakeForge,
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload([thread_node()]),
        permissions={"stranger": ok_permission("none")}, resolve=outcome,
    )
    snap = fetch()
    assert snap.thread_info.human_unresolved == 1
    assert snap.thread_info == read().thread_info
    assert write_calls(_active_fake_forge) == ["resolve_thread_checked"]
    assert audit_records(tmp_path) == [
        thread_record("stranger_thread_resolve_failed", permission="none", error=error),
    ]


def test_a_resolve_failure_text_is_capped_at_five_hundred_characters(
    _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload([thread_node()]),
        permissions={"stranger": ok_permission("read")},
        resolve=api_result(returncode=1, stderr="y" * 900),
    )
    fetch()
    assert audit_records(tmp_path) == [
        thread_record("stranger_thread_resolve_failed", error="y" * 500),
    ]


def test_a_read_back_showing_replies_unresolves_the_thread_and_counts_it(
    _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload([thread_node()]),
        permissions={"stranger": ok_permission("read")},
        resolve=resolved_ok(count=2), unresolve=unresolved_ok(count=2),
    )
    snap = fetch()
    assert snap.thread_info.human_unresolved == 1
    assert write_calls(_active_fake_forge) == ["resolve_thread_checked", "unresolve_thread"]
    assert call_args(_active_fake_forge, "unresolve_thread") == ["T1"]
    assert audit_records(tmp_path) == [
        thread_record("stranger_thread_resolved", comment_count=2),
        thread_record("stranger_thread_unresolved", comment_count=2),
    ]


@pytest.mark.parametrize("count", [None, "absent", "2", True, 0])
def test_a_read_back_without_a_single_readable_comment_is_unresolved_and_counted(
    count: object, _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload([thread_node()]),
        permissions={"stranger": ok_permission("read")},
        resolve=resolved_ok(count=count), unresolve=unresolved_ok(),
    )
    snap = fetch()
    assert snap.thread_info.human_unresolved == 1
    assert write_calls(_active_fake_forge) == ["resolve_thread_checked", "unresolve_thread"]
    actions = [record["action"] for record in audit_records(tmp_path)]
    assert actions == ["stranger_thread_resolved", "stranger_thread_unresolved"]


_UNRESOLVE_FAILURES = [
    pytest.param(api_result(returncode=1, stderr="refused"), "refused", id="refused"),
    pytest.param(api_result(returncode=1, stderr=_RATE_LIMITED), _RATE_LIMITED, id="rate-limited"),
    pytest.param(OSError("gh gone"), "gh gone", id="oserror"),
    pytest.param(_TIMEOUT, str(_TIMEOUT), id="timeout"),
    pytest.param(api_result("not json"), "unexpected read-back", id="bad-payload"),
    pytest.param(
        api_result(mutation_payload("unresolveReviewThread", resolved=True)),
        "unexpected read-back", id="still-resolved",
    ),
]


@pytest.mark.parametrize(("outcome", "error"), _UNRESOLVE_FAILURES)
def test_a_failed_unresolve_still_counts_the_thread_and_is_audited(
    outcome: object, error: str, _active_fake_forge: FakeForge,
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload([thread_node()]),
        permissions={"stranger": ok_permission("triage")},
        resolve=resolved_ok(count=2), unresolve=outcome,
    )
    snap = fetch()
    assert snap.thread_info.human_unresolved == 1
    assert audit_records(tmp_path) == [
        thread_record("stranger_thread_resolved", permission="triage", comment_count=2),
        thread_record(
            "stranger_thread_unresolve_failed", permission="triage", comment_count=2,
            error=error,
        ),
    ]


def test_audit_records_carry_the_documented_fields_and_a_timestamp(
    _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload([thread_node()]),
        permissions={"stranger": ok_permission("read")}, resolve=resolved_ok(),
    )
    fetch()
    lines = (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()
    assert len(lines) == 1
    assert set(json.loads(lines[0])) == {
        "timestamp_utc", "action", "pr_number", "repo", "thread_id", "login", "permission",
        "comment_count", "error",
    }


def test_settling_changes_only_the_human_counts_of_a_mixed_snapshot(
    _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch,
) -> None:
    nodes = [
        thread_node("S1", "alice"),
        thread_node("S2", "bob"),
        thread_node("W1", "carol", total=2),
        thread_node("C1", COPILOT, typename="Bot"),
        thread_node("B1", "dependabot", typename="Bot"),
        thread_node("R1", "alice", resolved=True),
    ]
    human_comment: dict[str, object] = {
        "databaseId": 5, "body": "x", "author": {"login": "eve", "__typename": "User"},
    }
    stub_forge(
        _active_fake_forge, monkeypatch,
        payload=feedback_payload(nodes, comments=[human_comment]),
        permissions={"alice": ok_permission("read"), "bob": ok_permission("write")},
        resolve=resolved_ok(),
    )
    before = read()
    after = fetch()
    assert after.thread_info.total == before.thread_info.total == 6
    assert after.thread_info.copilot_threads == before.thread_info.copilot_threads
    assert after.thread_info.other_bot_unresolved == before.thread_info.other_bot_unresolved == 1
    assert before.thread_info.human_unresolved == 3
    assert after.thread_info.human_unresolved == 2
    assert [t.thread_id for t in after.thread_info.human_threads] == ["S2", "W1"]
    assert after.shadow_comment_info == before.shadow_comment_info
    assert after.blocking_comment_info == before.blocking_comment_info
    assert after.ok is True
    assert after.failure_reason == ""


def test_settling_one_thread_does_not_settle_the_next_after_a_resolve_failure(
    _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_forge(
        _active_fake_forge, monkeypatch,
        payload=feedback_payload([thread_node("T1", "alice"), thread_node("T2", "bob")]),
        permissions={"alice": ok_permission("read"), "bob": ok_permission("none")},
        resolve={"T1": api_result(returncode=1, stderr="refused"), "T2": resolved_ok()},
    )
    snap = fetch()
    assert snap.thread_info.human_threads == (HumanThread("T1", "alice", 1),)
    assert snap.thread_info.human_unresolved == 1


def test_a_failed_feedback_read_is_returned_untouched_without_any_lookup(
    _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential_checks = stub_forge(_active_fake_forge, monkeypatch, payload="")
    always(_active_fake_forge, "feedback", api_result("", returncode=1, stderr="boom"))
    snap = fetch()
    assert snap.ok is False
    assert snap == read()
    assert credential_checks == []
    assert call_args(_active_fake_forge, "repo_permission") == []


def test_read_feedback_never_looks_up_or_writes_even_for_a_settleable_thread(
    _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    credential_checks = stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload([thread_node()]),
    )
    snap = read()
    assert snap.thread_info.human_unresolved == 1
    assert credential_checks == []
    assert call_args(_active_fake_forge, "repo_permission") == []
    assert write_calls(_active_fake_forge) == []
    assert audit_records(tmp_path) == []
