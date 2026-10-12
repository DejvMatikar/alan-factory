from __future__ import annotations

from pathlib import Path

from alan import self_update, store
from alan.self_update import _journal, _ops

_CLEAN_EXCLUDES: tuple[str, ...] = ("alan/web/node_modules", ".venv", "alan/web/dist*")
_VERIFY_BOUND_S = 60.0
_VERIFY_POLL_S = 3.0


class _Dirty(Exception):
    pass


def _restore_checkout(repo: Path, previous_sha: str) -> None:
    _ops.git(repo, "checkout", "-fB", "main", previous_sha)
    _ops.git(repo, "reset", "--hard", previous_sha)
    args = ["clean", "-fdx"]
    for excluded in _CLEAN_EXCLUDES:
        args += ["-e", excluded]
    _ops.git(repo, *args)


def _restore_web_slot(repo: Path, journal: _journal.Journal) -> None:
    if journal.previous_slot is not None:
        _ops.activate_slot(repo, journal.previous_slot, journal.previous_sha)


def _restore_service(
    repo: Path, journal: _journal.Journal, plist_path: Path, health_url: str
) -> None:
    _ops.restart_service(repo, plist_path)
    verified = _ops.poll(
        lambda: _ops.health_ok(health_url, journal.previous_sha), _VERIFY_BOUND_S, _VERIFY_POLL_S
    )
    if not verified:
        raise RuntimeError("rollback verification timed out")
    _ops.write_marker(journal.previous_sha)


def _append_rejected(target_sha: str) -> None:
    path = self_update.rejected_path()
    try:
        existing = self_update.read_rejected()
    except (OSError, ValueError):
        existing = frozenset[str]()
    if target_sha in existing:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{sha}\n" for sha in sorted({*existing, target_sha}))
    store.write_text_atomic(path, body)


def perform(
    repo: Path, journal: _journal.Journal, *, plist_path: Path, health_url: str
) -> bool:
    try:
        if _ops.is_dirty(repo):
            raise _Dirty
        _journal.write(journal.with_phase(_journal.Phase.ROLLING_BACK))
        _restore_checkout(repo, journal.previous_sha)
        _restore_web_slot(repo, journal)
        if journal.restarted:
            _ops.sync_dependencies(repo)
            _restore_service(repo, journal, plist_path, health_url)
        _append_rejected(journal.target_sha)
    except _Dirty:
        _journal.write(journal.with_phase(_journal.Phase.ROLLBACK_REFUSED_DIRTY))
        return False
    except Exception:  # noqa: BLE001
        _journal.write(journal.with_phase(_journal.Phase.ROLLBACK_FAILED))
        return False
    _journal.write(journal.with_phase(_journal.Phase.ROLLED_BACK))
    return True
