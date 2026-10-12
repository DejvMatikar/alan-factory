from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from alan import forge as forge_mod
from alan.engine_config import ForgeTarget, RepoIdentity
from alan.forge import (
    ChecksRead,
    CompareRead,
    FileRead,
    ForgePort,
    JsonObject,
    LoginRead,
    MergeAttempt,
    PrRead,
    RowsRead,
)
from alan.forge_gh import GhForge
from tests.fakes.forge import FakeForge

_conformance: ForgePort = GhForge()
_fake_conformance: ForgePort = FakeForge()

_TARGET = ForgeTarget(
    repo=RepoIdentity(owner="acme", repo="widget"),
    credential_env_name="ACME_GH_TOKEN",
)


def test_pr_read_is_frozen() -> None:
    read = PrRead(ok=True, fields={}, returncode=0, stderr="")
    with pytest.raises(FrozenInstanceError):
        read.ok = False  # type: ignore[misc]


def test_checks_read_is_frozen() -> None:
    read = ChecksRead(ok=True, checks=(), returncode=0, stderr="")
    with pytest.raises(FrozenInstanceError):
        read.ok = False  # type: ignore[misc]


def test_merge_attempt_is_frozen() -> None:
    attempt = MergeAttempt(ok=True, returncode=0, stderr="")
    with pytest.raises(FrozenInstanceError):
        attempt.ok = False  # type: ignore[misc]


def test_compare_read_is_frozen() -> None:
    read = CompareRead(
        ok=True, behind_by=0, files=frozenset(), truncated=False, returncode=0, stderr="",
    )
    with pytest.raises(FrozenInstanceError):
        read.ok = False  # type: ignore[misc]


def test_rows_read_is_frozen() -> None:
    read = RowsRead(outcome="ok", rows=(), returncode=0, stderr="")
    with pytest.raises(FrozenInstanceError):
        read.outcome = "failed"  # type: ignore[misc]


def test_login_read_is_frozen() -> None:
    read = LoginRead(outcome="ok", login="octocat", returncode=0, stderr="")
    with pytest.raises(FrozenInstanceError):
        read.login = None  # type: ignore[misc]


def test_file_read_is_frozen() -> None:
    read = FileRead(outcome="ok", text="", returncode=0, stderr="")
    with pytest.raises(FrozenInstanceError):
        read.text = None  # type: ignore[misc]


def test_pr_read_carries_decoded_fields() -> None:
    payload: JsonObject = {"headRefName": "teams/x"}
    read = PrRead(ok=True, fields=payload, returncode=0, stderr="")
    assert read.fields == payload


def test_checks_read_carries_check_tuple() -> None:
    read = ChecksRead(ok=True, checks=({"name": "ci"},), returncode=0, stderr="")
    assert read.checks == ({"name": "ci"},)


def test_merge_attempt_carries_returncode_and_stderr() -> None:
    attempt = MergeAttempt(ok=False, returncode=1, stderr="boom")
    assert attempt.returncode == 1
    assert attempt.stderr == "boom"


def test_compare_read_carries_its_fields() -> None:
    read = CompareRead(
        ok=False, behind_by=4, files=frozenset({"a.py"}), truncated=True,
        returncode=1, stderr="boom",
    )
    assert read.behind_by == 4
    assert read.files == frozenset({"a.py"})
    assert read.truncated is True
    assert read.returncode == 1
    assert read.stderr == "boom"


def test_forge_port_conformance_binding_is_gh_forge() -> None:
    assert isinstance(_conformance, GhForge)


def test_forge_target_used_in_conformance_binding_is_importable() -> None:
    assert _TARGET.repo.slug == "acme/widget"


def test_active_forge_returns_the_installed_instance() -> None:
    fake = FakeForge()
    forge_mod.set_active_forge(fake)
    assert forge_mod.active_forge() is fake


def test_active_forge_raises_when_nothing_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(forge_mod, "_active_forge", None)
    with pytest.raises(RuntimeError):
        forge_mod.active_forge()


def test_fake_forge_refuses_an_unstubbed_open_prs_listing() -> None:
    with pytest.raises(AssertionError, match="unstubbed forge call: open_prs_by_author"):
        FakeForge().open_prs_by_author("app/dependabot", ("number",), forge_target=_TARGET, limit=1)


def test_fake_forge_serves_a_stubbed_open_prs_listing_and_records_the_call() -> None:
    fake = FakeForge()
    fake.stub("open_prs_by_author", lambda *_a, **_k: ({"number": 3},))
    listing = fake.open_prs_by_author("octocat", ("number",), forge_target=_TARGET, limit=5)
    assert listing == ({"number": 3},)
    assert fake.calls == [
        ("open_prs_by_author", ("octocat", ("number",)), {"forge_target": _TARGET, "limit": 5}),
    ]


@pytest.mark.parametrize("name", ["prs_by_head", "create_pr", "pr_state"])
def test_fake_forge_refuses_unstubbed_scaffold_pr_operations(name: str) -> None:
    fake = FakeForge()
    method = getattr(fake, name)
    with pytest.raises(AssertionError, match=f"unstubbed forge call: {name}"):
        if name == "prs_by_head":
            method("branch", ("number", "url"), forge_target=_TARGET, limit=1, timeout=1.0)
        elif name == "create_pr":
            method(
                "t", "b", "branch",
                forge_target=_TARGET, workdir=Path("workdir"), timeout=1.0,
            )
        else:
            method(1, forge_target=_TARGET, timeout=1.0)
