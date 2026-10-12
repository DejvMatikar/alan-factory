from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pytest

from alan import deploy_refresh, process_runner, self_update
from alan.self_update import Reason, StatusRecord, Verdict, check
from tests.self_update_fixtures import (
    OTHER_SHA,
    Scenario,
    build_scenario,
    commit,
    completed,
    git,
    make_shallow_clone,
    redirect_env,
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    redirect_env(monkeypatch, tmp_path)


@pytest.fixture
def scenario(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Scenario:
    return build_scenario(monkeypatch, tmp_path)


def verdict_of(record: StatusRecord) -> tuple[Verdict, Reason]:
    return record.verdict, record.reason


def assert_local(scenario: Scenario, verdict: Verdict, reason: Reason) -> StatusRecord:
    record = check(scenario.repo)
    assert verdict_of(record) == (verdict, reason)
    assert scenario.router.calls == []
    return record


def write_rejected(content: str | bytes) -> None:
    path = self_update.rejected_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def stage_new_file(repo: Path) -> None:
    (repo / "new.txt").write_text("new\n", encoding="utf-8")
    git(repo, "add", "new.txt")


def stage_modification(repo: Path) -> None:
    (repo / "tracked.txt").write_text("staged change\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")


def modify_tracked(repo: Path) -> None:
    (repo / "tracked.txt").write_text("unstaged change\n", encoding="utf-8")


def delete_tracked(repo: Path) -> None:
    (repo / "tracked.txt").unlink()


def add_untracked_at_root(repo: Path) -> None:
    (repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")


def add_untracked_in_subdirectory(repo: Path) -> None:
    (repo / "sub" / "deep").mkdir(parents=True)
    (repo / "sub" / "deep" / "untracked.txt").write_text("untracked\n", encoding="utf-8")


DIRTY_CASES: Mapping[str, Callable[[Path], None]] = {
    "staged_new_file": stage_new_file,
    "staged_modification": stage_modification,
    "unstaged_modification": modify_tracked,
    "unstaged_deletion": delete_tracked,
    "untracked_at_root": add_untracked_at_root,
    "untracked_in_subdirectory": add_untracked_in_subdirectory,
}


def diverge(repo: Path) -> None:
    commit(repo, "local.txt")


def test_shallow_clone_is_blocked_before_any_fetch(scenario: Scenario, tmp_path: Path) -> None:
    shallow = make_shallow_clone(scenario.pair, tmp_path)
    record = check(shallow)
    assert verdict_of(record) == (Verdict.BLOCKED, Reason.SHALLOW_CLONE)
    assert record.fetched_sha is None
    assert record.head_sha is None
    assert scenario.fetch_calls == []
    assert scenario.router.calls == []


def test_topic_branch_is_blocked_as_not_on_main(scenario: Scenario) -> None:
    git(scenario.repo, "checkout", "-q", "-b", "topic")
    record = assert_local(scenario, Verdict.BLOCKED, Reason.NOT_ON_MAIN)
    assert record.fetched_sha is None
    assert record.head_sha == scenario.head
    assert scenario.fetch_calls == []


def test_detached_head_is_blocked_as_not_on_main(scenario: Scenario) -> None:
    git(scenario.repo, "checkout", "-q", "--detach")
    assert_local(scenario, Verdict.BLOCKED, Reason.NOT_ON_MAIN)
    assert scenario.fetch_calls == []


def test_unborn_head_on_another_branch_is_blocked_with_no_head(
    scenario: Scenario, tmp_path: Path
) -> None:
    unborn = tmp_path / "unborn"
    subprocess.run(["git", "init", "-q", "-b", "main", str(unborn)], check=True)
    git(unborn, "symbolic-ref", "HEAD", "refs/heads/topic")
    record = check(unborn)
    assert verdict_of(record) == (Verdict.BLOCKED, Reason.NOT_ON_MAIN)
    assert record.head_sha is None


def test_unborn_head_on_main_is_a_git_error(scenario: Scenario, tmp_path: Path) -> None:
    unborn = tmp_path / "unborn"
    subprocess.run(["git", "init", "-q", "-b", "main", str(unborn)], check=True)
    record = check(unborn)
    assert verdict_of(record) == (Verdict.ERROR, Reason.GIT_FAILED)
    assert record.head_sha is None
    assert record.fetched_sha is None
    assert scenario.fetch_calls == []


def test_head_equal_to_fetched_is_up_to_date(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    current = build_scenario(monkeypatch, tmp_path, behind=False)
    record = assert_local(current, Verdict.UP_TO_DATE, Reason.ALREADY_CURRENT)
    assert record.head_sha == current.head
    assert record.fetched_sha == current.head


def test_fetched_commit_on_the_rejected_list_is_blocked(scenario: Scenario) -> None:
    write_rejected(f"{scenario.fetched}\n")
    record = assert_local(scenario, Verdict.BLOCKED, Reason.REJECTED_COMMIT)
    assert record.fetched_sha == scenario.fetched
    assert record.head_sha == scenario.head


def test_rejected_list_with_blank_lines_and_other_commits_still_blocks(
    scenario: Scenario,
) -> None:
    write_rejected(f"\n{OTHER_SHA}\n\n  {scenario.fetched}  \n\n")
    assert_local(scenario, Verdict.BLOCKED, Reason.REJECTED_COMMIT)


@pytest.mark.parametrize(
    "content",
    [None, "", "\n\n  \n", f"{OTHER_SHA}\n", f"\n{OTHER_SHA}\n\n"],
    ids=["missing", "empty", "blank_lines", "non_matching", "non_matching_with_blanks"],
)
def test_rejected_list_without_the_fetched_commit_does_not_block(
    scenario: Scenario, content: str | None
) -> None:
    if content is not None:
        write_rejected(content)
    scenario.set_runs()
    record = check(scenario.repo)
    assert verdict_of(record) == (Verdict.PENDING, Reason.NO_PUSH_RUN)


@pytest.mark.parametrize(
    "content",
    ["not-a-sha\n", "a" * 39 + "\n", "A" * 40 + "\n", "a" * 40 + " extra\n", b"\xff\xfe\x00"],
    ids=["text", "short", "uppercase", "trailing_text", "not_utf8"],
)
def test_malformed_rejected_list_is_an_error(scenario: Scenario, content: str | bytes) -> None:
    write_rejected(content)
    assert_local(scenario, Verdict.ERROR, Reason.REJECTED_LIST_UNREADABLE)


def test_rejected_list_that_is_a_directory_is_an_error(scenario: Scenario) -> None:
    self_update.rejected_path().mkdir(parents=True)
    assert_local(scenario, Verdict.ERROR, Reason.REJECTED_LIST_UNREADABLE)


@pytest.mark.parametrize("arrange", DIRTY_CASES.values(), ids=DIRTY_CASES.keys())
def test_every_kind_of_dirty_tree_is_blocked(
    scenario: Scenario, arrange: Callable[[Path], None]
) -> None:
    arrange(scenario.repo)
    record = assert_local(scenario, Verdict.BLOCKED, Reason.DIRTY_TREE)
    assert record.fetched_sha == scenario.fetched


@pytest.mark.parametrize(
    "arrange_ignored",
    ["file", "directory"],
)
def test_gitignored_paths_never_make_the_tree_dirty(
    scenario: Scenario, arrange_ignored: str
) -> None:
    if arrange_ignored == "file":
        (scenario.repo / "ignored.txt").write_text("ignored\n", encoding="utf-8")
    else:
        (scenario.repo / "ignored_dir").mkdir()
        (scenario.repo / "ignored_dir" / "nested.txt").write_text("ignored\n", encoding="utf-8")
    scenario.set_runs()
    record = check(scenario.repo)
    assert verdict_of(record) == (Verdict.PENDING, Reason.NO_PUSH_RUN)


def test_head_that_is_not_an_ancestor_is_blocked(scenario: Scenario) -> None:
    diverge(scenario.repo)
    record = assert_local(scenario, Verdict.BLOCKED, Reason.NOT_FAST_FORWARD)
    assert record.head_sha != scenario.head


def test_head_ahead_of_the_fetched_commit_is_blocked(scenario: Scenario) -> None:
    git(scenario.repo, "fetch", "-q", str(scenario.pair.bare), "main")
    git(scenario.repo, "merge", "-q", "--ff-only", scenario.fetched)
    diverge(scenario.repo)
    assert_local(scenario, Verdict.BLOCKED, Reason.NOT_FAST_FORWARD)


def test_missing_origin_remote_is_blocked(scenario: Scenario) -> None:
    git(scenario.repo, "remote", "remove", "origin")
    record = assert_local(scenario, Verdict.BLOCKED, Reason.NO_GITHUB_ORIGIN)
    assert record.fetched_sha is None
    assert scenario.fetch_calls == []


@pytest.mark.parametrize(
    "url",
    [
        "https://gitlab.example.com/example-owner/example-repo",
        "https://ghe.example.com/example-owner/example-repo",
        "https://github.com.example.com/example-owner/example-repo",
        "https://github.com/example-owner/example-repo/extra",
    ],
)
def test_origin_that_is_not_a_github_repository_is_blocked(scenario: Scenario, url: str) -> None:
    git(scenario.repo, "remote", "set-url", "origin", url)
    record = assert_local(scenario, Verdict.BLOCKED, Reason.NO_GITHUB_ORIGIN)
    assert record.fetched_sha is None
    assert scenario.fetch_calls == []


def test_shallow_beats_not_on_main(scenario: Scenario, tmp_path: Path) -> None:
    shallow = make_shallow_clone(scenario.pair, tmp_path)
    git(shallow, "checkout", "-q", "-b", "topic")
    record = check(shallow)
    assert verdict_of(record) == (Verdict.BLOCKED, Reason.SHALLOW_CLONE)


def test_not_on_main_beats_up_to_date(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    current = build_scenario(monkeypatch, tmp_path, behind=False)
    git(current.repo, "checkout", "-q", "-b", "topic")
    assert_local(current, Verdict.BLOCKED, Reason.NOT_ON_MAIN)


def test_up_to_date_beats_rejected_and_dirty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    current = build_scenario(monkeypatch, tmp_path, behind=False)
    write_rejected(f"{current.head}\n")
    add_untracked_at_root(current.repo)
    assert_local(current, Verdict.UP_TO_DATE, Reason.ALREADY_CURRENT)


def test_rejected_beats_dirty(scenario: Scenario) -> None:
    write_rejected(f"{scenario.fetched}\n")
    add_untracked_at_root(scenario.repo)
    assert_local(scenario, Verdict.BLOCKED, Reason.REJECTED_COMMIT)


def test_dirty_beats_not_an_ancestor(scenario: Scenario) -> None:
    diverge(scenario.repo)
    add_untracked_at_root(scenario.repo)
    assert_local(scenario, Verdict.BLOCKED, Reason.DIRTY_TREE)


def test_a_missing_github_origin_beats_not_an_ancestor(scenario: Scenario) -> None:
    diverge(scenario.repo)
    git(scenario.repo, "remote", "remove", "origin")
    record = assert_local(scenario, Verdict.BLOCKED, Reason.NO_GITHUB_ORIGIN)
    assert record.fetched_sha is None


def test_the_step_table_runs_in_the_documented_order() -> None:
    assert [step.__name__ for step in self_update._STEPS] == [
        "_step_shallow",
        "_step_branch",
        "_step_origin",
        "_step_fetch",
        "_step_up_to_date",
        "_step_rejected",
        "_step_dirty",
        "_step_ancestry",
    ]


FETCH_FAILURES: dict[str, subprocess.CompletedProcess[str] | BaseException] = {
    "nonzero_exit": completed(returncode=128),
    "timeout": subprocess.TimeoutExpired(["git", "fetch"], 300.0),
    "missing_binary": FileNotFoundError("git"),
    "permission_denied": PermissionError("git"),
}


@pytest.mark.parametrize("failure", FETCH_FAILURES.values(), ids=FETCH_FAILURES.keys())
def test_a_failed_fetch_is_an_error_with_only_the_head_recorded(
    scenario: Scenario, failure: subprocess.CompletedProcess[str] | BaseException
) -> None:
    scenario.router.fail_fetch(failure)
    record = assert_local(scenario, Verdict.ERROR, Reason.FETCH_FAILED)
    assert record.fetched_sha is None
    assert record.head_sha == scenario.head
    assert len(scenario.fetch_calls) == 1


@pytest.mark.parametrize("fetched", ["not-a-sha", "", "A" * 40, "a" * 39])
def test_a_fetched_value_that_is_not_a_commit_hash_is_an_error(
    monkeypatch: pytest.MonkeyPatch, scenario: Scenario, fetched: str
) -> None:
    routed = process_runner.run_subprocess

    def failing(
        cmd: Sequence[str], *, cwd: str | None, timeout: float,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if "rev-parse" in cmd and deploy_refresh.TARGET_REF in cmd:
            return completed(fetched)
        return routed(cmd, cwd=cwd, timeout=timeout, env=env)

    monkeypatch.setattr(process_runner, "run_subprocess", failing)
    record = assert_local(scenario, Verdict.ERROR, Reason.FETCH_FAILED)
    assert record.fetched_sha is None


def test_a_failing_rev_parse_of_the_fetched_ref_is_an_error(
    monkeypatch: pytest.MonkeyPatch, scenario: Scenario
) -> None:
    routed = process_runner.run_subprocess

    def failing(
        cmd: Sequence[str], *, cwd: str | None, timeout: float,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if "rev-parse" in cmd and deploy_refresh.TARGET_REF in cmd:
            return completed(returncode=128)
        return routed(cmd, cwd=cwd, timeout=timeout, env=env)

    monkeypatch.setattr(process_runner, "run_subprocess", failing)
    record = assert_local(scenario, Verdict.ERROR, Reason.FETCH_FAILED)
    assert record.fetched_sha is None


def test_a_failing_git_status_is_a_git_error(
    monkeypatch: pytest.MonkeyPatch, scenario: Scenario
) -> None:
    routed = process_runner.run_subprocess

    def failing(
        cmd: Sequence[str], *, cwd: str | None, timeout: float,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if "status" in cmd:
            return completed(returncode=128)
        return routed(cmd, cwd=cwd, timeout=timeout, env=env)

    monkeypatch.setattr(process_runner, "run_subprocess", failing)
    record = assert_local(scenario, Verdict.ERROR, Reason.GIT_FAILED)
    assert record.fetched_sha == scenario.fetched
    assert record.head_sha == scenario.head


def test_a_failing_symbolic_ref_is_a_git_error_not_a_branch_block(
    monkeypatch: pytest.MonkeyPatch, scenario: Scenario
) -> None:
    routed = process_runner.run_subprocess

    def failing(
        cmd: Sequence[str], *, cwd: str | None, timeout: float,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if "symbolic-ref" in cmd:
            return completed(returncode=128)
        return routed(cmd, cwd=cwd, timeout=timeout, env=env)

    monkeypatch.setattr(process_runner, "run_subprocess", failing)
    record = assert_local(scenario, Verdict.ERROR, Reason.GIT_FAILED)
    assert record.fetched_sha is None
    assert record.head_sha == scenario.head
    assert scenario.fetch_calls == []


def test_a_missing_git_binary_is_a_git_error(
    monkeypatch: pytest.MonkeyPatch, scenario: Scenario
) -> None:
    def missing(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", missing)
    assert_local(scenario, Verdict.ERROR, Reason.GIT_FAILED)


@pytest.mark.parametrize("kind", ["missing_directory", "plain_directory"])
def test_a_path_that_is_not_a_repository_is_a_git_error_with_no_shas(
    scenario: Scenario, tmp_path: Path, kind: str
) -> None:
    target = tmp_path / "nowhere"
    if kind == "plain_directory":
        target.mkdir()
    record = check(target)
    assert verdict_of(record) == (Verdict.ERROR, Reason.GIT_FAILED)
    assert record.fetched_sha is None
    assert record.head_sha is None
    assert scenario.router.calls == []
