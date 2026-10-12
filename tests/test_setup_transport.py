from __future__ import annotations

import getpass
import io
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from alan import github_repos, secrets_store, setup_transport
from alan.control_plane import CONTROL_TOKEN_HEADER
from tests._setup_support import LEAK_PATTERNS, TOKEN_SENTINEL, TRANSPORT_SENTINEL, leaks

FAKE_TOKEN = TRANSPORT_SENTINEL
FAKE_CONTROL = TOKEN_SENTINEL
CLASSIC_SHAPED = "gh" + "p_" + "0123456789abcdef"


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def _identity(monkeypatch: pytest.MonkeyPatch, result: object) -> list[tuple[str, float]]:
    seen: list[tuple[str, float]] = []

    def _fake(token: str, *, timeout: float) -> github_repos.TokenIdentity:
        seen.append((token, timeout))
        if isinstance(result, BaseException):
            raise result
        assert isinstance(result, github_repos.TokenIdentity)
        return result

    monkeypatch.setattr(github_repos, "probe_token_identity", _fake)
    return seen


def _state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "state"
    root.mkdir()
    monkeypatch.setenv("ALAN_STATE_ROOT", str(root))
    return root


def _seed_control_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _state_root(tmp_path, monkeypatch)
    (root / "control_token").write_text(FAKE_CONTROL, encoding="utf-8")


def test_the_permissions_name_administration_write_and_say_why() -> None:
    text = " ".join(setup_transport.PERMISSIONS_TEXT.split())
    assert "Administration: Read and write" in text
    assert "Why Administration write" in text
    assert "branch protection" in text
    assert "Only select repositories" in text
    assert "Metadata: Read-only" in text
    assert "not shown, not printed and not passed on any command line" in text


def test_the_risk_says_the_token_stays_in_alans_store_for_slot_clones_and_pushes() -> None:
    text = " ".join(setup_transport.RISK_TEXT.split())
    assert "--use-gh-token" in text
    assert "every repository your GitHub account can reach" in text
    assert "stays there" in text
    assert "slot clone, fetch and push" in text


@pytest.mark.parametrize("text", [setup_transport.PERMISSIONS_TEXT, setup_transport.RISK_TEXT])
def test_the_printed_texts_carry_no_credential_or_identity_pattern(text: str) -> None:
    assert leaks(text) == {}
    assert set(LEAK_PATTERNS) == {"machine-name", "personal-email", "tailnet-host", "token-prefix"}


def test_a_private_file_has_mode_600_and_its_text(tmp_path: Path) -> None:
    target = tmp_path / "private.txt"
    setup_transport.write_private_file(target, "value")
    assert target.read_text(encoding="utf-8") == "value"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert list(tmp_path.iterdir()) == [target]


def test_the_control_header_carries_the_token_from_the_pinned_state_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (_state_root(tmp_path, monkeypatch) / "control_token").write_text(
        FAKE_CONTROL + "\n", encoding="utf-8"
    )
    assert setup_transport.control_header_text() == f"{CONTROL_TOKEN_HEADER}: {FAKE_CONTROL}\n"


def test_a_missing_control_token_file_is_refused_without_naming_a_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _state_root(tmp_path, monkeypatch)
    with pytest.raises(setup_transport.TransportRefused, match="run the start step") as raised:
        setup_transport.control_header_text()
    assert str(root) not in str(raised.value)


def test_an_empty_control_token_file_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (_state_root(tmp_path, monkeypatch) / "control_token").write_text(" \n", encoding="utf-8")
    with pytest.raises(setup_transport.TransportRefused, match="is empty"):
        setup_transport.control_header_text()


def test_a_token_is_read_from_the_first_line_and_stripped() -> None:
    assert setup_transport.read_token(io.StringIO(f"  {FAKE_TOKEN} \nsecond\n")) == FAKE_TOKEN


@pytest.mark.parametrize("text", ["", "\n", "   \t\n"], ids=["eof", "blank", "spaces"])
def test_empty_input_and_end_of_file_are_refused_with_the_way_out(text: str) -> None:
    with pytest.raises(setup_transport.TransportRefused) as raised:
        setup_transport.read_token(io.StringIO(text))
    assert "--use-gh-token" in str(raised.value)
    assert "terminal" in str(raised.value)


def test_a_terminal_is_read_without_echo_through_getpass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[str] = []

    def _fake_getpass(prompt: str) -> str:
        prompts.append(prompt)
        return FAKE_TOKEN + "\n"

    monkeypatch.setattr(getpass, "getpass", _fake_getpass)
    assert setup_transport.read_token(_Tty("")) == FAKE_TOKEN
    assert prompts == [setup_transport.TOKEN_PROMPT]


def test_end_of_file_on_a_terminal_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def _eof(prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr(getpass, "getpass", _eof)
    with pytest.raises(setup_transport.TransportRefused, match="no token was entered"):
        setup_transport.read_token(_Tty(""))


class _Piped(io.StringIO):
    def __init__(self, fd: int) -> None:
        super().__init__("")
        self._fd = fd

    def fileno(self) -> int:
        return self._fd


def _pipe(payload: bytes) -> tuple[int, int]:
    read_end, write_end = os.pipe()
    os.write(write_end, payload)
    return read_end, write_end


def _close(*fds: int) -> None:
    for fd in fds:
        os.close(fd)


def test_the_fd_reader_takes_one_line_and_leaves_the_rest_of_the_pipe_for_the_next_reader() -> (
    None
):
    read_end, write_end = _pipe(b"first-token\nsecond-token\nthird\n")
    try:
        assert setup_transport.read_fd0_line(read_end) == "first-token"
        assert setup_transport.read_fd0_line(read_end) == "second-token"
        os.close(write_end)
        assert os.read(read_end, 100) == b"third\n"
    finally:
        _close(read_end)


def test_the_fd_reader_returns_what_it_has_at_end_of_input_and_then_nothing() -> None:
    read_end, write_end = _pipe(b"no-newline")
    os.close(write_end)
    try:
        assert setup_transport.read_fd0_line(read_end) == "no-newline"
        assert setup_transport.read_fd0_line(read_end) == ""
    finally:
        _close(read_end)


def test_the_fd_reader_treats_a_closed_descriptor_as_end_of_input() -> None:
    read_end, write_end = os.pipe()
    _close(read_end, write_end)
    assert setup_transport.read_fd0_line(read_end) == ""


def test_the_fd_reader_keeps_bytes_that_are_not_utf8_from_raising() -> None:
    read_end, write_end = os.pipe()
    os.write(write_end, b"caf\xe9\n")
    try:
        assert setup_transport.read_fd0_line(read_end) == "caf�"
    finally:
        _close(read_end, write_end)


def test_a_stream_backed_by_a_descriptor_is_read_unbuffered_so_a_second_reader_sees_the_rest() -> (
    None
):
    read_end, write_end = _pipe(f"{FAKE_TOKEN}\nnext-token\n".encode())
    try:
        assert setup_transport.read_token(_Piped(read_end)) == FAKE_TOKEN
        assert setup_transport.read_token(_Piped(read_end)) == "next-token"
    finally:
        _close(read_end, write_end)


def test_a_descriptor_stream_at_end_of_input_is_refused_with_the_way_out() -> None:
    read_end, write_end = os.pipe()
    os.close(write_end)
    try:
        with pytest.raises(setup_transport.TransportRefused, match="no token was entered"):
            setup_transport.read_token(_Piped(read_end))
    finally:
        os.close(read_end)


def test_a_terminal_prompt_can_be_named_by_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []

    def _fake_getpass(prompt: str) -> str:
        prompts.append(prompt)
        return FAKE_TOKEN

    monkeypatch.setattr(getpass, "getpass", _fake_getpass)
    assert setup_transport.read_token(_Tty(""), prompt="Other (hidden): ") == FAKE_TOKEN
    assert prompts == ["Other (hidden): "]


def test_a_token_is_read_from_a_pipe_for_the_gh_session_with_its_own_message() -> None:
    assert setup_transport.read_token(io.StringIO(FAKE_TOKEN + "\n"), missing="x") == FAKE_TOKEN
    with pytest.raises(setup_transport.TransportRefused, match="gh session token cannot be read"):
        setup_transport.read_token(
            io.StringIO(""), missing="the gh session token cannot be read; sign in"
        )


def test_a_token_that_githubs_answer_shows_fine_grained_and_the_same_account_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _identity(monkeypatch, github_repos.TokenIdentity(login="Octo", fine_grained=True))
    assert setup_transport.verified_token(FAKE_TOKEN, "octo") == FAKE_TOKEN
    assert seen == [(FAKE_TOKEN, setup_transport.GH_TIMEOUT_S)]


def test_a_token_that_is_not_fine_grained_is_refused_before_the_account_is_compared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _identity(monkeypatch, github_repos.TokenIdentity(login="octo", fine_grained=False))
    with pytest.raises(setup_transport.TransportRefused, match="not a fine-grained") as raised:
        setup_transport.verified_token(CLASSIC_SHAPED, "someone-else")
    assert CLASSIC_SHAPED not in str(raised.value)


def test_a_token_for_another_account_is_refused_without_naming_either_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _identity(monkeypatch, github_repos.TokenIdentity(login="intruder", fine_grained=True))
    refused = pytest.raises(setup_transport.TransportRefused, match="different GitHub account")
    with refused as raised:
        setup_transport.verified_token(FAKE_TOKEN, "octo")
    assert "intruder" not in str(raised.value)
    assert "octo" not in str(raised.value)


def test_a_token_githubs_answer_rejects_is_refused_with_githubs_reason_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _identity(monkeypatch, github_repos.TokenIdentityError("GitHub rejected the token"))
    with pytest.raises(setup_transport.TransportRefused, match="GitHub rejected") as raised:
        setup_transport.verified_token(FAKE_TOKEN, "octo")
    assert raised.value.__cause__ is None
    assert FAKE_TOKEN not in str(raised.value)


def test_an_empty_signed_in_user_never_matches_an_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _identity(monkeypatch, github_repos.TokenIdentity(login="octo", fine_grained=True))
    with pytest.raises(setup_transport.TransportRefused, match="different GitHub account"):
        setup_transport.verified_token(FAKE_TOKEN, "")


def test_the_body_names_the_engine_and_the_transport_purpose() -> None:
    body = json.loads(setup_transport.transport_body("Acme/Widget", FAKE_TOKEN))
    assert body == {
        "engine": "Acme/Widget",
        "purpose": secrets_store.TRANSPORT_PURPOSE,
        "value": FAKE_TOKEN,
    }


def test_the_permissions_are_printed_before_the_token_is_read_and_the_body_is_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _identity(monkeypatch, github_repos.TokenIdentity(login="octo", fine_grained=True))
    out = tmp_path / "body.json"
    setup_transport.run_transport_body(
        "a/b",
        out,
        use_gh_token=False,
        signed_in_user="octo",
        stream=io.StringIO(FAKE_TOKEN + "\n"),
    )
    printed = capsys.readouterr()
    assert setup_transport.PERMISSIONS_TEXT in printed.out
    assert setup_transport.RISK_TEXT not in printed.out
    assert FAKE_TOKEN not in printed.out + printed.err
    assert json.loads(out.read_text(encoding="utf-8"))["value"] == FAKE_TOKEN
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_the_flag_prints_the_risk_and_stores_the_gh_session_token_without_checking_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = _identity(monkeypatch, github_repos.TokenIdentityError("must not be asked"))
    out = tmp_path / "body.json"
    setup_transport.run_transport_body(
        "a/b",
        out,
        use_gh_token=True,
        signed_in_user="",
        stream=io.StringIO(FAKE_TOKEN + "\n"),
    )
    printed = capsys.readouterr()
    assert setup_transport.RISK_TEXT in printed.out
    assert setup_transport.PERMISSIONS_TEXT not in printed.out
    assert FAKE_TOKEN not in printed.out + printed.err
    assert json.loads(out.read_text(encoding="utf-8"))["value"] == FAKE_TOKEN
    assert seen == []


def test_main_writes_the_private_header_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_control_token(tmp_path, monkeypatch)
    out = tmp_path / "control.header"
    assert setup_transport.main(["control-header", "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == f"{CONTROL_TOKEN_HEADER}: {FAKE_CONTROL}\n"
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    printed = capsys.readouterr()
    assert FAKE_CONTROL not in printed.out + printed.err


def test_main_writes_the_transport_body_from_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _identity(monkeypatch, github_repos.TokenIdentity(login="octo", fine_grained=True))
    monkeypatch.setattr(sys, "stdin", io.StringIO(FAKE_TOKEN + "\n"))
    out = tmp_path / "body.json"
    argv = ["transport-body", "--engine", "a/b", "--out", str(out), "--signed-in-user", "octo"]
    assert setup_transport.main(argv) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["engine"] == "a/b"
    assert FAKE_TOKEN not in capsys.readouterr().out


def test_main_passes_the_flag_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(FAKE_TOKEN + "\n"))
    out = tmp_path / "body.json"
    argv = ["transport-body", "--engine", "a/b", "--out", str(out), "--use-gh-token"]
    assert setup_transport.main(argv) == 0
    assert setup_transport.RISK_TEXT in capsys.readouterr().out


def test_main_returns_1_with_the_reason_on_stderr_and_writes_nothing_on_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    out = tmp_path / "body.json"
    assert setup_transport.main(["transport-body", "--engine", "a/b", "--out", str(out)]) == 1
    printed = capsys.readouterr()
    assert printed.err.startswith("setup_transport: no token was entered")
    assert not out.exists()


def test_main_returns_1_when_the_output_directory_does_not_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_control_token(tmp_path, monkeypatch)
    target = tmp_path / "missing" / "control.header"
    assert setup_transport.main(["control-header", "--out", str(target)]) == 1
    printed = capsys.readouterr()
    assert printed.err.startswith("setup_transport:")
    assert FAKE_CONTROL not in printed.out + printed.err


@pytest.mark.parametrize("argv", [[], ["unknown"], ["transport-body", "--out", "x"]])
def test_main_exits_2_on_a_usage_error(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        setup_transport.main(argv)
    assert raised.value.code == 2


def test_the_module_reads_no_environment_variable_of_its_own() -> None:
    source = Path(setup_transport.__file__).read_text(encoding="utf-8")
    assert "os.environ" not in source
    assert "getenv" not in source
