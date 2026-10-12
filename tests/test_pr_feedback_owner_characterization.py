from __future__ import annotations

from pathlib import Path

import pytest

import alan.teams._pr_feedback as _pr_feedback
from alan import unblock_recipes as recipes_mod
from alan import unblock_sweep as sweep_mod
from alan.events import PubSub
from alan.queue import Queue
from alan.teams import _pr_merge_checks
from alan.workspace_index import WorkspaceIndex
from tests import _unblock_support as support
from tests.fakes.forge import FakeForge
from tests.test_pr_feedback_stranger_threads import (
    COPILOT,
    PR,
    audit_records,
    call_args,
    feedback_payload,
    fetch,
    read,
    stub_forge,
    thread_node,
    write_calls,
)

_PASSING_CI = [{"name": "ci", "state": "SUCCESS"}]
_RECIPES = {recipe.name: recipe for recipe in recipes_mod.RECIPES}


def _owner_shaped_nodes() -> list[dict[str, object]]:
    return [
        thread_node("O1", "DejvMatikar"),
        thread_node("O2", "dejvmatikar", total=3),
        thread_node("O3", "DEJVMATIKAR", total="absent"),
        thread_node("O4", "DejvMatikar", resolved=True),
        thread_node("C1", COPILOT, typename="Bot"),
        thread_node("C2", COPILOT, typename="Bot", resolved=True),
        thread_node("B1", "dependabot", typename="Bot"),
        thread_node("R1", "alan-verifier"),
        thread_node("R2", "Alan-Verifier", total=3),
        thread_node("R3", "alan-verifier", resolved=True),
        thread_node("R4", "alan-verifier", total=2, resolved=True),
    ]


def _shadow_comments() -> list[dict[str, object]]:
    owner = {"login": "DejvMatikar", "__typename": "User"}
    bot = {"login": COPILOT, "__typename": "Bot"}
    return [
        {"databaseId": 1, "body": "owner note", "author": owner},
        {"databaseId": 2, "body": "bot note", "author": bot},
    ]


def test_owner_shaped_feedback_matches_read_feedback_with_zero_lookups_and_writes(
    _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    nodes = _owner_shaped_nodes()
    credential_checks = stub_forge(
        _active_fake_forge, monkeypatch,
        payload=feedback_payload(nodes, comments=_shadow_comments()),
    )
    snap = fetch()
    assert snap == read()
    info = snap.thread_info
    assert info.human_unresolved == 5
    assert len(info.copilot_threads) == 1
    assert info.other_bot_unresolved == 1
    assert info.total == len(nodes)
    assert _pr_feedback.has_unresolved_human(info, snap.blocking_comment_info) is True
    assert _pr_merge_checks.classify_checks(_PASSING_CI, info, snap.blocking_comment_info) == (
        "unresolved"
    )
    assert credential_checks == []
    assert call_args(_active_fake_forge, "repo_permission") == []
    assert call_args(_active_fake_forge, "resolve_thread_checked") == []
    assert call_args(_active_fake_forge, "unresolve_thread") == []
    assert audit_records(tmp_path) == []


def test_owner_only_resolved_feedback_reads_ready_with_zero_lookups_and_writes(
    _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch,
) -> None:
    nodes = [
        thread_node("O4", "DejvMatikar", resolved=True),
        thread_node("R3", "alan-verifier", resolved=True),
        thread_node("B1", "dependabot", typename="Bot"),
    ]
    stub_forge(_active_fake_forge, monkeypatch, payload=feedback_payload(nodes))
    snap = fetch()
    assert snap == read()
    assert _pr_merge_checks.classify_checks(
        _PASSING_CI, snap.thread_info, snap.blocking_comment_info,
    ) == "ready"
    assert {called for called, _, _ in _active_fake_forge.calls} == {"feedback"}


def test_read_feedback_keeps_stranger_threads_blocking_with_zero_lookups_and_writes(
    _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    nodes = [thread_node("S1", "stranger"), thread_node("S2", "other", total=2)]
    credential_checks = stub_forge(_active_fake_forge, monkeypatch, payload=feedback_payload(nodes))
    snap = read()
    assert snap.thread_info.human_unresolved == 2
    assert snap.thread_info.total == 2
    assert _pr_feedback.has_unresolved_human(snap.thread_info, snap.blocking_comment_info) is True
    assert credential_checks == []
    assert call_args(_active_fake_forge, "repo_permission") == []
    assert write_calls(_active_fake_forge) == []
    assert audit_records(tmp_path) == []


def test_unblocker_human_feedback_probe_still_declines_a_stranger_thread_without_lookups(
    _active_fake_forge: FakeForge, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    credential_checks = stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload([thread_node("S1", "stranger")]),
    )
    queue = Queue(db_path=tmp_path / "queue.db")
    index = WorkspaceIndex(root=tmp_path / "ws", queue=queue)
    ws = support.parked_ws(tmp_path)
    ctx = support.context(
        ws, latest=support.handover(ws, pr_number=PR), ready=support.readiness(),
        pr_number=PR, recipe_deps=sweep_mod.default_deps(queue, index, PubSub()),
    )
    assert _RECIPES["green_not_merging"].probe(ctx).state == "declined"
    assert credential_checks == []
    assert call_args(_active_fake_forge, "repo_permission") == []
    assert write_calls(_active_fake_forge) == []
    assert audit_records(tmp_path) == []
