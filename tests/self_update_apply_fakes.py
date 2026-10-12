from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from alan import process_runner, self_update
from alan.self_update import _extract, _ops
from tests.self_update_fixtures import (
    GhRouter,
    RepoPair,
    Scenario,
    TemplateBuilder,
    advance_origin,
    commit,
    completed,
    git,
    init_pair,
    redirect_env,
    store_transport_credential,
    templated_pair,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SWAP_SCRIPT = REPO_ROOT / "scripts" / "swap_web_dist.py"
HEALTH_URL = "http://health.invalid/api/health"
DOMAIN = f"gui/{os.getuid()}"
LABEL = f"{DOMAIN}/ai.alan"
UNREACHABLE_SHA = "1" * 40
BODY_LIMIT = 1024 * 1024
EXCLUDED = ("alan/web/dist", "alan/web/dist.*", ".venv/", "node_modules/")
ROLLBACK_GIT_VERBS = ("reset", "checkout", "clean")

Argv = tuple[str, ...]
Fault = Callable[[Argv, float], "subprocess.CompletedProcess[str]"]


def exit_with(code: int) -> Fault:
    def fault(argv: Argv, timeout: float) -> subprocess.CompletedProcess[str]:
        return completed(returncode=code)

    return fault


def reply(stdout: str = "", code: int = 0) -> Fault:
    def fault(argv: Argv, timeout: float) -> subprocess.CompletedProcess[str]:
        return completed(stdout=stdout, returncode=code)

    return fault


def hang(argv: Argv, timeout: float) -> subprocess.CompletedProcess[str]:
    raise subprocess.TimeoutExpired(list(argv), timeout)


def vanish(argv: Argv, timeout: float) -> subprocess.CompletedProcess[str]:
    raise FileNotFoundError(argv[0])


@dataclass(frozen=True)
class Call:
    argv: Argv
    cwd: str | None
    timeout: float


@dataclass
class Injection:
    tokens: frozenset[str]
    fault: Fault
    skip: int


class Launchctl:
    def __init__(self, loaded: bool) -> None:
        self.loaded = loaded
        self.stuck = False
        self.unload_delay = 0
        self.lingering = 0
        self.runs = 0
        self.on_print: Callable[[], None] | None = None

    def handle(self, argv: Argv) -> subprocess.CompletedProcess[str]:
        return getattr(self, f"_{argv[1]}", self._accept)(argv)

    def _accept(self, argv: Argv) -> subprocess.CompletedProcess[str]:
        return completed()

    def _kickstart(self, argv: Argv) -> subprocess.CompletedProcess[str]:
        self.runs += 1
        return completed()

    def _print(self, argv: Argv) -> subprocess.CompletedProcess[str]:
        if self.on_print is not None:
            self.on_print()
        shows_loaded = self.loaded
        if self.lingering > 0:
            self.lingering -= 1
            self.loaded = self.lingering > 0
        stdout = f"\truns = {self.runs}\n" if shows_loaded else ""
        return completed(stdout=stdout, returncode=0 if shows_loaded else 113)

    def _bootout(self, argv: Argv) -> subprocess.CompletedProcess[str]:
        if not self.stuck:
            self.lingering = self.unload_delay
            self.loaded = self.unload_delay > 0
        return completed()

    def _bootstrap(self, argv: Argv) -> subprocess.CompletedProcess[str]:
        if self.loaded:
            return completed(returncode=5)
        self.loaded = True
        return completed()


class CommandRecorder:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, launchctl: Launchctl) -> None:
        self.calls: list[Call] = []
        self.launchctl = launchctl
        self.injections: list[Injection] = []
        self._passthrough = process_runner.run_subprocess
        monkeypatch.setattr(process_runner, "run_subprocess", self._route)

    def inject(self, tokens: Argv, fault: Fault, skip: int = 0) -> None:
        self.injections.append(Injection(frozenset(tokens), fault, skip))

    def _fault_for(self, argv: Argv) -> Fault | None:
        for injection in self.injections:
            if not injection.tokens <= set(argv):
                continue
            if injection.skip > 0:
                injection.skip -= 1
                continue
            return injection.fault
        return None

    def _route(
        self, cmd: Sequence[str], *, cwd: str | None, timeout: float,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        argv = tuple(cmd)
        self.calls.append(Call(argv, cwd, timeout))
        fault = self._fault_for(argv)
        if fault is not None:
            return fault(argv, timeout)
        program = argv[0]
        if program in ("git", "gh", sys.executable):
            return self._passthrough(cmd, cwd=cwd, timeout=timeout, env=env)
        if program == "launchctl":
            return self.launchctl.handle(argv)
        if program == "npm":
            return self._npm(argv, cwd)
        if program in ("uv", "bash"):
            return completed()
        raise AssertionError(f"unrouted argv: {list(argv)}")

    def _npm(self, argv: Argv, cwd: str | None) -> subprocess.CompletedProcess[str]:
        if argv[1:3] == ("run", "build") and cwd is not None:
            out = Path(cwd) / argv[argv.index("--outDir") + 1]
            out.mkdir(parents=True, exist_ok=True)
            (out / "index.html").write_text("<html></html>\n", encoding="utf-8")
        return completed()

    def argvs(self) -> list[Argv]:
        return [call.argv for call in self.calls]

    def launchctl_argvs(self) -> list[Argv]:
        return [argv for argv in self.argvs() if argv[0] == "launchctl"]

    def launchctl_mutations(self) -> list[Argv]:
        return [argv for argv in self.launchctl_argvs() if argv[1] != "print"]

    def mutations(self) -> list[Argv]:
        return [argv for argv in self.argvs() if _mutating(argv)]

    def count(self, tokens: Argv) -> int:
        return sum(1 for argv in self.argvs() if set(tokens) <= set(argv))

    def has(self, tokens: Argv) -> bool:
        return self.count(tokens) > 0

    def index_of(self, tokens: Argv) -> int:
        return next(i for i, argv in enumerate(self.argvs()) if set(tokens) <= set(argv))

    def rolled_back(self) -> bool:
        git_rollback = any(
            argv[0] == "git" and any(verb in argv for verb in ROLLBACK_GIT_VERBS)
            for argv in self.argvs()
        )
        old_slot_activation = any(
            "activate" in argv and "dist.a" in argv for argv in self.argvs()
        )
        return git_rollback or old_slot_activation


def _mutating(argv: Argv) -> bool:
    program = argv[0]
    if program == "launchctl":
        return argv[1] != "print"
    if program == "git":
        return "merge" in argv or any(verb in argv for verb in ROLLBACK_GIT_VERBS)
    return program in ("uv", "npm", "bash", sys.executable)


class FakeClock:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []
        self.on_sleep: Callable[[int], None] | None = None
        monkeypatch.setattr(self_update, "_monotonic", self.monotonic)
        monkeypatch.setattr(self_update, "_sleep", self.sleep)
        monkeypatch.setattr(_ops, "monotonic", self.monotonic)
        monkeypatch.setattr(_ops, "sleep", self.sleep)

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        if self.on_sleep is not None:
            self.on_sleep(len(self.sleeps))

    @property
    def slept(self) -> float:
        return sum(self.sleeps)


class FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self.body if size < 0 else self.body[:size]


def health_body(sha: str, **overrides: object) -> bytes:
    return json.dumps({"ok": True, "served_sha": sha, **overrides}).encode()


class Health:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, sha: str) -> None:
        self.sha = sha
        self.urls: list[str] = []
        self.timeouts: list[float] = []
        self.up: Callable[[], bool] = lambda: True
        self.answer: Callable[[], FakeResponse] | None = None
        monkeypatch.setattr(self_update, "_urlopen", self.urlopen)
        monkeypatch.setattr(_ops, "urlopen", self.urlopen)

    def urlopen(self, url: str, timeout: float) -> FakeResponse:
        self.urls.append(url)
        self.timeouts.append(timeout)
        if not self.up():
            raise urllib.error.URLError("connection refused")
        if self.answer is not None:
            return self.answer()
        return FakeResponse(200, health_body(self.sha))

    def respond_with(self, response: FakeResponse | BaseException) -> None:
        def answer() -> FakeResponse:
            if isinstance(response, BaseException):
                raise response
            return response

        self.answer = answer


class IdleProbe:
    def __init__(self, busy: int | None = 0) -> None:
        self.busy = busy
        self.calls = 0
        self.on_call: Callable[[int], None] | None = None

    def __call__(self) -> bool:
        self.calls += 1
        if self.on_call is not None:
            self.on_call(self.calls)
        return self.busy is not None and self.calls > self.busy


class ExecRecorder:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        monkeypatch.setattr(_extract, "_exec", self._exec)

    def _exec(self, python: str, argv: list[str]) -> None:
        self.calls.append((python, list(argv)))


@dataclass
class ApplyScenario:
    base: Scenario
    launchctl: Launchctl
    commands: CommandRecorder
    clock: FakeClock
    health: Health
    idle: IdleProbe
    marker: Path
    plist_path: Path
    execs: ExecRecorder

    @property
    def repo(self) -> Path:
        return self.base.repo

    @property
    def web(self) -> Path:
        return self.base.repo / "alan" / "web"

    @property
    def target(self) -> str:
        return self.base.fetched

    @property
    def previous(self) -> str:
        return self.base.head

    def inputs(self, **overrides: Any) -> self_update.ApplyInputs:
        values: dict[str, Any] = {
            "detached": False,
            "idle_probe": self.idle,
            "wait_bound_s": 30.0,
            "health_url": HEALTH_URL,
            "plist_path": self.plist_path,
        }
        return self_update.ApplyInputs(**{**values, **overrides})

    def apply(self, **overrides: Any) -> self_update.StatusRecord:
        return self_update.apply(self.repo, self.inputs(**overrides))

    def marker_bytes(self) -> bytes | None:
        return self.marker.read_bytes() if self.marker.exists() else None

    def dist_link(self) -> str | None:
        dist = self.web / "dist"
        return os.readlink(dist) if dist.is_symlink() else None

    def head(self) -> str:
        return git(self.repo, "rev-parse", "HEAD")

    def stored(self) -> dict[str, Any]:
        stored: dict[str, Any] = json.loads(self_update.status_path().read_text(encoding="utf-8"))
        return stored


def seed_web(web: Path, dist: str, head: str) -> None:
    web.mkdir(parents=True)
    (web / "node_modules").mkdir()
    (web / "node_modules" / "keep.txt").write_text("keep\n", encoding="utf-8")
    if dist == "link":
        (web / "dist.a").mkdir()
        (web / "dist.a" / "index.html").write_text("<html>old</html>\n", encoding="utf-8")
        (web / "dist.a" / ".deploy_sha").write_text(head, encoding="utf-8")
        os.symlink("dist.a", web / "dist")
    elif dist == "real":
        (web / "dist").mkdir()
        (web / "dist" / "index.html").write_text("<html>legacy</html>\n", encoding="utf-8")


def seed_marker(marker: Path, kind: str, head: str) -> None:
    marker.parent.mkdir(parents=True, exist_ok=True)
    if kind == "head":
        marker.write_text(f"{head}\n", encoding="utf-8")
    elif kind == "unreachable":
        marker.write_text(f"{UNREACHABLE_SHA}\n", encoding="utf-8")


def _apply_builder(changed: str | None) -> TemplateBuilder:
    def build(pair: RepoPair) -> tuple[str, ...]:
        init_pair(pair)
        commit(pair.clone, "scripts/swap_web_dist.py", SWAP_SCRIPT.read_text(encoding="utf-8"))
        commit(pair.clone, "alan/self_update_kit_marker.txt", "kit archive placeholder\n")
        git(pair.clone, "push", "-q", str(pair.bare), "main")
        exclude = pair.clone / ".git" / "info" / "exclude"
        exclude.parent.mkdir(exist_ok=True)
        exclude.write_text("\n".join(EXCLUDED) + "\n", encoding="utf-8")
        head = git(pair.clone, "rev-parse", "HEAD")
        return head, advance_origin(pair, changed) if changed is not None else head

    return build


def build_apply_scenario(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    changed: str | None = "upstream.txt",
    marker: str = "head",
    dist: str = "link",
    loaded: bool = True,
) -> ApplyScenario:
    monkeypatch.chdir(Path.cwd())
    redirect_env(monkeypatch, tmp_path)
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    pair, (head, fetched) = templated_pair(
        f"apply-changed={changed}", _apply_builder(changed), tmp_path
    )
    (pair.clone / ".venv").mkdir()
    (pair.clone / ".venv" / "keep.txt").write_text("keep\n", encoding="utf-8")
    seed_web(pair.clone / "alan" / "web", dist, head)
    base = Scenario(pair, GhRouter(monkeypatch, bare=pair.bare), head, fetched)
    store_transport_credential()
    base.set_green()
    marker_path = tmp_path / "deploy" / "restarted_sha"
    seed_marker(marker_path, marker, head)
    launchctl = Launchctl(loaded)
    return ApplyScenario(
        base,
        launchctl,
        CommandRecorder(monkeypatch, launchctl),
        FakeClock(monkeypatch),
        Health(monkeypatch, fetched),
        IdleProbe(),
        marker_path,
        home / "Library" / "LaunchAgents" / "ai.alan.plist",
        ExecRecorder(monkeypatch),
    )
