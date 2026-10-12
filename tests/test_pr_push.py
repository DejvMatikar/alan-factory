from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

import pytest

from alan import process_runner, secrets_store
from alan.engine_config import DEFAULT_FORGE_TARGET, ForgeTarget, RepoIdentity
from alan.teams import _pr_audit_log, _pr_branch, _pr_forge, _pr_push
from tests import _pr_branch_support as support
from tests._pr_branch_support import (
    _FAKE_TOKEN,
    _VALID_SHA,
    _audit_entries,
    _call_push_fix,
)


@pytest.fixture(autouse=True)
def _default_transport_token(monkeypatch: pytest.MonkeyPatch) -> None:
    support.set_verifier_transport_token(monkeypatch)


def test_remote_lines_extracts_and_strips_remote_prefixed_lines() -> None:
    stderr = (
        "To https://github.com/acme/widget.git\n"
        " ! [remote rejected] branch -> branch\n"
        "remote: line one\n"
        "remote: line two\n"
        "error: failed to push some refs\n"
    )
    assert _pr_push._remote_lines(stderr) == ["line one", "line two"]  # noqa: SLF001


def test_remote_lines_empty_when_no_remote_prefixed_lines() -> None:
    assert _pr_push._remote_lines("plain failure, no remote lines\n") == []  # noqa: SLF001


def test_remote_lines_drops_blank_remote_lines() -> None:
    stderr = "remote: \nremote:   \nremote: kept\n"
    assert _pr_push._remote_lines(stderr) == ["kept"]  # noqa: SLF001


def test_truncated_returns_text_unchanged_under_the_limit() -> None:
    assert _pr_push._truncated("short") == "short"  # noqa: SLF001


def test_truncated_cuts_at_the_last_whitespace_before_the_limit() -> None:
    text = "word " * 100
    result = _pr_push._truncated(text)  # noqa: SLF001
    assert result.endswith("…")
    assert len(result) <= 402
    assert not result[:-1].endswith(" ")


def test_truncated_hard_cuts_when_no_whitespace_before_the_limit() -> None:
    text = "a" * 500
    result = _pr_push._truncated(text)  # noqa: SLF001
    assert result == "a" * 400 + "…"


def test_push_branch_default_argv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    seen: list[list[str]] = []

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert (
        _pr_push.push_branch(
            tmp_path, "feat/foo", "task-x", forge_target=DEFAULT_FORGE_TARGET
        ).outcome
        is _pr_push.PushOutcome.PUSHED
    )
    assert seen == [
        ["git", "-c", "credential.helper=", "push", "-u", "origin", "feat/foo"]
    ]


def test_push_branch_env_carries_transport_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    captured: dict[str, object] = {}

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert (
        _pr_push.push_branch(
            tmp_path, "feat/foo", "task-x", forge_target=DEFAULT_FORGE_TARGET
        ).outcome
        is _pr_push.PushOutcome.PUSHED
    )
    env = cast("dict[str, str]", captured["env"])
    assert env[secrets_store.ASKPASS_TOKEN_ENV] == _FAKE_TOKEN
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_push_branch_raises_missing_secret_when_no_transport_credential(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("ALAN_VERIFIER_GH_TOKEN", raising=False)
    with pytest.raises(secrets_store.MissingSecretError):
        _pr_push.push_branch(tmp_path, "feat/foo", "task-x", forge_target=DEFAULT_FORGE_TARGET)


def test_push_fix_branch_clean_push(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    seen: list[list[str]] = []

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_push.push_fix_branch_with_reconcile(
        tmp_path, "fix/foo", "task-x", _VALID_SHA,
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert result.outcome is _pr_push.PushOutcome.PUSHED
    assert len(seen) == 1
    assert f"--force-with-lease=fix/foo:{_VALID_SHA}" in seen[0]
    entry = _audit_entries(tmp_path)[-1]
    assert entry["action"] == "push_ok"
    assert entry["phase"] == "fix"


def test_push_fix_branch_invalid_base_sha(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    seen: list[list[str]] = []

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_push.push_fix_branch_with_reconcile(
        tmp_path, "fix/foo", "task-x", "not-a-sha",
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert result.outcome is _pr_push.PushOutcome.DIVERGED_ESCALATE
    assert seen == []
    entry = _audit_entries(tmp_path)[-1]
    assert entry["action"] == "push_diverged_escalate"
    assert entry.get("reason") == "base_sha_not_concrete"


def test_push_fix_branch_push_raises_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_push.push_fix_branch_with_reconcile(
        tmp_path, "fix/foo", "task-x", _VALID_SHA,
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert result.outcome is _pr_push.PushOutcome.PUSH_FAILED
    entry = _audit_entries(tmp_path)[-1]
    assert entry["action"] == "push_failed"
    assert "git not found" in str(entry["error"])


def test_push_fix_branch_push_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=60.0)

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_push.push_fix_branch_with_reconcile(
        tmp_path, "fix/foo", "task-x", _VALID_SHA,
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert result.outcome is _pr_push.PushOutcome.PUSH_FAILED


def test_push_fix_branch_nonlease_push_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    result = _call_push_fix(
        monkeypatch, tmp_path, [(1, "remote: error: access denied")]
    )
    assert result.outcome is _pr_push.PushOutcome.PUSH_FAILED
    assert result.detail == "error: access denied"
    entry = _audit_entries(tmp_path)[-1]
    assert entry["action"] == "push_failed"
    assert entry["phase"] == "fix"


def test_push_branch_populates_detail_from_remote_prefixed_stderr_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(
            cmd, 1, stdout="",
            stderr=(
                "To https://github.com/acme/widget.git\n"
                "remote: refusing to allow a Personal Access Token to create or "
                "update workflow without `workflow` scope\n"
                "error: failed to push some refs\n"
            ),
        )

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_push.push_branch(tmp_path, "feat/foo", "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.outcome is _pr_push.PushOutcome.PUSH_FAILED
    assert result.detail == (
        "refusing to allow a Personal Access Token to create or update workflow "
        "without `workflow` scope"
    )


def test_push_branch_detail_empty_when_stderr_has_no_remote_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="connection reset")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_push.push_branch(tmp_path, "feat/foo", "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.outcome is _pr_push.PushOutcome.PUSH_FAILED
    assert result.detail == ""


def test_push_fix_branch_lease_rejection_fetch_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    call_n: list[int] = [0]

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        call_n[0] += 1
        if call_n[0] == 1:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="stale info")
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=60.0)

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_push.push_fix_branch_with_reconcile(
        tmp_path, "fix/foo", "task-x", _VALID_SHA,
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert result.outcome is _pr_push.PushOutcome.PUSH_FAILED
    assert call_n[0] == 2


def test_push_fix_branch_lease_rejection_fetch_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    result = _call_push_fix(
        monkeypatch, tmp_path, [(1, "stale info"), (1, "network error")]
    )
    assert result.outcome is _pr_push.PushOutcome.PUSH_FAILED


def test_push_fix_branch_lease_rejection_rebase_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if "push" in cmd:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="stale info")
        if "fetch" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise FileNotFoundError("git")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_push.push_fix_branch_with_reconcile(
        tmp_path, "fix/foo", "task-x", _VALID_SHA,
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert result.outcome is _pr_push.PushOutcome.DIVERGED_ESCALATE
    entry = _audit_entries(tmp_path)[-1]
    assert entry["action"] == "push_diverged_escalate"


def test_push_fix_branch_lease_rejection_rebase_conflict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    result = _call_push_fix(
        monkeypatch, tmp_path,
        [(1, "stale info"), (0, ""), (1, "rev-parse failed"), (1, "CONFLICT (content)")],
    )
    assert result.outcome is _pr_push.PushOutcome.DIVERGED_ESCALATE
    entry = _audit_entries(tmp_path)[-1]
    assert entry["action"] == "push_diverged_escalate"
    assert entry["phase"] == "fix"


def test_push_fix_branch_reconcile_repush_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    call_n: list[int] = [0]

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        call_n[0] += 1
        if call_n[0] == 1:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="stale info")
        if call_n[0] == 3:
            return subprocess.CompletedProcess(cmd, 0, stdout="b" * 40, stderr="")
        if call_n[0] <= 4:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise FileNotFoundError("git")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_push.push_fix_branch_with_reconcile(
        tmp_path, "fix/foo", "task-x", _VALID_SHA,
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert result.outcome is _pr_push.PushOutcome.PUSH_FAILED


def test_push_fix_branch_reconcile_repush_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    result = _call_push_fix(
        monkeypatch, tmp_path,
        [(1, "stale info"), (0, ""), (0, ""), (0, ""), (1, "rejected")],
        stdout_by_index={2: "b" * 40},
    )
    assert result.outcome is _pr_push.PushOutcome.PUSH_FAILED


_RECONCILED_REMOTE_SHA = "b" * 40


def test_push_fix_branch_reconcile_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    result = _call_push_fix(
        monkeypatch, tmp_path,
        [(1, "stale info"), (0, ""), (0, ""), (0, ""), (0, "")],
        stdout_by_index={2: _RECONCILED_REMOTE_SHA},
    )
    assert result.outcome is _pr_push.PushOutcome.RECONCILED
    entry = _audit_entries(tmp_path)[-1]
    assert entry["action"] == "push_reconciled"
    assert entry.get("base_sha") == _VALID_SHA
    assert entry.get("remote_sha") == _RECONCILED_REMOTE_SHA


def test_push_fix_branch_reconcile_action_labels_never_collide_with_pr_reconcile_actor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    result = _call_push_fix(
        monkeypatch, tmp_path,
        [(1, "stale info"), (0, ""), (0, ""), (0, ""), (0, "")],
        stdout_by_index={2: _RECONCILED_REMOTE_SHA},
    )
    assert result.outcome is _pr_push.PushOutcome.RECONCILED
    actions = {e["action"] for e in _audit_entries(tmp_path)}
    assert actions <= {"push_ok", "push_lease_rejected", "push_reconciled"}
    assert "pr_reconcile_attempt" not in actions


def test_push_fix_branch_reconcile_repush_uses_pinned_lease_against_remote_sha(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    seen: list[list[str]] = []

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        idx = len(seen)
        seen.append(list(cmd))
        if idx == 0:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="stale info")
        stdout = _RECONCILED_REMOTE_SHA if idx == 2 else ""
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_push.push_fix_branch_with_reconcile(
        tmp_path, "fix/foo", "task-x", _VALID_SHA, forge_target=DEFAULT_FORGE_TARGET
    )
    assert result.outcome is _pr_push.PushOutcome.RECONCILED
    repush_argv = seen[-1]
    assert f"--force-with-lease=fix/foo:{_RECONCILED_REMOTE_SHA}" in repush_argv
    assert "--force-with-lease" not in repush_argv


def test_repush_reconciled_fails_closed_when_remote_sha_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def boom(*_a: object, **_kw: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("no push should be attempted with an empty remote_sha")

    monkeypatch.setattr(process_runner, "run_subprocess", boom)
    result = _pr_push._repush_reconciled(  # noqa: SLF001
        tmp_path, "fix/foo", "task-x", _VALID_SHA, "",
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert result.outcome is _pr_push.PushOutcome.PUSH_FAILED
    entry = _audit_entries(tmp_path)[-1]
    assert entry["action"] == "push_failed"
    assert entry["reason"] == "empty_remote_sha_resolve"


def test_push_branch_expired_token_stderr_sets_credential_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Bad credentials")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_push.push_branch(tmp_path, "feat/foo", "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.outcome is _pr_push.PushOutcome.PUSH_FAILED
    assert result.credential_failure == "expired_token"


def test_push_branch_transient_stderr_leaves_credential_failure_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="connection reset")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_push.push_branch(tmp_path, "feat/foo", "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert result.outcome is _pr_push.PushOutcome.PUSH_FAILED
    assert result.credential_failure is None


def test_push_fix_branch_expired_token_sets_credential_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    result = _call_push_fix(
        monkeypatch, tmp_path, [(1, "HTTP 401: Bad credentials")]
    )
    assert result.outcome is _pr_push.PushOutcome.PUSH_FAILED
    assert result.credential_failure == "expired_token"


def test_push_fix_branch_secondary_rate_limit_is_not_credential_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    result = _call_push_fix(
        monkeypatch, tmp_path,
        [(1, "You have exceeded a secondary rate limit (HTTP 403)")],
    )
    assert result.outcome is _pr_push.PushOutcome.PUSH_FAILED
    assert result.credential_failure is None


def test_push_fix_branch_reconcile_repush_expired_token_sets_credential_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    result = _call_push_fix(
        monkeypatch, tmp_path,
        [(1, "stale info"), (0, ""), (0, ""), (0, ""), (1, "token revoked")],
        stdout_by_index={2: "b" * 40},
    )
    assert result.outcome is _pr_push.PushOutcome.PUSH_FAILED
    assert result.credential_failure == "expired_token"


def test_classify_credential_failure_missing_token_when_absent() -> None:
    result = _pr_branch.classify_credential_failure(
        token_present=False, returncode=1, stderr="401 Unauthorized",
    )
    assert result == "missing_token"


def test_classify_credential_failure_expired_token_marker() -> None:
    result = _pr_branch.classify_credential_failure(
        token_present=True, returncode=1, stderr="HTTP 401: Bad credentials",
    )
    assert result == "expired_token"


def test_classify_credential_failure_expired_token_case_insensitive() -> None:
    result = _pr_branch.classify_credential_failure(
        token_present=True, returncode=1, stderr="Unauthorized",
    )
    assert result == "expired_token"


def test_classify_credential_failure_revoked_marker_maps_to_expired_token() -> None:
    result = _pr_branch.classify_credential_failure(
        token_present=True, returncode=1, stderr="This token has been revoked",
    )
    assert result == "expired_token"


def test_classify_credential_failure_transient_ambiguous_default() -> None:
    result = _pr_branch.classify_credential_failure(
        token_present=True, returncode=1, stderr="boom",
    )
    assert result == "transient"


def test_classify_credential_failure_transient_when_returncode_none() -> None:
    result = _pr_branch.classify_credential_failure(
        token_present=True, returncode=None, stderr=None,
    )
    assert result == "transient"


def test_classify_credential_failure_never_misreports_present_token_as_missing() -> None:
    result = _pr_branch.classify_credential_failure(
        token_present=True, returncode=1, stderr="connection reset by peer",
    )
    assert result != "missing_token"
    assert result == "transient"


def test_classify_credential_failure_secondary_rate_limit_is_transient_not_expired() -> None:
    result = _pr_branch.classify_credential_failure(
        token_present=True, returncode=1,
        stderr="You have exceeded a secondary rate limit (HTTP 403 Forbidden)",
    )
    assert result == "transient"


def test_classify_credential_failure_primary_rate_limit_is_transient() -> None:
    result = _pr_branch.classify_credential_failure(
        token_present=True, returncode=1, stderr="API rate limit exceeded for user",
    )
    assert result == "transient"


def test_classify_credential_failure_bare_403_without_rate_marker_is_expired() -> None:
    result = _pr_branch.classify_credential_failure(
        token_present=True, returncode=1, stderr="403 Forbidden",
    )
    assert result == "expired_token"


def test_credential_recovery_recipe_names_purpose_and_stable_fallback() -> None:
    recipe = _pr_branch.credential_recovery_recipe("transport", None)
    assert "transport credential" in recipe
    assert "stable fallback name" in recipe
    assert "re-dispatch" in recipe


def test_credential_recovery_recipe_includes_forge_slug_when_available() -> None:
    forge_target = ForgeTarget(
        repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
        credential_env_name="ACME_WIDGET_GH_TOKEN",
    )
    recipe = _pr_branch.credential_recovery_recipe("reviewer", forge_target)
    assert "reviewer credential" in recipe
    assert "acme-corp/widget-app" in recipe
    assert "ACME_WIDGET_GH_TOKEN" in recipe


def test_credential_recovery_recipe_omits_slug_when_forge_target_absent() -> None:
    recipe = _pr_branch.credential_recovery_recipe("transport", None)
    assert "acme" not in recipe.lower()
    assert "credential_env_name" in recipe


def test_audit_dir_default_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_LOGS_ROOT", raising=False)
    assert _pr_audit_log.audit_dir() == Path.home() / ".alan" / "logs"


def test_run_subprocess_env_kwarg_propagates_merged_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess as subprocess_mod

    captured: list[dict[str, object]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess_mod.CompletedProcess[str]:
        captured.append(dict(kwargs))
        return subprocess_mod.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setenv("ALAN_SENTINEL_INHERIT", "sentinel_value")
    monkeypatch.setattr(subprocess_mod, "run", fake_run)
    process_runner.run_subprocess(["dummy"], cwd=None, timeout=1.0, env={"MY_VAR": "hello"})
    assert len(captured) == 1
    env_arg = captured[0].get("env")
    assert isinstance(env_arg, dict)
    assert env_arg["MY_VAR"] == "hello"
    assert env_arg["ALAN_SENTINEL_INHERIT"] == "sentinel_value"


def test_run_subprocess_env_none_passes_none_to_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess as subprocess_mod

    captured: list[dict[str, object]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess_mod.CompletedProcess[str]:
        captured.append(dict(kwargs))
        return subprocess_mod.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess_mod, "run", fake_run)
    process_runner.run_subprocess(["dummy"], cwd=None, timeout=1.0)
    assert captured[0].get("env") is None


def test_verifier_gh_env_empty_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: None)  # noqa: ARG005
    monkeypatch.delenv("ALAN_VERIFIER_GH_TOKEN", raising=False)
    assert secrets_store.verifier_gh_env(DEFAULT_FORGE_TARGET) == {}


def test_verifier_gh_env_empty_for_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: None)  # noqa: ARG005
    monkeypatch.setenv("ALAN_VERIFIER_GH_TOKEN", "   ")
    assert secrets_store.verifier_gh_env(DEFAULT_FORGE_TARGET) == {}


def test_verifier_gh_env_returns_token_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_resolve(*_a: object, env_fallback: str | None = None, **_kw: object) -> str | None:
        return "ghp_xxx" if env_fallback == "ALAN_VERIFIER_GH_TOKEN" else None

    monkeypatch.setattr(secrets_store, "resolve_secret", _fake_resolve)
    monkeypatch.setenv("ALAN_VERIFIER_GH_TOKEN", "ghp_xxx")
    assert secrets_store.verifier_gh_env(DEFAULT_FORGE_TARGET) == {"GH_TOKEN": "ghp_xxx"}


def test_verifier_gh_env_prefers_store_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_VERIFIER_GH_TOKEN", "env-token")
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "store-token")  # noqa: ARG005
    assert secrets_store.verifier_gh_env(DEFAULT_FORGE_TARGET) == {"GH_TOKEN": "store-token"}


def test_verifier_gh_env_falls_back_to_env_when_store_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_VERIFIER_GH_TOKEN", "env-token")

    def _fake_resolve(*_a: object, env_fallback: str | None = None, **_kw: object) -> str | None:
        return "env-token" if env_fallback == "ALAN_VERIFIER_GH_TOKEN" else None

    monkeypatch.setattr(secrets_store, "resolve_secret", _fake_resolve)
    assert secrets_store.verifier_gh_env(DEFAULT_FORGE_TARGET) == {"GH_TOKEN": "env-token"}


def test_verifier_gh_env_empty_when_store_and_env_both_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_VERIFIER_GH_TOKEN", raising=False)
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: None)  # noqa: ARG005
    assert secrets_store.verifier_gh_env(DEFAULT_FORGE_TARGET) == {}


def test_verifier_gh_env_calls_resolve_secret_with_reviewer_purpose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str | None]] = []

    def _spy(engine: str, purpose: str, *, env_fallback: str | None) -> str | None:
        calls.append((engine, purpose, env_fallback))
        return None

    monkeypatch.setattr(secrets_store, "resolve_secret", _spy)
    secrets_store.verifier_gh_env(DEFAULT_FORGE_TARGET)
    assert calls == [
        (
            DEFAULT_FORGE_TARGET.repo.slug,
            secrets_store.REVIEWER_PURPOSE,
            DEFAULT_FORGE_TARGET.credential_env_name,
        )
    ]


def test_list_open_prs_returns_parsed_list(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps([
        {"number": 10, "title": "add authentication"},
        {"number": 11, "title": "fix bug"},
    ])

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        assert "--state" in cmd
        assert "open" in cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=payload, stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    prs = _pr_branch.list_open_prs()
    assert len(prs) == 2
    assert prs[0].number == 10
    assert prs[0].title == "add authentication"
    assert prs[1].number == 11


def test_list_open_prs_returns_empty_when_gh_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del cmd, kwargs
        raise FileNotFoundError("gh")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_branch.list_open_prs() == []


def test_list_open_prs_returns_empty_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=60.0)

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_branch.list_open_prs() == []


def test_list_open_prs_returns_empty_on_nonzero_rc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="auth error")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_branch.list_open_prs() == []


def test_list_open_prs_returns_empty_on_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout="not-json", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_branch.list_open_prs() == []


def test_list_open_prs_returns_empty_when_json_is_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_branch.list_open_prs() == []


def test_list_open_prs_skips_items_missing_number_or_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.dumps([
        {"number": 5, "title": "valid"},
        {"number": 6},
        {"title": "no number"},
        "not-a-dict",
    ])

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout=payload, stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    prs = _pr_branch.list_open_prs()
    assert len(prs) == 1
    assert prs[0].number == 5


def test_forge_target_for_ws_default_workspace_uses_default_target(
    tmp_path: Path,
) -> None:
    from alan.workspace import create

    ws = create("t", "d", root=tmp_path / "ws")
    target = _pr_forge.forge_target_for_ws(ws)
    assert target == DEFAULT_FORGE_TARGET


def test_forge_target_for_ws_resolves_second_engine_from_snapshot(
    tmp_path: Path,
) -> None:
    from alan import workspace as workspace_mod
    from alan.engine_config import EngineConfig, ForgeConfig, Posture, RepoIdentity
    from alan.workspace import EngineSnapshot, create

    ws = create("t", "d", root=tmp_path / "ws", engine_key="acme-corp/widget-app")
    snapshot = EngineSnapshot(
        posture=Posture.propose,
        transition_graph={},
        config=EngineConfig(
            repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
            forge=ForgeConfig(
                repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
                credential_env_name="ACME_WIDGET_GH_TOKEN",
                authoritative_checks=frozenset({"Widget CI"}),
            ),
        ),
    )
    ws = workspace_mod.stamp_engine_snapshot(ws, snapshot)

    target = _pr_forge.forge_target_for_ws(ws)

    assert target.repo.slug == "acme-corp/widget-app"
    assert target.credential_env_name == "ACME_WIDGET_GH_TOKEN"  # noqa: S105
    assert target.authoritative_checks == frozenset({"Widget CI"})


def test_governing_config_for_ws_no_snapshot_falls_back_to_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.engine_config import default_engine_config
    from alan.workspace import create

    monkeypatch.setattr("alan.engine_config.get_registered", lambda: None)
    ws = create("t", "d", root=tmp_path / "ws")

    cfg = _pr_forge.governing_config_for_ws(ws)

    assert cfg == default_engine_config()


def test_governing_config_for_ws_no_snapshot_uses_registered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.engine_config import EngineConfig, Posture, RepoIdentity
    from alan.workspace import create

    registered = EngineConfig(
        repo=RepoIdentity(owner="reg", repo="istered"), posture=Posture.propose
    )
    monkeypatch.setattr("alan.engine_config.get_registered", lambda: registered)
    ws = create("t", "d", root=tmp_path / "ws")

    cfg = _pr_forge.governing_config_for_ws(ws)

    assert cfg == registered


def test_governing_config_for_ws_no_snapshot_engine_key_uses_registry_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.engine_config import EngineConfig, RepoIdentity
    from alan.workspace import create

    named = EngineConfig(repo=RepoIdentity(owner="acme", repo="widget"))
    monkeypatch.setattr(
        "alan.engine_config.lookup_by_slug",
        lambda slug: named if slug == "acme/widget" else None,
    )
    ws = create("t", "d", root=tmp_path / "ws", engine_key="acme/widget")

    cfg = _pr_forge.governing_config_for_ws(ws)

    assert cfg == named


def test_governing_config_for_ws_unknown_engine_key_falls_back_to_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.engine_config import default_engine_config
    from alan.workspace import create

    monkeypatch.setattr("alan.engine_config.get_registered", lambda: None)
    ws = create("t", "d", root=tmp_path / "ws", engine_key="ghost/repo")

    cfg = _pr_forge.governing_config_for_ws(ws)

    assert cfg == default_engine_config()


def test_governing_config_for_ws_returns_pinned_snapshot_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import workspace as workspace_mod
    from alan.engine_config import EngineConfig, Posture, RepoIdentity
    from alan.workspace import EngineSnapshot, create

    pinned = EngineConfig(repo=RepoIdentity(owner="acme", repo="widget"), posture=Posture.propose)
    monkeypatch.setattr("alan.engine_config.get_registered", lambda: None)
    ws = create("t", "d", root=tmp_path / "ws", engine_key="acme/widget")
    snapshot = EngineSnapshot(posture=Posture.propose, transition_graph={}, config=pinned)
    ws = workspace_mod.stamp_engine_snapshot(ws, snapshot)

    cfg = _pr_forge.governing_config_for_ws(ws)

    assert cfg == pinned


def test_governing_config_for_ws_legacy_snapshot_without_config_falls_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import workspace as workspace_mod
    from alan.engine_config import Posture, default_engine_config
    from alan.workspace import EngineSnapshot, create

    monkeypatch.setattr("alan.engine_config.get_registered", lambda: None)
    ws = create("t", "d", root=tmp_path / "ws")
    legacy = EngineSnapshot(posture=Posture.propose, transition_graph={"a": ["b"]})
    ws = workspace_mod.stamp_engine_snapshot(ws, legacy)
    assert ws.meta.engine_snapshot is not None
    assert ws.meta.engine_snapshot.config is None

    cfg = _pr_forge.governing_config_for_ws(ws)

    assert cfg == default_engine_config()


def test_governing_config_for_ws_legacy_snapshot_with_engine_key_uses_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import workspace as workspace_mod
    from alan.engine_config import EngineConfig, Posture, RepoIdentity
    from alan.workspace import EngineSnapshot, create

    named = EngineConfig(repo=RepoIdentity(owner="acme", repo="widget"))
    monkeypatch.setattr(
        "alan.engine_config.lookup_by_slug",
        lambda slug: named if slug == "acme/widget" else None,
    )
    ws = create("t", "d", root=tmp_path / "ws", engine_key="acme/widget")
    legacy = EngineSnapshot(posture=Posture.observe, transition_graph={})
    ws = workspace_mod.stamp_engine_snapshot(ws, legacy)

    cfg = _pr_forge.governing_config_for_ws(ws)

    assert cfg == named


def test_role_model_effort_uses_configured_role() -> None:
    from alan.engine_config import EngineConfig, RepoIdentity, RoleConfig

    cfg = EngineConfig(
        repo=RepoIdentity(owner="a", repo="b"),
        roles={"implementer": RoleConfig(model="opus", effort="high")},
    )
    model, effort = _pr_forge.role_model_effort(
        cfg, "implementer", RoleConfig(model="sonnet", effort="xhigh")
    )
    assert model == "opus"
    assert effort == "high"


def test_role_model_effort_falls_back_to_default_when_role_missing() -> None:
    from alan.engine_config import EngineConfig, RepoIdentity, RoleConfig

    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    model, effort = _pr_forge.role_model_effort(
        cfg, "implementer", RoleConfig(model="sonnet", effort="xhigh")
    )
    assert model == "sonnet"
    assert effort == "xhigh"
