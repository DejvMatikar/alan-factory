from __future__ import annotations

import atexit
import base64
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from alan import process_runner, roots, secrets_store

OWNER = "example-owner"
REPO = "example-repo"
TRANSPORT_SENTINEL = "transport-sentinel-not-a-credential"
ORIGIN_URL = f"https://github.com/{OWNER}/{REPO}"
SUITE_ID = 4242
OTHER_SHA = "f" * 40
RUNS_NEEDLE = "actions/workflows"
CHECKS_NEEDLE = "check-suites"
ACTIONS_APP: Mapping[str, object] = {
    "id": 15368, "slug": "github-actions", "name": "GitHub Actions",
}
FOREIGN_APP: Mapping[str, object] = {"id": 99, "slug": "some-other-app", "name": "Other App"}
MISSING = object()

Responder = Callable[[], "subprocess.CompletedProcess[str]"]


def isolate_secrets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_SECRETS_STORE_ROOT", str(tmp_path / "secrets"))


def store_transport_credential(
    owner: str = OWNER, repo: str = REPO, value: str = TRANSPORT_SENTINEL
) -> None:
    secrets_store.write_secret(f"{owner}/{repo}", "transport", value)


Mutation = Callable[[Path], object]
SLUG = f"{OWNER}/{REPO}"


def record_path() -> Path:
    (path,) = roots.secrets_store_root().glob("*.json")
    return path


def write_bytes(data: bytes) -> Mutation:
    def mutate(path: Path) -> None:
        path.write_bytes(data)

    return mutate


def write_json(payload: object) -> Mutation:
    return write_bytes(json.dumps(payload).encode("utf-8"))


def replace_with_directory(path: Path) -> None:
    path.unlink()
    path.mkdir()


def delete_record(path: Path) -> bool:
    del path
    return secrets_store.delete_secret(SLUG, "transport")


UNUSABLE_RECORDS: dict[str, Mutation] = {
    "absent": delete_record,
    "directory_at_record_path": replace_with_directory,
    "not_utf8": write_bytes(b"\xff\xfe" + TRANSPORT_SENTINEL.encode("utf-8")),
    "not_json": write_bytes(f"{TRANSPORT_SENTINEL} not json".encode()),
    "deeply_nested_json": write_bytes(b"[" * 100000),
    "json_list": write_json([TRANSPORT_SENTINEL]),
    "json_string": write_json(TRANSPORT_SENTINEL),
    "json_null": write_json(None),
    "value_missing": write_json({"other": TRANSPORT_SENTINEL}),
    "value_null": write_json({"value": None, "other": TRANSPORT_SENTINEL}),
    "value_number": write_json({"value": 7, "other": TRANSPORT_SENTINEL}),
    "value_list": write_json({"value": [TRANSPORT_SENTINEL]}),
    "value_empty": write_json({"value": "", "other": TRANSPORT_SENTINEL}),
    "value_whitespace": write_json({"value": " \t\n ", "other": TRANSPORT_SENTINEL}),
}


def forbid_credential_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden() -> Path:
        raise AssertionError("the credential store was read")

    monkeypatch.setattr(roots, "secrets_store_root", forbidden)


def count_credential_reads(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    reads: list[Path] = []
    real = roots.secrets_store_root

    def counting() -> Path:
        reads.append(real())
        return reads[-1]

    monkeypatch.setattr(roots, "secrets_store_root", counting)
    return reads


def redirect_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    isolate_secrets(monkeypatch, tmp_path)
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("GIT_DIR", raising=False)
    monkeypatch.delenv("GIT_WORK_TREE", raising=False)
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.com")


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def commit(repo: Path, name: str, content: str = "content\n") -> str:
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", f"add {name}")
    return git(repo, "rev-parse", "HEAD")


@dataclass(frozen=True)
class RepoPair:
    bare: Path
    clone: Path


TemplateBuilder = Callable[[RepoPair], tuple[str, ...]]
_TEMPLATE_ROOT: list[Path] = []
_TEMPLATES: dict[str, tuple[Path, tuple[str, ...]]] = {}
_EXECUTABLES: dict[str, Path] = {}


def _template_root() -> Path:
    if not _TEMPLATE_ROOT:
        root = Path(tempfile.mkdtemp(prefix="self-update-templates-"))
        atexit.register(shutil.rmtree, root, True)
        _TEMPLATE_ROOT.append(root)
    return _TEMPLATE_ROOT[0]


def link_executable(path: Path, body: str) -> None:
    source = _EXECUTABLES.get(body)
    if source is None:
        source = _template_root() / f"executable-{len(_EXECUTABLES)}"
        source.write_text(body, encoding="utf-8")
        source.chmod(0o755)
        _EXECUTABLES[body] = source
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(source)


def _pair_under(root: Path) -> RepoPair:
    return RepoPair(root / "origin.git", root / "clone")


def _build_template(key: str, build: TemplateBuilder) -> tuple[Path, tuple[str, ...]]:
    root = _template_root() / str(len(_TEMPLATES))
    root.mkdir()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
        patch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        for role in ("AUTHOR", "COMMITTER"):
            patch.setenv(f"GIT_{role}_NAME", "test")
            patch.setenv(f"GIT_{role}_EMAIL", "test@example.com")
        values = build(_pair_under(root))
    _TEMPLATES[key] = (root, values)
    return _TEMPLATES[key]


def templated_pair(
    key: str, build: TemplateBuilder, dest: Path
) -> tuple[RepoPair, tuple[str, ...]]:
    root, values = _TEMPLATES.get(key) or _build_template(key, build)
    source = _pair_under(root)
    pair = _pair_under(dest)
    shutil.copytree(source.bare, pair.bare, symlinks=True)
    shutil.copytree(source.clone, pair.clone, symlinks=True)
    return pair, values


def init_pair(pair: RepoPair) -> None:
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(pair.bare)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(pair.clone)], check=True, capture_output=True
    )
    git(pair.clone, "remote", "add", "origin", str(pair.bare))
    commit(pair.clone, ".gitignore", "ignored.txt\nignored_dir/\n")
    commit(pair.clone, "tracked.txt")
    git(pair.clone, "push", "-q", "origin", "main")
    git(pair.clone, "remote", "set-url", "origin", ORIGIN_URL)


def advance_origin(pair: RepoPair, name: str = "upstream.txt") -> str:
    other = pair.bare.parent / f"other-{name}"
    subprocess.run(
        ["git", "clone", "-q", str(pair.bare), str(other)], check=True, capture_output=True
    )
    sha = commit(other, name)
    git(other, "push", "-q", "origin", "main")
    return sha


def make_shallow_clone(pair: RepoPair, tmp_path: Path) -> Path:
    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", f"file://{pair.bare}", str(shallow)],
        check=True,
        capture_output=True,
    )
    assert git(shallow, "rev-parse", "--is-shallow-repository") == "true"
    return shallow


def completed(
    stdout: str = "", returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def ok(payload: object) -> subprocess.CompletedProcess[str]:
    return completed(json.dumps(payload))


FetchEnv = Mapping[str, str | None]
FetchCall = tuple[list[str], FetchEnv | None]
FETCH_URL_PREFIX = "https://github.com/"
HEADER_VALUE_KEY = "GIT_CONFIG_VALUE_1"
HEADER_PREFIX = "Authorization: Basic "
FETCH_USER = "x-access-token"


def is_credentialed_fetch(argv: Sequence[str]) -> bool:
    return (
        argv[0] == "git"
        and "fetch" in argv
        and any(part.startswith(FETCH_URL_PREFIX) for part in argv)
    )


def decoded_header_credential(env: FetchEnv | None) -> str | None:
    header = (env or {}).get(HEADER_VALUE_KEY) or ""
    if not header.startswith(HEADER_PREFIX):
        return None
    try:
        return base64.b64decode(header.removeprefix(HEADER_PREFIX), validate=True).decode()
    except ValueError:
        return None


def expected_credential(token: str) -> str:
    return f"{FETCH_USER}:{token}"


def local_fetch_argv(argv: Sequence[str], bare: Path) -> list[str]:
    repo = argv[argv.index("-C") + 1]
    return [
        "git", "-C", repo, "fetch", "--no-tags", "--no-write-fetch-head", "--refmap=",
        str(bare), argv[-1],
    ]


class GhRouter:
    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bare: Path | None = None,
        expected: str = TRANSPORT_SENTINEL,
    ) -> None:
        self.calls: list[list[str]] = []
        self.envs: list[FetchEnv | None] = []
        self.routes: dict[str, Responder] = {}
        self.fetch_calls: list[FetchCall] = []
        self.fetch_fault: Responder | None = None
        self.bare = bare
        self.expected = expected
        original = process_runner.run_subprocess

        def serve_fetch(
            argv: list[str], timeout: float, env: FetchEnv | None
        ) -> subprocess.CompletedProcess[str]:
            self.fetch_calls.append((argv, env))
            if self.fetch_fault is not None:
                return self.fetch_fault()
            if decoded_header_credential(env) != expected_credential(self.expected):
                return completed(returncode=128)
            assert self.bare is not None
            return original(local_fetch_argv(argv, self.bare), cwd=None, timeout=timeout, env=None)

        def route(
            cmd: Sequence[str], *, cwd: str | None, timeout: float,
            env: FetchEnv | None = None,
        ) -> subprocess.CompletedProcess[str]:
            argv = list(cmd)
            if is_credentialed_fetch(argv):
                return serve_fetch(argv, timeout, env)
            if argv[0] != "gh":
                return original(cmd, cwd=cwd, timeout=timeout, env=env)
            self.calls.append(argv)
            self.envs.append(env)
            for needle, respond in self.routes.items():
                if needle in argv[-1]:
                    return respond()
            raise AssertionError(f"unrouted gh argv: {argv}")

        monkeypatch.setattr(process_runner, "run_subprocess", route)

    def respond(self, needle: str, value: subprocess.CompletedProcess[str] | BaseException) -> None:
        self.routes[needle] = _responder(value)

    def fail_fetch(self, value: subprocess.CompletedProcess[str] | BaseException) -> None:
        self.fetch_fault = _responder(value)


def _responder(value: subprocess.CompletedProcess[str] | BaseException) -> Responder:
    def responder() -> subprocess.CompletedProcess[str]:
        if isinstance(value, BaseException):
            raise value
        return value

    return responder


def _build(defaults: dict[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    merged = {**defaults, **overrides}
    return {key: value for key, value in merged.items() if value is not MISSING}


def push_run(sha: str, **overrides: Any) -> dict[str, Any]:
    defaults = {
        "id": 30433642,
        "name": "tests",
        "node_id": "WFR_kwLOexample",
        "check_suite_id": SUITE_ID,
        "check_suite_node_id": "CS_kwDOexample",
        "head_branch": "main",
        "head_sha": sha,
        "path": ".github/workflows/tests.yml",
        "run_number": 10,
        "run_attempt": 1,
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "workflow_id": 159038,
    }
    return _build(defaults, overrides)


def gate_check(sha: str, **overrides: Any) -> dict[str, Any]:
    defaults = {
        "id": 100,
        "name": "gate",
        "head_sha": sha,
        "node_id": "CR_kwDOexample",
        "external_id": "",
        "status": "completed",
        "conclusion": "success",
        "check_suite": {"id": SUITE_ID},
        "app": dict(ACTIONS_APP),
        "pull_requests": [],
    }
    return _build(defaults, overrides)


def runs_envelope(runs: Sequence[object]) -> dict[str, object]:
    return {"total_count": len(runs), "workflow_runs": list(runs)}


def checks_envelope(checks: Sequence[object]) -> dict[str, object]:
    return {"total_count": len(checks), "check_runs": list(checks)}


@dataclass
class Scenario:
    pair: RepoPair
    router: GhRouter
    head: str
    fetched: str

    @property
    def fetch_calls(self) -> list[FetchCall]:
        return self.router.fetch_calls

    @property
    def repo(self) -> Path:
        return self.pair.clone

    def set_runs(self, *runs: object) -> None:
        self.router.respond(RUNS_NEEDLE, ok(runs_envelope(runs)))

    def set_checks(self, *checks: object) -> None:
        self.router.respond(CHECKS_NEEDLE, ok(checks_envelope(checks)))

    def set_green(self) -> None:
        self.set_runs(push_run(self.fetched))
        self.set_checks(gate_check(self.fetched))


def _scenario_builder(behind: bool) -> TemplateBuilder:
    def build(pair: RepoPair) -> tuple[str, ...]:
        init_pair(pair)
        head = git(pair.clone, "rev-parse", "HEAD")
        return head, advance_origin(pair) if behind else head

    return build


def build_scenario(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, behind: bool = True
) -> Scenario:
    isolate_secrets(monkeypatch, tmp_path)
    store_transport_credential()
    pair, (head, fetched) = templated_pair(
        f"scenario-behind={behind}", _scenario_builder(behind), tmp_path
    )
    return Scenario(pair, GhRouter(monkeypatch, bare=pair.bare), head, fetched)
