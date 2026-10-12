from __future__ import annotations

import dataclasses
import functools
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, settings

import alan.roots as _roots_mod
from tests.gh_guard import (
    GhGuardBlocked,
    append_baseline_key,
    baseline_key,
    classify,
    drain_background_thread_blocks,
    drain_stubbed_reads,
    gh_read_ratchet_failure,
    popen_refusal,
    record_blocked,
    record_stubbed_read,
    refusal_message,
    set_current_test,
    strip_ambient_gh_credentials,
    stubbed_completed_process,
)
from tests.gh_read_baseline import GH_READ_STUBBED_TESTS

_REAL_CLAUDE_SPAWN_BLOCKED = (
    "A test tried to exec the real 'claude' CLI. Tests must mock the spawn "
    "(patch run_agent / run_agent_streaming, or subprocess.run / Popen). "
    "See tests/conftest.py::_no_real_manager_summary_spawn / "
    "_forbid_real_claude_spawn and docs/architecture/ci_isolation.md."
)

_REAL_TMUX_SPAWN_BLOCKED = (
    "A test tried to exec the real 'tmux'. Tests must fake the operator tmux runner "
    "(patch alan.operator_tmux.run_tmux) or subprocess.run / Popen. "
    "See tests/conftest.py::_no_real_operator_spawn."
)
_REAL_OPERATOR_GIT_BLOCKED = (
    "A test tried to run the operator checkout's real git runner. Tests must fake it "
    "(patch alan.operator_checkout.run_git). See tests/conftest.py::_no_real_operator_spawn."
)

os.environ.setdefault("ALAN_DISABLE_DISPATCHER", "1")
os.environ.setdefault("ALAN_UNBLOCK_DISABLE", "1")
_SHARED_TEST_DIR = Path(tempfile.mkdtemp(prefix="alan-test-shared-"))
_TESTS_BIN_DIR = Path(__file__).resolve().parent / "bin"
os.environ["PATH"] = os.pathsep.join([str(_TESTS_BIN_DIR), os.environ.get("PATH", "")])
_GH_CONFIG_DIR = _SHARED_TEST_DIR / "gh-config"
_GH_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
os.environ["GH_CONFIG_DIR"] = str(_GH_CONFIG_DIR)
strip_ambient_gh_credentials(os.environ)
_GH_READ_BASELINE_WRITE = os.environ.get("GH_READ_BASELINE_WRITE", "").strip() or None
os.environ["ALAN_QUEUE_DB_PATH"] = str(_SHARED_TEST_DIR / "queue.db")
_STATE_ROOT_SUBDIR_OVERRIDES = {".": "driver-loop-state"}
for _state_root_token, _state_root_subdir in _roots_mod.STATE_ROOT_SUBDIRS.items():
    os.environ[_state_root_token] = str(
        _SHARED_TEST_DIR / _STATE_ROOT_SUBDIR_OVERRIDES.get(_state_root_subdir, _state_root_subdir)
    )
os.environ["ALAN_SLOT_POOL_ROOT"] = ""
if not os.environ.get("ALAN_WEB_DIST_DIR", "").strip():
    os.environ["ALAN_WEB_DIST_DIR"] = str(_SHARED_TEST_DIR / "no-such-dist")
_TEST_DEFAULT_PROFILE = Path(__file__).resolve().parent / "fixtures" / "default_profile_test.yaml"
_roots_mod.engines_root().mkdir(parents=True, exist_ok=True)
shutil.copyfile(_TEST_DEFAULT_PROFILE, _roots_mod.engines_root() / "default_profile.yaml")

import alan.config_files as _config_files_mod  # noqa: E402
import alan.default_profile as _default_profile_mod  # noqa: E402
import alan.engine_config as _engine_config_mod  # noqa: E402
import alan.investigator as _investigator_mod  # noqa: E402
import alan.manager_summary as _manager_summary_mod  # noqa: E402
import alan.operator_checkout as _operator_checkout_mod  # noqa: E402
import alan.operator_repos as _operator_repos_mod  # noqa: E402
import alan.operator_tmux as _operator_tmux_mod  # noqa: E402
from alan.engine_config import RepoIdentity  # noqa: E402
from alan.events import PubSub  # noqa: E402
from alan.queue import Queue  # noqa: E402
from alan.teams._verification_artifacts import reviewer_count_var  # noqa: E402
from tests.fakes.forge import FakeForge, installed_fake_forge  # noqa: E402

_WORKTREE_REPO_DIR = _SHARED_TEST_DIR / "worktree-repo"
_WORKTREE_REPO_DIR.mkdir(parents=True, exist_ok=True)
_WORKTREE_REPO_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "alan-test",
    "GIT_AUTHOR_EMAIL": "alan-test@localhost",
    "GIT_COMMITTER_NAME": "alan-test",
    "GIT_COMMITTER_EMAIL": "alan-test@localhost",
}
subprocess.run(  # noqa: S603,S607
    ["git", "init", "-q", "-b", "main"],
    cwd=_WORKTREE_REPO_DIR, env=_WORKTREE_REPO_GIT_ENV,
    capture_output=True, text=True, check=True,
)
subprocess.run(  # noqa: S603,S607
    ["git", "commit", "-q", "--allow-empty", "--no-gpg-sign", "-m", "init"],
    cwd=_WORKTREE_REPO_DIR, env=_WORKTREE_REPO_GIT_ENV,
    capture_output=True, text=True, check=True,
)
os.environ["ALAN_WORKTREE_REPO_ROOT"] = str(_WORKTREE_REPO_DIR)

_RESOLVED_BY_THE_PROCESS = _default_profile_mod.resolved


@functools.cache
def _owner_source_resolved() -> _default_profile_mod.Resolved:
    return dataclasses.replace(
        _RESOLVED_BY_THE_PROCESS(), source=_default_profile_mod.Source.owner
    )


_MODULE_INIT_REGISTRY = dict(_engine_config_mod._registry)  # pyright: ignore[reportPrivateUsage]
_MODULE_INIT_DEFAULT_KEY = _engine_config_mod._default_key  # pyright: ignore[reportPrivateUsage]


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    import shutil

    del session, exitstatus
    shutil.rmtree(_SHARED_TEST_DIR, ignore_errors=True)


class _FakeProc:
    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: str | None = "",
        stderr: str | None = "",
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeTmux:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.cwds: list[str | None] = []
        self.live = False
        self.pane_dead = False
        self.capture = "captured screen\n"
        self.capture_rc = 0
        self.new_session_rc = 0
        self.new_session_stderr = ""
        self.kill_leaves_session = False
        self.raises: dict[str, BaseException] = {}

    def subcommands(self) -> list[str]:
        return [call[4] for call in self.calls]

    def calls_of(self, subcommand: str) -> list[list[str]]:
        return [call for call in self.calls if call[4] == subcommand]

    def __call__(
        self, args: Any, *, cwd: str | None = None, timeout: float = 15.0
    ) -> _FakeProc:
        del timeout
        call = list(args)
        self.calls.append(call)
        self.cwds.append(cwd)
        subcommand = call[4]
        if subcommand in self.raises:
            raise self.raises[subcommand]
        handler: Callable[[], _FakeProc] = getattr(self, "_" + subcommand.replace("-", "_"))
        return handler()

    def _has_session(self) -> _FakeProc:
        return _FakeProc(returncode=0 if self.live else 1)

    def _list_panes(self) -> _FakeProc:
        return _FakeProc(stdout="1\n" if self.pane_dead else "0\n")

    def _capture_pane(self) -> _FakeProc:
        return _FakeProc(returncode=self.capture_rc, stdout=self.capture)

    def _kill_session(self) -> _FakeProc:
        if not self.kill_leaves_session:
            self.live = False
        return _FakeProc()

    def _new_session(self) -> _FakeProc:
        if self.new_session_rc == 0:
            self.live = True
        return _FakeProc(returncode=self.new_session_rc, stderr=self.new_session_stderr)


def git_reply(*, returncode: int = 1, stdout: str = "", stderr: str = "") -> _FakeProc:
    return _FakeProc(returncode=returncode, stdout=stdout, stderr=stderr)


def register_test_engine(
    slug: str, *, hidden: bool = False, default: bool = False
) -> RepoIdentity:
    config = _engine_config_mod.EngineConfig(repo=RepoIdentity.from_slug(slug), hidden=hidden)
    _engine_config_mod.register(config, default=default, validate_env=False)
    return config.repo


@dataclass(frozen=True)
class _GitFailure:
    verb: str
    needle: str
    reply: _FakeProc | BaseException
    partial: bool


class FakeGit:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], Path, float]] = []
        self.origin = "https://example.test/install-owner/install-repo.git"
        self.failures: list[_GitFailure] = []

    def verbs(self) -> list[str]:
        return [args[0] for args, _cwd, _timeout in self.calls]

    def calls_of(self, verb: str) -> list[tuple[list[str], Path, float]]:
        return [call for call in self.calls if call[0][0] == verb]

    def fail(
        self,
        verb: str,
        needle: str,
        reply: _FakeProc | BaseException,
        *,
        partial: bool = False,
    ) -> None:
        self.failures.append(_GitFailure(verb, needle, reply, partial))

    def __call__(self, args: Sequence[str], *, cwd: Path, timeout: float) -> _FakeProc:
        self.calls.append((list(args), cwd, timeout))
        verb = args[0]
        haystack = " ".join([*args, str(cwd)])
        for failure in self.failures:
            if failure.verb == verb and failure.needle in haystack:
                return self._failed(failure, args)
        handler: Callable[[Sequence[str]], _FakeProc] = getattr(self, "_" + verb)
        return handler(args)

    def _failed(self, failure: _GitFailure, args: Sequence[str]) -> _FakeProc:
        if failure.partial:
            (Path(args[-1]) / "partial").mkdir(parents=True, exist_ok=True)
        if isinstance(failure.reply, BaseException):
            raise failure.reply
        return failure.reply

    def _config(self, args: Sequence[str]) -> _FakeProc:
        del args
        return _FakeProc(stdout=f"{self.origin}\n")

    def _clone(self, args: Sequence[str]) -> _FakeProc:
        (Path(args[-1]) / ".git").mkdir(parents=True)
        return _FakeProc()

    def _fetch(self, args: Sequence[str]) -> _FakeProc:
        del args
        return _FakeProc()

    def _merge(self, args: Sequence[str]) -> _FakeProc:
        del args
        return _FakeProc()


def make_fake_run(
    *,
    returncode: int = 0,
    stdout: str | None = "",
    stderr: str | None = "",
    raises: BaseException | None = None,
) -> Callable[..., _FakeProc]:
    def _fake(*_args: Any, **_kwargs: Any) -> _FakeProc:
        if raises is not None:
            raise raises
        return _FakeProc(returncode=returncode, stdout=stdout, stderr=stderr)

    return _fake


@pytest.fixture(scope="session")
def module_init_registry_state() -> tuple[dict[Any, Any], Any]:
    return _MODULE_INIT_REGISTRY, _MODULE_INIT_DEFAULT_KEY


@pytest.fixture(autouse=True)
def _reset_registry() -> Iterator[None]:
    saved_registry = dict(_engine_config_mod._registry)  # pyright: ignore[reportPrivateUsage]
    saved_default_key = _engine_config_mod._default_key  # pyright: ignore[reportPrivateUsage]
    saved_ceilings = dict(_engine_config_mod._file_ceilings)  # pyright: ignore[reportPrivateUsage]
    saved_fallback_ceiling = (
        _engine_config_mod._fallback_ceiling  # pyright: ignore[reportPrivateUsage]
    )
    saved_default_ceiling = dict(
        _engine_config_mod._default_ceiling  # pyright: ignore[reportPrivateUsage]
    )
    saved_quarantined_configs = list(
        _engine_config_mod._quarantined_configs  # pyright: ignore[reportPrivateUsage]
    )
    _engine_config_mod._registry = {}  # pyright: ignore[reportPrivateUsage]
    _engine_config_mod._default_key = None  # pyright: ignore[reportPrivateUsage]
    _engine_config_mod._file_ceilings = {}  # pyright: ignore[reportPrivateUsage]
    _engine_config_mod._fallback_ceiling = (  # pyright: ignore[reportPrivateUsage]
        _engine_config_mod.Posture.autonomous
    )
    _engine_config_mod._quarantined_configs = []  # pyright: ignore[reportPrivateUsage]
    yield
    _engine_config_mod._registry = saved_registry  # pyright: ignore[reportPrivateUsage]
    _engine_config_mod._default_key = saved_default_key  # pyright: ignore[reportPrivateUsage]
    _engine_config_mod._file_ceilings = saved_ceilings  # pyright: ignore[reportPrivateUsage]
    _engine_config_mod._fallback_ceiling = (  # pyright: ignore[reportPrivateUsage]
        saved_fallback_ceiling
    )
    _engine_config_mod._default_ceiling = (  # pyright: ignore[reportPrivateUsage]
        saved_default_ceiling
    )
    _engine_config_mod._quarantined_configs = (  # pyright: ignore[reportPrivateUsage]
        saved_quarantined_configs
    )


@pytest.fixture(autouse=True)
def _owner_source_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_default_profile_mod, "resolved", _owner_source_resolved)


@pytest.fixture(autouse=True)
def _active_fake_forge() -> Iterator[FakeForge]:
    with installed_fake_forge() as fake:
        yield fake


@pytest.fixture(autouse=True)
def _empty_engine_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _config_files_mod, "engines_config_dir", lambda: tmp_path / "no-such-engines-config"
    )


@pytest.fixture(autouse=True)
def _redirect_alan_logs_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_QUEUE_DB_PATH", str(tmp_path / "queue.db"))


@pytest.fixture(autouse=True)
def _strip_verifier_gh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_VERIFIER_GH_TOKEN", raising=False)


_FLEET_WORKER_ENV_CAPS = (
    "VITEST_MAX_WORKERS",
    "PYTEST_ADDOPTS",
    "ALAN_DISPATCHER_CONCURRENCY",
    "ALAN_DETACHED_WORKERS",
)


@pytest.fixture(autouse=True)
def _scrub_ambient_fleet_worker_env_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _FLEET_WORKER_ENV_CAPS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _no_real_manager_summary_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _manager_summary_mod, "trigger_manager_summary", lambda *_a, **_kw: None
    )


@pytest.fixture(autouse=True)
def _no_real_investigator_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _investigator_mod, "trigger_investigator", lambda *_a, **_kw: None
    )


@pytest.fixture(autouse=True)
def _no_real_operator_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    def _refuse_tmux(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError(_REAL_TMUX_SPAWN_BLOCKED)

    def _refuse_git(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError(_REAL_OPERATOR_GIT_BLOCKED)

    monkeypatch.setattr(_operator_tmux_mod, "run_tmux", _refuse_tmux)
    monkeypatch.setattr(_operator_checkout_mod, "run_git", _refuse_git)


@pytest.fixture(autouse=True)
def _redirect_operator_install_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _operator_checkout_mod, "install_root", lambda: tmp_path / "operator-install"
    )


@pytest.fixture(autouse=True)
def _isolate_reviewer_count_var() -> Iterator[None]:
    token = reviewer_count_var.set(None)
    try:
        yield
    finally:
        reviewer_count_var.reset(token)


@pytest.fixture
def fake_tmux(monkeypatch: pytest.MonkeyPatch) -> FakeTmux:
    fake = FakeTmux()
    monkeypatch.setattr(_operator_tmux_mod, "run_tmux", fake)
    return fake


@pytest.fixture
def fake_git(monkeypatch: pytest.MonkeyPatch) -> FakeGit:
    fake = FakeGit()
    monkeypatch.setattr(_operator_checkout_mod, "run_git", fake)
    return fake


@pytest.fixture
def operator_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_tmux: FakeTmux, fake_git: FakeGit
) -> Path:
    del fake_tmux, fake_git
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("ALAN_DETACHED_WORKERS", "on")
    return _operator_repos_mod.operator_root()


def spawns_real_tmux(cmd: object) -> bool:
    first = cmd[0] if isinstance(cmd, (list, tuple)) and cmd else cmd
    if not isinstance(first, (str, bytes, os.PathLike)):
        return False
    try:
        decoded = os.fsdecode(first)
    except (UnicodeDecodeError, TypeError, ValueError):
        return False
    return os.path.basename(decoded) == "tmux"


def spawns_real_claude(cmd: object) -> bool:
    first = cmd[0] if isinstance(cmd, (list, tuple)) and cmd else cmd
    if not isinstance(first, (str, bytes, os.PathLike)):
        return False
    try:
        decoded = os.fsdecode(first)
    except (UnicodeDecodeError, TypeError, ValueError):
        return True
    return os.path.basename(decoded) == "claude"


settings.register_profile(
    "alan",
    derandomize=True,
    database=None,
    deadline=None,
    max_examples=30,
    stateful_step_count=15,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
settings.load_profile("alan")


@pytest.fixture(scope="session", autouse=True)
def _forbid_real_claude_spawn() -> Iterator[None]:
    real_run = subprocess.run
    real_popen = subprocess.Popen

    def _guarded_run(cmd: Any, *args: Any, **kwargs: Any) -> Any:
        if spawns_real_claude(cmd):
            raise RuntimeError(_REAL_CLAUDE_SPAWN_BLOCKED)
        if spawns_real_tmux(cmd):
            raise RuntimeError(_REAL_TMUX_SPAWN_BLOCKED)
        decision = classify(cmd)
        if not decision.is_gh:
            return real_run(cmd, *args, **kwargs)
        if not decision.permitted:
            record_blocked(decision)
            raise GhGuardBlocked(refusal_message(decision))
        record_stubbed_read(decision)
        return stubbed_completed_process(cmd, kwargs)

    def _guarded_popen(cmd: Any, *args: Any, **kwargs: Any) -> Any:
        if spawns_real_claude(cmd):
            raise RuntimeError(_REAL_CLAUDE_SPAWN_BLOCKED)
        if spawns_real_tmux(cmd):
            raise RuntimeError(_REAL_TMUX_SPAWN_BLOCKED)
        decision = classify(cmd)
        if not decision.is_gh:
            return real_popen(cmd, *args, **kwargs)
        refused = popen_refusal(decision)
        record_blocked(refused)
        raise GhGuardBlocked(refusal_message(refused))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(subprocess, "run", _guarded_run)
        mp.setattr(subprocess, "Popen", _guarded_popen)
        yield


@pytest.fixture(autouse=True)
def _fail_on_background_thread_gh_guard_block() -> Iterator[None]:
    drain_background_thread_blocks()
    yield
    blocked = drain_background_thread_blocks()
    if blocked:
        details = "; ".join(refusal_message(c.decision) for c in blocked)
        pytest.fail(
            f"gh guard refused {len(blocked)} call(s) on a background thread during this "
            f"test, which cannot fail the test on its own: {details}"
        )


@pytest.fixture
def expect_gh_read_stub() -> None:
    return None


@pytest.fixture(autouse=True)
def _gh_read_ratchet(request: pytest.FixtureRequest) -> Iterator[None]:
    key = baseline_key(request.node.nodeid)
    drain_stubbed_reads()
    set_current_test(key)
    yield
    set_current_test(None)
    reads = drain_stubbed_reads()
    expected = "expect_gh_read_stub" in request.fixturenames
    if reads and not expected and _GH_READ_BASELINE_WRITE is not None:
        append_baseline_key(Path(_GH_READ_BASELINE_WRITE), key)
        return
    failure = gh_read_ratchet_failure(
        key, reads, listed=key in GH_READ_STUBBED_TESTS, expected=expected,
    )
    if failure is not None:
        pytest.fail(failure)


@pytest.fixture
def patch_subprocess(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    def _patch(**kwargs: Any) -> None:
        monkeypatch.setattr(subprocess, "run", make_fake_run(**kwargs))

    return _patch


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "queue.db"


@pytest.fixture
def queue(db_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=db_path)
    try:
        yield q
    finally:
        q.close()


@pytest.fixture
def pubsub() -> PubSub:
    return PubSub()


@pytest.fixture
def claude_json() -> Callable[..., str]:
    def _build(
        *,
        result: Any = "improved text",
        is_error: Any = None,
        subtype: Any = None,
        omit_result: bool = False,
    ) -> str:
        payload: dict[str, Any] = {}
        if not omit_result:
            payload["result"] = result
        if is_error is not None:
            payload["is_error"] = is_error
        if subtype is not None:
            payload["subtype"] = subtype
        return json.dumps(payload)

    return _build
