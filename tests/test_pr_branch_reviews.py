from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from alan import process_runner
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _pr_audit_log, _pr_branch, _pr_json, _pr_post
from tests import _pr_branch_support as support


@pytest.fixture(autouse=True)
def _default_transport_token(monkeypatch: pytest.MonkeyPatch) -> None:
    support.set_verifier_transport_token(monkeypatch)


_IDENTITY = _pr_branch.VerifierIdentity("alan-verifier")


def test_append_copilot_audit_writes_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _pr_audit_log.append_copilot_audit({
        "action": "copilot_thread_routed",
        "pr_number": 42,
        "thread_id": "PRTI_abc",
    })
    log = tmp_path / "logs" / _pr_audit_log.COPILOT_AUDIT_FILE
    assert log.exists()
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    assert entries[-1]["action"] == "copilot_thread_routed"
    assert entries[-1]["pr_number"] == 42


def test_nav_json_path_returns_none_on_non_dict_layer() -> None:
    assert _pr_json._nav_json_path('"x"', "anything") is None
    assert _pr_json._nav_json_path('{"a": 1}', "a", "b") is None


def test_parse_review_user_login_user_not_dict() -> None:
    assert _pr_post._parse_review_user_login({"user": "string"}) is None  # noqa: SLF001


def test_parse_review_user_login_login_not_str() -> None:
    assert _pr_post._parse_review_user_login({"user": {"login": 123}}) is None  # noqa: SLF001


def test_parse_review_user_login_happy() -> None:
    assert _pr_post._parse_review_user_login(  # noqa: SLF001
        {"user": {"login": "alan-verifier"}}
    ) == "alan-verifier"


def test_extract_verifier_review_id_not_dict() -> None:
    assert _pr_post._extract_verifier_review_id("nope", _IDENTITY, "sha-head") is None  # noqa: SLF001


def test_extract_verifier_review_id_wrong_state() -> None:
    raw = {"state": "APPROVED", "user": {"login": "alan-verifier"}, "id": 7}
    assert _pr_post._extract_verifier_review_id(raw, _IDENTITY, "sha-head") is None  # noqa: SLF001


def test_extract_verifier_review_id_wrong_login() -> None:
    raw = {"state": "CHANGES_REQUESTED", "user": {"login": "someone-else"}, "id": 7}
    assert _pr_post._extract_verifier_review_id(raw, _IDENTITY, "sha-head") is None  # noqa: SLF001


def test_extract_verifier_review_id_rid_not_int() -> None:
    raw = {"state": "CHANGES_REQUESTED", "user": {"login": "alan-verifier"}, "id": "nope"}
    assert _pr_post._extract_verifier_review_id(raw, _IDENTITY, "sha-head") is None  # noqa: SLF001


def test_extract_verifier_review_id_happy() -> None:
    raw = {"state": "CHANGES_REQUESTED", "user": {"login": "alan-verifier"}, "id": 42}
    assert _pr_post._extract_verifier_review_id(raw, _IDENTITY, "sha-head") == 42  # noqa: SLF001


def test_extract_verifier_review_id_current_head_excluded() -> None:
    raw = {
        "state": "CHANGES_REQUESTED",
        "user": {"login": "alan-verifier"},
        "id": 99,
        "commit_id": "abc123",
    }
    assert _pr_post._extract_verifier_review_id(raw, _IDENTITY, "abc123") is None  # noqa: SLF001


def test_extract_verifier_review_id_stale_sha_included() -> None:
    raw = {
        "state": "CHANGES_REQUESTED",
        "user": {"login": "alan-verifier"},
        "id": 99,
        "commit_id": "old-sha",
    }
    assert _pr_post._extract_verifier_review_id(raw, _IDENTITY, "abc123") == 99  # noqa: SLF001


def test_parse_changes_requested_ids_empty() -> None:
    assert _pr_post._parse_changes_requested_ids("", _IDENTITY, "sha-head") == ()  # noqa: SLF001


def test_parse_changes_requested_ids_bad_json_skipped() -> None:
    assert _pr_post._parse_changes_requested_ids(  # noqa: SLF001
        "not-json\n{}", _IDENTITY, "sha-head"
    ) == ()


def test_parse_changes_requested_ids_wrong_review_skipped() -> None:
    line = json.dumps({"state": "APPROVED", "user": {"login": "alan-verifier"}, "id": 1})
    assert _pr_post._parse_changes_requested_ids(line, _IDENTITY, "sha-head") == ()  # noqa: SLF001


def test_parse_changes_requested_ids_blank_lines_skipped() -> None:
    line = json.dumps(
        {"state": "CHANGES_REQUESTED", "user": {"login": "alan-verifier"}, "id": 99}
    )
    result = _pr_post._parse_changes_requested_ids(  # noqa: SLF001
        f"\n{line}\n\n", _IDENTITY, "sha-head"
    )
    assert result == (99,)


def test_parse_changes_requested_ids_matching() -> None:
    lines = "\n".join(
        json.dumps({"state": "CHANGES_REQUESTED", "user": {"login": "alan-verifier"}, "id": i})
        for i in [10, 20]
    )
    result = _pr_post._parse_changes_requested_ids(lines, _IDENTITY, "sha-head")  # noqa: SLF001
    assert result == (10, 20)


def test_parse_changes_requested_ids_current_head_excluded() -> None:
    line = json.dumps({
        "state": "CHANGES_REQUESTED",
        "user": {"login": "alan-verifier"},
        "id": 77,
        "commit_id": "abc123",
    })
    result = _pr_post._parse_changes_requested_ids(line, _IDENTITY, "abc123")  # noqa: SLF001
    assert result == ()


def test_dismiss_stale_verifier_reviews_audits_head_sha_fetch_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not authorized")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    result = _pr_post.dismiss_stale_verifier_reviews(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET
    )
    assert result is False
    audit = [
        json.loads(ln)
        for ln in (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()
        if ln.strip()
    ]
    gh_level = [
        e for e in audit
        if e["action"] == "dismiss_stale_reviews_failed"
        and "returncode=1" in str(e.get("reason", ""))
    ]
    head_sha_unavailable = [
        e for e in audit
        if e["action"] == "dismiss_stale_reviews_failed"
        and e.get("reason") == "head_sha_unavailable"
    ]
    assert len(gh_level) == 1
    assert len(head_sha_unavailable) == 1


def test_parse_review_objects_skips_blank_and_bad_json_and_non_objects() -> None:
    lines = "\n".join([
        "",
        "not-json",
        "[1, 2]",
        '"just-a-string"',
        json.dumps({"id": 1, "state": "APPROVED"}),
    ])
    result = _pr_post.parse_review_objects(lines)
    assert result == ({"id": 1, "state": "APPROVED"},)
