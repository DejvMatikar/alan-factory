from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path

import pytest

import alan.forge as forge_mod
from alan import unblock_ledger as ledger
from alan import unblock_sweep as sweep_mod
from alan.engine_config import RepoIdentity
from alan.events import PubSub
from alan.forge_gh import GhForge
from alan.queue import Queue
from alan.teams import _pr_flake_ledger, triage_pregate, unblock_issue
from alan.workspace_index import WorkspaceIndex
from tests import _unblock_support as support
from tests._pr_branch_support import _WIDGET_FORGE
from tests.fakes.forge import installed_fake_forge
from tests.forge_stubs import always, rows_read

_TITLE = "Unblocker: a stale verifier review blocked the merge (acme-corp/widget-app)"
_REPO = RepoIdentity(owner="acme", repo="widget")


@pytest.fixture(autouse=True)
def _unblock_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sweep_mod.DISABLE_ENV, raising=False)


def _installed() -> object:
    return forge_mod._active_forge  # pyright: ignore[reportPrivateUsage]


def _exit_failure(body: Callable[[], object]) -> str:
    before = _installed()
    with (
        pytest.raises(AssertionError) as failure,
        installed_fake_forge(),
        contextlib.suppress(AssertionError),
    ):
        body()
    assert _installed() is before
    return str(failure.value)


def _seed_intent(queue: Queue) -> None:
    ledger.ensure_schema(queue)
    ledger.record(
        queue,
        ledger.Entry(
            workspace_key="ws-1", scope_ref="pr:1", shape="stale_review", fingerprint="fp",
            kind=ledger.KIND_ISSUE_INTENT, engine_key=None,
            detail={"slug": "o/r", "title": "T", "issue_body": "B", "comment": "C"},
        ),
    )


def test_an_unstubbed_triage_read_swallowed_by_the_production_catch_still_fails_on_exit() -> None:
    reads: list[list[dict[str, object]]] = []
    message = _exit_failure(lambda: reads.append(triage_pregate._fetch_raw_prs()))  # noqa: SLF001
    assert reads == [[]]
    assert "prs_by_head_prefix" in message


def test_the_adapter_backed_create_label_default_fires_from_the_unblocker() -> None:
    message = _exit_failure(lambda: unblock_issue.ensure_label(forge_target=_WIDGET_FORGE))
    assert "create_label" in message


def test_a_drain_that_swallows_a_scoped_unstubbed_call_still_fails_on_exit(
    queue: Queue,
) -> None:
    _seed_intent(queue)
    delivered: list[int] = []
    message = _exit_failure(
        lambda: delivered.append(sweep_mod.drain_issue_intents(queue, unblock_issue))
    )
    assert delivered == [0]
    assert "open_issues_by_title" in message
    assert len(ledger.pending_issue_intents(queue, 5)) == 1


def test_a_sweep_using_its_default_issue_client_fails_on_exit_when_the_forge_is_unstubbed(
    queue: Queue, tmp_path: Path,
) -> None:
    _seed_intent(queue)
    index = WorkspaceIndex(root=tmp_path / "ws", queue=queue)
    message = _exit_failure(
        lambda: sweep_mod.sweep(
            queue=queue, pubsub=PubSub(), workspace_index=index, workspaces_root=tmp_path / "ws",
            now=0.0, lock_for=support.lock_for, enqueue_workflow=support.never_enqueue,
        )
    )
    assert "open_issues_by_title" in message


def test_an_adapter_backed_default_reached_from_an_unscoped_module_exits_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(GhForge, "create_label", lambda _self, *_a, **_k: True)
    before = _installed()
    with installed_fake_forge() as fake:
        _pr_flake_ledger._ensure_flake_label(forge_target=_WIDGET_FORGE)  # noqa: SLF001
        assert [name for name, _args, _kwargs in fake.calls] == ["create_label"]
        assert fake.scoped_unstubbed == []
    assert _installed() is before


def test_an_explicitly_stubbed_operation_stays_silent_from_a_scoped_module() -> None:
    before = _installed()
    with installed_fake_forge() as fake:
        always(fake, "open_issues_by_title", rows_read([{"number": 4, "title": _TITLE}]))
        always(fake, "create_label", True)
        lookup = unblock_issue.find_open_issue(_TITLE, forge_target=_WIDGET_FORGE)
        unblock_issue.ensure_label(forge_target=_WIDGET_FORGE)
        assert lookup == unblock_issue.IssueLookup(ok=True, number=4)
        assert fake.scoped_unstubbed == []
    assert _installed() is before


def test_an_unstubbed_call_from_a_test_module_keeps_the_plain_message_and_records_nothing() -> None:
    with installed_fake_forge() as fake:
        with pytest.raises(AssertionError, match=r"^unstubbed forge call: prs_by_head_prefix$"):
            fake.prs_by_head_prefix(
                "teams/", ("number",), forge_target=_WIDGET_FORGE,
                state="all", limit=1, timeout=1.0,
            )
        assert fake.scoped_unstubbed == []


def test_an_unstubbed_adapter_backed_call_from_a_scoped_module_raises_the_scoped_message() -> None:
    with installed_fake_forge() as fake:
        with pytest.raises(AssertionError, match=r"^unstubbed forge call from a scoped path: "):
            unblock_issue.ensure_label(forge_target=_WIDGET_FORGE)
        assert fake.scoped_unstubbed == ["create_label"]
        fake.scoped_unstubbed.clear()


def test_the_exit_check_does_not_mask_a_failing_body() -> None:
    before = _installed()
    with pytest.raises(RuntimeError, match="body failed"), installed_fake_forge() as fake:
        with contextlib.suppress(AssertionError):
            unblock_issue.ensure_label(forge_target=_WIDGET_FORGE)
        assert fake.scoped_unstubbed == ["create_label"]
        raise RuntimeError("body failed")
    assert _installed() is before


_SCOPED_CALLS: dict[str, Callable[[], object]] = {
    "open_issues_by_title": lambda: unblock_issue.find_open_issue(
        _TITLE, forge_target=_WIDGET_FORGE
    ),
    "create_numbered_issue": lambda: unblock_issue.create_issue(
        _TITLE, "body", forge_target=_WIDGET_FORGE
    ),
    "comment_on_issue": lambda: unblock_issue.comment(1, "body", forge_target=_WIDGET_FORGE),
    "prs_by_head_prefix": lambda: triage_pregate._fetch_open_pr_rollups(),  # noqa: SLF001
    "latest_branch_run": lambda: triage_pregate._latest_main_run(  # noqa: SLF001
        _REPO, "tests.yml"
    ),
}


@pytest.mark.parametrize("name", sorted(_SCOPED_CALLS))
def test_each_new_operation_fires_when_unstubbed_and_reached_from_a_scoped_caller(
    name: str,
) -> None:
    assert name in _exit_failure(_SCOPED_CALLS[name])
