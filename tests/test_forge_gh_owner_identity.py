from __future__ import annotations

from pathlib import Path

import pytest

from alan import process_runner, secrets_store
from alan.default_profile import Source
from alan.forge_gh import GhForge
from tests.fakes.forge import _FORGE_METHOD_NAMES  # pyright: ignore[reportPrivateUsage]
from tests.forge_identity_table import (
    FALLBACK_VALUE,
    ROWS,
    TARGET,
    TRANSPORT_VALUE,
    Recorder,
    Row,
    install_source,
)

_ROW_IDS = [row.id for row in ROWS]
_NEVER_CONSULTING_ROWS = [row for row in ROWS if not isinstance(row.kwargs.get("env"), dict)]


@pytest.fixture
def stored_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_SECRETS_STORE_ROOT", str(tmp_path / "secrets"))
    secrets_store.write_secret(TARGET.repo.slug, "transport", TRANSPORT_VALUE)
    monkeypatch.setenv(TARGET.credential_env_name, FALLBACK_VALUE)


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    fake = Recorder()
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    return fake


@pytest.mark.usefixtures("stored_credentials")
@pytest.mark.parametrize("row", ROWS, ids=_ROW_IDS)
def test_owner_install_forge_operations_run_exactly_as_before(
    row: Row, recorder: Recorder, monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_source(monkeypatch, Source.owner)
    row.call(GhForge())
    assert recorder.calls == [(row.argv, row.kwargs)]


@pytest.mark.usefixtures("stored_credentials")
@pytest.mark.parametrize("row", ROWS, ids=_ROW_IDS)
def test_unconfigured_install_matches_owner_command_lines(
    row: Row, recorder: Recorder, monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_source(monkeypatch, Source.unconfigured)
    row.call(GhForge())
    assert recorder.calls == [(row.argv, row.kwargs)]


def _forbid_lookup(*_args: object, **_kwargs: object) -> object:
    pytest.fail("the credential store was consulted")


@pytest.mark.parametrize(
    "row", _NEVER_CONSULTING_ROWS, ids=[row.id for row in _NEVER_CONSULTING_ROWS],
)
def test_owner_install_ops_without_credential_lookup_never_consult_the_store(
    row: Row, recorder: Recorder, monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_source(monkeypatch, Source.owner)
    for name in ("transport_credential", "verifier_gh_env", "resolve_secret"):
        monkeypatch.setattr(secrets_store, name, _forbid_lookup)
    row.call(GhForge())
    assert recorder.calls == [(row.argv, row.kwargs)]


def test_owner_table_covers_every_forge_method() -> None:
    covered = {row.method for row in ROWS}
    public = {name for name in dir(GhForge) if not name.startswith("_")}
    assert covered == set(_FORGE_METHOD_NAMES)
    assert covered == public


def test_owner_table_row_ids_are_unique() -> None:
    assert len(set(_ROW_IDS)) == len(_ROW_IDS)


def test_the_never_consulting_rows_include_the_ambient_pr_operations() -> None:
    methods = {row.method for row in _NEVER_CONSULTING_ROWS}
    assert {"read_pr", "merge_pr", "create_pr_from_checkout", "comment_on_pr"} <= methods
