from __future__ import annotations

import ast
import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from alan import process_runner
from alan.self_update import _ops, _origin
from tests._deploy_render_support import EXAMPLE_ORIGIN, scratch_checkout
from tests.self_update_child import (
    run_self_update,
    sealed_env,
    start_health_server,
    stateful_launchctl_shim,
    stop_health_server,
    succeeding_shim,
)
from tests.self_update_fixtures import completed, git, redirect_env
from tests.self_update_run_fakes import (
    build_good_repo,
    push_sabotaged_commit,
    read_rejected,
    write_journal,
)

_OPS_SOURCE = Path(__file__).resolve().parent.parent / "alan" / "self_update" / "_ops.py"
_OWNER_URL_WRITER = (
    "#!/bin/sh\n"
    "printf '%s\\n' 'https://github.com/DejvMatikar/alan.git' > \"$HOME/owner-url-written\"\n"
    "exit 0\n"
)
_REFUSING_RENDERER = "#!/bin/sh\nexit 78\n"
_ORIGIN_READING_RENDERER = (
    "#!/bin/bash\n"
    'REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"\n'
    '( unset GIT_DIR GIT_WORK_TREE; git -C "$REPO_DIR" remote get-url origin ) > "__SEEN__"\n'
    "exit 0\n"
)
_UNUSABLE_ORIGINS = {
    "no-origin": None,
    "other-host": "https://gitlab.example.com/example-owner/example-repo.git",
    "uppercase-host": "https://GitHub.com/example-owner/example-repo.git",
    "explicit-port": "https://github.com:443/example-owner/example-repo.git",
    "trailing-slash": "https://github.com/example-owner/example-repo/",
}
_GOOD_ORIGIN = "git@github.com:example-owner/example-repo.git"
_UNUSABLE_ORIGIN = "https://gitlab.example.com/example-owner/example-repo.git"


class RenderRecorder:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, code: int) -> None:
        self.scripts: list[tuple[str, ...]] = []
        self.environments: list[Mapping[str, str | None] | None] = []
        self._code = code
        self._real = process_runner.run_subprocess
        monkeypatch.setattr(process_runner, "run_subprocess", self._run)

    def _run(
        self,
        cmd: Sequence[str],
        *,
        cwd: str | None,
        timeout: float,
        env: Mapping[str, str | None] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        argv = tuple(cmd)
        if argv[0] != "bash":
            return self._real(cmd, cwd=cwd, timeout=timeout, env=env)
        self.scripts.append(argv)
        self.environments.append(env)
        return completed(returncode=self._code)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    redirect_env(monkeypatch, tmp_path)


def test_a_usable_origin_runs_the_renderer_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = scratch_checkout(tmp_path, EXAMPLE_ORIGIN)
    recorder = RenderRecorder(monkeypatch, 0)
    _ops.render_launch_config(repo)
    assert recorder.scripts == [("bash", str(repo / "deploy" / "render_plist.sh"))]


def test_the_renderer_runs_with_the_origin_pinned_to_the_validated_slot_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = scratch_checkout(tmp_path, EXAMPLE_ORIGIN)
    recorder = RenderRecorder(monkeypatch, 0)
    _ops.render_launch_config(repo)
    assert recorder.environments == [_origin.pinned_environment("example-owner", "example-repo")]
    environment = recorder.environments[0]
    assert environment is not None
    assert environment["GIT_CONFIG_VALUE_1"] == EXAMPLE_ORIGIN


class RacingOrigin:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, repo: Path, unusable: str) -> None:
        self._repo = repo
        self._unusable = unusable
        self._real = process_runner.run_subprocess
        monkeypatch.setattr(process_runner, "run_subprocess", self._run)

    def _run(
        self,
        cmd: Sequence[str],
        *,
        cwd: str | None,
        timeout: float,
        env: Mapping[str, str | None] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "bash":
            git(self._repo, "remote", "set-url", "origin", self._unusable)
        return self._real(cmd, cwd=cwd, timeout=timeout, env=env)


def install_origin_reading_renderer(repo: Path, seen: Path) -> None:
    script = repo / "deploy" / "render_plist.sh"
    script.write_text(_ORIGIN_READING_RENDERER.replace("__SEEN__", str(seen)), encoding="utf-8")


def test_a_renderer_reading_the_origin_after_it_turned_unusable_still_sees_the_validated_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = scratch_checkout(tmp_path, EXAMPLE_ORIGIN)
    seen = tmp_path / "origin-seen"
    install_origin_reading_renderer(repo, seen)
    RacingOrigin(monkeypatch, repo, _UNUSABLE_ORIGIN)
    _ops.render_launch_config(repo)
    assert seen.read_text(encoding="utf-8").strip() == EXAMPLE_ORIGIN
    assert git(repo, "remote", "get-url", "origin") == _UNUSABLE_ORIGIN


def test_a_renderer_reading_the_origin_without_the_pin_would_see_the_unusable_one(
    tmp_path: Path,
) -> None:
    repo = scratch_checkout(tmp_path, EXAMPLE_ORIGIN)
    seen = tmp_path / "origin-seen"
    install_origin_reading_renderer(repo, seen)
    git(repo, "remote", "set-url", "origin", _UNUSABLE_ORIGIN)
    _ops.run_step(["bash", str(repo / "deploy" / "render_plist.sh")], 30.0, cwd=repo)
    assert seen.read_text(encoding="utf-8").strip() == _UNUSABLE_ORIGIN


@pytest.mark.parametrize("origin", _UNUSABLE_ORIGINS.values(), ids=_UNUSABLE_ORIGINS.keys())
def test_an_unusable_origin_skips_the_renderer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, origin: str | None
) -> None:
    repo = scratch_checkout(tmp_path, origin)
    recorder = RenderRecorder(monkeypatch, 0)
    _ops.render_launch_config(repo)
    assert recorder.scripts == []


def test_a_refusal_from_the_renderer_keeps_the_plist_and_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = scratch_checkout(tmp_path, EXAMPLE_ORIGIN)
    recorder = RenderRecorder(monkeypatch, _ops.RENDER_REFUSED_EXIT)
    _ops.render_launch_config(repo)
    assert len(recorder.scripts) == 1


@pytest.mark.parametrize("code", [1, 2, 77, 79, 127])
def test_any_other_nonzero_exit_from_the_renderer_still_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    repo = scratch_checkout(tmp_path, EXAMPLE_ORIGIN)
    RenderRecorder(monkeypatch, code)
    with pytest.raises(RuntimeError):
        _ops.render_launch_config(repo)


def test_the_refusal_exit_is_the_one_the_renderer_uses() -> None:
    assert _ops.RENDER_REFUSED_EXIT == 78


@pytest.mark.parametrize(
    ("origin", "code"),
    [(EXAMPLE_ORIGIN, 78), (_UNUSABLE_ORIGIN, 0)],
    ids=["renderer-refuses", "origin-unusable"],
)
def test_a_service_restart_carries_on_after_a_render_that_kept_the_plist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, origin: str, code: int
) -> None:
    repo = scratch_checkout(tmp_path, origin)
    RenderRecorder(monkeypatch, code)
    steps: list[str] = []
    monkeypatch.setattr(_ops, "teardown_service", lambda: steps.append("teardown"))
    monkeypatch.setattr(_ops, "bootstrap_service", lambda plist: steps.append("bootstrap"))
    monkeypatch.setattr(_ops, "kickstart_service", lambda: steps.append("kickstart"))
    _ops.restart_service(repo, tmp_path / "ai.alan.plist")
    assert steps == ["teardown", "bootstrap", "kickstart"]


def test_the_ops_module_never_imports_the_package_init() -> None:
    tree = ast.parse(_OPS_SOURCE.read_text(encoding="utf-8"))
    plain = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    from_alan = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "alan"
        for alias in node.names
    }
    from_package = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "alan.self_update"
        for alias in node.names
    }
    assert "alan.self_update" not in plain
    assert "self_update" not in from_alan
    assert from_package == {"_origin"}


@dataclass(frozen=True)
class LeftoverRollback:
    returncode: int
    stdout: str
    stderr: str
    home: Path
    state_root: Path
    clone: Path
    good_sha: str
    bad_sha: str
    launchctl_state: Path

    def journal_phase(self) -> str:
        path = self.state_root.parent / "deploy" / "self_update_journal.json"
        return str(json.loads(path.read_text(encoding="utf-8"))["phase"])

    def marker(self) -> str:
        return (self.state_root.parent / "deploy" / "restarted_sha").read_text(encoding="utf-8")

    @property
    def owner_url_probe(self) -> Path:
        return self.home / "owner-url-written"


def run_leftover_rollback(tmp_path: Path, *, origin: str, render_script: str) -> LeftoverRollback:
    repo = build_good_repo(tmp_path, render_script=render_script)
    bad_sha = push_sabotaged_commit(repo, tmp_path, render_fails=True)
    git(repo.clone, "fetch", "-q", str(repo.bare), "main")
    git(repo.clone, "checkout", "-fB", "main", bad_sha)
    git(repo.clone, "remote", "set-url", "origin", origin)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    launchctl_state = tmp_path / "launchctl_state.txt"
    stateful_launchctl_shim(bin_dir, launchctl_state, loaded=True)
    succeeding_shim(bin_dir, "uv")
    health_url, server = start_health_server(repo.good_sha)
    try:
        env = sealed_env(tmp_path, bin_dir)
        state_root = Path(env["ALAN_STATE_ROOT"])
        write_journal(
            state_root,
            {
                "phase": "rolling_back",
                "target_sha": bad_sha,
                "previous_sha": repo.good_sha,
                "previous_slot": "dist.a",
                "restarted": True,
            },
        )
        result = run_self_update(
            repo.clone, env, "apply", "--plist-path", str(tmp_path / "ai.alan.plist"),
            "--health-url", health_url,
        )
    finally:
        stop_health_server(server)
    return LeftoverRollback(
        result.returncode,
        result.stdout,
        result.stderr,
        Path(env["HOME"]),
        state_root,
        repo.clone,
        repo.good_sha,
        bad_sha,
        launchctl_state,
    )


def assert_rolled_back_with_the_service_restarted(run: LeftoverRollback) -> None:
    detail = f"stdout={run.stdout!r} stderr={run.stderr!r}"
    assert run.returncode == 0, detail
    assert git(run.clone, "rev-parse", "HEAD") == run.good_sha
    assert run.journal_phase() == "rolled_back"
    assert read_rejected(run.state_root) == frozenset({run.bad_sha})
    assert run.launchctl_state.read_text(encoding="utf-8") == "1"
    assert run.marker() == f"{run.good_sha}\n"


def test_a_rollback_never_runs_an_owner_url_renderer_on_an_unusable_origin(
    tmp_path: Path,
) -> None:
    run = run_leftover_rollback(
        tmp_path, origin=_UNUSABLE_ORIGIN, render_script=_OWNER_URL_WRITER
    )
    assert_rolled_back_with_the_service_restarted(run)
    assert not run.owner_url_probe.exists()


def test_the_same_rollback_runs_the_restored_renderer_when_the_origin_is_usable(
    tmp_path: Path,
) -> None:
    run = run_leftover_rollback(tmp_path, origin=_GOOD_ORIGIN, render_script=_OWNER_URL_WRITER)
    assert_rolled_back_with_the_service_restarted(run)
    assert run.owner_url_probe.is_file()


def test_a_rollback_to_a_renderer_that_refuses_is_still_rolled_back_and_restarted(
    tmp_path: Path,
) -> None:
    run = run_leftover_rollback(tmp_path, origin=_GOOD_ORIGIN, render_script=_REFUSING_RENDERER)
    assert_rolled_back_with_the_service_restarted(run)
