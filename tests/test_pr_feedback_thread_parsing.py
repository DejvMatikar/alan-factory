from __future__ import annotations

import json

import pytest

import alan.teams._pr_feedback as _pr_feedback
from alan.teams._pr_feedback import HumanThread


def _payload(nodes: list[object]) -> str:
    return json.dumps({"data": {"repository": {"pullRequest": {
        "reviewThreads": {"totalCount": len(nodes), "nodes": nodes},
    }}}})


def _node(
    thread_id: object = "T1", *, login: str | None = "stranger", typename: str = "User",
    comments: object = "default", total: object = 1, resolved: bool = False,
) -> dict[str, object]:
    comment: dict[str, object] = {"body": "b", "databaseId": 1}
    if login is not None:
        comment["author"] = {"login": login, "__typename": typename}
    if comments == "default":
        block: dict[str, object] = {"nodes": [comment]}
        if total != "absent":
            block["totalCount"] = total
        comments = block
    node: dict[str, object] = {"id": thread_id, "isResolved": resolved}
    if comments != "absent":
        node["comments"] = comments
    return node


def test_human_thread_carries_its_id_login_and_comment_count() -> None:
    info = _pr_feedback.parse_thread_info(_payload([_node("T9", login="octo", total=1)]))
    assert info.human_threads == (HumanThread(thread_id="T9", login="octo", comment_count=1),)
    assert info.human_unresolved == 1


def test_human_thread_keeps_a_multi_comment_total() -> None:
    info = _pr_feedback.parse_thread_info(_payload([_node(total=4)]))
    assert info.human_threads[0].comment_count == 4


@pytest.mark.parametrize("total", [True, False, "1", 1.0, None, [1]])
def test_human_thread_comment_count_is_none_for_a_non_int_total(total: object) -> None:
    info = _pr_feedback.parse_thread_info(_payload([_node(total=total)]))
    assert info.human_threads[0].comment_count is None
    assert info.human_unresolved == 1


def test_human_thread_comment_count_is_none_when_total_count_is_absent() -> None:
    info = _pr_feedback.parse_thread_info(_payload([_node(total="absent")]))
    assert info.human_threads[0].comment_count is None


@pytest.mark.parametrize("comments", ["absent", None, [], "text"])
def test_human_thread_without_a_comments_object_has_no_login_and_no_count(
    comments: object,
) -> None:
    info = _pr_feedback.parse_thread_info(_payload([_node(comments=comments)]))
    assert info.human_threads == (HumanThread(thread_id="T1", login="", comment_count=None),)
    assert info.human_unresolved == 1


def test_human_thread_with_a_missing_author_has_an_empty_login() -> None:
    info = _pr_feedback.parse_thread_info(_payload([_node(login=None)]))
    assert info.human_threads == (HumanThread(thread_id="T1", login="", comment_count=1),)
    assert info.human_unresolved == 1


def test_human_thread_with_a_non_string_id_is_stringified() -> None:
    info = _pr_feedback.parse_thread_info(_payload([_node(thread_id=7)]))
    assert info.human_threads[0].thread_id == "7"


def test_bot_threads_and_resolved_threads_are_not_human_threads() -> None:
    nodes: list[object] = [
        _node("C", login="copilot-pull-request-reviewer", typename="Bot"),
        _node("B", login="dependabot", typename="Bot"),
        _node("R", login="stranger", resolved=True),
        "not-a-node",
        _node("H", login="stranger"),
    ]
    info = _pr_feedback.parse_thread_info(_payload(nodes))
    assert [t.thread_id for t in info.human_threads] == ["H"]
    assert info.human_unresolved == 1
    assert info.other_bot_unresolved == 1
    assert [t.thread_id for t in info.copilot_threads] == ["C"]
    assert info.total == 5


def test_human_threads_default_to_empty_on_a_hand_built_thread_info() -> None:
    info = _pr_feedback.ThreadInfo(
        copilot_threads=(), human_unresolved=3, other_bot_unresolved=0, total=3,
    )
    assert info.human_threads == ()
    assert _pr_feedback.parse_thread_info("").human_threads == ()
