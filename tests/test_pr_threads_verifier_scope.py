from __future__ import annotations

import subprocess

import pytest

from alan.engine_config import ForgeTarget, RepoIdentity
from alan.teams import _pr_threads
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, api_result
from tests.thread_forge_fake import (
    resolve_mutation_stdout,
    review_threads_stdout,
    thread_node,
)

_FORGE = ForgeTarget(
    repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
    credential_env_name="ACME_WIDGET_GH_TOKEN",  # noqa: S106  # nosec B106
)


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


def _queue_threads(forge: ForgeScript, nodes: list[dict[str, object]]) -> None:
    forge.queue("unresolved_threads", api_result(review_threads_stdout(nodes)))


def _fetch(pr_number: int = 7) -> tuple[_pr_threads.ReviewThread, ...] | None:
    return _pr_threads.fetch_unresolved_threads(pr_number, forge_target=_FORGE)


def test_fetch_unresolved_threads_returns_coerced_threads(forge: ForgeScript) -> None:
    _queue_threads(forge, [thread_node("t1", author_login="alan-verifier", total_count=1)])
    result = _fetch()
    assert result is not None
    assert len(result) == 1
    assert result[0].reply_count == 0
    assert result[0].author_login == "alan-verifier"


@pytest.mark.parametrize(
    "outcome",
    [
        FileNotFoundError("gh missing"),
        subprocess.TimeoutExpired(cmd="gh", timeout=1),
        api_result(returncode=1, stderr="boom"),
        api_result("not json"),
    ],
    ids=["missing-gh", "timeout", "nonzero-exit", "malformed-payload"],
)
def test_fetch_unresolved_threads_returns_none_on_any_read_failure(
    forge: ForgeScript, outcome: object,
) -> None:
    forge.queue("unresolved_threads", outcome)
    assert _fetch() is None


def test_fetch_unresolved_threads_reads_as_the_verifier_on_the_forge_repo(
    forge: ForgeScript,
) -> None:
    _queue_threads(forge, [])
    _fetch(9)
    assert forge.calls("unresolved_threads") == [
        ((9,), {"forge_target": _FORGE, "as_verifier": True}),
    ]


def test_reply_count_none_when_total_count_missing(forge: ForgeScript) -> None:
    _queue_threads(forge, [thread_node("t1", author_login="alan-verifier", total_count=None)])
    result = _fetch(1)
    assert result is not None
    assert result[0].reply_count is None


@pytest.mark.parametrize(("total_count", "expected"), [(1, 0), (4, 3)])
def test_reply_count_excludes_the_root_comment(
    forge: ForgeScript, total_count: int, expected: int,
) -> None:
    _queue_threads(
        forge, [thread_node("t1", author_login="alan-verifier", total_count=total_count)],
    )
    result = _fetch(1)
    assert result is not None
    assert result[0].reply_count == expected


def test_resolved_threads_are_dropped(forge: ForgeScript) -> None:
    _queue_threads(
        forge,
        [thread_node("t1", author_login="alan-verifier", total_count=1, is_resolved=True)],
    )
    assert _fetch(1) == ()


def test_resolve_thread_as_ok_when_resolved(forge: ForgeScript) -> None:
    forge.queue("resolve_thread", api_result(resolve_mutation_stdout(True)))
    outcome = _pr_threads.resolve_thread_as("thread-id", forge_target=_FORGE)
    assert outcome.ok
    assert outcome.error is None


def test_resolve_thread_as_fails_when_not_resolved(forge: ForgeScript) -> None:
    forge.queue("resolve_thread", api_result(resolve_mutation_stdout(False)))
    outcome = _pr_threads.resolve_thread_as("thread-id", forge_target=_FORGE)
    assert not outcome.ok
    assert outcome.error == "unexpected payload"


def test_resolve_thread_as_fails_on_nonzero_exit(forge: ForgeScript) -> None:
    forge.queue("resolve_thread", api_result(returncode=1, stderr="denied"))
    outcome = _pr_threads.resolve_thread_as("thread-id", forge_target=_FORGE)
    assert not outcome.ok
    assert outcome.error == "denied"


def test_resolve_thread_as_fails_on_exception(forge: ForgeScript) -> None:
    forge.queue("resolve_thread", subprocess.TimeoutExpired(cmd="gh", timeout=1))
    outcome = _pr_threads.resolve_thread_as("thread-id", forge_target=_FORGE)
    assert not outcome.ok
    assert outcome.error is not None


def test_resolve_thread_as_caps_error_length(forge: ForgeScript) -> None:
    forge.queue("resolve_thread", api_result(returncode=1, stderr="x" * 1000))
    outcome = _pr_threads.resolve_thread_as("thread-id", forge_target=_FORGE)
    assert outcome.error is not None
    assert len(outcome.error) == 500


def test_resolve_thread_as_resolves_as_the_verifier_on_the_forge_repo(
    forge: ForgeScript,
) -> None:
    forge.queue("resolve_thread", api_result(resolve_mutation_stdout(True)))
    _pr_threads.resolve_thread_as("thread-id", forge_target=_FORGE)
    assert forge.calls("resolve_thread") == [
        (("thread-id",), {"forge_target": _FORGE, "as_verifier": True}),
    ]
