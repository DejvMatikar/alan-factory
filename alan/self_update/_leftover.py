from __future__ import annotations

from pathlib import Path

from alan.self_update import _journal, _ops, _rollback, _soak

_SCHEDULED_TERMINAL = frozenset({_journal.Phase.ROLLBACK_FAILED, _journal.Phase.ROLLING_BACK})


def needs_resolution(journal: _journal.Journal | None) -> bool:
    return journal is not None and journal.phase in _journal.LEFTOVER_PHASES


def _resoak(repo: Path, journal: _journal.Journal, *, plist_path: Path, health_url: str) -> None:
    _journal.write(journal)
    if _soak.run(journal.target_sha, health_url):
        _journal.write(journal.with_phase(_journal.Phase.LAST_GOOD))
    else:
        _rollback.perform(repo, journal, plist_path=plist_path, health_url=health_url)


def resolve(
    repo: Path, journal: _journal.Journal, *, scheduled: bool, plist_path: Path, health_url: str
) -> None:
    if journal.phase in _SCHEDULED_TERMINAL and scheduled:
        return
    if journal.phase is _journal.Phase.ROLLBACK_REFUSED_DIRTY and _ops.is_dirty(repo):
        return
    if journal.phase is _journal.Phase.SOAKING:
        _resoak(repo, journal, plist_path=plist_path, health_url=health_url)
        return
    _rollback.perform(repo, journal, plist_path=plist_path, health_url=health_url)
