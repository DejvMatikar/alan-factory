from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from alan import process_runner, secrets_store
from alan.engine_config import DEFAULT_FORGE_TARGET, ForgeTarget
from alan.forge import PrRead
from alan.teams import _pr_reconcile
from tests import _pr_branch_support as support
from tests.fakes.forge import FakeForge

_BASE_SHA = "b" * 40
_PRE_SHA = "1" * 40
_POST_SHA = "2" * 40

_DEFAULTS: dict[str, tuple[int, str, str]] = {
    "git_init": (0, "", ""),
    "git_remote_add": (0, "", ""),
    "git_fetch": (0, "", ""),
    "rev_parse_base": (0, f"{_BASE_SHA}\n", ""),
    "rev_parse_head": (0, f"{_PRE_SHA}\n", ""),
    "merge_base": (1, "", ""),
    "checkout": (0, "", ""),
    "rebase": (0, "", ""),
    "rebase_abort": (0, "", ""),
    "diff_unmerged": (0, "", ""),
    "git_log_behind": (0, "", ""),
    "rev_parse_HEAD": (0, f"{_POST_SHA}\n", ""),
    "push": (0, "", ""),
}


def _classify(cmd: list[str]) -> str:
    if "rev-parse" in cmd:
        ref = cmd[-1]
        if ref == "HEAD":
            return "rev_parse_HEAD"
        if "base" in ref:
            return "rev_parse_base"
        if "head" in ref:
            return "rev_parse_head"
        raise AssertionError(f"unclassified rev-parse ref: {ref}")
    if "merge-base" in cmd:
        return "merge_base"
    if "diff" in cmd and "--diff-filter=U" in cmd:
        return "diff_unmerged"
    if "log" in cmd and "--oneline" in cmd:
        return "git_log_behind"
    if "checkout" in cmd and "-B" in cmd:
        return "checkout"
    if "rebase" in cmd and "--abort" in cmd:
        return "rebase_abort"
    if "rebase" in cmd:
        return "rebase"
    if "push" in cmd:
        return "push"
    if "fetch" in cmd:
        return "git_fetch"
    if "remote" in cmd and "add" in cmd:
        return "git_remote_add"
    if "init" in cmd:
        return "git_init"
    raise AssertionError(f"unclassified command: {cmd}")


def _fake_run(
    overrides: dict[str, tuple[int, str, str]] | None = None,
    raises: dict[str, Exception] | None = None,
) -> tuple[list[list[str]], object]:
    merged = {**_DEFAULTS, **(overrides or {})}
    raises = raises or {}
    calls: list[list[str]] = []

    def fake(
        cmd: list[str], *, cwd: object = None, timeout: object = None, env: object = None
    ) -> subprocess.CompletedProcess[str]:
        del cwd, timeout, env
        calls.append(list(cmd))
        step = _classify(cmd)
        if step in raises:
            raise raises[step]
        rc, stdout, stderr = merged[step]
        return subprocess.CompletedProcess(cmd, rc, stdout=stdout, stderr=stderr)

    return calls, fake


@pytest.fixture(autouse=True)
def _default_transport_token(monkeypatch: pytest.MonkeyPatch) -> None:
    support.set_verifier_transport_token(monkeypatch)


@pytest.fixture(autouse=True)
def _default_read_pr(_active_fake_forge: FakeForge) -> None:
    _active_fake_forge.stub(
        "read_pr",
        lambda *_a, **_k: PrRead(
            ok=True, fields={"headRefName": "feature-x", "baseRefName": "main"},
            returncode=0, stderr="",
        ),
    )


def _steps(calls: list[list[str]]) -> list[str]:
    return [_classify(c) for c in calls]


def test_reconcile_branch_rebases_and_pushes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    calls, fake = _fake_run()
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_reconcile.reconcile_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.outcome is _pr_reconcile.ReconcileOutcome.RECONCILED
    assert result.base_sha == _BASE_SHA
    assert result.pre_sha == _PRE_SHA
    assert result.post_sha == _POST_SHA
    assert _steps(calls) == [
        "git_init", "git_remote_add", "git_fetch",
        "rev_parse_base", "rev_parse_head", "merge_base",
        "checkout", "rebase", "rev_parse_HEAD", "push",
    ]
    push_call = calls[-1]
    assert f"--force-with-lease=feature-x:{_PRE_SHA}" in push_call
    audit = json.loads((tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()[-1])
    assert audit["action"] == "pr_reconcile_attempt"
    assert audit["task_id"] == "task-x"
    assert audit["pr_number"] == 42
    assert audit["outcome"] == "reconciled"
    assert audit["base_sha"] == _BASE_SHA
    assert audit["pre_sha"] == _PRE_SHA
    assert audit["post_sha"] == _POST_SHA


def test_reconcile_branch_already_current_when_base_already_ancestor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    calls, fake = _fake_run({"merge_base": (0, "", "")})
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_reconcile.reconcile_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.outcome is _pr_reconcile.ReconcileOutcome.ALREADY_CURRENT
    assert result.base_sha == _BASE_SHA
    assert result.pre_sha == _PRE_SHA
    assert result.post_sha == _PRE_SHA
    assert _steps(calls) == [
        "git_init", "git_remote_add", "git_fetch",
        "rev_parse_base", "rev_parse_head", "merge_base",
    ]


def test_reconcile_branch_already_current_when_rebase_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    calls, fake = _fake_run({"rev_parse_HEAD": (0, f"{_PRE_SHA}\n", "")})
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_reconcile.reconcile_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.outcome is _pr_reconcile.ReconcileOutcome.ALREADY_CURRENT
    assert result.post_sha == _PRE_SHA
    assert "push" not in _steps(calls)


def test_reconcile_branch_transport_failed_when_gh_view_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _active_fake_forge.stub(
        "read_pr",
        lambda *_a, **_k: PrRead(ok=False, fields={}, returncode=1, stderr="not found"),
    )
    calls, fake = _fake_run()
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_reconcile.reconcile_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result == _pr_reconcile.ReconcileResult(_pr_reconcile.ReconcileOutcome.TRANSPORT_FAILED)
    assert calls == []


def test_reconcile_branch_transport_failed_when_gh_view_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def _raise(*_a: object, **_k: object) -> PrRead:
        raise FileNotFoundError("gh")

    _active_fake_forge.stub("read_pr", _raise)
    result = _pr_reconcile.reconcile_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.outcome is _pr_reconcile.ReconcileOutcome.TRANSPORT_FAILED


def test_reconcile_branch_transport_failed_when_gh_view_times_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def _raise(*_a: object, **_k: object) -> PrRead:
        raise subprocess.TimeoutExpired(cmd=["gh"], timeout=60)

    _active_fake_forge.stub("read_pr", _raise)
    result = _pr_reconcile.reconcile_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.outcome is _pr_reconcile.ReconcileOutcome.TRANSPORT_FAILED


def test_reconcile_branch_propagates_unexpected_read_pr_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def _raise(*_a: object, **_k: object) -> PrRead:
        raise RuntimeError("boom")

    _active_fake_forge.stub("read_pr", _raise)
    with pytest.raises(RuntimeError):
        _pr_reconcile.reconcile_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)


def test_reconcile_branch_transport_failed_when_head_ref_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _active_fake_forge.stub(
        "read_pr",
        lambda *_a, **_k: PrRead(
            ok=True, fields={"baseRefName": "main"}, returncode=0, stderr="",
        ),
    )
    result = _pr_reconcile.reconcile_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.outcome is _pr_reconcile.ReconcileOutcome.TRANSPORT_FAILED


def test_reconcile_branch_transport_failed_when_base_ref_empty_string(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _active_fake_forge.stub(
        "read_pr",
        lambda *_a, **_k: PrRead(
            ok=True, fields={"headRefName": "feature-x", "baseRefName": ""},
            returncode=0, stderr="",
        ),
    )
    result = _pr_reconcile.reconcile_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.outcome is _pr_reconcile.ReconcileOutcome.TRANSPORT_FAILED


def test_reconcile_branch_transport_failed_when_git_init_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    calls, fake = _fake_run({"git_init": (1, "", "boom")})
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_reconcile.reconcile_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.outcome is _pr_reconcile.ReconcileOutcome.TRANSPORT_FAILED
    assert "git_remote_add" not in _steps(calls)


def test_reconcile_branch_transport_failed_when_remote_add_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    calls, fake = _fake_run({"git_remote_add": (128, "", "boom")})
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_reconcile.reconcile_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.outcome is _pr_reconcile.ReconcileOutcome.TRANSPORT_FAILED
    assert "git_fetch" not in _steps(calls)


def test_reconcile_branch_transport_failed_when_no_transport_credential(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.delenv("ALAN_VERIFIER_GH_TOKEN", raising=False)
    calls, fake = _fake_run()
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_reconcile.reconcile_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.outcome is _pr_reconcile.ReconcileOutcome.TRANSPORT_FAILED
    assert "git_fetch" not in _steps(calls)


def test_reconcile_branch_transport_failed_when_fetch_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    calls, fake = _fake_run({"git_fetch": (1, "", "network error")})
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_reconcile.reconcile_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.outcome is _pr_reconcile.ReconcileOutcome.TRANSPORT_FAILED
    assert "rev_parse_base" not in _steps(calls)


def test_reconcile_branch_transport_failed_when_base_sha_unresolvable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    calls, fake = _fake_run({"rev_parse_base": (0, "", "")})
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_reconcile.reconcile_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.outcome is _pr_reconcile.ReconcileOutcome.TRANSPORT_FAILED
    assert "merge_base" not in _steps(calls)


def test_reconcile_branch_transport_failed_when_head_sha_unresolvable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    calls, fake = _fake_run({"rev_parse_head": (1, "", "no such ref")})
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_reconcile.reconcile_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.outcome is _pr_reconcile.ReconcileOutcome.TRANSPORT_FAILED
    assert "merge_base" not in _steps(calls)


def test_reconcile_branch_transport_failed_when_checkout_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    calls, fake = _fake_run({"checkout": (1, "", "boom")})
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_reconcile.reconcile_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.outcome is _pr_reconcile.ReconcileOutcome.TRANSPORT_FAILED
    assert result.base_sha == _BASE_SHA
    assert result.pre_sha == _PRE_SHA
    assert result.post_sha is None
    assert "rebase" not in _steps(calls)


def test_reconcile_branch_conflict_when_rebase_fails_and_aborts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    calls, fake = _fake_run({
        "rebase": (1, "", "CONFLICT (content): foo.py"),
        "diff_unmerged": (0, "foo.py\n", ""),
    })
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_reconcile.reconcile_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.outcome is _pr_reconcile.ReconcileOutcome.CONFLICT
    assert result.conflicted_paths == ("foo.py",)
    assert "diff_unmerged" in _steps(calls)
    assert "rebase_abort" in _steps(calls)
    assert "push" not in _steps(calls)
    audit = json.loads((tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()[-1])
    assert audit["outcome"] == "conflict"
    assert audit["post_sha"] is None


def test_reconcile_branch_conflict_captures_multiple_unmerged_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    calls, fake = _fake_run({
        "rebase": (1, "", "CONFLICT (content): foo.py"),
        "diff_unmerged": (0, "foo.py\nbar/baz.py\n", ""),
    })
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_reconcile.reconcile_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.outcome is _pr_reconcile.ReconcileOutcome.CONFLICT
    assert result.conflicted_paths == ("foo.py", "bar/baz.py")
    assert "push" not in _steps(calls)


def test_reconcile_branch_transport_failed_conflicted_paths_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    calls, fake = _fake_run({"rebase": (1, "", "hook declined")})
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_reconcile.reconcile_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.outcome is _pr_reconcile.ReconcileOutcome.TRANSPORT_FAILED
    assert result.conflicted_paths == ()
    assert "diff_unmerged" in _steps(calls)


def test_reconcile_branch_reconciled_conflicted_paths_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _calls, fake = _fake_run()
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_reconcile.reconcile_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.outcome is _pr_reconcile.ReconcileOutcome.RECONCILED
    assert result.conflicted_paths == ()


def test_reconcile_branch_transport_failed_when_rebase_fails_without_unmerged_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    calls, fake = _fake_run({"rebase": (1, "", "hook declined")})
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_reconcile.reconcile_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.outcome is _pr_reconcile.ReconcileOutcome.TRANSPORT_FAILED
    assert "diff_unmerged" in _steps(calls)
    assert "rebase_abort" in _steps(calls)
    assert "push" not in _steps(calls)
    audit = json.loads((tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()[-1])
    assert audit["outcome"] == "transport_failed"


def test_reconcile_branch_transport_failed_when_unmerged_check_itself_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    calls, fake = _fake_run(
        {"rebase": (1, "", "boom")}, raises={"diff_unmerged": FileNotFoundError("git")}
    )
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_reconcile.reconcile_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.outcome is _pr_reconcile.ReconcileOutcome.TRANSPORT_FAILED
    assert "rebase_abort" in _steps(calls)


def test_reconcile_branch_transport_failed_when_post_rebase_rev_parse_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    calls, fake = _fake_run({"rev_parse_HEAD": (1, "", "boom")})
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_reconcile.reconcile_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.outcome is _pr_reconcile.ReconcileOutcome.TRANSPORT_FAILED
    assert result.base_sha == _BASE_SHA
    assert result.pre_sha == _PRE_SHA
    assert "push" not in _steps(calls)


def test_reconcile_branch_raced_on_lease_rejection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _calls, fake = _fake_run({"push": (1, "", "! [rejected] (stale info)")})
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_reconcile.reconcile_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.outcome is _pr_reconcile.ReconcileOutcome.RACED


def test_reconcile_branch_transport_failed_on_other_push_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _calls, fake = _fake_run({"push": (1, "", "remote: access denied")})
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_reconcile.reconcile_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.outcome is _pr_reconcile.ReconcileOutcome.TRANSPORT_FAILED


def test_reconcile_branch_transport_failed_when_push_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _calls, fake = _fake_run(raises={"push": subprocess.TimeoutExpired(cmd=["git"], timeout=60)})
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_reconcile.reconcile_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.outcome is _pr_reconcile.ReconcileOutcome.TRANSPORT_FAILED


def test_reconcile_branch_transport_failed_when_push_missing_credential(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    real_transport_env = secrets_store.transport_env
    call_count = {"n": 0}

    def flaky_transport_env(forge_target: ForgeTarget) -> dict[str, str]:
        call_count["n"] += 1
        if call_count["n"] > 1:
            raise secrets_store.MissingSecretError("gone")
        return real_transport_env(forge_target)

    monkeypatch.setattr(secrets_store, "transport_env", flaky_transport_env)
    _calls, fake = _fake_run()
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_reconcile.reconcile_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.outcome is _pr_reconcile.ReconcileOutcome.TRANSPORT_FAILED
    assert "push" not in _steps(_calls)


def test_behind_commits_default_empty() -> None:
    result = _pr_reconcile.ReconcileResult(_pr_reconcile.ReconcileOutcome.TRANSPORT_FAILED)
    assert result.behind_commits == ()


def test_checkout_and_rebase_captures_behind_commits_on_conflict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    calls, fake = _fake_run({
        "rebase": (1, "", "CONFLICT (content): foo.py"),
        "diff_unmerged": (0, "foo.py\n", ""),
        "git_log_behind": (0, "abc123 commit one\ndef456 commit two\n", ""),
    })
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_reconcile.reconcile_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.outcome is _pr_reconcile.ReconcileOutcome.CONFLICT
    assert result.behind_commits == ("abc123 commit one", "def456 commit two")
    assert "git_log_behind" in _steps(calls)


def test_behind_commits_empty_when_log_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _calls, fake = _fake_run({
        "rebase": (1, "", "CONFLICT (content): foo.py"),
        "diff_unmerged": (0, "foo.py\n", ""),
        "git_log_behind": (1, "", "boom"),
    })
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_reconcile.reconcile_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.behind_commits == ()


def test_finish_audit_row_carries_behind_commits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _calls, fake = _fake_run({
        "rebase": (1, "", "CONFLICT (content): foo.py"),
        "diff_unmerged": (0, "foo.py\n", ""),
        "git_log_behind": (0, "abc123 c1\n", ""),
    })
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    _pr_reconcile.reconcile_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    audit = json.loads((tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()[-1])
    assert audit["behind_commits"] == ["abc123 c1"]
