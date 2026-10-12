from __future__ import annotations

import base64
import http.client
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple, cast

from alan import (
    deploy_refresh,
    deploy_restart,
    process_runner,
    roots,
    self_update_github,
    store,
)
from alan.self_update import _origin

_MAIN_REF = "refs/heads/main"
_SYMBOLIC_REF_RETURN_CODES = frozenset({0, 1})
_WORKFLOW_PATH = ".github/workflows/tests.yml"
_GATE_NAME = "gate"
_ACTIONS_APP_SLUG = "github-actions"
_COMPLETED = "completed"
_SUCCESS = "success"
_GIT_TIMEOUT_S = 60.0
_GIT_ENV: Mapping[str, str] = {"GIT_TERMINAL_PROMPT": "0"}
_FETCH_TIMEOUT_S = 300.0
_FETCH_USER = "x-access-token"
_FETCH_BASE_ENV: Mapping[str, str | None] = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "",
    "SSH_ASKPASS": "",
    "GIT_CONFIG_PARAMETERS": "",
    "GIT_ALLOW_PROTOCOL": "https",
    "GIT_TRACE": "",
    "GIT_TRACE_CURL": "",
    "GIT_TRACE_PACKET": "",
    "GIT_TRACE2": "",
    "GIT_TRACE2_EVENT": "",
    "GIT_TRACE2_PERF": "",
    "GIT_TRACE_REDACT": "1",
    "GIT_CURL_VERBOSE": None,
    "GIT_SSL_NO_VERIFY": None,
}
_REWRITE_PROBE_ENV: Mapping[str, str | None] = {**_FETCH_BASE_ENV, "GIT_CONFIG_COUNT": "0"}
_STATUS_FILE = "self_update_status.json"
_REJECTED_FILE = "self_update_rejected"
_SHA_RE = re.compile(r"\A[0-9a-f]{40}\Z")
_SERVICE_LABEL = "ai.alan"
_MARKER_FILE = "restarted_sha"
_SLOTS = ("dist.a", "dist.b")
_DEPLOY_SHA_FILE = ".deploy_sha"
_LAUNCHCTL_TIMEOUT_S = 15.0
_RENDER_TIMEOUT_S = 60.0
_SYNC_TIMEOUT_S = 900.0
_NPM_TIMEOUT_S = 900.0
_SWAP_TIMEOUT_S = 60.0
_HEALTH_TIMEOUT_S = 5.0
_UNLOAD_BOUND_S = 15.0
_UNLOAD_POLL_S = 1.0
_VERIFY_BOUND_S = 60.0
_VERIFY_POLL_S = 3.0
_IDLE_POLL_S = 5.0
_HEALTH_BODY_LIMIT = 1024 * 1024
_monotonic = time.monotonic
_sleep = time.sleep
_urlopen = urllib.request.urlopen


class Verdict(StrEnum):
    BLOCKED = "blocked"
    UP_TO_DATE = "up_to_date"
    PENDING = "pending"
    RED = "red"
    ERROR = "error"
    GREEN = "green"
    APPLIED = "applied"
    FAILED = "failed"


class ApplyPath(StrEnum):
    RESTART = "restart"
    RESTART_FREE = "restart_free"


class Reason(StrEnum):
    SHALLOW_CLONE = "shallow_clone"
    NOT_ON_MAIN = "not_on_main"
    REJECTED_COMMIT = "rejected_commit"
    DIRTY_TREE = "dirty_tree"
    NOT_FAST_FORWARD = "not_fast_forward"
    NO_GITHUB_ORIGIN = "no_github_origin"
    ALREADY_CURRENT = "already_current"
    NO_PUSH_RUN = "no_push_run"
    RUN_IN_PROGRESS = "run_in_progress"
    GATE_IN_PROGRESS = "gate_in_progress"
    RUN_NOT_SUCCESSFUL = "run_not_successful"
    NO_QUALIFYING_GATE = "no_qualifying_gate"
    GATE_NOT_SUCCESSFUL = "gate_not_successful"
    FETCH_FAILED = "fetch_failed"
    GIT_FAILED = "git_failed"
    REJECTED_LIST_UNREADABLE = "rejected_list_unreadable"
    GITHUB_READ_FAILED = "github_read_failed"
    CI_PROVED_GREEN = "ci_proved_green"
    SERVICE_PROBE_FAILED = "service_probe_failed"
    FAST_FORWARD_FAILED = "fast_forward_failed"
    RENDER_FAILED = "render_failed"
    DEPENDENCY_SYNC_FAILED = "dependency_sync_failed"
    WEB_BUILD_FAILED = "web_build_failed"
    TEARDOWN_FAILED = "teardown_failed"
    BOOTSTRAP_FAILED = "bootstrap_failed"
    KICKSTART_FAILED = "kickstart_failed"
    VERIFICATION_TIMEOUT = "verification_timeout"
    VERIFICATION_MISMATCH = "verification_mismatch"
    TARGET_VERIFIED = "target_verified"
    PIN_NOT_ON_MAIN = "pin_not_on_main"
    OPERATOR_FORCED = "operator_forced"


ALLOWED: Mapping[Verdict, frozenset[Reason]] = {
    Verdict.BLOCKED: frozenset({
        Reason.SHALLOW_CLONE,
        Reason.NOT_ON_MAIN,
        Reason.REJECTED_COMMIT,
        Reason.DIRTY_TREE,
        Reason.NOT_FAST_FORWARD,
        Reason.NO_GITHUB_ORIGIN,
        Reason.PIN_NOT_ON_MAIN,
    }),
    Verdict.UP_TO_DATE: frozenset({Reason.ALREADY_CURRENT}),
    Verdict.PENDING: frozenset({
        Reason.NO_PUSH_RUN,
        Reason.RUN_IN_PROGRESS,
        Reason.GATE_IN_PROGRESS,
    }),
    Verdict.RED: frozenset({
        Reason.RUN_NOT_SUCCESSFUL,
        Reason.NO_QUALIFYING_GATE,
        Reason.GATE_NOT_SUCCESSFUL,
    }),
    Verdict.ERROR: frozenset({
        Reason.FETCH_FAILED,
        Reason.GIT_FAILED,
        Reason.REJECTED_LIST_UNREADABLE,
        Reason.GITHUB_READ_FAILED,
        Reason.SERVICE_PROBE_FAILED,
    }),
    Verdict.GREEN: frozenset({Reason.CI_PROVED_GREEN, Reason.OPERATOR_FORCED}),
    Verdict.APPLIED: frozenset({Reason.TARGET_VERIFIED}),
    Verdict.FAILED: frozenset({
        Reason.FAST_FORWARD_FAILED,
        Reason.RENDER_FAILED,
        Reason.DEPENDENCY_SYNC_FAILED,
        Reason.WEB_BUILD_FAILED,
        Reason.TEARDOWN_FAILED,
        Reason.BOOTSTRAP_FAILED,
        Reason.KICKSTART_FAILED,
        Reason.VERIFICATION_TIMEOUT,
        Reason.VERIFICATION_MISMATCH,
    }),
}


def _sha_or_none(value: object) -> bool:
    return value is None or (isinstance(value, str) and bool(_SHA_RE.fullmatch(value)))


def _apply_path_or_none(value: object) -> bool:
    return value is None or type(value) is ApplyPath


def _slot_or_none(value: object) -> bool:
    return value is None or value in _SLOTS


@dataclass(frozen=True)
class StatusRecord:
    verdict: Verdict
    reason: Reason
    fetched_sha: str | None
    head_sha: str | None
    checked_at: str
    path: ApplyPath | None = None
    target_sha: str | None = None
    previous_sha: str | None = None
    previous_slot: str | None = None

    def __post_init__(self) -> None:
        if self.reason not in ALLOWED[self.verdict]:
            raise ValueError(f"reason {self.reason} is not allowed for verdict {self.verdict}")
        valid = {
            "path": _apply_path_or_none(self.path),
            "target_sha": _sha_or_none(self.target_sha),
            "previous_sha": _sha_or_none(self.previous_sha),
            "previous_slot": _slot_or_none(self.previous_slot),
        }
        invalid = [name for name, ok in valid.items() if not ok]
        if invalid:
            raise ValueError(f"invalid apply fields: {invalid}")

    def as_json(self) -> dict[str, str | None]:
        return {
            "verdict": self.verdict.value,
            "reason": self.reason.value,
            "fetched_sha": self.fetched_sha,
            "head_sha": self.head_sha,
            "checked_at": self.checked_at,
            "path": self.path.value if self.path is not None else None,
            "target_sha": self.target_sha,
            "previous_sha": self.previous_sha,
            "previous_slot": self.previous_slot,
        }


@dataclass(frozen=True)
class _Outcome:
    verdict: Verdict
    reason: Reason


@dataclass
class _Ctx:
    repo: Path
    head: str = ""
    fetched: str = ""
    owner: str = ""
    name: str = ""


def _blocked(reason: Reason) -> _Outcome:
    return _Outcome(Verdict.BLOCKED, reason)


def status_path() -> Path:
    return roots.deploy_marker_root() / _STATUS_FILE


def rejected_path() -> Path:
    return roots.deploy_marker_root() / _REJECTED_FILE


def read_rejected() -> frozenset[str]:
    try:
        text = rejected_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return frozenset()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not all(_SHA_RE.fullmatch(line) for line in lines):
        raise ValueError("malformed rejected list")
    return frozenset(lines)


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return process_runner.run_subprocess(
        ["git", "-C", str(repo), *args], cwd=None, timeout=_GIT_TIMEOUT_S, env=_GIT_ENV
    )


def _git(repo: Path, *args: str) -> str:
    result = _run_git(repo, *args)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, ["git", *args])
    return result.stdout


def _step_shallow(ctx: _Ctx) -> _Outcome | None:
    try:
        deploy_refresh.assert_not_shallow(ctx.repo)
    except deploy_refresh.ShallowRepositoryError:
        return _blocked(Reason.SHALLOW_CLONE)
    return None


def _step_branch(ctx: _Ctx) -> _Outcome | None:
    head = _run_git(ctx.repo, "rev-parse", "--verify", "-q", "HEAD")
    ctx.head = head.stdout.strip() if head.returncode == 0 else ""
    ref = _run_git(ctx.repo, "symbolic-ref", "-q", "HEAD")
    if ref.returncode not in _SYMBOLIC_REF_RETURN_CODES:
        return _Outcome(Verdict.ERROR, Reason.GIT_FAILED)
    if ref.stdout.strip() != _MAIN_REF:
        return _blocked(Reason.NOT_ON_MAIN)
    if not _SHA_RE.fullmatch(ctx.head):
        return _Outcome(Verdict.ERROR, Reason.GIT_FAILED)
    return None


def _fetch_url(ctx: _Ctx) -> str:
    return _origin.slot_repo_url(ctx.owner, ctx.name)


def _basic_header(token: str) -> str:
    encoded = base64.b64encode(f"{_FETCH_USER}:{token}".encode()).decode("ascii")
    return f"Authorization: Basic {encoded}"


def _fetch_env(url: str, token: str) -> dict[str, str | None]:
    key = f"http.{url}.extraHeader"
    return {
        **_FETCH_BASE_ENV,
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": key,
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": key,
        "GIT_CONFIG_VALUE_1": _basic_header(token),
    }


def _fetch_argv(repo: Path, url: str) -> list[str]:
    return [
        "git",
        "-C",
        str(repo),
        "-c",
        "credential.helper=",
        "-c",
        "core.askPass=",
        "fetch",
        "--no-tags",
        "--no-write-fetch-head",
        "--refmap=",
        url,
        f"+{_MAIN_REF}:{deploy_refresh.TARGET_REF}",
    ]


def _url_is_not_rewritten(repo: Path, url: str) -> bool:
    result = process_runner.run_subprocess(
        ["git", "-C", str(repo), "ls-remote", "--get-url", url],
        cwd=None,
        timeout=_GIT_TIMEOUT_S,
        env=_REWRITE_PROBE_ENV,
    )
    return result.returncode == 0 and result.stdout.strip() == url


def _credentialed_fetch(ctx: _Ctx) -> str:
    token = self_update_github.transport_token(ctx.owner, ctx.name)
    if token is None:
        return ""
    url = _fetch_url(ctx)
    env = _fetch_env(url, token)
    if not _url_is_not_rewritten(ctx.repo, url):
        return ""
    result = process_runner.run_subprocess(
        _fetch_argv(ctx.repo, url), cwd=None, timeout=_FETCH_TIMEOUT_S, env=env
    )
    if result.returncode != 0:
        return ""
    return _git(ctx.repo, "rev-parse", deploy_refresh.TARGET_REF).strip()


def _step_fetch(ctx: _Ctx) -> _Outcome | None:
    try:
        fetched = _credentialed_fetch(ctx)
    except (subprocess.SubprocessError, OSError, ValueError):
        fetched = ""
    if not _SHA_RE.fullmatch(fetched):
        return _Outcome(Verdict.ERROR, Reason.FETCH_FAILED)
    ctx.fetched = fetched
    return None


def _step_up_to_date(ctx: _Ctx) -> _Outcome | None:
    if ctx.head == ctx.fetched:
        return _Outcome(Verdict.UP_TO_DATE, Reason.ALREADY_CURRENT)
    return None


def _step_rejected(ctx: _Ctx) -> _Outcome | None:
    try:
        rejected = read_rejected()
    except (OSError, ValueError):
        return _Outcome(Verdict.ERROR, Reason.REJECTED_LIST_UNREADABLE)
    if ctx.fetched in rejected:
        return _blocked(Reason.REJECTED_COMMIT)
    return None


def _step_dirty(ctx: _Ctx) -> _Outcome | None:
    status = _git(
        ctx.repo, "--no-optional-locks", "status", "--porcelain", "--untracked-files=normal"
    )
    if status.strip():
        return _blocked(Reason.DIRTY_TREE)
    return None


def _step_ancestry(ctx: _Ctx) -> _Outcome | None:
    try:
        deploy_refresh.assert_ancestry(ctx.repo, ctx.head, ctx.fetched)
    except (deploy_refresh.PushedShaAbsentError, deploy_refresh.NotContainedError):
        return _blocked(Reason.NOT_FAST_FORWARD)
    return None


def _step_origin(ctx: _Ctx) -> _Outcome | None:
    parsed = _origin.resolve_origin(ctx.repo)
    if parsed is None:
        return _blocked(Reason.NO_GITHUB_ORIGIN)
    ctx.owner, ctx.name = parsed
    return None


_STEPS: tuple[Callable[[_Ctx], _Outcome | None], ...] = (
    _step_shallow,
    _step_branch,
    _step_origin,
    _step_fetch,
    _step_up_to_date,
    _step_rejected,
    _step_dirty,
    _step_ancestry,
)


def _step_pin(ctx: _Ctx, pin: str) -> _Outcome | None:
    if not _SHA_RE.fullmatch(pin):
        return _blocked(Reason.PIN_NOT_ON_MAIN)
    try:
        deploy_refresh.assert_ancestry(ctx.repo, ctx.head, pin)
        deploy_refresh.assert_ancestry(ctx.repo, pin, ctx.fetched)
    except (deploy_refresh.PushedShaAbsentError, deploy_refresh.NotContainedError):
        return _blocked(Reason.PIN_NOT_ON_MAIN)
    ctx.fetched = pin
    return None


def _skip_step(step: Callable[[_Ctx], _Outcome | None], *, force: bool) -> bool:
    return force and step is _step_rejected


def _after_step(
    ctx: _Ctx, step: Callable[[_Ctx], _Outcome | None], pin: str | None
) -> _Outcome | None:
    if pin is None or step is not _step_fetch:
        return None
    return _step_pin(ctx, pin)


def _run_steps(ctx: _Ctx, *, pin: str | None, force: bool) -> _Outcome | None:
    for step in _STEPS:
        if _skip_step(step, force=force):
            continue
        outcome = step(ctx) or _after_step(ctx, step, pin)
        if outcome is not None:
            return outcome
    return None


def _positive_int(value: object) -> int:
    return value if type(value) is int and value > 0 else 0


def _nested(obj: dict[str, object], key: str, field: str) -> object:
    inner = obj.get(key)
    return cast("dict[str, object]", inner).get(field) if isinstance(inner, dict) else None


def _matches(obj: dict[str, object], expected: Mapping[str, object]) -> bool:
    return all(obj.get(key) == value for key, value in expected.items())


def _workflow_path(value: object) -> str | None:
    return value.partition("@")[0] if isinstance(value, str) else None


def _run_number(run: dict[str, object]) -> int:
    return _positive_int(run.get("run_number"))


def _qualifying_run(run: dict[str, object], sha: str) -> bool:
    return (
        _matches(run, {"event": "push", "head_branch": "main", "head_sha": sha})
        and _workflow_path(run.get("path")) == _WORKFLOW_PATH
        and _run_number(run) > 0
        and _positive_int(run.get("check_suite_id")) > 0
    )


def select_run(runs: list[dict[str, object]], sha: str) -> dict[str, object] | None:
    return max((run for run in runs if _qualifying_run(run, sha)), key=_run_number, default=None)


def classify_run(run: dict[str, object]) -> _Outcome | None:
    if run.get("status") != _COMPLETED:
        return _Outcome(Verdict.PENDING, Reason.RUN_IN_PROGRESS)
    if run.get("conclusion") != _SUCCESS:
        return _Outcome(Verdict.RED, Reason.RUN_NOT_SUCCESSFUL)
    return None


def _check_id(check: dict[str, object]) -> int:
    return _positive_int(check.get("id"))


def _qualifying_gate(check: dict[str, object], sha: str, suite_id: int) -> bool:
    return (
        _matches(check, {"name": _GATE_NAME, "head_sha": sha})
        and _check_id(check) > 0
        and _nested(check, "app", "slug") == _ACTIONS_APP_SLUG
        and _positive_int(_nested(check, "check_suite", "id")) == suite_id
    )


def select_gate(
    checks: list[dict[str, object]], sha: str, suite_id: int
) -> dict[str, object] | None:
    return max(
        (check for check in checks if _qualifying_gate(check, sha, suite_id)),
        key=_check_id,
        default=None,
    )


def classify_gate(gate: dict[str, object] | None) -> _Outcome:
    if gate is None:
        return _Outcome(Verdict.RED, Reason.NO_QUALIFYING_GATE)
    if gate.get("status") != _COMPLETED:
        return _Outcome(Verdict.PENDING, Reason.GATE_IN_PROGRESS)
    if gate.get("conclusion") != _SUCCESS:
        return _Outcome(Verdict.RED, Reason.GATE_NOT_SUCCESSFUL)
    return _Outcome(Verdict.GREEN, Reason.CI_PROVED_GREEN)


def _classify_ci(ctx: _Ctx) -> _Outcome:
    runs = self_update_github.push_runs(ctx.owner, ctx.name, ctx.fetched)
    run = select_run(runs, ctx.fetched)
    if run is None:
        return _Outcome(Verdict.PENDING, Reason.NO_PUSH_RUN)
    early = classify_run(run)
    if early is not None:
        return early
    suite_id = _positive_int(run.get("check_suite_id"))
    checks = self_update_github.gate_check_runs(ctx.owner, ctx.name, suite_id)
    return classify_gate(select_gate(checks, ctx.fetched, suite_id))


def _ci_outcome(ctx: _Ctx) -> _Outcome:
    try:
        return _classify_ci(ctx)
    except self_update_github.GithubReadError:
        return _Outcome(Verdict.ERROR, Reason.GITHUB_READ_FAILED)


def _first_outcome(ctx: _Ctx, *, pin: str | None = None, force: bool = False) -> _Outcome:
    outcome = _run_steps(ctx, pin=pin, force=force)
    if outcome is not None:
        return outcome
    if force:
        return _Outcome(Verdict.GREEN, Reason.OPERATOR_FORCED)
    return _ci_outcome(ctx)


def decide(repo: Path, *, pin: str | None = None, force: bool = False) -> tuple[_Outcome, _Ctx]:
    ctx = _Ctx(repo)
    try:
        return _first_outcome(ctx, pin=pin, force=force), ctx
    except (subprocess.SubprocessError, OSError):
        return _Outcome(Verdict.ERROR, Reason.GIT_FAILED), ctx


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_status(record: StatusRecord) -> None:
    path = status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    store.write_text_atomic(path, json.dumps(record.as_json(), indent=2) + "\n")


def _check_record(outcome: _Outcome, ctx: _Ctx) -> StatusRecord:
    return StatusRecord(
        outcome.verdict, outcome.reason, ctx.fetched or None, ctx.head or None, _now()
    )


def check(repo: Path) -> StatusRecord:
    outcome, ctx = decide(repo)
    record = _check_record(outcome, ctx)
    _write_status(record)
    return record


def _valid_bound(value: float) -> bool:
    return math.isfinite(value) and value >= 0


def _valid_url(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme in {"http", "https"} and bool(parts.hostname)


@dataclass(frozen=True)
class ApplyInputs:
    detached: bool
    idle_probe: Callable[[], bool]
    wait_bound_s: float
    health_url: str
    plist_path: Path

    def __post_init__(self) -> None:
        if not _valid_bound(self.wait_bound_s):
            raise ValueError("wait bound must be a finite, non-negative number of seconds")
        if not _valid_url(self.health_url):
            raise ValueError("health url must be an http or https url")


@dataclass
class _ApplyCtx:
    repo: Path
    inputs: ApplyInputs
    target: str
    head: str
    previous_sha: str
    previous_slot: str | None = None
    path: ApplyPath | None = None
    owner: str = ""
    name: str = ""


class _StepError(Exception):
    pass


def _failed(reason: Reason) -> _Outcome:
    return _Outcome(Verdict.FAILED, reason)


def _poll(probe: Callable[[], bool], bound_s: float, interval_s: float) -> bool:
    deadline = _monotonic() + bound_s
    while not probe():
        remaining = deadline - _monotonic()
        if remaining <= 0:
            return False
        _sleep(min(interval_s, remaining))
    return True


def _run_step(
    argv: Sequence[str],
    timeout_s: float,
    *,
    cwd: Path | None = None,
    check: bool = True,
    env: Mapping[str, str | None] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = process_runner.run_subprocess(
        argv, cwd=str(cwd) if cwd is not None else None, timeout=timeout_s, env=env
    )
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, list(argv))
    return result


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _label() -> str:
    return f"{_domain()}/{_SERVICE_LABEL}"


def _launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run_step(["launchctl", *args], _LAUNCHCTL_TIMEOUT_S, check=check)


def _service_loaded() -> bool:
    return _launchctl("print", _label(), check=False).returncode == 0


def _web_dir(repo: Path) -> Path:
    return repo / "alan" / "web"


def _marker_path() -> Path:
    return roots.deploy_marker_root() / _MARKER_FILE


def _previous_slot(web_dir: Path) -> str | None:
    dist = web_dir / "dist"
    if not dist.is_symlink():
        return None
    target = os.readlink(dist)
    return target if target in _SLOTS else None


def _active_slot_sha(web_dir: Path) -> str | None:
    try:
        text = (web_dir / "dist" / _DEPLOY_SHA_FILE).read_text(encoding="utf-8")
    except OSError:
        return None
    return text.strip() or None


def _swap(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    script = repo / "scripts" / "swap_web_dist.py"
    argv = [sys.executable, str(script), "--web-dir", str(_web_dir(repo)), *args]
    return _run_step(argv, _SWAP_TIMEOUT_S)


def _next_slot(repo: Path) -> str:
    slot = _swap(repo, "next-slot").stdout.strip()
    if slot not in _SLOTS:
        raise _StepError(f"unexpected web slot {slot!r}")
    return slot


def _clear_slot(web_dir: Path, slot: str) -> None:
    target = web_dir / slot
    if os.path.lexists(target):
        shutil.rmtree(target)


def _fetch_health(url: str) -> bytes | None:
    try:
        with _urlopen(url, timeout=_HEALTH_TIMEOUT_S) as response:
            if response.status != 200:
                return None
            return cast("bytes", response.read(_HEALTH_BODY_LIMIT + 1))
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):
        return None


def _reports_target(body: bytes | None, target: str) -> bool:
    if body is None or len(body) > _HEALTH_BODY_LIMIT:
        return False
    try:
        payload: object = json.loads(body)
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False
    fields = cast("dict[str, object]", payload)
    return fields.get("ok") is True and fields.get("served_sha") == target


def _health_ok(ctx: _ApplyCtx) -> bool:
    return _reports_target(_fetch_health(ctx.inputs.health_url), ctx.target)


def _step_snapshot(ctx: _ApplyCtx) -> _Outcome | None:
    ctx.previous_slot = _previous_slot(_web_dir(ctx.repo))
    return None


def _step_classify(ctx: _ApplyCtx) -> _Outcome | None:
    loaded = _service_loaded()
    restart = deploy_restart.decide(ctx.repo, _marker_path(), loaded, rev=ctx.target)[0]
    ctx.path = ApplyPath.RESTART if restart else ApplyPath.RESTART_FREE
    return None


def _step_wait(ctx: _ApplyCtx) -> _Outcome | None:
    inputs = ctx.inputs
    if not inputs.detached:
        _poll(inputs.idle_probe, inputs.wait_bound_s, _IDLE_POLL_S)
    return None


def _step_same_origin(ctx: _ApplyCtx, fresh: _Ctx) -> _Outcome | None:
    if (fresh.owner, fresh.name) != (ctx.owner, ctx.name):
        return _blocked(Reason.NO_GITHUB_ORIGIN)
    return None


def _step_recheck(ctx: _ApplyCtx) -> _Outcome | None:
    fresh = _Ctx(ctx.repo, fetched=ctx.target)
    early = (
        _step_branch(fresh)
        or _step_origin(fresh)
        or _step_same_origin(ctx, fresh)
        or _step_dirty(fresh)
    )
    if early is not None:
        return early
    if fresh.head != ctx.previous_sha:
        return _blocked(Reason.NOT_FAST_FORWARD)
    return _step_ancestry(fresh)


def _step_fast_forward(ctx: _ApplyCtx) -> _Outcome | None:
    _git(ctx.repo, "merge", "--ff-only", ctx.target)
    ctx.head = ctx.target
    return None


def _step_render(ctx: _ApplyCtx) -> _Outcome | None:
    script = ctx.repo / "deploy" / "render_plist.sh"
    _run_step(
        ["bash", str(script)],
        _RENDER_TIMEOUT_S,
        cwd=ctx.repo,
        env=_origin.pinned_environment(ctx.owner, ctx.name),
    )
    return None


def _step_sync(ctx: _ApplyCtx) -> _Outcome | None:
    _run_step(["uv", "sync", "--locked"], _SYNC_TIMEOUT_S, cwd=ctx.repo)
    return None


def _step_web(ctx: _ApplyCtx) -> _Outcome | None:
    web = _web_dir(ctx.repo)
    slot = _next_slot(ctx.repo)
    _run_step(["npm", "ci", "--quiet"], _NPM_TIMEOUT_S, cwd=web)
    _clear_slot(web, slot)
    _run_step(["npm", "run", "build", "--", "--outDir", slot], _NPM_TIMEOUT_S, cwd=web)
    _swap(ctx.repo, "activate", "--slot", slot, "--sha", ctx.target)
    return None


def _step_teardown(_ctx: _ApplyCtx) -> _Outcome | None:
    if not _service_loaded():
        return None
    _launchctl("bootout", _label(), check=False)
    if _poll(lambda: not _service_loaded(), _UNLOAD_BOUND_S, _UNLOAD_POLL_S):
        return None
    return _failed(Reason.TEARDOWN_FAILED)


def _step_bootstrap(ctx: _ApplyCtx) -> _Outcome | None:
    _launchctl("enable", _label(), check=False)
    _launchctl("bootstrap", _domain(), str(ctx.inputs.plist_path))
    return None


def _step_kickstart(_ctx: _ApplyCtx) -> _Outcome | None:
    _launchctl("kickstart", "-p", _label())
    return None


def _verify_served(ctx: _ApplyCtx) -> _Outcome | None:
    if _poll(lambda: _health_ok(ctx), _VERIFY_BOUND_S, _VERIFY_POLL_S):
        return None
    return _failed(Reason.VERIFICATION_TIMEOUT)


def _verify_checkout(ctx: _ApplyCtx) -> _Outcome | None:
    head = _git(ctx.repo, "rev-parse", "HEAD").strip()
    if head == ctx.target and _active_slot_sha(_web_dir(ctx.repo)) == ctx.target:
        return None
    return _failed(Reason.VERIFICATION_MISMATCH)


def _step_verify(ctx: _ApplyCtx) -> _Outcome | None:
    if ctx.path is ApplyPath.RESTART:
        return _verify_served(ctx)
    return _verify_checkout(ctx)


class _ApplyStep(NamedTuple):
    run: Callable[[_ApplyCtx], _Outcome | None]
    failure: _Outcome
    restart_only: bool = False


_UNCHANGED = _Outcome(Verdict.ERROR, Reason.GIT_FAILED)

_APPLY_STEPS: tuple[_ApplyStep, ...] = (
    _ApplyStep(_step_snapshot, _UNCHANGED),
    _ApplyStep(_step_classify, _Outcome(Verdict.ERROR, Reason.SERVICE_PROBE_FAILED)),
    _ApplyStep(_step_wait, _UNCHANGED, restart_only=True),
    _ApplyStep(_step_recheck, _UNCHANGED),
    _ApplyStep(_step_fast_forward, _failed(Reason.FAST_FORWARD_FAILED)),
    _ApplyStep(_step_render, _failed(Reason.RENDER_FAILED)),
    _ApplyStep(_step_sync, _failed(Reason.DEPENDENCY_SYNC_FAILED)),
    _ApplyStep(_step_web, _failed(Reason.WEB_BUILD_FAILED)),
    _ApplyStep(_step_teardown, _failed(Reason.TEARDOWN_FAILED), restart_only=True),
    _ApplyStep(_step_bootstrap, _failed(Reason.BOOTSTRAP_FAILED), restart_only=True),
    _ApplyStep(_step_kickstart, _failed(Reason.KICKSTART_FAILED), restart_only=True),
    _ApplyStep(_step_verify, _failed(Reason.VERIFICATION_TIMEOUT)),
)


def _first_apply_outcome(ctx: _ApplyCtx) -> _Outcome:
    for step in _APPLY_STEPS:
        if step.restart_only and ctx.path is not ApplyPath.RESTART:
            continue
        try:
            outcome = step.run(ctx)
        except Exception:  # noqa: BLE001
            return step.failure
        if outcome is not None:
            return outcome
    return _Outcome(Verdict.APPLIED, Reason.TARGET_VERIFIED)


def _apply_record(outcome: _Outcome, ctx: _ApplyCtx) -> StatusRecord:
    return StatusRecord(
        outcome.verdict,
        outcome.reason,
        ctx.target,
        ctx.head,
        _now(),
        ctx.path,
        ctx.target,
        ctx.previous_sha,
        ctx.previous_slot,
    )


def _write_marker(target: str) -> None:
    path = _marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    store.write_text_atomic(path, f"{target}\n")


def _apply_green(check_ctx: _Ctx, inputs: ApplyInputs) -> StatusRecord:
    ctx = _ApplyCtx(
        check_ctx.repo,
        inputs,
        check_ctx.fetched,
        check_ctx.head,
        check_ctx.head,
        owner=check_ctx.owner,
        name=check_ctx.name,
    )
    outcome = _first_apply_outcome(ctx)
    if outcome.verdict is Verdict.APPLIED and ctx.path is ApplyPath.RESTART:
        _write_marker(ctx.target)
    return _apply_record(outcome, ctx)


def apply(
    repo: Path, inputs: ApplyInputs, *, pin: str | None = None, force: bool = False
) -> StatusRecord:
    outcome, check_ctx = decide(repo, pin=pin, force=force)
    if outcome.verdict is Verdict.GREEN:
        record = _apply_green(check_ctx, inputs)
    else:
        record = _check_record(outcome, check_ctx)
    _write_status(record)
    return record
