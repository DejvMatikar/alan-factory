from __future__ import annotations

import json
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

import alan.teams._pr_feedback as _pr_feedback
import alan.teams._pr_state_paths as _pr_state_paths
from alan import forge_gh
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _forge_errors, _forge_quota
from alan.teams import pr_merge as pr_merge_mod
from alan.teams._pr_forge import GH_TIMEOUT_S
from alan.workspace import create
from tests.fakes.forge import FakeForge
from tests.forge_stubs import always, api_result


def _thread_node(
    *,
    thread_id: str = "T1",
    resolved: bool = False,
    body: str = "body",
    database_id: int = 1,
    login: str | None = None,
    typename: str | None = None,
    no_author: bool = False,
) -> dict[str, object]:
    comment: dict[str, object] = {"body": body, "databaseId": database_id}
    if not no_author:
        author: dict[str, object] = {}
        if login is not None:
            author["login"] = login
        if typename is not None:
            author["__typename"] = typename
        comment["author"] = author
    return {"id": thread_id, "isResolved": resolved, "comments": {"nodes": [comment]}}


def _threads_payload(nodes: list[object], *, total: int | None = None) -> str:
    return json.dumps({"data": {"repository": {"pullRequest": {
        "reviewThreads": {"totalCount": total if total is not None else len(nodes), "nodes": nodes},
    }}}})


def _comment_node(
    *,
    database_id: int = 1,
    body: str = "body",
    login: str | None = None,
    typename: str | None = None,
    state: str | None = None,
    no_author: bool = False,
) -> dict[str, object]:
    node: dict[str, object] = {"databaseId": database_id, "body": body}
    if not no_author:
        author: dict[str, object] = {}
        if login is not None:
            author["login"] = login
        if typename is not None:
            author["__typename"] = typename
        node["author"] = author
    if state is not None:
        node["state"] = state
    return node


def _feedback_payload(
    *,
    threads: list[object] | None = None,
    comments: list[object] | None = None,
    reviews: list[object] | None = None,
    threads_total: int | None = None,
) -> str:
    pr: dict[str, object] = {}
    if threads is not None:
        pr["reviewThreads"] = {
            "totalCount": threads_total if threads_total is not None else len(threads),
            "nodes": threads,
        }
    if comments is not None:
        pr["comments"] = {"totalCount": len(comments), "nodes": comments}
    if reviews is not None:
        pr["reviews"] = {"totalCount": len(reviews), "nodes": reviews}
    return json.dumps({"data": {"repository": {"pullRequest": pr}}})


_COPILOT_LOGIN = "copilot-pull-request-reviewer"


def test_graphql_actor_login_is_frozen_and_comparable() -> None:
    a = _pr_feedback.GraphQLActorLogin(_COPILOT_LOGIN)
    b = _pr_feedback.GraphQLActorLogin(_COPILOT_LOGIN)
    assert a == b
    with pytest.raises(FrozenInstanceError):
        a.login = "y"  # type: ignore[misc]


def test_pinned_copilot_login_matches_live_verified_graphql_value() -> None:
    assert _pr_feedback._COPILOT_GRAPHQL_LOGIN.login == "copilot-pull-request-reviewer"  # noqa: SLF001
    assert _pr_feedback._COPILOT_GRAPHQL_LOGIN.login != "Copilot"  # noqa: SLF001
    assert _pr_feedback._COPILOT_GRAPHQL_LOGIN.login != "copilot-pull-request-reviewer[bot]"  # noqa: SLF001


def test_feedback_query_shape_never_selects_author_directly_on_thread_node() -> None:
    query = forge_gh._FEEDBACK_QUERY  # noqa: SLF001
    threads_shape = query.split("reviewThreads(first:100){totalCount,nodes{", 1)[1]
    threads_shape = threads_shape.split("comments(first:1)", 1)[0]
    assert "author" not in threads_shape


def test_feedback_query_shape_reads_author_and_typename_off_thread_first_comment() -> None:
    query = forge_gh._FEEDBACK_QUERY  # noqa: SLF001
    assert (
        "comments(first:1){totalCount,nodes{body,databaseId,author{login,__typename}}}" in query
    )


def test_feedback_query_shape_selects_typename_on_comments_and_reviews() -> None:
    query = forge_gh._FEEDBACK_QUERY  # noqa: SLF001
    assert (
        "comments(first:100){totalCount,nodes{databaseId,body,author{login,__typename}}}"
        in query
    )
    assert (
        "reviews(first:100){totalCount,nodes{databaseId,body,author{login,__typename},state}}"
        in query
    )


def test_classify_actor_missing_author_is_human() -> None:
    assert _pr_feedback._classify_actor(None, _pr_feedback._COPILOT_GRAPHQL_LOGIN) == "human"  # noqa: SLF001


def test_classify_actor_user_typename_is_human() -> None:
    actor = _pr_feedback._Actor(login=_COPILOT_LOGIN, typename="User")  # noqa: SLF001
    assert _pr_feedback._classify_actor(actor, _pr_feedback._COPILOT_GRAPHQL_LOGIN) == "human"  # noqa: SLF001


def test_classify_actor_bot_with_pinned_login_is_copilot() -> None:
    actor = _pr_feedback._Actor(login=_COPILOT_LOGIN, typename="Bot")  # noqa: SLF001
    assert (
        _pr_feedback._classify_actor(actor, _pr_feedback._COPILOT_GRAPHQL_LOGIN) == "copilot"  # noqa: SLF001
    )


def test_classify_actor_bot_with_other_login_is_other_bot() -> None:
    actor = _pr_feedback._Actor(login="sonarqubecloud", typename="Bot")  # noqa: SLF001
    assert (
        _pr_feedback._classify_actor(actor, _pr_feedback._COPILOT_GRAPHQL_LOGIN) == "other_bot"  # noqa: SLF001
    )


def test_parse_actor_non_dict_author_returns_none() -> None:
    assert _pr_feedback._parse_actor({"author": "not-a-dict"}) is None  # noqa: SLF001


def test_parse_actor_non_string_fields_return_none() -> None:
    assert _pr_feedback._parse_actor({"author": {"login": 1, "__typename": "Bot"}}) is None  # noqa: SLF001
    assert _pr_feedback._parse_actor(  # noqa: SLF001
        {"author": {"login": "x", "__typename": 1}}
    ) is None


def test_parse_actor_happy_path() -> None:
    actor = _pr_feedback._parse_actor(  # noqa: SLF001
        {"author": {"login": _COPILOT_LOGIN, "__typename": "Bot"}}
    )
    assert actor == _pr_feedback._Actor(login=_COPILOT_LOGIN, typename="Bot")  # noqa: SLF001


def test_parse_thread_info_empty_string() -> None:
    info = _pr_feedback.parse_thread_info("")
    assert info == _pr_feedback._EMPTY_THREAD_INFO  # noqa: SLF001


def test_parse_thread_info_not_json() -> None:
    assert _pr_feedback.parse_thread_info("not json").total == 0


def test_parse_thread_info_non_dict_review_threads() -> None:
    payload = json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": "no"}}}})
    assert _pr_feedback.parse_thread_info(payload).total == 0


def test_parse_thread_info_copilot_thread_via_first_comment_author() -> None:
    node = _thread_node(
        thread_id="PRTI_abc", body="fix this", database_id=42,
        login=_COPILOT_LOGIN, typename="Bot",
    )
    info = _pr_feedback.parse_thread_info(_threads_payload([node]))
    assert len(info.copilot_threads) == 1
    assert info.copilot_threads[0] == _pr_feedback.CopilotThread(
        thread_id="PRTI_abc", body="fix this", database_id=42
    )
    assert info.human_unresolved == 0
    assert info.other_bot_unresolved == 0
    assert info.total == 1


def test_parse_thread_info_human_thread_user_typename() -> None:
    node = _thread_node(login="some-human", typename="User")
    info = _pr_feedback.parse_thread_info(_threads_payload([node]))
    assert info.copilot_threads == ()
    assert info.human_unresolved == 1
    assert info.other_bot_unresolved == 0


def test_parse_thread_info_other_bot_thread_is_non_blocking() -> None:
    node = _thread_node(login="sonarqubecloud[bot]", typename="Bot")
    info = _pr_feedback.parse_thread_info(_threads_payload([node]))
    assert info.copilot_threads == ()
    assert info.human_unresolved == 0
    assert info.other_bot_unresolved == 1


def test_parse_thread_info_resolved_thread_skipped() -> None:
    node = _thread_node(resolved=True, login=_COPILOT_LOGIN, typename="Bot")
    info = _pr_feedback.parse_thread_info(_threads_payload([node]))
    assert info.copilot_threads == ()
    assert info.human_unresolved == 0
    assert info.other_bot_unresolved == 0
    assert info.total == 1


def test_parse_thread_info_missing_author_counts_as_human() -> None:
    node = _thread_node(no_author=True)
    info = _pr_feedback.parse_thread_info(_threads_payload([node]))
    assert info.copilot_threads == ()
    assert info.human_unresolved == 1


def test_parse_thread_info_custom_copilot_login() -> None:
    node = _thread_node(thread_id="t1", login="custom-bot", typename="Bot")
    info = _pr_feedback.parse_thread_info(
        _threads_payload([node]), copilot_login=_pr_feedback.GraphQLActorLogin("custom-bot"),
    )
    assert len(info.copilot_threads) == 1
    assert info.copilot_threads[0].thread_id == "t1"


def test_parse_thread_info_skips_non_dict_nodes() -> None:
    good = _thread_node(thread_id="t2", login=_COPILOT_LOGIN, typename="Bot")
    payload = _threads_payload(["not-a-dict", 42, good], total=3)
    info = _pr_feedback.parse_thread_info(payload)
    assert len(info.copilot_threads) == 1


def test_parse_thread_info_copilot_null_comments() -> None:
    node = {"id": "PRTI_nc", "isResolved": False, "comments": None}
    info = _pr_feedback.parse_thread_info(_threads_payload([node]))
    assert info.human_unresolved == 1


def test_parse_thread_info_copilot_empty_nodes() -> None:
    node = {"id": "PRTI_en", "isResolved": False, "comments": {"nodes": []}}
    info = _pr_feedback.parse_thread_info(_threads_payload([node]))
    assert info.human_unresolved == 1


def test_parse_thread_info_non_dict_first_comment() -> None:
    node = {"id": "PRTI_nd", "isResolved": False, "comments": {"nodes": ["not-a-dict"]}}
    info = _pr_feedback.parse_thread_info(_threads_payload([node]))
    assert info.human_unresolved == 1


def test_parse_thread_info_total_robust_to_shape() -> None:
    assert _pr_feedback.parse_thread_info(json.dumps({"data": "no"})).total == 0
    assert _pr_feedback.parse_thread_info(json.dumps({"data": {"repository": {
        "pullRequest": {"reviewThreads": {"totalCount": "not-an-int"}}
    }}})).total == 0
    assert _pr_feedback.parse_thread_info(json.dumps({"data": {"repository": {
        "pullRequest": {"reviewThreads": {"totalCount": 17, "nodes": "not-a-list"}}
    }}})).total == 17


def test_parse_thread_info_database_id_non_int_falls_back_to_zero() -> None:
    node = {
        "id": "t", "isResolved": False,
        "comments": {"nodes": [{
            "body": "x", "databaseId": "not-int",
            "author": {"login": _COPILOT_LOGIN, "__typename": "Bot"},
        }]},
    }
    info = _pr_feedback.parse_thread_info(_threads_payload([node]))
    assert info.copilot_threads[0].database_id == 0


def test_parse_comment_info_missing_pull_request_returns_empty() -> None:
    info = _pr_feedback.parse_comment_info("not json")
    assert info == _pr_feedback._EMPTY_COMMENT_INFO  # noqa: SLF001


def test_parse_comment_info_copilot_issue_comment() -> None:
    raw = _feedback_payload(comments=[_comment_node(
        database_id=99, body="Consider using dependency injection",
        login=_COPILOT_LOGIN, typename="Bot",
    )])
    info = _pr_feedback.parse_comment_info(raw)
    assert info.total == 1
    assert info.human_items == 0
    assert info.other_bot_items == 0
    assert len(info.copilot_items) == 1
    assert info.copilot_items[0] == _pr_feedback.CopilotComment(
        database_id=99, body="Consider using dependency injection", source="issue_comment",
    )


def test_parse_comment_info_copilot_review_body() -> None:
    raw = _feedback_payload(reviews=[_comment_node(
        database_id=7, body="Refactor please", login=_COPILOT_LOGIN, typename="Bot",
        state="COMMENTED",
    )])
    info = _pr_feedback.parse_comment_info(raw)
    assert len(info.copilot_items) == 1
    assert info.copilot_items[0].source == "review_body"


def test_parse_comment_info_other_bot_is_counted_separately() -> None:
    raw = _feedback_payload(comments=[_comment_node(
        database_id=1, body="lint says", login="github-actions[bot]", typename="Bot",
    )])
    info = _pr_feedback.parse_comment_info(raw)
    assert info.copilot_items == ()
    assert info.human_items == 0
    assert info.other_bot_items == 1


def test_parse_comment_info_skips_approved_copilot_review() -> None:
    raw = _feedback_payload(reviews=[_comment_node(
        database_id=8, body="LGTM overall", login=_COPILOT_LOGIN, typename="Bot",
        state="APPROVED",
    )])
    info = _pr_feedback.parse_comment_info(raw)
    assert info.copilot_items == ()
    assert info.human_items == 0
    assert info.total == 1


def test_parse_comment_info_skips_empty_body_comments() -> None:
    raw = _feedback_payload(
        comments=[_comment_node(
            database_id=1, body="   \n  ", login=_COPILOT_LOGIN, typename="Bot",
        )],
        reviews=[_comment_node(
            database_id=2, body="", login="human", typename="User", state="COMMENTED",
        )],
    )
    info = _pr_feedback.parse_comment_info(raw)
    assert info.copilot_items == ()
    assert info.human_items == 0


def test_parse_comment_info_human_issue_comment() -> None:
    raw = _feedback_payload(comments=[_comment_node(
        database_id=51, body="Could you also handle X?", login="some-human", typename="User",
    )])
    info = _pr_feedback.parse_comment_info(raw)
    assert info.copilot_items == ()
    assert info.human_items == 1


def test_parse_comment_info_non_dict_section_returns_empty() -> None:
    raw = json.dumps({"data": {"repository": {"pullRequest": {
        "comments": "not-a-dict", "reviews": "also-not-a-dict",
    }}}})
    info = _pr_feedback.parse_comment_info(raw)
    assert info == _pr_feedback._EMPTY_COMMENT_INFO  # noqa: SLF001


def test_parse_comment_info_non_dict_node_skipped() -> None:
    raw = _feedback_payload(comments=["not-a-dict"])
    info = _pr_feedback.parse_comment_info(raw)
    assert info.copilot_items == ()
    assert info.total == 1


def test_parse_comment_info_custom_copilot_login() -> None:
    raw = _feedback_payload(comments=[_comment_node(
        database_id=33, body="custom", login="my-bot", typename="Bot",
    )])
    info = _pr_feedback.parse_comment_info(
        raw, copilot_login=_pr_feedback.GraphQLActorLogin("my-bot"),
    )
    assert len(info.copilot_items) == 1
    assert info.copilot_items[0].database_id == 33


def test_parse_comment_info_non_int_database_id_falls_back_to_zero() -> None:
    raw = _feedback_payload(comments=[{
        "databaseId": "not-int", "body": "weird",
        "author": {"login": _COPILOT_LOGIN, "__typename": "Bot"},
    }])
    info = _pr_feedback.parse_comment_info(raw)
    assert info.copilot_items[0].database_id == 0


def test_parse_comment_info_missing_author_counts_as_human() -> None:
    raw = _feedback_payload(
        comments=[_comment_node(database_id=9, body="no author", no_author=True)]
    )
    info = _pr_feedback.parse_comment_info(raw)
    assert info.copilot_items == ()
    assert info.human_items == 1


def test_blocking_comment_info_excludes_shadow_categories() -> None:
    raw = _feedback_payload(
        comments=[_comment_node(database_id=1, body="a", login=_COPILOT_LOGIN, typename="Bot")],
        reviews=[_comment_node(
            database_id=2, body="b", login="human", typename="User", state="COMMENTED"
        )],
    )
    all_info = _pr_feedback.parse_comment_info(raw)
    assert all_info.total == 2
    blocking = _pr_feedback._blocking_comment_info(  # noqa: SLF001
        raw, _pr_feedback._COPILOT_GRAPHQL_LOGIN,  # noqa: SLF001
    )
    assert blocking == _pr_feedback._EMPTY_COMMENT_INFO  # noqa: SLF001


def test_shadow_categories_cover_both_non_thread_sources() -> None:
    assert _pr_feedback._SHADOW_CATEGORIES == frozenset({"issue_comment", "review_body"})  # noqa: SLF001


def test_comment_info_by_source_missing_pull_request_returns_empty_per_source() -> None:
    by_source = _pr_feedback._comment_info_by_source(  # noqa: SLF001
        "not json", _pr_feedback._COPILOT_GRAPHQL_LOGIN,  # noqa: SLF001
    )
    assert by_source == {
        "issue_comment": _pr_feedback._EMPTY_COMMENT_INFO,  # noqa: SLF001
        "review_body": _pr_feedback._EMPTY_COMMENT_INFO,  # noqa: SLF001
    }


def _stub_feedback(fake: FakeForge, stdout: str = "", rc: int = 0, stderr: str = "") -> None:
    always(fake, "feedback", api_result(stdout, returncode=rc, stderr=stderr))


def _stub_feedback_raising(fake: FakeForge, error: BaseException) -> None:
    def boom(*_a: object, **_k: object) -> object:
        raise error

    fake.stub("feedback", boom)


def test_fetch_feedback_timeout_yields_not_ok(_active_fake_forge: FakeForge) -> None:
    _stub_feedback_raising(_active_fake_forge, subprocess.TimeoutExpired("gh", 1.0))
    snap = _pr_feedback.fetch_feedback(42, forge_target=DEFAULT_FORGE_TARGET)
    assert snap.ok is False
    assert "timed out" in snap.failure_reason
    assert snap.thread_info == _pr_feedback._EMPTY_THREAD_INFO  # noqa: SLF001


def test_fetch_feedback_missing_gh_yields_not_ok(_active_fake_forge: FakeForge) -> None:
    _stub_feedback_raising(_active_fake_forge, FileNotFoundError("gh"))
    snap = _pr_feedback.fetch_feedback(42, forge_target=DEFAULT_FORGE_TARGET)
    assert snap.ok is False
    assert "raised" in snap.failure_reason


def test_fetch_feedback_nonzero_rc_yields_not_ok(_active_fake_forge: FakeForge) -> None:
    _stub_feedback(_active_fake_forge, rc=1, stderr="boom")
    snap = _pr_feedback.fetch_feedback(42, forge_target=DEFAULT_FORGE_TARGET)
    assert snap.ok is False
    assert "rc=1" in snap.failure_reason
    assert "boom" in snap.failure_reason


def test_fetch_feedback_transient_rc_raises_forge_transient_error(
    _active_fake_forge: FakeForge,
) -> None:
    _stub_feedback(_active_fake_forge, rc=1, stderr="dial tcp: connection refused")
    with pytest.raises(_forge_errors.ForgeTransientError):
        _pr_feedback.fetch_feedback(42, forge_target=DEFAULT_FORGE_TARGET)


def test_fetch_feedback_rate_limited_rc_raises_forge_rate_limited(
    _active_fake_forge: FakeForge,
) -> None:
    _stub_feedback(_active_fake_forge, rc=1, stderr="secondary rate limit")
    with pytest.raises(_forge_errors.ForgeRateLimited):
        _pr_feedback.fetch_feedback(42, forge_target=DEFAULT_FORGE_TARGET)


def test_fetch_feedback_unparseable_body_yields_not_ok(_active_fake_forge: FakeForge) -> None:
    _stub_feedback(_active_fake_forge, stdout="not json")
    snap = _pr_feedback.fetch_feedback(42, forge_target=DEFAULT_FORGE_TARGET)
    assert snap.ok is False
    assert "did not parse" in snap.failure_reason


def test_fetch_feedback_graphql_errors_payload_yields_not_ok(
    _active_fake_forge: FakeForge,
) -> None:
    body = json.dumps({"errors": [{"message": "Field 'author' doesn't exist"}]})
    _stub_feedback(_active_fake_forge, stdout=body)
    snap = _pr_feedback.fetch_feedback(42, forge_target=DEFAULT_FORGE_TARGET)
    assert snap.ok is False
    assert "graphql errors payload" in snap.failure_reason
    sample = _forge_quota.last_sample(_pr_feedback._QUOTA_CALL_SITE)  # noqa: SLF001
    assert sample is not None
    assert sample.outcome == "error"


def test_fetch_feedback_graphql_body_rate_limited_error_records_sample_and_raises(
    _active_fake_forge: FakeForge,
) -> None:
    body = json.dumps({
        "data": {"rateLimit": {
            "limit": 5000, "cost": 1, "remaining": 10, "resetAt": "2026-08-19T15:04:05Z",
        }},
        "errors": [{"type": "RATE_LIMITED", "message": "API rate limit exceeded"}],
    })
    _stub_feedback(_active_fake_forge, stdout=body)
    with pytest.raises(_forge_errors.ForgeRateLimited):
        _pr_feedback.fetch_feedback(42, forge_target=DEFAULT_FORGE_TARGET)
    sample = _forge_quota.last_sample(_pr_feedback._QUOTA_CALL_SITE)  # noqa: SLF001
    assert sample is not None
    assert sample.outcome == "rate_limited"
    assert sample.remaining == 10


def test_fetch_feedback_empty_errors_list_does_not_fail_on_that_branch(
    _active_fake_forge: FakeForge,
) -> None:
    body = json.dumps({"errors": [], "data": {"repository": {"pullRequest": None}}})
    _stub_feedback(_active_fake_forge, stdout=body)
    snap = _pr_feedback.fetch_feedback(42, forge_target=DEFAULT_FORGE_TARGET)
    assert snap.ok is False
    assert "missing data.repository.pullRequest" in snap.failure_reason


def test_fetch_feedback_missing_pull_request_yields_not_ok(
    _active_fake_forge: FakeForge,
) -> None:
    body = json.dumps({"data": {"repository": {"pullRequest": None}}})
    _stub_feedback(_active_fake_forge, stdout=body)
    snap = _pr_feedback.fetch_feedback(42, forge_target=DEFAULT_FORGE_TARGET)
    assert snap.ok is False
    assert "missing data.repository.pullRequest" in snap.failure_reason


def test_fetch_feedback_happy_path_builds_full_snapshot(
    _active_fake_forge: FakeForge,
) -> None:
    _stub_feedback(_active_fake_forge, stdout=_feedback_payload(
        threads=[_thread_node(login=_COPILOT_LOGIN, typename="Bot")],
        comments=[_comment_node(login=_COPILOT_LOGIN, typename="Bot")],
    ))
    snap = _pr_feedback.fetch_feedback(42, forge_target=DEFAULT_FORGE_TARGET)
    assert snap.ok is True
    assert snap.failure_reason == ""
    assert len(snap.thread_info.copilot_threads) == 1
    assert snap.shadow_comment_info.total == 1
    assert snap.blocking_comment_info == _pr_feedback._EMPTY_COMMENT_INFO  # noqa: SLF001
    assert _active_fake_forge.calls == [
        ("feedback", (42,), {"forge_target": DEFAULT_FORGE_TARGET, "timeout": GH_TIMEOUT_S}),
    ]
    sample = _forge_quota.last_sample(_pr_feedback._QUOTA_CALL_SITE)  # noqa: SLF001
    assert sample is not None
    assert sample.outcome == "ok"


def test_filter_acked_empty_comments_returns_as_is() -> None:
    assert _pr_feedback._filter_acked(42, (), DEFAULT_FORGE_TARGET) == ()  # noqa: SLF001


def test_filter_acked_excludes_acked_database_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _pr_state_paths.save_acked_comment_id(42, 99, DEFAULT_FORGE_TARGET)
    c1 = _pr_feedback.CopilotComment(database_id=99, body="acked", source="issue_comment")
    c2 = _pr_feedback.CopilotComment(database_id=100, body="still open", source="issue_comment")
    out = _pr_feedback._filter_acked(  # noqa: SLF001
        42, (c1, c2), DEFAULT_FORGE_TARGET,
    )
    assert out == (c2,)


def test_fetch_feedback_excludes_acked_comment_from_shadow_view(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _pr_state_paths.save_acked_comment_id(42, 99, DEFAULT_FORGE_TARGET)
    body = _feedback_payload(comments=[_comment_node(
        database_id=99, body="already-acked", login=_COPILOT_LOGIN, typename="Bot",
    )])
    _stub_feedback(_active_fake_forge, stdout=body)
    snap = _pr_feedback.fetch_feedback(42, forge_target=DEFAULT_FORGE_TARGET)
    assert snap.ok is True
    assert snap.shadow_comment_info.copilot_items == ()


def test_has_unresolved_human_false_when_both_zero() -> None:
    assert _pr_feedback.has_unresolved_human(
        _pr_feedback._EMPTY_THREAD_INFO, _pr_feedback._EMPTY_COMMENT_INFO  # noqa: SLF001
    ) is False


def test_has_unresolved_human_true_from_threads() -> None:
    info = _pr_feedback.ThreadInfo(
        copilot_threads=(), human_unresolved=1, other_bot_unresolved=0, total=1
    )
    assert _pr_feedback.has_unresolved_human(info, _pr_feedback._EMPTY_COMMENT_INFO) is True  # noqa: SLF001


def test_has_unresolved_human_true_from_blocking_comments() -> None:
    cinfo = _pr_feedback.CommentInfo(
        copilot_items=(), human_items=1, other_bot_items=0, total=1
    )
    assert _pr_feedback.has_unresolved_human(_pr_feedback._EMPTY_THREAD_INFO, cinfo) is True  # noqa: SLF001


def test_has_unresolved_copilot_false_when_empty() -> None:
    assert _pr_feedback.has_unresolved_copilot(_pr_feedback._EMPTY_THREAD_INFO, ()) is False  # noqa: SLF001


def test_has_unresolved_copilot_true_from_threads() -> None:
    t = _pr_feedback.CopilotThread(thread_id="t", body="b", database_id=1)
    info = _pr_feedback.ThreadInfo(
        copilot_threads=(t,), human_unresolved=0, other_bot_unresolved=0, total=1
    )
    assert _pr_feedback.has_unresolved_copilot(info, ()) is True


def test_has_unresolved_copilot_true_from_comments() -> None:
    c = _pr_feedback.CopilotComment(database_id=1, body="b", source="issue_comment")
    assert _pr_feedback.has_unresolved_copilot(_pr_feedback._EMPTY_THREAD_INFO, (c,)) is True  # noqa: SLF001


def test_emit_copilot_audits_writes_comment_unresolved_row(tmp_path: Path) -> None:
    ws = create("t1", "test", root=tmp_path / "ws")
    c = _pr_feedback.CopilotComment(database_id=5, body="fix this", source="issue_comment")
    outcome = pr_merge_mod.PrMergeOutcome(kind="copilot_unresolved", pr_number=42, reason="r")
    entries = pr_merge_mod._emit_copilot_audits(ws, outcome, (), (c,))  # noqa: SLF001
    assert entries[0]["action"] == "comment_unresolved"
    assert entries[0]["database_id"] == 5
