from __future__ import annotations

import json
import subprocess
from datetime import date

import pytest

from alan import branch_protection_compat as bpc
from alan import github_repos as gr

_Queued = subprocess.CompletedProcess[str] | BaseException
CLASSIC_SHAPED = "gh" + "p_" + "0123456789abcdef"
FINE_GRAINED_SHAPED = "github" + "_pat_" + "0123456789abcdef"


def _completed(
    *, rc: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)


def _identity(*header_lines: str, body: str = '{"login":"octo"}\n', eol: str = "\r\n") -> str:
    head = "HTTP/2.0 200 OK" + eol + "".join(line + eol for line in header_lines)
    return head + eol + body


def _queue(monkeypatch: pytest.MonkeyPatch, *responses: _Queued) -> list[tuple[list[str], str]]:
    seen: list[tuple[list[str], str]] = []
    pending = iter(responses)

    def _fake(args: list[str], *, token: str, timeout: float) -> subprocess.CompletedProcess[str]:
        del timeout
        seen.append((list(args), token))
        item = next(pending)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(gr, "_invoke_gh_api", _fake)
    return seen


def test_a_response_without_the_scopes_header_is_a_fine_grained_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _queue(monkeypatch, _completed(stdout=_identity("content-type: application/json")))
    identity = gr.probe_token_identity("tok")
    assert identity == gr.TokenIdentity(login="octo", fine_grained=True)
    assert seen == [(["-i", "user"], "tok")]


@pytest.mark.parametrize(
    "header",
    ["x-oauth-scopes: repo, workflow", "X-OAuth-Scopes: repo", "x-oauth-scopes:"],
    ids=["lowercase", "mixed-case", "empty-value"],
)
def test_a_response_with_the_scopes_header_is_not_fine_grained_even_when_it_is_empty(
    monkeypatch: pytest.MonkeyPatch, header: str
) -> None:
    _queue(monkeypatch, _completed(stdout=_identity(header)))
    assert gr.probe_token_identity("tok").fine_grained is False


@pytest.mark.parametrize("eol", ["\r\n", "\n"], ids=["crlf", "lf"])
def test_the_login_is_read_from_the_body_under_either_line_ending(
    monkeypatch: pytest.MonkeyPatch, eol: str
) -> None:
    _queue(monkeypatch, _completed(stdout=_identity("etag: x", eol=eol)))
    assert gr.probe_token_identity("tok").login == "octo"


def test_the_body_may_span_several_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps({"id": 1, "login": "octo"}, indent=2) + "\n"
    _queue(monkeypatch, _completed(stdout=_identity(body=body)))
    assert gr.probe_token_identity("tok").login == "octo"


@pytest.mark.parametrize("token", [CLASSIC_SHAPED, FINE_GRAINED_SHAPED])
def test_the_classification_never_looks_at_the_token_prefix(
    monkeypatch: pytest.MonkeyPatch, token: str
) -> None:
    fine = _completed(stdout=_identity())
    classic = _completed(stdout=_identity("x-oauth-scopes:"))
    _queue(monkeypatch, fine, classic)
    assert gr.probe_token_identity(token).fine_grained is True
    assert gr.probe_token_identity(token).fine_grained is False


def test_a_rejected_token_raises_a_reason_that_carries_nothing_from_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _queue(monkeypatch, _completed(rc=1, stderr=f"bad credentials {CLASSIC_SHAPED}"))
    with pytest.raises(gr.TokenIdentityError) as raised:
        gr.probe_token_identity(CLASSIC_SHAPED)
    assert "rejected the token" in str(raised.value)
    assert CLASSIC_SHAPED not in str(raised.value)


def test_a_call_that_cannot_run_raises_an_unreachable_reason_without_the_underlying_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _queue(monkeypatch, bpc._GhApiCallError(f"raised with {CLASSIC_SHAPED}"))  # noqa: SLF001
    with pytest.raises(gr.TokenIdentityError) as raised:
        gr.probe_token_identity(CLASSIC_SHAPED)
    assert "could not be reached" in str(raised.value)
    assert CLASSIC_SHAPED not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        "HTTP/2.0 200 OK\r\ncontent-type: application/json\r\n",
        _identity(body="not json"),
        _identity(body='["login"]'),
        _identity(body='{"id": 1}'),
        _identity(body='{"login": ""}'),
        _identity(body='{"login": 7}'),
    ],
    ids=["empty", "no-body", "not-json", "not-object", "no-login", "empty-login", "login-not-text"],
)
def test_an_answer_that_names_no_account_is_refused(
    monkeypatch: pytest.MonkeyPatch, stdout: str
) -> None:
    _queue(monkeypatch, _completed(stdout=stdout))
    with pytest.raises(gr.TokenIdentityError, match="named no account"):
        gr.probe_token_identity("tok")


def test_an_answer_with_no_output_at_all_is_refused_without_a_type_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _queue(monkeypatch, subprocess.CompletedProcess(args=[], returncode=0, stdout=None))
    with pytest.raises(gr.TokenIdentityError):
        gr.probe_token_identity("tok")


EXPIRY = "github-authentication-token-expiration"


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (f"{EXPIRY}: 2099-06-30 18:30:00 UTC", date(2099, 6, 30)),
        (f"{EXPIRY}: 2099-06-30 23:59:59 UTC", date(2099, 6, 30)),
        (f"{EXPIRY}: 2099-01-01 01:30:00 +0300", date(2098, 12, 31)),
        ("Github-Authentication-Token-Expiration: 2099-01-01 01:30:00 +03:00", date(2098, 12, 31)),
        (f"{EXPIRY}: 2099-01-01 23:30:00 -0300", date(2099, 1, 2)),
        (f"{EXPIRY}:   2099-06-30 00:00:00 UTC  ", date(2099, 6, 30)),
    ],
    ids=["utc", "utc-late", "east-offset", "colon-offset", "west-offset", "padded"],
)
def test_the_expiry_header_is_reported_as_its_utc_calendar_date(
    monkeypatch: pytest.MonkeyPatch, header: str, expected: date
) -> None:
    _queue(monkeypatch, _completed(stdout=_identity("x-oauth-scopes: repo", header)))
    identity = gr.probe_token_identity("tok")
    assert identity.expires_on == expected
    assert identity.fine_grained is False


def test_a_response_without_the_expiry_header_reports_no_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _queue(monkeypatch, _completed(stdout=_identity("x-oauth-scopes: repo")))
    assert gr.probe_token_identity("tok").expires_on is None


@pytest.mark.parametrize(
    "value",
    ["", "soon", "2099-06-30", "2099-13-45 00:00:00 UTC", "2099-06-30 18:30:00 CET"],
    ids=["empty", "words", "date-only", "impossible-date", "named-zone"],
)
def test_an_expiry_setup_cannot_read_is_refused_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    _queue(monkeypatch, _completed(stdout=_identity(f"{EXPIRY}: {value}")))
    unreadable = "expiry for the token that setup cannot read"
    with pytest.raises(gr.TokenIdentityError, match=unreadable) as raised:
        gr.probe_token_identity("tok")
    if value:
        assert value not in str(raised.value)


def _repository(**permissions: bool) -> subprocess.CompletedProcess[str]:
    return _completed(stdout=json.dumps({"full_name": "o/r", "permissions": permissions}))


@pytest.mark.parametrize("granted", ["push", "maintain", "admin"])
def test_push_maintain_or_admin_permission_is_write_access(
    monkeypatch: pytest.MonkeyPatch, granted: str
) -> None:
    seen = _queue(monkeypatch, _repository(**{granted: True, "pull": True}))
    assert gr.probe_repo_access("tok", "o/r") == "write"
    assert seen == [(["repos/o/r"], "tok")]


def test_read_or_triage_permission_alone_is_not_write_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _queue(
        monkeypatch,
        _repository(pull=True, triage=True, push=False),
        _completed(stdout="[]"),
    )
    assert gr.probe_repo_access("tok", "o/r") == "none"
    assert [args for args, _ in seen] == [["repos/o/r"], ["user/repository_invitations"]]


def _invitations(*names: object) -> subprocess.CompletedProcess[str]:
    return _completed(stdout=json.dumps([{"repository": {"full_name": name}} for name in names]))


def test_a_pending_invitation_to_the_repository_is_reported_in_any_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _queue(monkeypatch, _repository(pull=True), _invitations("Other/Repo", "O/R"))
    assert gr.probe_repo_access("tok", "o/r") == "invited"


def test_an_invitation_to_another_repository_is_not_an_invitation_to_this_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _queue(monkeypatch, _repository(pull=True), _invitations("other/repo", None, 7))
    assert gr.probe_repo_access("tok", "o/r") == "none"


def test_a_repository_the_token_cannot_see_falls_through_to_the_invitation_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _queue(monkeypatch, _completed(rc=1, stderr="gh: Not Found (HTTP 404)"), _invitations("o/r"))
    assert gr.probe_repo_access("tok", "o/r") == "invited"


@pytest.mark.parametrize(
    "answer",
    [
        _completed(stdout="not json"),
        _completed(stdout="[]"),
        _completed(stdout='{"permissions": "push"}'),
        _completed(stdout='{"permissions": {"push": "true"}}'),
        _completed(stdout='{"id": 1}'),
        subprocess.CompletedProcess(args=[], returncode=0, stdout=None),
    ],
    ids=[
        "not-json",
        "list",
        "permissions-text",
        "permission-not-true",
        "no-permissions",
        "no-output",
    ],
)
def test_a_repository_answer_that_grants_nothing_is_not_write_access(
    monkeypatch: pytest.MonkeyPatch, answer: subprocess.CompletedProcess[str]
) -> None:
    _queue(monkeypatch, answer, _completed(stdout="[]"))
    assert gr.probe_repo_access("tok", "o/r") == "none"


@pytest.mark.parametrize(
    "invitations",
    [
        _completed(rc=1, stderr="gh: Forbidden (HTTP 403)"),
        _completed(stdout="not json"),
        _completed(stdout='{"repository": {"full_name": "o/r"}}'),
        _completed(stdout='["o/r", {"repository": "o/r"}, {"repository": {}}]'),
    ],
    ids=["refused", "not-json", "not-a-list", "malformed-entries"],
)
def test_an_invitation_answer_that_names_nothing_is_no_invitation(
    monkeypatch: pytest.MonkeyPatch, invitations: subprocess.CompletedProcess[str]
) -> None:
    _queue(monkeypatch, _repository(pull=True), invitations)
    assert gr.probe_repo_access("tok", "o/r") == "none"


@pytest.mark.parametrize("fails_at", [0, 1], ids=["repository", "invitations"])
def test_a_call_that_cannot_run_raises_an_unreachable_reason_for_the_access_probe(
    monkeypatch: pytest.MonkeyPatch, fails_at: int
) -> None:
    answers: list[_Queued] = [_repository(pull=True), _completed(stdout="[]")]
    answers[fails_at] = bpc._GhApiCallError(f"raised with {CLASSIC_SHAPED}")  # noqa: SLF001
    _queue(monkeypatch, *answers)
    with pytest.raises(gr.RepoAccessError) as raised:
        gr.probe_repo_access(CLASSIC_SHAPED, "o/r")
    assert "could not be reached" in str(raised.value)
    assert CLASSIC_SHAPED not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True
