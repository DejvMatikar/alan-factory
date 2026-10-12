from __future__ import annotations

import base64
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.self_update_child import (
    GIT_FETCH_LOG,
    gh_green_shim,
    git_fetch_shim,
    run_self_update,
    sealed_env,
    start_health_server,
    stateful_launchctl_shim,
    stop_health_server,
    succeeding_shim,
)
from tests.self_update_fixtures import TRANSPORT_SENTINEL, git, redirect_env
from tests.self_update_run_fakes import (
    SabotageRepo,
    build_good_repo,
    push_sabotaged_commit,
    read_rejected,
    write_gh_envelopes,
    write_marker,
)

WRONG_VALUE = "other-value-not-a-credential"


@dataclass(frozen=True)
class FullRun:
    result: subprocess.CompletedProcess[str]
    repo: SabotageRepo
    bad_sha: str
    state_root: Path
    gh_calls: Path
    git_calls: Path

    def deploy_file(self, name: str) -> Path:
        return self.state_root.parent / "deploy" / name

    def status(self) -> dict[str, object]:
        text = self.deploy_file("self_update_status.json").read_text(encoding="utf-8")
        payload: dict[str, object] = json.loads(text)
        return payload

    def gh_verdicts(self) -> list[str]:
        return self.gh_calls.read_text(encoding="utf-8").split()

    def git_verdicts(self) -> list[str]:
        return self.git_calls.read_text(encoding="utf-8").split()


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    redirect_env(monkeypatch, tmp_path)


def run_full_apply(tmp_path: Path, *, credential: str | None = TRANSPORT_SENTINEL) -> FullRun:
    repo = build_good_repo(tmp_path)
    bad_sha = push_sabotaged_commit(repo, tmp_path, render_fails=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    runs_file, checks_file = write_gh_envelopes(tmp_path, bad_sha)
    gh_green_shim(bin_dir, runs_file, checks_file)
    stateful_launchctl_shim(bin_dir, tmp_path / "launchctl_state.txt", loaded=True)
    succeeding_shim(bin_dir, "uv")
    succeeding_shim(bin_dir, "npm")
    git_fetch_shim(bin_dir, repo.bare)
    health_url, server = start_health_server(repo.good_sha)
    try:
        env = sealed_env(tmp_path, bin_dir, credential=credential)
        state_root = Path(env["ALAN_STATE_ROOT"])
        write_marker(state_root, repo.good_sha)
        result = run_self_update(
            repo.clone, env, "apply", "--plist-path", str(tmp_path / "ai.alan.plist"),
            "--health-url", health_url,
        )
    finally:
        stop_health_server(server)
    return FullRun(
        result, repo, bad_sha, state_root, bin_dir / "gh.calls", bin_dir / GIT_FETCH_LOG
    )


def test_a_full_run_gets_past_both_gates_only_because_git_and_the_cli_saw_the_stored_credential(
    tmp_path: Path,
) -> None:
    run = run_full_apply(tmp_path)
    assert run.result.returncode == 0, f"stdout={run.result.stdout!r} stderr={run.result.stderr!r}"
    assert set(run.git_verdicts()) == {"match"}
    verdicts = run.gh_verdicts()
    assert len(verdicts) >= 2
    assert set(verdicts) == {"match"}
    assert read_rejected(run.state_root) == frozenset({run.bad_sha})
    phase = json.loads(run.deploy_file("self_update_journal.json").read_text(encoding="utf-8"))
    assert phase["phase"] == "rolled_back"


def test_a_full_run_with_an_empty_store_fails_the_fetch_without_invoking_git_or_the_cli(
    tmp_path: Path,
) -> None:
    run = run_full_apply(tmp_path, credential=None)
    status = run.status()
    assert (status["verdict"], status["reason"]) == ("error", "fetch_failed")
    assert status["fetched_sha"] is None
    assert not run.git_calls.exists()
    assert not run.gh_calls.exists()
    assert git(run.repo.clone, "rev-parse", "HEAD") == run.repo.good_sha
    assert read_rejected(run.state_root) == frozenset()


def test_a_full_run_with_a_wrong_stored_value_fails_the_fetch_before_any_cli_call(
    tmp_path: Path,
) -> None:
    run = run_full_apply(tmp_path, credential=WRONG_VALUE)
    status = run.status()
    assert (status["verdict"], status["reason"]) == ("error", "fetch_failed")
    verdicts = run.git_verdicts()
    assert verdicts
    assert set(verdicts) == {"mismatch"}
    assert not run.gh_calls.exists()
    assert git(run.repo.clone, "rev-parse", "HEAD") == run.repo.good_sha


def test_a_full_run_never_leaks_the_credential_into_its_output_status_record_or_shim_logs(
    tmp_path: Path,
) -> None:
    run = run_full_apply(tmp_path)
    assert set(run.gh_verdicts()) == {"match"}
    assert set(run.git_verdicts()) == {"match"}
    encoded = base64.b64encode(f"x-access-token:{TRANSPORT_SENTINEL}".encode()).decode()
    texts = [
        run.result.stdout,
        run.result.stderr,
        run.deploy_file("self_update_status.json").read_text(encoding="utf-8"),
        run.deploy_file("self_update_journal.json").read_text(encoding="utf-8"),
        run.gh_calls.read_text(encoding="utf-8"),
        run.git_calls.read_text(encoding="utf-8"),
    ]
    for text in texts:
        assert TRANSPORT_SENTINEL not in text
        assert encoded not in text
