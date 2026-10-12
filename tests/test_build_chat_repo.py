from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import alan.build_chat as bc
from alan.build_chat import (
    BuildChatMessage,
    RepoProvisionStatus,
    append_user_message_and_mark_started,
    create_session,
    delete_session,
    load_session,
    reap_stale_repo_checkouts,
    repo_dir,
    session_dir,
    set_repo_provision,
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "bc-sessions"


def _age_session(root: Path, session_id: str, ts: datetime) -> None:
    target = session_dir(session_id, root=root) / "session.json"
    raw = json.loads(target.read_text())
    raw["created_at"] = ts.isoformat()
    target.write_text(json.dumps(raw), encoding="utf-8")


def test_repo_dir_is_session_dir_slash_repo(root: Path) -> None:
    s = create_session(root=root)
    assert repo_dir(s.session_id, root=root) == session_dir(s.session_id, root=root) / "repo"


def test_fresh_session_defaults_to_unchecked(root: Path) -> None:
    s = create_session(root=root)
    assert s.repo_provision == RepoProvisionStatus.UNCHECKED
    loaded = load_session(s.session_id, root=root)
    assert loaded.repo_provision == RepoProvisionStatus.UNCHECKED


def test_set_repo_provision_persists_and_returns_updated(root: Path) -> None:
    s = create_session(root=root)
    updated = set_repo_provision(s.session_id, RepoProvisionStatus.READY, root=root)
    assert updated.repo_provision == RepoProvisionStatus.READY
    reloaded = load_session(s.session_id, root=root)
    assert reloaded.repo_provision == RepoProvisionStatus.READY


def test_delete_session_removes_repo_dir_with_the_tree(root: Path) -> None:
    s = create_session(root=root)
    set_repo_provision(s.session_id, RepoProvisionStatus.READY, root=root)
    repo_dir(s.session_id, root=root).mkdir(parents=True)
    (repo_dir(s.session_id, root=root) / "marker").write_text("x")

    delete_session(s.session_id, root=root)

    assert not session_dir(s.session_id, root=root).exists()


class TestReapStaleRepoCheckouts:
    def test_reaps_stale_ready_session(self, root: Path) -> None:
        s = create_session(root=root)
        set_repo_provision(s.session_id, RepoProvisionStatus.READY, root=root)
        repo_dir(s.session_id, root=root).mkdir(parents=True)
        (repo_dir(s.session_id, root=root) / "marker").write_text("x")
        _age_session(root, s.session_id, datetime.now(UTC) - timedelta(hours=25))

        reaped = reap_stale_repo_checkouts(root=root, now=datetime.now(UTC))

        assert reaped == [s.session_id]
        assert not repo_dir(s.session_id, root=root).exists()
        reloaded = load_session(s.session_id, root=root)
        assert reloaded.repo_provision == RepoProvisionStatus.UNCHECKED

    def test_reaps_stale_error_session(self, root: Path) -> None:
        s = create_session(root=root)
        set_repo_provision(s.session_id, RepoProvisionStatus.ERROR, root=root)
        _age_session(root, s.session_id, datetime.now(UTC) - timedelta(hours=25))

        reaped = reap_stale_repo_checkouts(root=root, now=datetime.now(UTC))

        assert reaped == [s.session_id]
        reloaded = load_session(s.session_id, root=root)
        assert reloaded.repo_provision == RepoProvisionStatus.UNCHECKED

    def test_reap_uses_last_message_timestamp_when_transcript_is_nonempty(
        self, root: Path
    ) -> None:
        s = create_session(root=root)
        old_ts = datetime.now(UTC) - timedelta(hours=25)
        msg = BuildChatMessage(role="user", content="hi", ts=old_ts)
        append_user_message_and_mark_started(s, msg, root=root)
        set_repo_provision(s.session_id, RepoProvisionStatus.READY, root=root)

        reaped = reap_stale_repo_checkouts(root=root, now=datetime.now(UTC))

        assert reaped == [s.session_id]

    def test_leaves_under_ttl_session_untouched(self, root: Path) -> None:
        s = create_session(root=root)
        set_repo_provision(s.session_id, RepoProvisionStatus.READY, root=root)

        reaped = reap_stale_repo_checkouts(root=root, now=datetime.now(UTC))

        assert reaped == []
        reloaded = load_session(s.session_id, root=root)
        assert reloaded.repo_provision == RepoProvisionStatus.READY

    @pytest.mark.parametrize(
        "status",
        [
            RepoProvisionStatus.UNCHECKED,
            RepoProvisionStatus.PENDING,
            RepoProvisionStatus.NOT_NEEDED,
        ],
    )
    def test_leaves_non_reapable_statuses_untouched_even_when_stale(
        self, root: Path, status: RepoProvisionStatus
    ) -> None:
        s = create_session(root=root)
        set_repo_provision(s.session_id, status, root=root)
        _age_session(root, s.session_id, datetime.now(UTC) - timedelta(hours=25))

        reaped = reap_stale_repo_checkouts(root=root, now=datetime.now(UTC))

        assert reaped == []
        reloaded = load_session(s.session_id, root=root)
        assert reloaded.repo_provision == status

    def test_no_dir(self, root: Path) -> None:
        assert reap_stale_repo_checkouts(root=root) == []

    def test_defaults_now_to_current_time(self, root: Path) -> None:
        s = create_session(root=root)
        set_repo_provision(s.session_id, RepoProvisionStatus.READY, root=root)
        _age_session(root, s.session_id, datetime.now(UTC) - timedelta(hours=25))

        reaped = bc.reap_stale_repo_checkouts(root=root)

        assert reaped == [s.session_id]

    def test_reaps_stale_session_older_than_the_list_cap(self, root: Path) -> None:
        stale = create_session(root=root)
        set_repo_provision(stale.session_id, RepoProvisionStatus.READY, root=root)
        _age_session(root, stale.session_id, datetime.now(UTC) - timedelta(hours=25))
        for _ in range(25):
            create_session(root=root)

        reaped = reap_stale_repo_checkouts(root=root, now=datetime.now(UTC))

        assert reaped == [stale.session_id]
