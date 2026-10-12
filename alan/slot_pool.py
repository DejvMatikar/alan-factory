from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from alan import fleet_load, worker_records
from alan.clock import SYSTEM_CLOCK, Clock, Monotonic
from alan.engine_config import _DEFAULT as _ENGINE_DEFAULT  # pyright: ignore[reportPrivateUsage]
from alan.engine_config import ForgeTarget, resolve_cache_env
from alan.roots import slot_repo_root
from alan.secrets_store import GIT_CRED_RESET_ARGS, MissingSecretError, transport_env

logger = logging.getLogger(__name__)

_GIT_TIMEOUT_S = 30.0
_BOOTSTRAP_TIMEOUT_S = 300.0
_STALE_LEASE_TIMEOUT_S: float = 8.0 * 3600
_RESERVATION_TTL_S: float = 3600.0
_SLOT_WAIT_WARN_THRESHOLD_S: float = 60.0
_LOAD_DEFER_MAX_S: float = 60.0
_LOAD_TICK_S: float = 1.0


class SlotReservedError(RuntimeError):
    pass
_REMOTE_URL_RE = re.compile(r"^(?:https?|git|ssh)://|^git@")

_active_pool_var: ContextVar[SlotPool | None] = ContextVar(
    "_active_pool_var", default=None
)


def is_remote_url(value: str) -> bool:
    return bool(_REMOTE_URL_RE.match(value))


def get_active_pool() -> SlotPool | None:
    return _active_pool_var.get()


def set_active_pool(pool: SlotPool | None) -> None:
    _active_pool_var.set(pool)


def _run_git(
    args: list[str], cwd: Path, *, extra_env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    argv = ["git", *args]
    if extra_env is not None:
        env.update(extra_env)
        argv = ["git", *GIT_CRED_RESET_ARGS, *args]
    return subprocess.run(  # noqa: S603
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
        env=env,
    )


def is_git_clone(path: Path) -> bool:
    return (path / ".git").is_dir()


def _clean_slot(path: Path) -> None:
    # `reset --hard` not `restore .`: `restore` rebuilds the worktree from the index,
    # so STAGED edits a crashed/killed worker left behind survive — and the next
    # `git checkout -B` then aborts ("local changes would be overwritten"). A hard
    # reset drops both staged and unstaged tracked changes.
    # `-fd` not `-fdx`: preserves gitignored .venv/node_modules so warm-boot is fast.
    # Bytecache (.pyc) is safe to keep — Python re-validates via mtime.
    _run_git(["reset", "--hard"], path)
    _run_git(["clean", "-fd"], path)


def _fetch_base(path: Path, forge_target: ForgeTarget | None) -> str:
    extra_env = transport_env(forge_target) if forge_target is not None else None
    result = _run_git(["fetch", "origin", "main"], path, extra_env=extra_env)
    if result.returncode == 0:
        return "origin/main"
    logger.warning("slot fetch origin main failed (rc=%s)", result.returncode)
    head = _run_git(["rev-parse", "HEAD"], path)
    sha = (head.stdout or "").strip()
    if head.returncode == 0 and sha:
        return sha
    logger.warning("slot HEAD unresolvable; using origin/main as fallback")
    return "origin/main"


def _fetch_existing_branch(path: Path, branch: str, forge_target: ForgeTarget | None) -> str:
    extra_env = transport_env(forge_target) if forge_target is not None else None
    result = _run_git(["fetch", "origin", branch], path, extra_env=extra_env)
    if result.returncode == 0:
        return f"origin/{branch}"
    logger.warning(
        "slot fetch origin %s failed (rc=%s); using local ref", branch, result.returncode
    )
    return branch


def _prepare_new_branch(
    path: Path, task_id: str, inv_id: str,
    forge_target: ForgeTarget | None,
) -> tuple[str, str]:
    base = _fetch_base(path, forge_target)
    _clean_slot(path)
    reset_result = _run_git(["reset", "--hard", base], path)
    if reset_result.returncode != 0:
        msg = f"git reset --hard failed (rc={reset_result.returncode})"
        raise RuntimeError(f"{msg}: {(reset_result.stderr or '')[:300]}")
    branch = f"teams/{task_id}-{inv_id}"
    checkout_result = _run_git(["checkout", "-B", branch], path)
    if checkout_result.returncode != 0:
        msg = f"git checkout -B failed (rc={checkout_result.returncode})"
        raise RuntimeError(f"{msg}: {(checkout_result.stderr or '')[:300]}")
    return _head_sha(path), branch


def _head_sha(path: Path) -> str:
    result = _run_git(["rev-parse", "HEAD"], path)
    return (result.stdout or "").strip() or "HEAD"


def _rescue_branch_name(task_id: str, inv_id: str) -> str:
    safe_task = re.sub(r"[^A-Za-z0-9_-]", "-", task_id)
    safe_inv = re.sub(r"[^A-Za-z0-9_-]", "-", inv_id)
    return f"rescue/{safe_task}@{safe_inv}"


def _count_commits_ahead(path: Path, base_sha: str, branch: str) -> int:
    verify = _run_git(["rev-parse", "--verify", branch], path)
    if verify.returncode != 0:
        return 0
    result = _run_git(["rev-list", "--count", f"{base_sha}..{branch}"], path)
    if result.returncode != 0:
        return 0
    try:
        return int((result.stdout or "").strip())
    except ValueError:
        return 0


def _anchor_rescue_ref(path: Path, branch: str, rescue: str) -> None:
    result = _run_git(["branch", "-f", rescue, branch], path)
    if result.returncode != 0:
        msg = f"git branch -f {rescue!r} failed (rc={result.returncode})"
        raise RuntimeError(f"{msg}: {(result.stderr or '')[:300]}")


def _validate_branch_arg(existing_branch: str | None) -> None:
    if existing_branch is not None and existing_branch.startswith("-"):
        raise ValueError(f"branch name must not start with '-': {existing_branch!r}")


def _fetch_for_resume(
    path: Path, existing_branch: str | None,
    forge_target: ForgeTarget | None,
) -> None:
    if existing_branch is not None:
        _fetch_existing_branch(path, existing_branch, forge_target)
    else:
        _fetch_base(path, forge_target)


def _fallback_prepare(
    path: Path, task_id: str, inv_id: str, existing_branch: str | None,
    forge_target: ForgeTarget | None,
) -> tuple[str, str]:
    if existing_branch is None:
        return _prepare_new_branch(path, task_id, inv_id, forge_target)
    return _prepare_existing_branch(path, existing_branch, forge_target)


def _prepare_resume(
    path: Path,
    task_id: str,
    inv_id: str,
    existing_branch: str | None,
    base_sha: str,
    forge_target: ForgeTarget | None,
) -> tuple[str, str, int, str | None]:
    branch = existing_branch or f"teams/{task_id}-{inv_id}"
    n = _count_commits_ahead(path, base_sha, branch)
    if n == 0:
        fb_base, fb_branch = _fallback_prepare(
            path, task_id, inv_id, existing_branch, forge_target
        )
        return fb_base, fb_branch, 0, None
    rescue = _rescue_branch_name(task_id, inv_id)
    _anchor_rescue_ref(path, branch, rescue)
    _fetch_for_resume(path, existing_branch, forge_target)
    _clean_slot(path)
    co = _run_git(["checkout", branch], path)
    if co.returncode == 0:
        return base_sha, branch, n, None
    fb_base, fb_branch = _fallback_prepare(
        path, task_id, inv_id, existing_branch, forge_target
    )
    return fb_base, fb_branch, 0, rescue


def _refresh_base_best_effort(path: Path, forge_target: ForgeTarget | None) -> None:
    try:
        extra_env = transport_env(forge_target) if forge_target is not None else None
        result = _run_git(["fetch", "origin", "main"], path, extra_env=extra_env)
    except (FileNotFoundError, subprocess.TimeoutExpired, MissingSecretError):
        logger.warning("slot base refresh failed for %s", path)
        return
    if result.returncode != 0:
        logger.warning("slot base refresh failed for %s (rc=%s)", path, result.returncode)


def _prepare_existing_branch(
    path: Path, branch: str,
    forge_target: ForgeTarget | None,
) -> tuple[str, str]:
    start = _fetch_existing_branch(path, branch, forge_target)
    _clean_slot(path)
    checkout_result = _run_git(["checkout", "-B", branch, start], path)
    if checkout_result.returncode != 0:
        msg = f"git checkout -B failed (rc={checkout_result.returncode})"
        raise RuntimeError(f"{msg}: {(checkout_result.stderr or '')[:300]}")
    _refresh_base_best_effort(path, forge_target)
    return _head_sha(path), branch


def _warmup_command_env(
    cache_env: dict[str, str], forge_target: ForgeTarget | None
) -> dict[str, str]:
    env = dict(os.environ)
    if forge_target is not None:
        env.update(transport_env(forge_target))
    env.update(resolve_cache_env(cache_env))
    return env


def _with_npm_ci_offline_flag(argv: list[str]) -> list[str]:
    if argv[0] == "npm" and "ci" in argv and "--prefer-offline" not in argv:
        return [*argv, "--prefer-offline"]
    return argv


def _run_warmup_command(
    path: Path,
    argv: list[str],
    cache_env: dict[str, str],
    forge_target: ForgeTarget | None,
) -> None:
    if not argv:
        return
    argv = _with_npm_ci_offline_flag(argv)
    label = " ".join(argv)
    try:
        result = subprocess.run(  # noqa: S603
            argv,
            cwd=str(path),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=_BOOTSTRAP_TIMEOUT_S,
            check=False,
            env=_warmup_command_env(cache_env, forge_target),
        )
        if result.returncode != 0:
            logger.warning(
                "slot warmup command failed (rc=%s) %r: %s",
                result.returncode, label, (result.stderr or "")[:300],
            )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("slot warmup command raised %r: %s", label, exc)


def _run_warmup(
    path: Path,
    commands: list[list[str]],
    cache_env: dict[str, str],
    forge_target: ForgeTarget | None,
) -> None:
    for argv in commands:
        _run_warmup_command(path, argv, cache_env, forge_target)


def _clone_env_and_argv(
    repo_root: str, path: Path, forge_target: ForgeTarget | None
) -> tuple[dict[str, str], list[str]]:
    if forge_target is None:
        return (
            {**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            ["git", "clone", "--", repo_root, str(path)],
        )
    return (
        {**os.environ, **transport_env(forge_target)},
        ["git", *GIT_CRED_RESET_ARGS, "clone", "--", repo_root, str(path)],
    )


def _init_slot(
    path: Path,
    repo_root: str,
    warmup_commands: list[list[str]],
    cache_env: dict[str, str],
    forge_target: ForgeTarget | None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    env, argv = _clone_env_and_argv(repo_root, path, forge_target)
    result = subprocess.run(  # noqa: S603,S607
        argv,
        capture_output=True,
        text=True,
        timeout=_BOOTSTRAP_TIMEOUT_S,
        check=False,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git clone failed (rc={result.returncode}): {(result.stderr or '')[:300]}"
        )
    _run_warmup(path, warmup_commands, cache_env, forge_target)


def resolve_repo_root() -> str | None:
    raw = slot_repo_root()
    if raw:
        return raw
    try:
        result = subprocess.run(  # noqa: S603,S607
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
        top = (result.stdout or "").strip()
        if result.returncode == 0 and top:
            return top
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


class PoolBusyError(RuntimeError):
    pass


class PoolDisposeIncompleteError(RuntimeError):
    pass


@dataclass(frozen=True)
class PoolState:
    slug: str
    size: int
    free_count: int
    leased_count: int
    warm: bool
    quiescing: bool
    disk_usage_bytes: int


@dataclass(frozen=True, slots=True)
class Reservation:
    token: str
    adoptable: bool


def _split_reservations(
    by_idx: dict[int, Reservation],
) -> tuple[dict[int, str], dict[int, str]]:
    redeemable = {idx: r.token for idx, r in by_idx.items() if r.adoptable}
    clean_excluded = {idx: r.token for idx, r in by_idx.items()}
    return redeemable, clean_excluded


def _resolve_warmup_commands(warmup_commands: list[list[str]] | None) -> list[list[str]]:
    if warmup_commands is not None:
        return [list(cmd) for cmd in warmup_commands]
    return [list(cmd) for cmd in _ENGINE_DEFAULT.warmup_commands]


def _resolve_pool_cache_env(cache_env: dict[str, str] | None) -> dict[str, str]:
    return dict(cache_env) if cache_env is not None else dict(_ENGINE_DEFAULT.cache_env)


def _resolve_forge_target_for_pool(
    forge_target: ForgeTarget | None, repo_root: str
) -> ForgeTarget | None:
    return forge_target if is_remote_url(repo_root) else None


class SlotPool:
    def __init__(
        self,
        root: Path,
        size: int,
        repo_root: str,
        warmup_commands: list[list[str]] | None = None,
        forge_target: ForgeTarget | None = None,
        cache_env: dict[str, str] | None = None,
        on_warmup_measured: Callable[[float], None] | None = None,
        *,
        clock: Clock = SYSTEM_CLOCK,
        reserved: Mapping[Path, Reservation] | None = None,
        load_sampler: Callable[[], float] | None = None,
    ) -> None:
        if size <= 0:
            raise ValueError(f"SlotPool size must be positive, got {size}")
        self._root = root
        self._size = size
        self._repo_root = repo_root
        self._warmup_commands = _resolve_warmup_commands(warmup_commands)
        self._cache_env = _resolve_pool_cache_env(cache_env)
        self._on_warmup_measured = on_warmup_measured
        self._forge_target = _resolve_forge_target_for_pool(forge_target, repo_root)
        self._clock = clock
        self._load_sampler = load_sampler
        self._cond = threading.Condition()
        self._lease_times: dict[int, Monotonic] = {}
        self._preserve_info: dict[int, tuple[int, str | None]] = {}
        reserved_by_idx = self._resolve_reserved(reserved)
        self._reserved, self._clean_excluded = _split_reservations(reserved_by_idx)
        self._reserved_at = dict.fromkeys(self._reserved, clock.monotonic())
        self._free: set[int] = set(range(size)) - set(self._reserved)
        self._quiescing = False
        self._warm = False

    def slot_path(self, idx: int) -> Path:
        return self._root / f"slot_{idx}"

    def mark_warm(self) -> None:
        with self._cond:
            self._warm = True

    @property
    def warm(self) -> bool:
        with self._cond:
            return self._warm

    def disk_usage(self) -> int:
        if not self._root.is_dir():
            return 0
        return sum(p.stat().st_size for p in self._root.rglob("*") if p.is_file())

    def snapshot(self, slug: str) -> PoolState:
        with self._cond:
            free_count = len(self._free)
            warm = self._warm
            quiescing = self._quiescing
        return PoolState(
            slug=slug,
            size=self._size,
            free_count=free_count,
            leased_count=self._size - free_count,
            warm=warm,
            quiescing=quiescing,
            disk_usage_bytes=self.disk_usage(),
        )

    def begin_dispose(self) -> None:
        # Invariant: no clone dir is removed while any lease token is live —
        # leases must be confirmed drained (free_count == size) before the
        # caller proceeds to finish_dispose()'s rmtree. _lease_times is
        # checked directly (not only through the free-set proxy) so a
        # bookkeeping bug can never green-light a dispose under a live lease;
        # free_count < size with no leases means reset_all is mid-clean, which
        # this refuses, while reset_all's own quiescing bail-out covers the
        # reverse order — the rmtree/_clean_slot exclusion holds both ways.
        with self._cond:
            if self._lease_times:
                raise PoolBusyError(f"pool at {self._root} has active leases")
            if len(self._free) < self._size:
                raise PoolBusyError(f"pool at {self._root} has slot cleanup in flight")
            self._quiescing = True

    def finish_dispose(self) -> None:
        if self._root.exists():
            shutil.rmtree(self._root, ignore_errors=True)
        if self._root.exists():
            raise PoolDisposeIncompleteError(
                f"failed to fully remove pool directory {self._root}"
            )

    def _raise_if_quiescing(self) -> None:
        if self._quiescing:
            raise PoolBusyError(f"pool at {self._root} is quiescing (being disposed)")

    def reset_all(
        self, reserved: Mapping[Path, Reservation] | None = None, *, clean: bool = True
    ) -> None:
        # Invariant: a slot with a live lease is never cleaned or re-freed —
        # register_pool publishes the pool before its daemon-thread warmup,
        # so a lease claimed in that window must survive reset_all untouched.
        # Slots picked for cleaning leave _free inside the same lock section
        # that snapshots the leases (no claim can slip in between) and stay
        # out of it while _clean_slot runs, so a concurrent claim cannot grab
        # a clone mid-clean and begin_dispose refuses that window; the
        # quiescing bail-out below covers the reverse order (dispose already
        # begun). The re-free is a finally: cleaning is best-effort and a
        # dirty-but-usable slot beats one wedged out of _free forever.
        # A `reserved` slot gets the identical no-window treatment: excluded
        # from the cleaning batch inside this same lock section, and — only
        # when adoptable — also pulled from `_free`. `reserved=None` leaves
        # `_reserved`/`_clean_excluded` as-is (only an explicit mapping,
        # including an empty one, is authoritative over them).
        with self._cond:
            if self._quiescing:
                return
            if reserved is not None:
                self._apply_reserved_under_lock(reserved)
            to_clean = self._to_clean_under_lock() if clean else []
            self._free -= set(to_clean)
            self._free -= set(self._reserved)
        try:
            for i in to_clean:
                _clean_slot(self.slot_path(i))
        finally:
            with self._cond:
                self._free |= set(to_clean)
                self._clean_excluded.clear()
                self._cond.notify_all()

    def _is_under_root(self, path: Path) -> bool:
        try:
            path.relative_to(self._root)
        except ValueError:
            return False
        return True

    def _apply_reserved_under_lock(self, reserved: Mapping[Path, Reservation]) -> None:
        reserved_by_idx = self._resolve_reserved(reserved)
        redeemable, clean_excluded = _split_reservations(reserved_by_idx)
        resolved_paths = {self.slot_path(i) for i in reserved_by_idx}
        unresolved = {
            path for path in set(reserved) - resolved_paths if self._is_under_root(path)
        }
        released = set(self._reserved) - set(redeemable)
        self._reserved = redeemable
        self._clean_excluded = clean_excluded
        now = self._clock.monotonic()
        self._reserved_at = dict.fromkeys(redeemable, now)
        self._free |= released - set(self._lease_times)
        logger.info(
            "slot pool %s: %d reservation(s) requested, %d resolved to this pool",
            self._root, len(reserved), len(reserved_by_idx),
        )
        if unresolved:
            logger.warning(
                "slot pool %s: %d reservation(s) did not resolve to a slot in this pool: %s",
                self._root, len(unresolved),
                ", ".join(str(p) for p in sorted(unresolved)),
            )

    def _to_clean_under_lock(self) -> list[int]:
        unleased = set(range(self._size)) - set(self._lease_times) - set(self._clean_excluded)
        return [i for i in sorted(unleased) if is_git_clone(self.slot_path(i))]

    def _resolve_reserved(
        self, reserved: Mapping[Path, Reservation] | None
    ) -> dict[int, Reservation]:
        if not reserved:
            return {}
        by_path = {self.slot_path(i): i for i in range(self._size)}
        return {
            by_path[path]: reservation
            for path, reservation in reserved.items()
            if path in by_path
        }

    @contextmanager
    def lease(
        self,
        task_id: str,
        inv_id: str,
        *,
        existing_branch: str | None = None,
        preferred_idx: int | None = None,
        resume_base_ref: str | None = None,
        on_blocked: Callable[[], None] | None = None,
    ) -> Generator[tuple[Path, str, str, int], None, None]:
        _validate_branch_arg(existing_branch)
        is_resume = preferred_idx is not None and resume_base_ref is not None
        idx, token = self._claim(preferred_idx, on_blocked)
        slot_path = self.slot_path(idx)
        try:
            if not is_git_clone(slot_path):
                start = self._clock.monotonic()
                _init_slot(
                    slot_path, self._repo_root, self._warmup_commands,
                    self._cache_env, self._forge_target,
                )
                self._safe_measure_warmup(self._clock.monotonic() - start)
            if is_resume:
                base_ref, branch, n_pres, rescue = _prepare_resume(
                    slot_path, task_id, inv_id, existing_branch, cast(str, resume_base_ref),
                    self._forge_target,
                )
                self._preserve_info[idx] = (n_pres, rescue)
            elif existing_branch is None:
                base_ref, branch = _prepare_new_branch(
                    slot_path, task_id, inv_id, self._forge_target
                )
                self._preserve_info.pop(idx, None)
            else:
                base_ref, branch = _prepare_existing_branch(
                    slot_path, existing_branch, self._forge_target
                )
                self._preserve_info.pop(idx, None)
            yield slot_path, base_ref, branch, idx
        finally:
            self._release(idx, token)

    def get_preserve_info(self, idx: int) -> tuple[int, str | None]:
        return self._preserve_info.get(idx, (0, None))

    def _safe_measure_warmup(self, elapsed: float) -> None:
        if self._on_warmup_measured is None:
            return
        try:
            self._on_warmup_measured(elapsed)
        except Exception:  # noqa: BLE001 - measurement sink failure must not fail slot creation
            logger.exception("on_warmup_measured callback raised")

    def _claim(
        self,
        preferred_idx: int | None = None,
        on_blocked: Callable[[], None] | None = None,
    ) -> tuple[int, Monotonic]:
        with self._cond:
            self._raise_if_quiescing()
            # Admission control, not affinity: a buried host defers every claim
            # equally, so this deliberately never fires `on_blocked` — that callback
            # means "resumed session waiting for its own pinned slot", not "host load
            # is too high to admit any claim".
            self._await_load_under_lock()
            if preferred_idx is not None and 0 <= preferred_idx < self._size:
                return self._claim_preferred_under_lock(preferred_idx, on_blocked)
            if preferred_idx is not None:
                logger.warning(
                    "slot affinity %d out of range [0,%d); falling back to any free slot",
                    preferred_idx, self._size,
                )
            self._await_free_slot_under_lock()
            idx = self._free.pop()
            return idx, self._stamp_lease_under_lock(idx)

    def _await_free_slot_under_lock(self) -> None:
        # Caller must hold self._cond.
        start = self._clock.monotonic()
        warned = False
        while not self._free:
            self._cond.wait(timeout=1.0)
            self._raise_if_quiescing()
            self._reclaim_stale_under_lock()
            self._expire_reservations_under_lock()
            elapsed = self._clock.monotonic() - start
            if not warned and elapsed > _SLOT_WAIT_WARN_THRESHOLD_S:
                warned = True
                logger.warning(
                    "slot pool %s: claim blocked for over %.0fs waiting for a free "
                    "slot (%d/%d free)",
                    self._root, elapsed, len(self._free), self._size,
                )

    def _await_load_under_lock(self) -> None:
        # Caller must hold self._cond.
        if self._load_sampler is None:
            return
        threshold = fleet_load.load_threshold()
        load = self._load_sampler()
        if load <= threshold:
            return
        cores = fleet_load.host_cores()
        logger.warning(
            "slot pool %s: deferring claim — host load %.2f exceeds threshold %.2f "
            "(cores=%d)",
            self._root, load, threshold, cores,
        )
        start = self._clock.monotonic()
        while load > threshold:
            elapsed = self._clock.monotonic() - start
            if elapsed >= _LOAD_DEFER_MAX_S:
                logger.warning(
                    "slot pool %s: host load still above threshold %.2f (cores=%d) "
                    "after %.0fs; admitting claim anyway",
                    self._root, threshold, cores, elapsed,
                )
                return
            self._cond.wait(timeout=_LOAD_TICK_S)
            self._raise_if_quiescing()
            self._reclaim_stale_under_lock()
            self._expire_reservations_under_lock()
            load = self._load_sampler()

    def _claim_preferred_under_lock(
        self, idx: int, on_blocked: Callable[[], None] | None = None
    ) -> tuple[int, Monotonic]:
        self._raise_if_quiescing()
        notified = False
        while idx not in self._free:
            if not notified:
                notified = True
                if on_blocked is not None:
                    # `on_blocked` runs caller-supplied I/O (e.g. emitting an SSE
                    # event); it must run with `self._cond` released so a slow
                    # callback can't stall every other thread's claim/release.
                    self._cond.release()
                    try:
                        on_blocked()
                    finally:
                        self._cond.acquire()
                logger.warning(
                    "slot affinity: preferred slot %d is held; resumed session "
                    "waiting for it to free up",
                    idx,
                )
            self._cond.wait(timeout=1.0)
            self._raise_if_quiescing()
            self._reclaim_stale_under_lock()
            self._expire_reservations_under_lock()
        self._free.discard(idx)
        return idx, self._stamp_lease_under_lock(idx)

    def _stamp_lease_under_lock(self, idx: int) -> Monotonic:
        # The stamp doubles as the lease's identity token (compared by equality
        # in _release): a clock that does not advance between two stamps of the
        # same slot yields colliding tokens, so callers must use an
        # explicit-advance clock in tests.
        token = self._clock.monotonic()
        self._lease_times[idx] = token
        return token

    def _release(self, idx: int, token: Monotonic) -> None:
        # Only free the slot when our token matches — guards against a worker that
        # was stale-reclaimed and then un-hangs issuing a late _release that would
        # evict the re-leased slot from under the new holder.
        with self._cond:
            if self._lease_times.get(idx) == token:
                del self._lease_times[idx]
                self._free.add(idx)
                self._cond.notify_all()

    def _reclaim_stale_under_lock(self) -> None:
        # Caller must hold self._cond.
        now = self._clock.monotonic()
        stale = [
            idx for idx, t in self._lease_times.items()
            if now - t > _STALE_LEASE_TIMEOUT_S
        ]
        for idx in stale:
            logger.warning(
                "slot %d stale lease reclaimed after %.0fs — worker may have crashed",
                idx, now - self._lease_times[idx],
            )
            del self._lease_times[idx]
            self._free.add(idx)

    def _session_still_live(self, session_id: str) -> bool:
        record = worker_records.load_one(session_id)
        return record is not None and worker_records.reservation_is_live(record)

    def _expire_one_reservation_under_lock(self, idx: int, now: Monotonic) -> None:
        # Caller must hold self._cond. A concurrent adopt()/release_reservation()
        # on a DIFFERENT idx, run during another idx's unlocked probe window
        # below, can delete this idx's entries before we get here — so this
        # lookup cannot assume presence the way `self._reserved[idx]` would.
        session_id = self._reserved.get(idx)
        if session_id is None:
            return
        # The liveness probe can shell out to `ps` (up to its own 5s
        # timeout); released here so it can never stall unrelated
        # claim/release calls on the pool, then re-checked against the
        # still-held reservation below since it may have changed while
        # the lock was released.
        self._cond.release()
        try:
            still_live = self._session_still_live(session_id)
        finally:
            self._cond.acquire()
        if self._reserved.get(idx) != session_id:
            return
        if still_live:
            # A reservation whose record still probes live re-arms rather
            # than expiring: attach arrives through job dispatch, which
            # can queue behind fleet concurrency, so the boot-to-attach
            # window is not bounded by the TTL above.
            self._reserved_at[idx] = now
            return
        logger.warning(
            "slot %d reservation expired after %.0fs — no adoption or "
            "termination arrived",
            idx, now - self._reserved_at[idx],
        )
        del self._reserved[idx]
        del self._reserved_at[idx]
        self._free.add(idx)

    def _expire_reservations_under_lock(self) -> None:
        # Caller must hold self._cond.
        now = self._clock.monotonic()
        expired = [
            idx for idx, t in self._reserved_at.items()
            if now - t > _RESERVATION_TTL_S
        ]
        for idx in expired:
            self._expire_one_reservation_under_lock(idx, now)

    def _idx_for_path(self, slot_path: Path) -> int | None:
        for i in range(self._size):
            if self.slot_path(i) == slot_path:
                return i
        return None

    def adopt(self, slot_path: Path, owner_token: str) -> int:
        with self._cond:
            idx = self._idx_for_path(slot_path)
            if idx is None or self._reserved.get(idx) != owner_token:
                raise SlotReservedError(
                    f"no matching reservation for {slot_path} owned by {owner_token!r}"
                )
            del self._reserved[idx]
            del self._reserved_at[idx]
            self._stamp_lease_under_lock(idx)
        return idx

    def release_reservation(self, slot_path: Path) -> None:
        with self._cond:
            idx = self._idx_for_path(slot_path)
            if idx is None or idx not in self._reserved:
                return
            del self._reserved[idx]
            del self._reserved_at[idx]
            self._free.add(idx)
            self._cond.notify_all()

    @contextmanager
    def adopted_lease(
        self, slot_path: Path, owner_token: str
    ) -> Generator[int, None, None]:
        idx = self.adopt(slot_path, owner_token)
        with self._cond:
            token = self._lease_times[idx]
        # adopt() holds self._cond for its whole body, and a 30s network fetch
        # inside it would stall every concurrent claim/release — so this
        # best-effort refresh runs after adopt() returns, outside the lock.
        _refresh_base_best_effort(slot_path, self._forge_target)
        try:
            yield idx
        finally:
            self._release(idx, token)


__all__ = [
    "SlotReservedError",
    "PoolBusyError",
    "PoolDisposeIncompleteError",
    "PoolState",
    "Reservation",
    "SlotPool",
    "get_active_pool",
    "is_git_clone",
    "is_remote_url",
    "resolve_repo_root",
    "set_active_pool",
]
