from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from alan import process_runner
from alan import workspace as workspace_mod
from alan.engine_config import (
    DEFAULT_FORGE_TARGET,
    EngineConfig,
    ForgeConfig,
    ForgeTarget,
    Posture,
    RepoIdentity,
)
from alan.forge import ApiResult
from alan.teams import _copilot_resolution as resolution_mod
from alan.teams import _pr_audit_log, _pr_feedback, _pr_state_paths, _pr_threads
from alan.teams._copilot_resolution_block import CopilotResolutionRow
from alan.workspace import EngineSnapshot, Workspace, create
from alan.worktree import WorktreeHandle
from tests.fakes.forge import FakeForge
from tests.forge_stubs import always, api_result

_WIDGET_FORGE = ForgeTarget(
    repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
    credential_env_name="ACME_WIDGET_GH_TOKEN",  # noqa: S106  # nosec B106
)


def _listed(
    *thread_ids: str, author: str = _pr_feedback._COPILOT_GRAPHQL_LOGIN.login,  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
) -> tuple[_pr_threads.ReviewThread, ...]:
    return tuple(
        _pr_threads.ReviewThread(
            thread_id=tid, author_login=author, body="", path="", line=0, is_outdated=False,
        )
        for tid in thread_ids
    )


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))


def _fake_gh(rc: int = 0) -> object:
    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(cmd, rc, stdout="{}", stderr="")
    return fake


def _post_stub(*returncodes: int) -> Callable[..., ApiResult]:
    remaining = list(returncodes)

    def _post(*_args: object, **_kwargs: object) -> ApiResult:
        rc = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        return api_result("{}", returncode=rc, stderr="boom" if rc else "")

    return _post


def _post_calls(fake: FakeForge, method: str) -> list[tuple[object, ...]]:
    return [args for name, args, _kwargs in fake.calls if name == method]


@pytest.fixture(autouse=True)
def _posts_succeed(_active_fake_forge: FakeForge) -> None:
    _active_fake_forge.stub("reply_to_thread", _post_stub(0))
    _active_fake_forge.stub("comment_on_pr", _post_stub(0))


_TRANSPORT_FAULTS = pytest.mark.parametrize(
    "fault",
    [FileNotFoundError("gh"), subprocess.TimeoutExpired(cmd="gh", timeout=1.0)],
    ids=["missing-binary", "timeout"],
)


def test_apply_fix_mode_resolves_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_resolve(thread_id: str) -> bool:
        calls.append(thread_id)
        return True

    monkeypatch.setattr(_pr_threads, "resolve_review_thread", fake_resolve)
    row = CopilotResolutionRow(thread_id="PRTI_1", mode="fix")
    outcome = resolution_mod.apply(
        (row,), pr_number=42, forge_target=_WIDGET_FORGE, head_sha="a" * 40,
        listed=_listed("PRTI_1"),
    )
    assert outcome.resolved == ("PRTI_1",)
    assert outcome.refused == ()
    assert calls == ["PRTI_1"]


def test_apply_fix_mode_receives_only_thread_id(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[object, ...]] = []

    def fake_resolve(*args: object, **kwargs: object) -> bool:
        captured.append(args)
        assert kwargs == {}
        return True

    monkeypatch.setattr(_pr_threads, "resolve_review_thread", fake_resolve)
    row = CopilotResolutionRow(thread_id="PRTI_1", mode="fix")
    resolution_mod.apply(
        (row,), pr_number=42, forge_target=_WIDGET_FORGE, head_sha=None,
        listed=_listed("PRTI_1"),
    )
    assert captured == [("PRTI_1",)]


def test_apply_fix_mode_never_claims_and_retries_freely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_resolve(thread_id: str) -> bool:
        calls.append(thread_id)
        return False

    monkeypatch.setattr(_pr_threads, "resolve_review_thread", fake_resolve)
    row = CopilotResolutionRow(thread_id="PRTI_1", mode="fix")
    outcome1 = resolution_mod.apply(
        (row,), pr_number=42, forge_target=_WIDGET_FORGE, head_sha=None,
        listed=_listed("PRTI_1"),
    )
    outcome2 = resolution_mod.apply(
        (row,), pr_number=42, forge_target=_WIDGET_FORGE, head_sha=None,
        listed=_listed("PRTI_1"),
    )
    assert outcome1.refused[0].reason == "forge_call_failed"
    assert outcome2.refused[0].reason == "forge_call_failed"
    assert calls == ["PRTI_1", "PRTI_1"]


def test_apply_fix_mode_refuses_unlisted_thread_with_no_forge_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_resolve(thread_id: str) -> bool:
        calls.append(thread_id)
        return True

    monkeypatch.setattr(_pr_threads, "resolve_review_thread", fake_resolve)
    row = CopilotResolutionRow(thread_id="PRTI_unlisted", mode="fix")
    outcome = resolution_mod.apply(
        (row,), pr_number=42, forge_target=_WIDGET_FORGE, head_sha="a" * 40, listed=(),
    )
    assert outcome.refused[0].reason == "thread_not_listed"
    assert calls == []


def test_apply_fix_mode_refuses_listed_human_authored_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_resolve(thread_id: str) -> bool:
        calls.append(thread_id)
        return True

    monkeypatch.setattr(_pr_threads, "resolve_review_thread", fake_resolve)
    row = CopilotResolutionRow(thread_id="PRTI_human", mode="fix")
    outcome = resolution_mod.apply(
        (row,), pr_number=42, forge_target=_WIDGET_FORGE, head_sha="a" * 40,
        listed=_listed("PRTI_human", author="a-human"),
    )
    assert outcome.refused[0].reason == "thread_not_listed"
    assert calls == []


def test_apply_fix_mode_empty_listing_refuses_every_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_threads, "resolve_review_thread", lambda t: True)
    rows = (
        CopilotResolutionRow(thread_id="PRTI_e1", mode="fix"),
        CopilotResolutionRow(thread_id="PRTI_e2", mode="fix"),
    )
    outcome = resolution_mod.apply(
        rows, pr_number=42, forge_target=_WIDGET_FORGE, head_sha="a" * 40, listed=(),
    )
    assert [r.reason for r in outcome.refused] == ["thread_not_listed", "thread_not_listed"]


def test_apply_fix_mode_already_resolved_listed_thread_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_threads, "resolve_review_thread", lambda t: True)
    row = CopilotResolutionRow(thread_id="PRTI_already", mode="fix")
    outcome = resolution_mod.apply(
        (row,), pr_number=42, forge_target=_WIDGET_FORGE, head_sha="a" * 40,
        listed=_listed("PRTI_already"),
    )
    assert outcome.resolved == ("PRTI_already",)
    assert outcome.refused == ()


def test_apply_acknowledge_claims_before_posting(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    calls: list[str] = []

    def fake_reply(*_args: object, **_kwargs: object) -> ApiResult:
        calls.append("post")
        assert _pr_state_paths.claim_acked_thread(99, "PRTI_2", _WIDGET_FORGE) is False
        return api_result("{}")

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append("resolve")
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    _active_fake_forge.stub("reply_to_thread", fake_reply)
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    row = CopilotResolutionRow(thread_id="PRTI_2", mode="acknowledge", body="ack reason")
    outcome = resolution_mod.apply(
        (row,), pr_number=99, forge_target=_WIDGET_FORGE, head_sha="a" * 40,
        listed=_listed("PRTI_2"),
    )
    assert outcome.acknowledged == ("PRTI_2",)
    assert outcome.resolved == ("PRTI_2",)
    assert calls == ["post", "resolve"]


def test_apply_acknowledge_second_attempt_skips_repost(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    calls: list[str] = []

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(str(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    row = CopilotResolutionRow(thread_id="PRTI_3", mode="acknowledge", body="ack reason")
    listed = _listed("PRTI_3")
    resolution_mod.apply(
        (row,), pr_number=5, forge_target=_WIDGET_FORGE, head_sha="a" * 40, listed=listed,
    )
    calls.clear()
    outcome2 = resolution_mod.apply(
        (row,), pr_number=5, forge_target=_WIDGET_FORGE, head_sha="a" * 40, listed=listed,
    )
    assert calls == []
    assert len(_post_calls(_active_fake_forge, "reply_to_thread")) == 1
    assert outcome2.resolved == ()
    assert outcome2.acknowledged == ()
    assert outcome2.refused[0].reason == "already_handled"


def test_apply_acknowledge_already_handled_replay_is_audited(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh(0))
    row = CopilotResolutionRow(thread_id="PRTI_dup", mode="acknowledge", body="ack reason")
    listed = _listed("PRTI_dup")
    resolution_mod.apply(
        (row,), pr_number=22, forge_target=_WIDGET_FORGE, head_sha="a" * 40, listed=listed,
    )
    resolution_mod.apply(
        (row,), pr_number=22, forge_target=_WIDGET_FORGE, head_sha="a" * 40, listed=listed,
    )
    log = tmp_path / "logs" / _pr_audit_log.COPILOT_AUDIT_FILE
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    refusals = [r for r in rows if r["action"] == "copilot_resolution_refused"]
    assert refusals[-1]["reason"] == "already_handled"


def test_apply_acknowledge_failed_post_releases_claim_then_retry_posts_once(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh(0))
    _active_fake_forge.stub("reply_to_thread", _post_stub(1, 0))
    row = CopilotResolutionRow(thread_id="PRTI_retry", mode="acknowledge", body="ack reason")
    listed = _listed("PRTI_retry")
    outcome1 = resolution_mod.apply(
        (row,), pr_number=20, forge_target=_WIDGET_FORGE, head_sha="a" * 40, listed=listed,
    )
    budget_after_failure = _pr_state_paths.load_ack_budget(20, _WIDGET_FORGE, head_sha="a" * 40)
    outcome2 = resolution_mod.apply(
        (row,), pr_number=20, forge_target=_WIDGET_FORGE, head_sha="a" * 40, listed=listed,
    )
    budget_after_retry = _pr_state_paths.load_ack_budget(20, _WIDGET_FORGE, head_sha="a" * 40)
    assert outcome1.refused[0].reason == "forge_call_failed"
    assert outcome2.acknowledged == ("PRTI_retry",)
    assert len(_post_calls(_active_fake_forge, "reply_to_thread")) == 2
    assert budget_after_failure == 0
    assert budget_after_retry == 1


@_TRANSPORT_FAULTS
def test_apply_acknowledge_ambiguous_post_keeps_claim_and_retry_posts_nothing(
    fault: BaseException, _active_fake_forge: FakeForge,
) -> None:
    always(_active_fake_forge, "reply_to_thread", fault)
    row = CopilotResolutionRow(thread_id="PRTI_crash", mode="acknowledge", body="ack reason")
    listed = _listed("PRTI_crash")
    outcome1 = resolution_mod.apply(
        (row,), pr_number=21, forge_target=_WIDGET_FORGE, head_sha="a" * 40, listed=listed,
    )
    assert outcome1.refused[0].reason == "forge_call_failed"
    assert _pr_state_paths.load_ack_budget(21, _WIDGET_FORGE, head_sha="a" * 40) == 1

    always(_active_fake_forge, "reply_to_thread", api_result("{}"))
    outcome2 = resolution_mod.apply(
        (row,), pr_number=21, forge_target=_WIDGET_FORGE, head_sha="a" * 40, listed=listed,
    )
    assert len(_post_calls(_active_fake_forge, "reply_to_thread")) == 1
    assert outcome2.refused[0].reason == "already_handled"


def test_ack_cap_budget_not_consumed_by_already_handled_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh(0))
    row = CopilotResolutionRow(thread_id="PRTI_budget", mode="acknowledge", body="x")
    listed = _listed("PRTI_budget")
    resolution_mod.apply(
        (row,), pr_number=25, forge_target=_WIDGET_FORGE, head_sha="a" * 40, listed=listed,
    )
    budget_after_first = _pr_state_paths.load_ack_budget(25, _WIDGET_FORGE, head_sha="a" * 40)
    resolution_mod.apply(
        (row,), pr_number=25, forge_target=_WIDGET_FORGE, head_sha="a" * 40, listed=listed,
    )
    budget_after_replay = _pr_state_paths.load_ack_budget(25, _WIDGET_FORGE, head_sha="a" * 40)
    assert budget_after_replay == budget_after_first


def test_apply_acknowledge_post_failure_refuses_and_never_resolves(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    resolve_calls: list[str] = []

    def fake_resolve(thread_id: str) -> bool:
        resolve_calls.append(thread_id)
        return True

    _active_fake_forge.stub("reply_to_thread", _post_stub(1))
    monkeypatch.setattr(_pr_threads, "resolve_review_thread", fake_resolve)
    row = CopilotResolutionRow(thread_id="PRTI_4", mode="acknowledge", body="ack reason")
    outcome = resolution_mod.apply(
        (row,), pr_number=6, forge_target=_WIDGET_FORGE, head_sha="a" * 40,
        listed=_listed("PRTI_4"),
    )
    assert outcome.refused[0].reason == "forge_call_failed"
    assert resolve_calls == []


def test_apply_acknowledge_post_succeeds_but_resolve_fails_still_acknowledged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_threads, "resolve_review_thread", lambda t: False)  # noqa: ARG005
    row = CopilotResolutionRow(thread_id="PRTI_5", mode="acknowledge", body="ack reason")
    outcome = resolution_mod.apply(
        (row,), pr_number=15, forge_target=_WIDGET_FORGE, head_sha="a" * 40,
        listed=_listed("PRTI_5"),
    )
    assert outcome.acknowledged == ("PRTI_5",)
    assert outcome.resolved == ()
    assert outcome.refused == ()


def test_apply_acknowledge_replay_retries_resolve_without_reposting(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    resolves: list[str] = []

    def fake_resolve(thread_id: str) -> bool:
        resolves.append(thread_id)
        return len(resolves) > 1

    monkeypatch.setattr(_pr_threads, "resolve_review_thread", fake_resolve)
    row = CopilotResolutionRow(thread_id="PRTI_replay", mode="acknowledge", body="ack reason")
    listed = _listed("PRTI_replay")
    outcome1 = resolution_mod.apply(
        (row,), pr_number=17, forge_target=_WIDGET_FORGE, head_sha="a" * 40, listed=listed,
    )
    budget_after_first = _pr_state_paths.load_ack_budget(17, _WIDGET_FORGE, head_sha="a" * 40)
    outcome2 = resolution_mod.apply(
        (row,), pr_number=17, forge_target=_WIDGET_FORGE, head_sha="a" * 40, listed=listed,
    )
    budget_after_replay = _pr_state_paths.load_ack_budget(17, _WIDGET_FORGE, head_sha="a" * 40)
    assert outcome1.acknowledged == ("PRTI_replay",)
    assert outcome1.resolved == ()
    assert outcome2.resolved == ("PRTI_replay",)
    assert len(_post_calls(_active_fake_forge, "reply_to_thread")) == 1
    assert resolves == ["PRTI_replay", "PRTI_replay"]
    assert budget_after_first == 1
    assert budget_after_replay == 1


def test_apply_acknowledge_listed_human_thread_posts_reply_never_resolves(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    resolve_calls: list[str] = []

    def fake_resolve(thread_id: str) -> bool:
        resolve_calls.append(thread_id)
        return True

    monkeypatch.setattr(_pr_threads, "resolve_review_thread", fake_resolve)
    row = CopilotResolutionRow(thread_id="PRTI_humanack", mode="acknowledge", body="ack reason")
    listed = _listed("PRTI_humanack", author="a-human")
    outcome1 = resolution_mod.apply(
        (row,), pr_number=42, forge_target=_WIDGET_FORGE, head_sha="a" * 40, listed=listed,
    )
    assert outcome1.acknowledged == ("PRTI_humanack",)
    assert outcome1.resolved == ()
    assert len(_post_calls(_active_fake_forge, "reply_to_thread")) == 1
    assert resolve_calls == []

    outcome2 = resolution_mod.apply(
        (row,), pr_number=42, forge_target=_WIDGET_FORGE, head_sha="a" * 40, listed=listed,
    )
    assert outcome2.resolved == ()
    assert outcome2.acknowledged == ()
    assert outcome2.refused[0].reason == "already_handled"
    assert len(_post_calls(_active_fake_forge, "reply_to_thread")) == 1
    assert resolve_calls == []


def test_apply_comment_ack_claims_before_posting(_active_fake_forge: FakeForge) -> None:
    def fake_comment(*_args: object, **_kwargs: object) -> ApiResult:
        assert 11 in _pr_state_paths.load_acked_comment_ids(7, _WIDGET_FORGE)
        return api_result("ok")

    _active_fake_forge.stub("comment_on_pr", fake_comment)
    row = CopilotResolutionRow(database_id=11, mode="comment_ack", body="ack")
    outcome = resolution_mod.apply(
        (row,), pr_number=7, forge_target=_WIDGET_FORGE, head_sha="a" * 40, listed=(),
    )
    assert outcome.acknowledged == ("11",)


def test_apply_comment_ack_unaffected_by_empty_listed_set() -> None:
    row = CopilotResolutionRow(database_id=42, mode="comment_ack", body="ack")
    outcome = resolution_mod.apply(
        (row,), pr_number=7, forge_target=_WIDGET_FORGE, head_sha="a" * 40, listed=(),
    )
    assert outcome.acknowledged == ("42",)
    assert outcome.refused == ()


def test_apply_comment_ack_second_attempt_skips_repost(_active_fake_forge: FakeForge) -> None:
    row = CopilotResolutionRow(database_id=12, mode="comment_ack", body="ack")
    resolution_mod.apply(
        (row,), pr_number=8, forge_target=_WIDGET_FORGE, head_sha="a" * 40, listed=(),
    )
    outcome2 = resolution_mod.apply(
        (row,), pr_number=8, forge_target=_WIDGET_FORGE, head_sha="a" * 40, listed=(),
    )
    assert len(_post_calls(_active_fake_forge, "comment_on_pr")) == 1
    assert outcome2.acknowledged == ()


def test_apply_comment_ack_post_failure_refuses(_active_fake_forge: FakeForge) -> None:
    _active_fake_forge.stub("comment_on_pr", _post_stub(1))
    row = CopilotResolutionRow(database_id=14, mode="comment_ack", body="ack")
    outcome = resolution_mod.apply(
        (row,), pr_number=16, forge_target=_WIDGET_FORGE, head_sha="a" * 40, listed=(),
    )
    assert outcome.refused[0].reason == "forge_call_failed"
    assert outcome.acknowledged == ()


def test_apply_comment_ack_failed_post_releases_id_then_retry_posts_once(
    _active_fake_forge: FakeForge,
) -> None:
    _active_fake_forge.stub("comment_on_pr", _post_stub(1, 0))
    row = CopilotResolutionRow(database_id=30, mode="comment_ack", body="ack")
    outcome1 = resolution_mod.apply(
        (row,), pr_number=23, forge_target=_WIDGET_FORGE, head_sha="a" * 40, listed=(),
    )
    budget_after_failure = _pr_state_paths.load_ack_budget(23, _WIDGET_FORGE, head_sha="a" * 40)
    outcome2 = resolution_mod.apply(
        (row,), pr_number=23, forge_target=_WIDGET_FORGE, head_sha="a" * 40, listed=(),
    )
    budget_after_retry = _pr_state_paths.load_ack_budget(23, _WIDGET_FORGE, head_sha="a" * 40)
    assert outcome1.refused[0].reason == "forge_call_failed"
    assert outcome2.acknowledged == ("30",)
    assert len(_post_calls(_active_fake_forge, "comment_on_pr")) == 2
    assert budget_after_failure == 0
    assert budget_after_retry == 1


@_TRANSPORT_FAULTS
def test_apply_comment_ack_ambiguous_post_keeps_id_and_retry_posts_nothing(
    fault: BaseException, _active_fake_forge: FakeForge,
) -> None:
    always(_active_fake_forge, "comment_on_pr", fault)
    row = CopilotResolutionRow(database_id=31, mode="comment_ack", body="ack")
    outcome1 = resolution_mod.apply(
        (row,), pr_number=24, forge_target=_WIDGET_FORGE, head_sha="a" * 40, listed=(),
    )
    assert outcome1.refused[0].reason == "forge_call_failed"
    assert _pr_state_paths.load_ack_budget(24, _WIDGET_FORGE, head_sha="a" * 40) == 1

    always(_active_fake_forge, "comment_on_pr", api_result("ok"))
    outcome2 = resolution_mod.apply(
        (row,), pr_number=24, forge_target=_WIDGET_FORGE, head_sha="a" * 40, listed=(),
    )
    assert len(_post_calls(_active_fake_forge, "comment_on_pr")) == 1
    assert outcome2.refused[0].reason == "already_handled"


def test_apply_comment_ack_comments_through_the_port_under_the_caller_forge_target(
    _active_fake_forge: FakeForge,
) -> None:
    row = CopilotResolutionRow(database_id=13, mode="comment_ack", body="ack")
    resolution_mod.apply(
        (row,), pr_number=9, forge_target=_WIDGET_FORGE, head_sha="a" * 40, listed=(),
    )
    assert _active_fake_forge.calls == [
        ("comment_on_pr", (9, "ack"), {"forge_target": _WIDGET_FORGE}),
    ]


def test_apply_acknowledge_replies_through_the_port_under_the_default_forge_target(
    _active_fake_forge: FakeForge,
) -> None:
    row = CopilotResolutionRow(thread_id="PRTI_port", mode="acknowledge", body="ack reason")
    resolution_mod.apply(
        (row,), pr_number=9, forge_target=_WIDGET_FORGE, head_sha="a" * 40, listed=(),
    )
    assert _active_fake_forge.calls == [
        ("reply_to_thread", ("PRTI_port", "ack reason"), {"forge_target": DEFAULT_FORGE_TARGET}),
    ]


def test_ack_cap_exceeded_refuses_without_claiming(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh(0))
    for i in range(resolution_mod.ACK_CAP):
        row = CopilotResolutionRow(thread_id=f"PRTI_{i}", mode="acknowledge", body="x")
        resolution_mod.apply(
            (row,), pr_number=10, forge_target=_WIDGET_FORGE, head_sha="a" * 40, listed=(),
        )
    over_row = CopilotResolutionRow(thread_id="PRTI_over", mode="acknowledge", body="x")
    outcome = resolution_mod.apply(
        (over_row,), pr_number=10, forge_target=_WIDGET_FORGE, head_sha="a" * 40, listed=(),
    )
    assert outcome.refused[0].reason == "ack_cap_exceeded"
    assert _pr_state_paths.claim_acked_thread(10, "PRTI_over", _WIDGET_FORGE) is True


def test_ack_cap_resets_on_new_head_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh(0))
    for i in range(resolution_mod.ACK_CAP):
        row = CopilotResolutionRow(thread_id=f"PRTI_a{i}", mode="acknowledge", body="x")
        resolution_mod.apply(
            (row,), pr_number=11, forge_target=_WIDGET_FORGE, head_sha="sha-1", listed=(),
        )
    fresh_row = CopilotResolutionRow(thread_id="PRTI_fresh", mode="acknowledge", body="x")
    outcome = resolution_mod.apply(
        (fresh_row,), pr_number=11, forge_target=_WIDGET_FORGE, head_sha="sha-2", listed=(),
    )
    assert outcome.refused == ()
    assert outcome.acknowledged == ("PRTI_fresh",)


def test_ack_cap_fails_closed_on_unresolvable_head_sha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh(0))
    for i in range(resolution_mod.ACK_CAP):
        row = CopilotResolutionRow(thread_id=f"PRTI_b{i}", mode="acknowledge", body="x")
        resolution_mod.apply(
            (row,), pr_number=12, forge_target=_WIDGET_FORGE, head_sha=None, listed=(),
        )
    over_row = CopilotResolutionRow(thread_id="PRTI_over2", mode="acknowledge", body="x")
    outcome = resolution_mod.apply(
        (over_row,), pr_number=12, forge_target=_WIDGET_FORGE, head_sha=None, listed=(),
    )
    assert outcome.refused[0].reason == "ack_cap_exceeded"


def test_per_row_failure_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_resolve(thread_id: str) -> bool:
        return thread_id != "PRTI_bad"

    monkeypatch.setattr(_pr_threads, "resolve_review_thread", fake_resolve)
    rows = (
        CopilotResolutionRow(thread_id="PRTI_bad", mode="fix"),
        CopilotResolutionRow(thread_id="PRTI_good", mode="fix"),
    )
    outcome = resolution_mod.apply(
        rows, pr_number=13, forge_target=_WIDGET_FORGE, head_sha=None,
        listed=_listed("PRTI_bad", "PRTI_good"),
    )
    assert outcome.resolved == ("PRTI_good",)
    assert [r.thread_id for r in outcome.refused] == ["PRTI_bad"]


def test_apply_ack_cap_refusal_is_audited_with_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh(0))
    for i in range(resolution_mod.ACK_CAP):
        row = CopilotResolutionRow(thread_id=f"PRTI_c{i}", mode="acknowledge", body="x")
        resolution_mod.apply(
            (row,), pr_number=14, forge_target=_WIDGET_FORGE, head_sha="s", listed=(),
        )
    over_row = CopilotResolutionRow(thread_id="PRTI_over3", mode="acknowledge", body="x")
    resolution_mod.apply(
        (over_row,), pr_number=14, forge_target=_WIDGET_FORGE, head_sha="s", listed=(),
    )
    log = tmp_path / "logs" / _pr_audit_log.COPILOT_AUDIT_FILE
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    refusals = [r for r in rows if r["action"] == "copilot_resolution_refused"]
    assert refusals
    assert refusals[-1]["repo"] == "acme-corp/widget-app"
    assert refusals[-1]["reason"] == "ack_cap_exceeded"


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    ws = create("copilot-exec-task", "resolve threads", root=tmp_path / "ws")
    snapshot = EngineSnapshot(
        posture=Posture.propose,
        transition_graph={},
        config=EngineConfig(
            repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
            forge=ForgeConfig(
                repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
                credential_env_name="ACME_WIDGET_GH_TOKEN",
            ),
        ),
    )
    return workspace_mod.stamp_engine_snapshot(ws, snapshot)


def test_maybe_resolve_skips_when_no_block_attempted(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    called: list[object] = []
    monkeypatch.setattr(resolution_mod, "apply", lambda *a, **k: called.append(1))
    handle = WorktreeHandle(path=tmp_path, base_ref="a" * 40, branch="teams/x")
    resolution_mod.maybe_resolve(
        workspace, handle, "## Summary\nno block here", 42, _WIDGET_FORGE,
        role="implementation_fix", listed=(),
    )
    assert called == []


def test_maybe_resolve_applies_parsed_rows(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    applied: list[tuple[tuple[CopilotResolutionRow, ...], dict[str, object]]] = []

    def fake_apply(
        rows: tuple[CopilotResolutionRow, ...], **kwargs: object
    ) -> resolution_mod.CopilotResolutionOutcome:
        applied.append((rows, kwargs))
        return resolution_mod.CopilotResolutionOutcome((), (), ())

    monkeypatch.setattr(resolution_mod, "apply", fake_apply)

    def fake_git(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        assert cmd == ["git", "rev-parse", "HEAD"]
        return subprocess.CompletedProcess(cmd, 0, stdout="deadbeef\n", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_git)
    handle = WorktreeHandle(path=tmp_path, base_ref="a" * 40, branch="teams/x")
    synthesis = (
        "## Summary\ndone\n\n```yaml copilot_resolution.yaml\n"
        "- thread_id: PRTI_1\n  mode: fix\n```\n"
    )
    listed = _listed("PRTI_1")
    resolution_mod.maybe_resolve(
        workspace, handle, synthesis, 42, _WIDGET_FORGE, role="implementation_fix",
        listed=listed,
    )
    assert len(applied) == 1
    rows, kwargs = applied[0]
    assert rows[0].thread_id == "PRTI_1"
    assert kwargs["pr_number"] == 42
    assert kwargs["forge_target"] is _WIDGET_FORGE
    assert kwargs["head_sha"] == "deadbeef"
    assert kwargs["listed"] == listed


def test_maybe_resolve_skips_apply_when_allow_fix_false_filters_all_rows(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    called: list[object] = []
    monkeypatch.setattr(resolution_mod, "apply", lambda *a, **k: called.append(1))
    handle = WorktreeHandle(path=tmp_path, base_ref="a" * 40, branch="teams/x")
    synthesis = (
        "## Summary\ndone\n\n```yaml copilot_resolution.yaml\n"
        "- thread_id: PRTI_1\n  mode: fix\n```\n"
    )
    resolution_mod.maybe_resolve(
        workspace, handle, synthesis, 42, _WIDGET_FORGE,
        role="implementation_fix", allow_fix=False, listed=(),
    )
    assert called == []


def test_maybe_resolve_skips_apply_when_block_malformed(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    called: list[object] = []
    monkeypatch.setattr(resolution_mod, "apply", lambda *a, **k: called.append(1))
    handle = WorktreeHandle(path=tmp_path, base_ref="a" * 40, branch="teams/x")
    synthesis = "## Summary\n\n```yaml copilot_resolution.yaml\n[]\n```\n"
    resolution_mod.maybe_resolve(
        workspace, handle, synthesis, 42, _WIDGET_FORGE, role="implementation_fix", listed=(),
    )
    assert called == []


def test_current_head_sha_returns_none_on_nonzero_rc(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh(1))
    handle = WorktreeHandle(path=tmp_path, base_ref="a" * 40, branch="teams/x")
    assert resolution_mod._current_head_sha(handle) is None  # noqa: SLF001


def test_current_head_sha_returns_none_on_file_not_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    def boom(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del cmd, kwargs
        raise FileNotFoundError("git")
    monkeypatch.setattr(process_runner, "run_subprocess", boom)
    handle = WorktreeHandle(path=tmp_path, base_ref="a" * 40, branch="teams/x")
    assert resolution_mod._current_head_sha(handle) is None  # noqa: SLF001
