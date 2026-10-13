from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from alan import self_update
from alan.self_update import (
    ApplyPath,
    Reason,
    Verdict,
    _extract,
    _idle,
    _journal,
    _leftover,
    _lock,
    _ops,
    _rollback,
    _soak,
)

_ROLLBACK_REASONS = frozenset({
    Reason.RENDER_FAILED,
    Reason.DEPENDENCY_SYNC_FAILED,
    Reason.WEB_BUILD_FAILED,
    Reason.TEARDOWN_FAILED,
    Reason.BOOTSTRAP_FAILED,
    Reason.KICKSTART_FAILED,
    Reason.VERIFICATION_TIMEOUT,
    Reason.VERIFICATION_MISMATCH,
})


def _trusted_commit(repo: Path) -> str:
    journal = _journal.read()
    if journal is not None and journal.phase in _journal.LEFTOVER_PHASES:
        return journal.previous_sha
    return _ops.git(repo, "rev-parse", "HEAD").strip()


def _apply_argv(
    repo: Path,
    plist_path: Path,
    health_url: str,
    *,
    scheduled: bool,
    pin: str | None,
    force: bool,
    queue_db_path: Path | None,
) -> list[str]:
    argv = [
        "apply", "--kit-repo", str(repo),
        "--plist-path", str(plist_path), "--health-url", health_url,
    ]
    if scheduled:
        argv.append("--scheduled")
    if queue_db_path is not None:
        argv += ["--queue-db-path", str(queue_db_path)]
    if pin is not None:
        argv += ["--sha", pin]
    if force:
        argv.append("--force")
    return argv


def trampoline(
    repo: Path,
    *,
    scheduled: bool,
    plist_path: Path,
    health_url: str,
    pin: str | None = None,
    force: bool = False,
    queue_db_path: Path | None = None,
) -> int:
    with _lock.acquire() as acquired:
        if acquired:
            repo = repo.absolute()
            plist_path = plist_path.absolute()
            commit = _trusted_commit(repo)
            argv = _apply_argv(
                repo, plist_path, health_url,
                scheduled=scheduled, pin=pin, force=force, queue_db_path=queue_db_path,
            )
            _extract.extract_and_exec(repo, commit, argv)
    return 0


def _applied_shas(record: self_update.StatusRecord) -> tuple[str, str]:
    if record.target_sha is None or record.previous_sha is None:
        raise RuntimeError("an applied or failed record must carry both commits")
    return record.target_sha, record.previous_sha


def _handle_applied(
    repo: Path, record: self_update.StatusRecord, *, plist_path: Path, health_url: str
) -> None:
    target_sha, previous_sha = _applied_shas(record)
    restarted = record.path is ApplyPath.RESTART
    phase = _journal.Phase.SOAKING if restarted else _journal.Phase.LAST_GOOD
    journal = _journal.Journal(phase, target_sha, previous_sha, record.previous_slot, restarted)
    _journal.write(journal)
    if not restarted:
        return
    if _soak.run(target_sha, health_url):
        _journal.write(journal.with_phase(_journal.Phase.LAST_GOOD))
    else:
        _rollback.perform(repo, journal, plist_path=plist_path, health_url=health_url)


def _handle_failed(
    repo: Path, record: self_update.StatusRecord, *, plist_path: Path, health_url: str
) -> None:
    target_sha, previous_sha = _applied_shas(record)
    journal = _journal.Journal(
        _journal.Phase.ROLLING_BACK,
        target_sha,
        previous_sha,
        record.previous_slot,
        record.path is ApplyPath.RESTART,
    )
    _rollback.perform(repo, journal, plist_path=plist_path, health_url=health_url)


def _pending_journal(
    repo: Path, *, pin: str | None, force: bool
) -> _journal.Journal | None:
    outcome, ctx = self_update.decide(repo, pin=pin, force=force)
    if outcome.verdict is not Verdict.GREEN or not ctx.fetched or not ctx.head:
        return None
    slot = _ops.previous_slot(_ops.web_dir(repo))
    return _journal.Journal(_journal.Phase.APPLYING, ctx.fetched, ctx.head, slot, True)


def _apply_selected(
    repo: Path, inputs: self_update.ApplyInputs, *, pin: str | None, force: bool
) -> self_update.StatusRecord:
    if pin is None and not force:
        return self_update.apply(repo, inputs)
    return self_update.apply(repo, inputs, pin=pin, force=force)


def _backfill_marker(repo: Path, health_url: str) -> None:
    if _ops.marker_path().is_file():
        return
    try:
        head = _ops.git(repo, "rev-parse", "HEAD").strip()
    except (subprocess.SubprocessError, OSError):
        return
    if _ops.health_ok(health_url, head):
        _ops.write_marker(head)


def _tick(
    repo: Path,
    *,
    plist_path: Path,
    health_url: str,
    idle_probe: Callable[[], bool],
    wait_bound_s: float,
    detached: bool,
    pin: str | None = None,
    force: bool = False,
) -> self_update.StatusRecord:
    inputs = self_update.ApplyInputs(
        detached=detached,
        idle_probe=idle_probe,
        wait_bound_s=wait_bound_s,
        health_url=health_url,
        plist_path=plist_path,
    )
    _backfill_marker(repo, health_url)
    pending = _pending_journal(repo, pin=pin, force=force)
    if pending is not None:
        _journal.write(pending)
    record = _apply_selected(repo, inputs, pin=pin, force=force)
    if record.verdict is Verdict.APPLIED:
        _handle_applied(repo, record, plist_path=plist_path, health_url=health_url)
    elif record.verdict is Verdict.FAILED and record.reason in _ROLLBACK_REASONS:
        _handle_failed(repo, record, plist_path=plist_path, health_url=health_url)
    elif pending is not None:
        _journal.clear()
    return record


def perform(
    repo: Path,
    *,
    scheduled: bool,
    plist_path: Path,
    health_url: str,
    idle_probe: Callable[[], bool],
    wait_bound_s: float,
    detached: bool,
    pin: str | None = None,
    force: bool = False,
) -> self_update.StatusRecord | None:
    journal = _journal.read()
    if journal is not None and _leftover.needs_resolution(journal):
        _leftover.resolve(
            repo, journal, scheduled=scheduled, plist_path=plist_path, health_url=health_url
        )
        return None
    return _tick(
        repo,
        plist_path=plist_path,
        health_url=health_url,
        idle_probe=idle_probe,
        wait_bound_s=wait_bound_s,
        detached=detached,
        pin=pin,
        force=force,
    )


def _scheduled_apply(
    repo: Path, *, plist_path: Path, health_url: str, queue_db_path: Path | None
) -> None:
    journal = _journal.read()
    if journal is not None and _leftover.needs_resolution(journal):
        perform(
            repo,
            scheduled=True,
            plist_path=plist_path,
            health_url=health_url,
            idle_probe=lambda: True,
            wait_bound_s=0.0,
            detached=True,
        )
        return
    bound = _idle.wait_bound_s(repo)
    if bound is None:
        return
    probe = _idle.idle_probe(queue_db_path) if queue_db_path is not None else (lambda: True)
    perform(
        repo,
        scheduled=True,
        plist_path=plist_path,
        health_url=health_url,
        idle_probe=probe,
        wait_bound_s=bound,
        detached=False,
    )


def main_apply(
    repo: Path,
    *,
    scheduled: bool,
    extracted: bool,
    plist_path: Path,
    health_url: str,
    idle_probe: Callable[[], bool],
    wait_bound_s: float,
    detached: bool,
    pin: str | None = None,
    force: bool = False,
    queue_db_path: Path | None = None,
) -> int:
    if not extracted:
        return trampoline(
            repo,
            scheduled=scheduled,
            plist_path=plist_path,
            health_url=health_url,
            pin=pin,
            force=force,
            queue_db_path=queue_db_path,
        )
    if scheduled:
        _scheduled_apply(
            repo, plist_path=plist_path, health_url=health_url, queue_db_path=queue_db_path
        )
        return 0
    perform(
        repo,
        scheduled=scheduled,
        plist_path=plist_path,
        health_url=health_url,
        idle_probe=idle_probe,
        wait_bound_s=wait_bound_s,
        detached=detached,
        pin=pin,
        force=force,
    )
    return 0
