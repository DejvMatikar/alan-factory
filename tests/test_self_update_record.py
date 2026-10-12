from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from alan import roots, self_update
from alan.self_update import ALLOWED, Reason, StatusRecord, Verdict, check
from tests.self_update_fixtures import (
    RUNS_NEEDLE,
    Scenario,
    build_scenario,
    completed,
    git,
    push_run,
    redirect_env,
)

HEX40 = re.compile(r"\A[0-9a-f]{40}\Z")
STATUS_NAME = "self_update_status.json"
NINE_KEYS = [
    "verdict", "reason", "fetched_sha", "head_sha", "checked_at",
    "path", "target_sha", "previous_sha", "previous_slot",
]
APPLY_KEYS = NINE_KEYS[5:]
SHA = "a" * 40
STAMP = "2026-01-01T00:00:00+00:00"
STEP_REASONS = {
    "fast_forward_failed", "render_failed", "dependency_sync_failed", "web_build_failed",
    "teardown_failed", "bootstrap_failed", "kickstart_failed", "verification_timeout",
    "verification_mismatch",
}


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    redirect_env(monkeypatch, tmp_path)


@pytest.fixture
def scenario(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Scenario:
    return build_scenario(monkeypatch, tmp_path)


def stored_status() -> dict[str, Any]:
    stored: dict[str, Any] = json.loads(self_update.status_path().read_text(encoding="utf-8"))
    return stored


def test_the_stored_record_has_exactly_the_nine_documented_keys(scenario: Scenario) -> None:
    scenario.set_green()
    check(scenario.repo)
    stored = stored_status()
    assert list(stored) == NINE_KEYS
    assert stored["verdict"] == "green"
    assert stored["reason"] == "ci_proved_green"
    assert stored["fetched_sha"] == scenario.fetched
    assert stored["head_sha"] == scenario.head


def test_the_stored_timestamp_is_utc_iso_8601(scenario: Scenario) -> None:
    scenario.set_green()
    check(scenario.repo)
    parsed = datetime.fromisoformat(stored_status()["checked_at"])
    assert parsed.utcoffset() == timedelta(0)


def test_the_returned_record_matches_the_stored_one(scenario: Scenario) -> None:
    scenario.set_green()
    record = check(scenario.repo)
    assert record.as_json() == stored_status()


@pytest.mark.parametrize("verdict", list(Verdict))
def test_a_reason_outside_the_verdict_vocabulary_is_refused(verdict: Verdict) -> None:
    foreign = next(reason for reason in Reason if reason not in ALLOWED[verdict])
    with pytest.raises(ValueError):
        StatusRecord(verdict, foreign, None, None, "2026-01-01T00:00:00+00:00")


@pytest.mark.parametrize("verdict", list(Verdict))
def test_every_allowed_reason_builds_a_record(verdict: Verdict) -> None:
    for reason in ALLOWED[verdict]:
        record = StatusRecord(verdict, reason, None, None, "2026-01-01T00:00:00+00:00")
        assert record.as_json()["reason"] == reason.value


def test_every_verdict_has_reasons_and_every_reason_belongs_to_exactly_one_verdict() -> None:
    assert set(ALLOWED) == set(Verdict)
    assert all(ALLOWED[verdict] for verdict in Verdict)
    owners = [verdict for reason in Reason for verdict in Verdict if reason in ALLOWED[verdict]]
    assert len(owners) == len(list(Reason))


def test_the_verdict_set_is_the_documented_one() -> None:
    assert {verdict.value for verdict in Verdict} == {
        "blocked", "up_to_date", "pending", "red", "error", "green", "applied", "failed",
    }


def test_the_apply_path_vocabulary_is_restart_and_restart_free() -> None:
    assert {path.value for path in self_update.ApplyPath} == {"restart", "restart_free"}


def test_applied_admits_only_target_verified() -> None:
    assert ALLOWED[Verdict.APPLIED] == {Reason.TARGET_VERIFIED}


def test_failed_admits_exactly_the_nine_step_reasons() -> None:
    assert {reason.value for reason in ALLOWED[Verdict.FAILED]} == STEP_REASONS


def test_the_service_probe_failure_belongs_to_error_and_nothing_else() -> None:
    owners = [v for v in Verdict if Reason.SERVICE_PROBE_FAILED in ALLOWED[v]]
    assert owners == [Verdict.ERROR]


@pytest.mark.parametrize("state", ["green", "red", "pending", "dirty", "up_to_date", "error"])
def test_every_check_verdict_stores_four_null_apply_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, state: str
) -> None:
    scenario = build_scenario(monkeypatch, tmp_path, behind=state != "up_to_date")
    arrangements: dict[str, Callable[[Scenario], None]] = {
        "green": arrange_green,
        "red": arrange_red,
        "pending": lambda s: s.set_runs(),
        "dirty": arrange_dirty,
        "up_to_date": arrange_nothing,
        "error": arrange_read_error,
    }
    arrangements[state](scenario)
    check(scenario.repo)
    stored = stored_status()
    assert list(stored) == NINE_KEYS
    assert [stored[key] for key in APPLY_KEYS] == [None] * 4


def test_the_apply_fields_default_to_none() -> None:
    record = StatusRecord(Verdict.GREEN, Reason.CI_PROVED_GREEN, None, None, STAMP)
    assert [record.as_json()[key] for key in APPLY_KEYS] == [None] * 4


def test_a_full_apply_record_serialises_all_nine_keys_in_order() -> None:
    record = StatusRecord(
        Verdict.APPLIED, Reason.TARGET_VERIFIED, SHA, SHA, STAMP,
        self_update.ApplyPath.RESTART, SHA, "b" * 40, "dist.a",
    )
    assert record.as_json() == {
        "verdict": "applied", "reason": "target_verified", "fetched_sha": SHA,
        "head_sha": SHA, "checked_at": STAMP, "path": "restart", "target_sha": SHA,
        "previous_sha": "b" * 40, "previous_slot": "dist.a",
    }
    assert list(record.as_json()) == NINE_KEYS


BAD_SHAS = ["", "A" * 40, "a" * 39, "a" * 41, "g" * 40, 7, True, ["a" * 40]]
BAD_APPLY_VALUES: dict[str, list[Any]] = {
    "path": ["sideways", "restart", "restart_free", "", 7],
    "target_sha": BAD_SHAS,
    "previous_sha": BAD_SHAS,
    "previous_slot": ["dist.c", "../dist.a", "dist.a/", "dist", "", 7, ["dist.a"]],
}
BAD_APPLY_CASES = [(field, bad) for field, values in BAD_APPLY_VALUES.items() for bad in values]


@pytest.mark.parametrize(("field", "bad"), BAD_APPLY_CASES)
def test_an_apply_field_outside_its_vocabulary_is_refused(field: str, bad: Any) -> None:
    with pytest.raises(ValueError, match=field):
        StatusRecord(Verdict.FAILED, Reason.RENDER_FAILED, SHA, SHA, STAMP, **{field: bad})


@pytest.mark.parametrize(
    ("field", "good"),
    [
        ("path", self_update.ApplyPath.RESTART_FREE),
        ("target_sha", SHA),
        ("previous_sha", SHA),
        ("previous_slot", "dist.b"),
        ("path", None),
        ("target_sha", None),
        ("previous_sha", None),
        ("previous_slot", None),
    ],
)
def test_an_apply_field_inside_its_vocabulary_or_none_is_accepted(field: str, good: Any) -> None:
    record = StatusRecord(Verdict.FAILED, Reason.RENDER_FAILED, SHA, SHA, STAMP, **{field: good})
    assert getattr(record, field) == good


def apply_inputs(bound: float, url: str) -> self_update.ApplyInputs:
    return self_update.ApplyInputs(False, lambda: True, bound, url, Path("ai.alan.plist"))


@pytest.mark.parametrize(
    ("bound", "url"),
    [
        (-1.0, "http://health.invalid/"),
        (float("nan"), "http://health.invalid/"),
        (float("inf"), "http://health.invalid/"),
        (float("-inf"), "http://health.invalid/"),
        (1.0, "ftp://health.invalid/"),
        (1.0, "file:///etc/passwd"),
        (1.0, "http://"),
        (1.0, "http://:8080/api/health"),
        (1.0, "https://user@:8443/api/health"),
        (1.0, "health.invalid/api/health"),
        (1.0, ""),
    ],
)
def test_the_inputs_refuse_an_unbounded_wait_or_a_non_http_health_url(
    bound: float, url: str
) -> None:
    with pytest.raises(ValueError):
        apply_inputs(bound, url)


@pytest.mark.parametrize("url", ["http://health.invalid/x", "https://health.invalid:8443/x"])
@pytest.mark.parametrize("bound", [0.0, 45.5])
def test_the_inputs_accept_a_finite_non_negative_bound_and_an_http_or_https_health_url(
    bound: float, url: str
) -> None:
    accepted = apply_inputs(bound, url)
    assert (accepted.wait_bound_s, accepted.health_url) == (bound, url)


def test_the_status_path_is_inside_the_deploy_marker_root(tmp_path: Path) -> None:
    assert self_update.status_path() == roots.deploy_marker_root() / STATUS_NAME
    assert self_update.status_path() == tmp_path / "deploy" / STATUS_NAME
    assert self_update.rejected_path() == tmp_path / "deploy" / "self_update_rejected"


def test_the_marker_directory_is_created_and_holds_only_the_record(
    scenario: Scenario, tmp_path: Path
) -> None:
    assert not (tmp_path / "deploy").exists()
    scenario.set_green()
    check(scenario.repo)
    assert [entry.name for entry in (tmp_path / "deploy").iterdir()] == [STATUS_NAME]


def test_a_second_check_replaces_the_record_without_leaving_a_temporary_file(
    scenario: Scenario, tmp_path: Path
) -> None:
    scenario.set_runs()
    check(scenario.repo)
    scenario.set_green()
    check(scenario.repo)
    assert [entry.name for entry in (tmp_path / "deploy").iterdir()] == [STATUS_NAME]
    assert stored_status()["verdict"] == "green"


def test_a_failed_replace_keeps_the_prior_record_intact(
    monkeypatch: pytest.MonkeyPatch, scenario: Scenario, tmp_path: Path
) -> None:
    scenario.set_runs()
    check(scenario.repo)
    prior = self_update.status_path().read_bytes()

    def refuse(*args: object, **kwargs: object) -> None:
        raise OSError("replace refused")

    monkeypatch.setattr(os, "replace", refuse)
    scenario.set_green()
    with pytest.raises(OSError, match="replace refused"):
        check(scenario.repo)
    assert self_update.status_path().read_bytes() == prior
    assert [entry.name for entry in (tmp_path / "deploy").iterdir()] == [STATUS_NAME]


def test_a_write_failure_propagates_to_the_caller(scenario: Scenario, tmp_path: Path) -> None:
    (tmp_path / "deploy").write_text("not a directory\n", encoding="utf-8")
    scenario.set_green()
    with pytest.raises(OSError):
        check(scenario.repo)


def test_a_fresh_install_holds_no_rejected_list_and_reads_it_as_empty() -> None:
    assert self_update.read_rejected() == frozenset()


@pytest.mark.parametrize("state", ["green", "red", "pending", "dirty", "rejected"])
def test_every_decision_after_the_fetch_records_both_full_hashes(
    scenario: Scenario, state: str
) -> None:
    if state == "green":
        scenario.set_green()
    elif state == "red":
        scenario.set_runs(push_run(scenario.fetched, conclusion="failure"))
    elif state == "pending":
        scenario.set_runs()
    elif state == "dirty":
        (scenario.repo / "untracked.txt").write_text("u\n", encoding="utf-8")
    else:
        rejected = self_update.rejected_path()
        rejected.parent.mkdir(parents=True)
        rejected.write_text(f"{scenario.fetched}\n", encoding="utf-8")
    record = check(scenario.repo)
    assert HEX40.fullmatch(record.fetched_sha or "")
    assert HEX40.fullmatch(record.head_sha or "")


def arrange_green(scenario: Scenario) -> None:
    scenario.set_green()


def arrange_red(scenario: Scenario) -> None:
    scenario.set_runs(push_run(scenario.fetched, conclusion="failure"))


def arrange_dirty(scenario: Scenario) -> None:
    (scenario.repo / "untracked.txt").write_text("u\n", encoding="utf-8")


def arrange_nothing(scenario: Scenario) -> None:
    return None


def arrange_read_error(scenario: Scenario) -> None:
    scenario.router.respond(RUNS_NEEDLE, completed(returncode=1))


SIDE_EFFECT_CASES: dict[str, tuple[bool, Callable[[Scenario], None], Verdict]] = {
    "green": (True, arrange_green, Verdict.GREEN),
    "red": (True, arrange_red, Verdict.RED),
    "dirty": (True, arrange_dirty, Verdict.BLOCKED),
    "up_to_date": (False, arrange_nothing, Verdict.UP_TO_DATE),
    "error": (True, arrange_read_error, Verdict.ERROR),
}


def worktree_files(repo: Path) -> dict[str, bytes]:
    return {
        path.relative_to(repo).as_posix(): path.read_bytes()
        for path in sorted(repo.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(repo).parts
    }


def index_digest(repo: Path) -> str:
    return hashlib.sha256((repo / ".git" / "index").read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("behind", "arrange", "expected"), SIDE_EFFECT_CASES.values(), ids=SIDE_EFFECT_CASES.keys()
)
def test_a_check_changes_only_the_private_ref_and_the_status_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    behind: bool,
    arrange: Callable[[Scenario], None],
    expected: Verdict,
) -> None:
    scenario = build_scenario(monkeypatch, tmp_path, behind=behind)
    repo = scenario.repo
    arrange(scenario)
    marker_dir = tmp_path / "deploy"
    marker_dir.mkdir()
    (marker_dir / "restarted_sha").write_bytes(b"owner-marker\n")
    stamp = (repo / "tracked.txt").stat().st_mtime + 100
    os.utime(repo / "tracked.txt", (stamp, stamp))
    refs_before = git(repo, "for-each-ref").splitlines()
    head_before = git(repo, "rev-parse", "HEAD")
    index_before = index_digest(repo)
    status_before = git(repo, "--no-optional-locks", "status", "--porcelain")
    files_before = worktree_files(repo)

    record = check(repo)

    refs_after = git(repo, "for-each-ref").splitlines()
    assert record.verdict is expected
    assert set(refs_before) <= set(refs_after)
    assert [ref for ref in refs_after if ref not in refs_before] == [
        f"{scenario.fetched} commit\trefs/deploy/target"
    ]
    assert git(repo, "rev-parse", "HEAD") == head_before
    assert index_digest(repo) == index_before
    assert git(repo, "--no-optional-locks", "status", "--porcelain") == status_before
    assert worktree_files(repo) == files_before
    assert sorted(entry.name for entry in marker_dir.iterdir()) == [
        "restarted_sha",
        STATUS_NAME,
    ]
    assert (marker_dir / "restarted_sha").read_bytes() == b"owner-marker\n"
    assert not (repo / ".git" / "FETCH_HEAD").exists()


def test_every_git_command_targets_the_passed_repository_whatever_the_working_directory(
    monkeypatch: pytest.MonkeyPatch, scenario: Scenario, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "elsewhere"
    subprocess.run(["git", "init", "-q", "-b", "main", str(elsewhere)], check=True)
    monkeypatch.chdir(elsewhere)
    recorded: list[list[str]] = []
    real_run = subprocess.run

    def spy(cmd: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        recorded.append(list(cmd))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    scenario.set_green()
    record = check(scenario.repo)
    git_argvs = [argv for argv in recorded if argv[0] == "git"]
    assert record.verdict is Verdict.GREEN
    assert len(git_argvs) >= 8
    assert all(argv[1:3] == ["-C", str(scenario.repo)] for argv in git_argvs)
