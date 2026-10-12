from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import default_profile, process_runner, roots, self_update
from alan.default_profile import Source
from alan.self_update import Reason, StatusRecord, Verdict
from alan.teams import _triage_auto_update
from tests.self_update_fixtures import OWNER, REPO, redirect_env, store_transport_credential

NOW = datetime(2026, 1, 1, tzinfo=UTC)
SHA_A = "a" * 40
SHA_B = "b" * 40
PROFILE_TEXT = f"owner: {OWNER}\nrepo: {REPO}\nposture: observe\n"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    redirect_env(monkeypatch, tmp_path)
    monkeypatch.delenv("ALAN_DEPLOY_VERIFY_BUDGET_S", raising=False)


def seed_updater_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, installed_profile: bool
) -> None:
    owner_file = tmp_path / "owner" / "default_profile.yaml"
    owner_file.parent.mkdir()
    owner_file.write_text(PROFILE_TEXT, encoding="utf-8")
    engines = tmp_path / "engines"
    engines.mkdir()
    if installed_profile:
        (engines / "default_profile.yaml").write_text(PROFILE_TEXT, encoding="utf-8")
    monkeypatch.setattr(
        default_profile, "resolved", lambda: default_profile.resolve(engines, owner_file)
    )
    self_update._write_status(
        StatusRecord(Verdict.BLOCKED, Reason.DIRTY_TREE, SHA_A, SHA_B, NOW.isoformat())
    )
    store_transport_credential()
    assert self_update.status_path().is_file()
    assert len(list(roots.secrets_store_root().glob("*.json"))) == 1


def arm_spies(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden_store_root() -> Path:
        raise AssertionError("the credential store was read")

    def forbidden_spawn(*args: object, **kwargs: object) -> object:
        raise AssertionError("a command was spawned")

    monkeypatch.setattr(roots, "secrets_store_root", forbidden_store_root)
    monkeypatch.setattr(process_runner, "run_subprocess", forbidden_spawn)


def test_owner_install_never_reads_the_updater_credential_or_runs_gh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seed_updater_state(monkeypatch, tmp_path, installed_profile=False)
    assert default_profile.resolved().source is Source.owner
    arm_spies(monkeypatch)
    assert _triage_auto_update.auto_update_surfaces(now=NOW) == []


def test_a_profile_source_with_the_same_status_record_still_surfaces(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seed_updater_state(monkeypatch, tmp_path, installed_profile=True)
    assert default_profile.resolved().source is Source.profile
    arm_spies(monkeypatch)
    surfaces = _triage_auto_update.auto_update_surfaces(now=NOW)
    assert [surface.surface_id for surface in surfaces] == ["auto_update:dirty_tree"]
