from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from alan import self_update
from alan.self_update import _run

_DEFAULT_HEALTH_URL = "http://127.0.0.1:8585/api/health"
_VALUE_FLAGS = ("--kit-repo", "--plist-path", "--health-url", "--sha", "--queue-db-path")
_BOOL_FLAGS = ("--scheduled", "--dry-run", "--force")


class _ParsedApply(NamedTuple):
    repo: Path
    extracted: bool
    scheduled: bool
    dry_run: bool
    force: bool
    plist_path: Path
    health_url: str
    sha: str | None
    queue_db_path: Path | None


def _scan(argv: list[str]) -> tuple[dict[str, str], frozenset[str]] | None:
    values: dict[str, str] = {}
    flags: set[str] = set()
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in _VALUE_FLAGS:
            if index + 1 >= len(argv):
                return None
            values[token] = argv[index + 1]
            index += 2
        elif token in _BOOL_FLAGS:
            flags.add(token)
            index += 1
        else:
            return None
    return values, frozenset(flags)


def _scheduling_flags(
    flags: frozenset[str], values: dict[str, str]
) -> tuple[bool, bool, str | None] | None:
    scheduled = "--scheduled" in flags
    force = "--force" in flags
    sha = values.get("--sha")
    if scheduled and (force or sha is not None):
        return None
    return scheduled, force, sha


def _parse_apply(argv: list[str]) -> _ParsedApply | None:
    scanned = _scan(argv)
    if scanned is None:
        return None
    values, flags = scanned
    plist_value = values.get("--plist-path")
    if plist_value is None:
        return None
    scheduling = _scheduling_flags(flags, values)
    if scheduling is None:
        return None
    scheduled, force, sha = scheduling
    kit_repo = values.get("--kit-repo")
    queue_db = values.get("--queue-db-path")
    return _ParsedApply(
        repo=Path(kit_repo) if kit_repo is not None else Path.cwd(),
        extracted=kit_repo is not None,
        scheduled=scheduled,
        dry_run="--dry-run" in flags,
        force=force,
        plist_path=Path(plist_value),
        health_url=values.get("--health-url", _DEFAULT_HEALTH_URL),
        sha=sha,
        queue_db_path=Path(queue_db) if queue_db is not None else None,
    )


def main(argv: list[str]) -> int:
    if not argv or argv[0] != "apply":
        return 2
    parsed = _parse_apply(argv[1:])
    if parsed is None:
        return 2
    if parsed.dry_run:
        self_update.check(parsed.repo)
        return 0
    return _run.main_apply(
        parsed.repo,
        scheduled=parsed.scheduled,
        extracted=parsed.extracted,
        plist_path=parsed.plist_path,
        health_url=parsed.health_url,
        idle_probe=lambda: True,
        wait_bound_s=0.0,
        detached=True,
        pin=parsed.sha,
        force=parsed.force,
        queue_db_path=parsed.queue_db_path,
    )
