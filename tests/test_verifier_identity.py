from __future__ import annotations

import pytest

from alan import default_profile
from alan.default_profile import UNCONFIGURED_PROFILE, Resolved, Source
from alan.teams import _pr_branch
from alan.teams._pr_branch import VerifierIdentity, is_verifier, verifier_identity

_ENV = "ALAN_VERIFIER_LOGIN"
_OWNER_BOT = "alan-verifier"


def _profile_declares(monkeypatch: pytest.MonkeyPatch, login: str | None) -> None:
    profile = UNCONFIGURED_PROFILE.model_copy(update={"verifier_login": login})
    monkeypatch.setattr(default_profile, "resolved", lambda: Resolved(profile, Source.profile))


@pytest.mark.parametrize(
    ("author", "expected"),
    [
        (_OWNER_BOT, True),
        (None, False),
        ("", False),
        (" ", False),
        ("Alan-Verifier", False),
        (f" {_OWNER_BOT}", False),
        (f"{_OWNER_BOT} ", False),
        (f"{_OWNER_BOT}2", False),
        ("alan", False),
    ],
)
def test_an_identity_matches_only_the_exact_login(author: str | None, expected: bool) -> None:
    assert VerifierIdentity(_OWNER_BOT).matches(author) is expected


@pytest.mark.parametrize("author", [None, "", _OWNER_BOT])
def test_an_identity_with_an_empty_login_matches_nothing(author: str | None) -> None:
    assert VerifierIdentity("").matches(author) is False


@pytest.mark.parametrize(
    ("identity", "author", "expected"),
    [
        (None, _OWNER_BOT, False),
        (None, None, False),
        (None, "", False),
        (VerifierIdentity(_OWNER_BOT), _OWNER_BOT, True),
        (VerifierIdentity(_OWNER_BOT), "someone-else", False),
        (VerifierIdentity(_OWNER_BOT), None, False),
        (VerifierIdentity(_OWNER_BOT), "", False),
    ],
)
def test_no_identity_matches_any_author(
    identity: VerifierIdentity | None, author: str | None, expected: bool
) -> None:
    assert is_verifier(identity, author) is expected


def test_the_default_profile_supplies_the_login_when_the_environment_sets_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_ENV, raising=False)
    assert verifier_identity() == VerifierIdentity(_OWNER_BOT)


def test_the_environment_login_takes_precedence_over_the_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _profile_declares(monkeypatch, "profile-bot")
    monkeypatch.setenv(_ENV, "env-bot")
    assert verifier_identity() == VerifierIdentity("env-bot")


def test_the_environment_login_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    _profile_declares(monkeypatch, "profile-bot")
    monkeypatch.setenv(_ENV, "  env-bot\n")
    assert verifier_identity() == VerifierIdentity("env-bot")


def test_the_environment_login_alone_is_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    _profile_declares(monkeypatch, None)
    monkeypatch.setenv(_ENV, "env-bot")
    assert verifier_identity() == VerifierIdentity("env-bot")


@pytest.mark.parametrize("blank", ["", " ", "   ", "\t\n"])
def test_a_blank_environment_login_falls_through_to_the_profile(
    monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    _profile_declares(monkeypatch, "profile-bot")
    monkeypatch.setenv(_ENV, blank)
    assert verifier_identity() == VerifierIdentity("profile-bot")


def test_the_profile_login_alone_is_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    _profile_declares(monkeypatch, "profile-bot")
    monkeypatch.delenv(_ENV, raising=False)
    assert verifier_identity() == VerifierIdentity("profile-bot")


def test_neither_the_environment_nor_the_profile_leaves_no_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _profile_declares(monkeypatch, None)
    monkeypatch.delenv(_ENV, raising=False)
    assert verifier_identity() is None


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_environment_login_without_a_profile_login_leaves_no_identity(
    monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    _profile_declares(monkeypatch, None)
    monkeypatch.setenv(_ENV, blank)
    assert verifier_identity() is None


def test_an_unconfigured_install_has_no_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        default_profile,
        "resolved",
        lambda: Resolved(UNCONFIGURED_PROFILE, Source.unconfigured),
    )
    monkeypatch.delenv(_ENV, raising=False)
    assert verifier_identity() is None


def test_no_hardcoded_verifier_login_remains_in_the_branch_module() -> None:
    assert not hasattr(_pr_branch, "verifier_login")
    assert not hasattr(_pr_branch, "_DEFAULT_VERIFIER_LOGIN")
