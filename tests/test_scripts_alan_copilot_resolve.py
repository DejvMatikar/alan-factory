from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from alan import process_runner
from alan.engine_config import DEFAULT_FORGE_TARGET

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import alan_copilot_resolve as resolve_mod  # type: ignore[import-not-found]  # noqa: E402

import alan.forge as _forge_mod
from alan.engine_config import ForgeTarget, RepoIdentity
from alan.forge import ApiResult
from alan.forge_gh import GhForge
from alan.teams import _pr_audit_log, _pr_state_paths
from tests.fakes.forge import FakeForge
from tests.forge_stubs import always


def _fake_gh(rc: int = 0) -> Any:
    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(cmd, rc, stdout="{}", stderr="")
    return fake


def test_main_missing_args_returns_2() -> None:
    assert resolve_mod.main([]) == 2
    assert resolve_mod.main(["42"]) == 2
    assert resolve_mod.main(["42", "t1"]) == 2
    assert resolve_mod.main(["42", "t1", "fix"]) == 2


def test_main_fix_mode_resolves_thread(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    calls: list[list[str]] = []

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    rc = resolve_mod.main(["42", "PRTI_abc", "fix", "body"])
    assert rc == 0
    reply_calls = [c for c in calls if "addPullRequestReviewThreadReply" in str(c)]
    assert reply_calls == []
    resolve_calls = [c for c in calls if "resolveReviewThread" in str(c)]
    assert resolve_calls


def test_main_fix_mode_installs_the_gh_adapter_when_no_server_installed_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(_forge_mod, "_active_forge", None)
    resolved: list[tuple[str, bool]] = []

    def resolve_thread(
        _self: GhForge, thread_id: str, *, forge_target: ForgeTarget, as_verifier: bool,
    ) -> ApiResult:
        del forge_target
        resolved.append((thread_id, as_verifier))
        return ApiResult(ok=True, stdout="{}", returncode=0, stderr="")

    monkeypatch.setattr(GhForge, "resolve_thread", resolve_thread)
    rc = resolve_mod.main(["42", "PRTI_abc", "fix", "body"])
    assert rc == 0
    assert resolved == [("PRTI_abc", False)]


def test_main_comment_ack_mode_installs_the_gh_adapter_when_no_server_installed_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(_forge_mod, "_active_forge", None)
    commented: list[tuple[int, str, ForgeTarget]] = []

    def comment_on_pr(
        _self: GhForge, pr_number: int, body: str, *, forge_target: ForgeTarget,
    ) -> ApiResult:
        commented.append((pr_number, body, forge_target))
        return ApiResult(ok=True, stdout="ok", returncode=0, stderr="")

    monkeypatch.setattr(GhForge, "comment_on_pr", comment_on_pr)
    rc = resolve_mod.main(["comment_ack", "42", "7", "body"])
    assert rc == 0
    assert commented == [(42, "body", DEFAULT_FORGE_TARGET)]


def test_main_acknowledge_mode_posts_reply(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    calls: list[list[str]] = []

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    rc = resolve_mod.main(["42", "PRTI_abc", "acknowledge", "acknowledged"])
    assert rc == 0
    reply_calls = [c for c in calls if "addPullRequestReviewThreadReply" in str(c)]
    assert reply_calls


def test_main_resolve_failure_returns_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh(1))
    rc = resolve_mod.main(["42", "PRTI_abc", "fix", "body"])
    assert rc == 1


def test_main_acknowledge_reply_failure_aborts_resolve(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    calls: list[list[str]] = []

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(list(cmd))
        if "addPullRequestReviewThreadReply" in str(cmd):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    rc = resolve_mod.main(["42", "PRTI_abc", "acknowledge", "ack"])
    assert rc == 1
    resolve_calls = [c for c in calls if "resolveReviewThread" in str(c)]
    assert resolve_calls == []
    assert "Failed to post acknowledgement reply" in capsys.readouterr().err
    log = tmp_path / "logs" / _pr_audit_log.COPILOT_AUDIT_FILE
    entry = json.loads(log.read_text().splitlines()[-1])
    assert entry["reply_posted"] is False
    assert entry["resolved"] is False


def test_main_writes_copilot_audit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh(0))
    resolve_mod.main(["42", "PRTI_abc", "fix", "body"])
    log = tmp_path / "logs" / _pr_audit_log.COPILOT_AUDIT_FILE
    assert log.exists()
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    entry = entries[-1]
    assert entry["action"] == "fix"
    assert entry["pr_number"] == 42
    assert entry["thread_id"] == "PRTI_abc"
    assert entry["comment_body"] == "body"
    assert "resolved_at" in entry
    assert "mode" not in entry
    assert "replied" not in entry


def test_main_uses_sys_argv_when_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh(0))
    monkeypatch.setattr(sys, "argv", ["prog", "42", "PRTI_abc", "fix", "body"])
    rc = resolve_mod.main()
    assert rc == 0


def test_main_invalid_pr_number_returns_2(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    assert resolve_mod.main(["abc", "PRTI_abc", "fix", "body"]) == 2
    assert resolve_mod.main(["1.5", "PRTI_abc", "fix", "body"]) == 2
    captured = capsys.readouterr()
    assert "pr_number" in captured.err
    assert "1.5" in captured.err


def test_main_invalid_mode_returns_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh(0))
    assert resolve_mod.main(["42", "PRTI_abc", "Fix", "body"]) == 2
    assert resolve_mod.main(["42", "PRTI_abc", "ack", "body"]) == 2
    assert resolve_mod.main(["42", "PRTI_abc", "resolve", "body"]) == 2


def test_post_reply_returns_false_on_file_not_found(_active_fake_forge: FakeForge) -> None:
    always(_active_fake_forge, "reply_to_thread", FileNotFoundError("gh"))
    assert resolve_mod.post_reply("PRTI_abc", "body") is False


def test_post_reply_returns_false_on_timeout(_active_fake_forge: FakeForge) -> None:
    always(
        _active_fake_forge, "reply_to_thread", subprocess.TimeoutExpired(cmd="gh", timeout=1.0),
    )
    assert resolve_mod.post_reply("PRTI_abc", "body") is False


def test_comment_ack_success_writes_acked_id_and_audit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    calls: list[list[str]] = []

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    rc = resolve_mod.main(["comment_ack", "42", "99", "thanks for the catch"])
    assert rc == 0
    comment_calls = [c for c in calls if c[:3] == ["gh", "pr", "comment"]]
    assert comment_calls
    assert comment_calls[0] == [
        "gh", "pr", "comment", "42",
        "--repo", "DejvMatikar/alan", "--body", "thanks for the catch",
    ]
    assert _pr_state_paths.load_acked_comment_ids(42, DEFAULT_FORGE_TARGET) == frozenset({99})
    log = tmp_path / "logs" / _pr_audit_log.COPILOT_AUDIT_FILE
    assert log.exists()
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    entry = entries[-1]
    assert entry["action"] == "comment_ack"
    assert entry["database_id"] == 99
    assert entry["acked"] is True


def test_comment_ack_reply_failure_does_not_ack(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    rc = resolve_mod.main(["comment_ack", "42", "99", "body"])
    assert rc == 1
    assert _pr_state_paths.load_acked_comment_ids(42, DEFAULT_FORGE_TARGET) == frozenset()
    assert "Failed to post acknowledgement reply" in capsys.readouterr().err
    log = tmp_path / "logs" / _pr_audit_log.COPILOT_AUDIT_FILE
    entry = json.loads(log.read_text().splitlines()[-1])
    assert entry["action"] == "comment_ack"
    assert entry["acked"] is False
    assert entry["reply_posted"] is False


def test_comment_ack_handles_file_not_found(_active_fake_forge: FakeForge) -> None:
    always(_active_fake_forge, "comment_on_pr", FileNotFoundError("gh"))
    forge_target = DEFAULT_FORGE_TARGET
    assert resolve_mod.post_pr_comment(42, "body", forge_target=forge_target) is False


def test_comment_ack_handles_timeout(_active_fake_forge: FakeForge) -> None:
    always(
        _active_fake_forge, "comment_on_pr", subprocess.TimeoutExpired(cmd="gh", timeout=1.0),
    )
    forge_target = DEFAULT_FORGE_TARGET
    assert resolve_mod.post_pr_comment(42, "body", forge_target=forge_target) is False


def test_comment_ack_bad_pr_returns_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert resolve_mod.main(["comment_ack", "abc", "99", "body"]) == 2
    assert "pr_number" in capsys.readouterr().err


def test_comment_ack_bad_database_id_returns_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert resolve_mod.main(["comment_ack", "42", "abc", "body"]) == 2
    assert "database_id" in capsys.readouterr().err


def test_comment_ack_joins_multi_word_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    captured: list[list[str]] = []

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        captured.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    rc = resolve_mod.main(["comment_ack", "42", "99", "thanks", "for", "the", "catch"])
    assert rc == 0
    assert captured[-1] == [
        "gh", "pr", "comment", "42",
        "--repo", "DejvMatikar/alan", "--body", "thanks for the catch",
    ]


def test_thread_mode_joins_multi_word_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    captured: list[list[str]] = []

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        captured.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    rc = resolve_mod.main(
        ["42", "PRTI_abc", "acknowledge", "fix", "applied", "in", "commit", "abc"]
    )
    assert rc == 0
    reply_args = next(c for c in captured if "addPullRequestReviewThreadReply" in str(c))
    body_arg_idx = reply_args.index("-f") + 1
    while body_arg_idx < len(reply_args) and not reply_args[body_arg_idx].startswith("body="):
        body_arg_idx = reply_args.index("-f", body_arg_idx) + 1
    assert reply_args[body_arg_idx] == "body=fix applied in commit abc"


def test_main_repo_flag_threads_forge_target_into_comment_ack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    calls: list[list[str]] = []

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    rc = resolve_mod.main(
        ["--repo", "acme-corp/widget-app", "comment_ack", "42", "99", "ack"]
    )
    assert rc == 0
    comment_calls = [c for c in calls if c[:3] == ["gh", "pr", "comment"]]
    assert comment_calls[0] == [
        "gh", "pr", "comment", "42", "--repo", "acme-corp/widget-app", "--body", "ack",
    ]
    acme_forge = ForgeTarget(
        repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
        credential_env_name="X_GH_TOKEN",  # noqa: S106  # nosec B106
    )
    assert _pr_state_paths.load_acked_comment_ids(42, acme_forge) == frozenset({99})
    assert _pr_state_paths.load_acked_comment_ids(42, DEFAULT_FORGE_TARGET) == frozenset()


def test_main_repo_flag_thread_mode_unaffected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh(0))
    rc = resolve_mod.main(
        ["--repo", "acme-corp/widget-app", "42", "PRTI_abc", "fix", "body"]
    )
    assert rc == 0


def test_main_repo_flag_invalid_value_returns_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert resolve_mod.main(["--repo", "not-a-slug", "42", "t1", "fix", "body"]) == 2
    assert "Invalid --repo value" in capsys.readouterr().err


def test_main_repo_flag_missing_value_returns_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert resolve_mod.main(["42", "t1", "fix", "body", "--repo"]) == 2
    assert "Usage" in capsys.readouterr().err
