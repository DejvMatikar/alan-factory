from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan import default_profile, secrets_store
from alan import unblock_recipes as recipes_mod
from alan.default_profile import UNCONFIGURED_PROFILE, Resolved, Source
from alan.engine_config import DEFAULT_FORGE_TARGET, ForgeTarget, RepoIdentity
from alan.teams import (
    _pr_feedback,
    _pr_post,
    _pr_ready,
    _pr_threads,
    _pr_waiver_dismissal,
    _verdict,
)
from alan.teams import _verifier_thread_resolution as vtr
from alan.teams._pr_branch import VerifierIdentity
from alan.teams._pr_json import _JsonObject  # pyright: ignore[reportPrivateUsage]
from alan.workspace import Workspace
from tests import _unblock_support as support
from tests import review_forge_fake as forge_fake
from tests._pr_branch_support import set_verifier_transport_token
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, api_result, login_read
from tests.thread_forge_fake import (
    resolve_mutation_stdout,
    review_threads_stdout,
    thread_node,
)

_ENV = "ALAN_VERIFIER_LOGIN"
_BOT = "alan-verifier"
_FORGE = ForgeTarget(
    repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
    credential_env_name="ACME_WIDGET_GH_TOKEN",  # noqa: S106  # nosec B106
)
_REPORTED = _verdict.CriticalCountReport(reviewer=0, judge_reported=True)


def _unconfigure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENV, raising=False)
    monkeypatch.setattr(
        default_profile,
        "resolved",
        lambda: Resolved(UNCONFIGURED_PROFILE, Source.unconfigured),
    )


@pytest.fixture(autouse=True)
def _no_verifier_login(monkeypatch: pytest.MonkeyPatch) -> None:
    _unconfigure(monkeypatch)
    set_verifier_transport_token(monkeypatch)


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    return support.parked_ws(tmp_path)


def _reviews(*lines: str) -> tuple[_JsonObject, ...]:
    return _pr_post.parse_review_objects("\n".join(lines))


def _stale_ctx(ws: Workspace, stream: str) -> recipes_mod.RecipeContext:
    return support.context(
        ws,
        latest=support.handover(ws),
        ready=support.readiness(review_decision="CHANGES_REQUESTED"),
        recipe_deps=support.deps(review_stream=stream),
    )


def test_no_review_is_a_verifier_verdict_in_the_unblock_recipes() -> None:
    reviews = _reviews(
        support.review_line(commit_id="abc"),
        support.review_line(commit_id="abc", review_id=2),
        support.review_line(commit_id="old", review_id=3),
    )
    assert recipes_mod.verifier_rejections_at_head(reviews, None, "abc") == 0
    assert recipes_mod.verifier_review_position(reviews, None, "abc") == "none"


def test_the_bots_changes_requested_review_counts_as_another_authors_request() -> None:
    reviews = _reviews(support.review_line(login=_BOT, state="CHANGES_REQUESTED"))
    assert recipes_mod.other_author_requested_changes(reviews, None) is True
    assert (
        recipes_mod.other_author_requested_changes(reviews, VerifierIdentity(_BOT)) is False
    )


def test_the_stale_review_recipe_never_treats_a_stale_review_as_the_verifiers(
    ws: Workspace,
) -> None:
    probe = next(r for r in recipes_mod.RECIPES if r.name == "stale_review").probe(
        _stale_ctx(ws, support.review_line(commit_id="old"))
    )
    assert probe == recipes_mod.declined("a human review requests changes")


@pytest.mark.parametrize("login", [None, ""])
def test_a_review_with_a_missing_or_empty_author_is_never_a_verifier_verdict(
    login: str | None,
) -> None:
    reviews = _reviews(support.review_line(login=login, commit_id="abc"))
    identity = VerifierIdentity(_BOT)
    assert recipes_mod.verifier_rejections_at_head(reviews, identity, "abc") == 0
    assert recipes_mod.verifier_review_position(reviews, identity, "abc") == "none"


def test_stale_verifier_reviews_are_not_dismissed_without_a_verifier_login(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    fake = forge_fake.ReviewForgeFake(
        reviews=[
            forge_fake.FakeReview(id=7, login=_BOT, state="CHANGES_REQUESTED", commit_id="old"),
        ],
    )
    forge_fake.install(fake, monkeypatch)
    _unconfigure(monkeypatch)
    dismissed = _pr_post.dismiss_stale_verifier_reviews(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET
    )
    assert dismissed is False
    assert fake.attempted_dismiss_ids == []
    assert fake.reviews[0].state == "CHANGES_REQUESTED"


def test_the_changes_requested_lookup_reads_no_reviews_without_a_verifier_login(
    _active_fake_forge: FakeForge,
) -> None:
    assert _pr_post._fetch_changes_requested_ids(42, DEFAULT_FORGE_TARGET, "head") == ()  # noqa: SLF001
    assert _active_fake_forge.calls == []


def test_a_waiver_dismisses_no_review_without_a_verifier_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = forge_fake.ReviewForgeFake(
        reviews=[forge_fake.FakeReview(id=11, login=_BOT, state="CHANGES_REQUESTED")],
    )
    forge_fake.install(fake, monkeypatch)
    _unconfigure(monkeypatch)
    result = _pr_waiver_dismissal.dismiss_for_waiver(
        42, forge_target=DEFAULT_FORGE_TARGET, question_id="q1", floor_reason=None,
    )
    assert result == _pr_waiver_dismissal.WaiverDismissal("none_found", (), True)
    assert fake.attempted_dismiss_ids == []


@pytest.mark.parametrize(
    "latest_reviews",
    [
        [],
        None,
        "not-a-list",
        [{"author": {"login": _BOT}, "state": "CHANGES_REQUESTED"}],
        [{"state": "CHANGES_REQUESTED"}],
        [{"author": {"login": ""}, "state": "CHANGES_REQUESTED"}],
    ],
)
def test_the_lane_never_clears_changes_requested_without_a_verifier_login(
    latest_reviews: object,
) -> None:
    payload = {"reviewDecision": "CHANGES_REQUESTED", "latestReviews": latest_reviews}
    assert _pr_ready._verifier_lane_review_decision(payload) == "CHANGES_REQUESTED"  # noqa: SLF001


@pytest.mark.parametrize(
    "latest_reviews",
    [
        [{"author": {"login": ""}, "state": "CHANGES_REQUESTED"}],
        [{"state": "CHANGES_REQUESTED"}],
    ],
)
def test_the_lane_treats_an_authorless_review_as_human_with_a_verifier_login(
    latest_reviews: list[dict[str, object]],
) -> None:
    identity = VerifierIdentity(_BOT)
    assert _pr_ready._human_changes_requested(latest_reviews, identity) is True  # noqa: SLF001


def test_only_the_repository_owner_is_exempt_from_the_stranger_filter() -> None:
    assert _pr_feedback._skip_logins(_FORGE) == frozenset({"acme-corp"})  # noqa: SLF001


def test_a_configured_login_joins_the_stranger_filter_exemption_casefolded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENV, "Custom-Reviewer")
    assert _pr_feedback._skip_logins(_FORGE) == frozenset(  # noqa: SLF001
        {"acme-corp", "custom-reviewer"}
    )


@pytest.mark.parametrize("login", [_BOT, "Alan-Verifier"])
def test_the_bot_account_is_a_stranger_thread_candidate_without_a_verifier_login(
    login: str,
) -> None:
    info = _pr_feedback.ThreadInfo(
        copilot_threads=(),
        human_unresolved=1,
        other_bot_unresolved=0,
        total=1,
        human_threads=(_pr_feedback.HumanThread("T1", login, 1),),
    )
    candidates = _pr_feedback._stranger_candidates(info, _FORGE)  # noqa: SLF001
    assert [t.login for t in candidates] == [login]


def test_the_repository_owner_is_still_exempt_from_the_stranger_filter() -> None:
    info = _pr_feedback.ThreadInfo(
        copilot_threads=(),
        human_unresolved=1,
        other_bot_unresolved=0,
        total=1,
        human_threads=(_pr_feedback.HumanThread("T1", "Acme-Corp", 1),),
    )
    assert _pr_feedback._stranger_candidates(info, _FORGE) == []  # noqa: SLF001


def test_the_identity_check_holds_without_a_verifier_login_and_asks_the_forge_nothing(
    forge: ForgeScript,
) -> None:
    result = vtr._confirm_identity(_FORGE)  # noqa: SLF001
    assert result.reason == "verifier_login_unconfigured"
    assert result.observed_login is None
    assert forge.calls("viewer_login") == []


def test_no_thread_is_a_verifier_thread_without_a_verifier_login() -> None:
    threads = (
        _pr_threads.ReviewThread("t1", _BOT, "finding", "alan/foo.py", 1, False, 0),
        _pr_threads.ReviewThread("t2", "", "finding", "alan/foo.py", 1, False, 0),
    )
    assert vtr._verifier_threads(threads) == ()  # noqa: SLF001


def test_a_configured_login_selects_only_exact_author_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENV, _BOT)
    mine = _pr_threads.ReviewThread("t1", _BOT, "finding", "alan/foo.py", 1, False, 0)
    others = (
        _pr_threads.ReviewThread("t2", "", "finding", "alan/foo.py", 1, False, 0),
        _pr_threads.ReviewThread("t3", "Alan-Verifier", "finding", "alan/foo.py", 1, False, 0),
        _pr_threads.ReviewThread("t4", "someone-else", "finding", "alan/foo.py", 1, False, 0),
    )
    assert vtr._verifier_threads((mine, *others)) == (mine,)  # noqa: SLF001


def test_an_approval_resolves_no_thread_without_a_verifier_login(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    nodes = [thread_node("t1", author_login=_BOT, total_count=1)]
    forge.queue("viewer_login", login_read(_BOT))
    forge.queue("unresolved_threads", api_result(review_threads_stdout(nodes)))
    forge.queue("resolve_thread", api_result(resolve_mutation_stdout(True)))
    monkeypatch.setattr(secrets_store, "verifier_gh_env", lambda _t: {"GH_TOKEN": "verifier-tok"})
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    vtr.resolve_after_approval(
        "APPROVE", 0, 42, task_id="t1", inv_id="inv1", forge_target=_FORGE, counts=_REPORTED,
    )
    rows = [
        json.loads(line)
        for line in (tmp_path / "logs" / "verification_audit.jsonl").read_text().splitlines()
    ]
    assert [(r["action"], r["reason"]) for r in rows] == [
        (vtr.HELD_ACTION, "verifier_login_unconfigured")
    ]
    assert forge.calls("viewer_login") == []
    assert forge.calls("unresolved_threads") == []
    assert forge.calls("resolve_thread") == []
