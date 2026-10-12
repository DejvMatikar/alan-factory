from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from alan import secrets_store
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _pr_post
from alan.teams import _verification_grounding as grounding_mod
from alan.teams import _verification_review_post as review_post_mod
from tests._pr_branch_support import _WIDGET_FORGE
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, api_result

_TARGET = DEFAULT_FORGE_TARGET
_CREDENTIALS = [
    pytest.param({"GH_TOKEN": "tok"}, True, id="credential-resolved"),
    pytest.param({}, False, id="empty-credential"),
    pytest.param(None, False, id="no-credential"),
]
_WITH_CREDENTIAL = pytest.mark.parametrize(("credential", "requested"), _CREDENTIALS)


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


@pytest.fixture(autouse=True)
def _live_and_isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.delenv("ALAN_VERIFIER_GH_TOKEN", raising=False)
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    monkeypatch.setattr(_pr_post, "dismiss_stale_verifier_reviews", lambda _p, _t, **_kw: False)
    monkeypatch.setenv("ALAN_REVIEW_POST_RETRY_MAX", "0")


def _resolve(monkeypatch: pytest.MonkeyPatch, credential: dict[str, str] | None) -> None:
    monkeypatch.setattr(secrets_store, "verifier_gh_env", lambda _t: credential)


def _post_comment() -> None:
    grounding_mod._post_cannot_verify_comment(  # noqa: SLF001
        42, "task-x", "inv_1", forge_target=_TARGET,
    )


def _post_verdict_review() -> None:
    review_post_mod._post_review_with_retries(  # noqa: SLF001
        42, "APPROVE", "body", "task-x", "APPROVE", "inv_1", forge_target=_TARGET,
    )


def _requested(forge: ForgeScript) -> list[object]:
    return [kwargs["as_verifier"] for _, kwargs in forge.calls("post_review")]


@_WITH_CREDENTIAL
def test_the_comment_requests_identity_from_credential_resolution(
    monkeypatch: pytest.MonkeyPatch, forge: ForgeScript,
    credential: dict[str, str] | None, requested: bool,
) -> None:
    _resolve(monkeypatch, credential)
    forge.queue("post_review", api_result("{}"))
    _post_comment()
    assert _requested(forge) == [requested]


def test_the_comment_posts_the_cannot_verify_body_as_a_comment_without_findings(
    forge: ForgeScript,
) -> None:
    forge.queue("post_review", api_result("{}"))
    grounding_mod._post_cannot_verify_comment(  # noqa: SLF001
        7, "task-x", "inv_1", forge_target=_WIDGET_FORGE,
    )
    assert forge.calls("post_review") == [
        (
            (7, {
                "body": grounding_mod._CANNOT_VERIFY_BODY,  # noqa: SLF001
                "event": "COMMENT",
                "comments": [],
            }),
            {"forge_target": _WIDGET_FORGE, "as_verifier": False},
        ),
    ]


def test_dry_run_makes_no_forge_call_and_audits_the_comment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _post_comment()
    assert forge.calls("post_review") == []
    entries = [
        json.loads(ln)
        for ln in (tmp_path / "logs" / "verification_audit.jsonl").read_text().splitlines()
    ]
    fields = ("action", "verdict", "event", "inv_id", "pr_number")
    assert [tuple(e[f] for f in fields) for e in entries] == [
        ("dry_run", "COMMENT", "COMMENT", "inv_1", 42),
    ]


def test_a_nonzero_comment_post_warns_and_does_not_raise(
    forge: ForgeScript, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING")
    forge.queue("post_review", api_result(returncode=1, stderr="boom"))
    _post_comment()
    assert any("cannot_verify review post failed rc=1" in r.message for r in caplog.records)


@pytest.mark.parametrize(
    "error",
    [FileNotFoundError("gh"), subprocess.TimeoutExpired(cmd="gh", timeout=1.0)],
    ids=["missing-gh", "timeout"],
)
def test_a_comment_transport_failure_does_not_raise(
    forge: ForgeScript, error: BaseException,
) -> None:
    forge.queue("post_review", error)
    _post_comment()
    assert len(forge.calls("post_review")) == 1


def test_both_posting_paths_take_the_identity_from_the_shared_predicate(
    monkeypatch: pytest.MonkeyPatch, forge: ForgeScript,
) -> None:
    _resolve(monkeypatch, None)
    monkeypatch.setattr(_pr_post, "verifier_identity_available", lambda _t: True)
    forge.queue("post_review", api_result("{}"), api_result("{}"))
    _post_comment()
    _post_verdict_review()
    assert _requested(forge) == [True, True]


@_WITH_CREDENTIAL
def test_both_posting_paths_follow_the_same_credential_resolution(
    monkeypatch: pytest.MonkeyPatch, forge: ForgeScript,
    credential: dict[str, str] | None, requested: bool,
) -> None:
    _resolve(monkeypatch, credential)
    forge.queue("post_review", api_result("{}"), api_result("{}"))
    _post_comment()
    _post_verdict_review()
    assert _requested(forge) == [requested, requested]
