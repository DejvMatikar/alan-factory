from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest

from alan import process_runner, secrets_store
from alan.default_profile import Source
from alan.engine_config import ForgeTarget, RepoIdentity
from alan.forge import MergeAttempt, RowsRead
from alan.forge_gh import (
    GhForge,
    _gh_or_none,  # pyright: ignore[reportPrivateUsage]
)
from alan.teams import _forge_errors
from tests.forge_identity_table import (
    MISSING_TRANSPORT_STDERR,
    READ_TIMEOUT,
    REVIEWER_VALUE,
    ROWS,
    TARGET,
    TRANSPORT_VALUE,
    Recorder,
    Row,
    prepare,
)

_TRANSPORT_FAULTS = [FileNotFoundError("gh"), subprocess.TimeoutExpired(cmd="gh", timeout=1)]


def _gh_must_not_run(*_args: object, **_kwargs: object) -> object:
    pytest.fail("gh was invoked without a resolved credential")


class _Raiser:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    def __call__(self, *_args: object, **_kwargs: object) -> object:
        raise self.error


@pytest.fixture
def credentialless_copy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    prepare(monkeypatch, tmp_path, Source.profile)
    monkeypatch.setattr(process_runner, "run_subprocess", _gh_must_not_run)


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Recorder:
    return prepare(monkeypatch, tmp_path, Source.profile)


@pytest.mark.usefixtures("credentialless_copy")
@pytest.mark.parametrize("row", ROWS, ids=[row.id for row in ROWS])
def test_copy_with_no_transport_credential_never_invokes_gh_and_returns_the_existing_failure(
    row: Row,
) -> None:
    assert row.call(GhForge()) == row.refusal


@pytest.mark.usefixtures("credentialless_copy")
def test_copy_missing_credential_warning_names_the_transport_credential_without_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secrets_store.write_secret(TARGET.repo.slug, "reviewer", REVIEWER_VALUE)
    with caplog.at_level(logging.WARNING, logger="alan.forge_gh"):
        result = GhForge().merge_pr(7, "abc123", forge_target=TARGET, timeout=READ_TIMEOUT)
    [record] = caplog.records
    assert MISSING_TRANSPORT_STDERR in record.getMessage()
    assert result == MergeAttempt(ok=False, returncode=-1, stderr=MISSING_TRANSPORT_STDERR)
    surfaces = [record.getMessage(), caplog.text, repr(result)]
    assert all(REVIEWER_VALUE not in text for text in surfaces)


def test_missing_credential_message_classifies_as_unknown() -> None:
    classified = _forge_errors.classify_forge_failure(
        returncode=-1, stderr=MISSING_TRANSPORT_STDERR,
    )
    assert classified == "unknown"


@pytest.mark.usefixtures("credentialless_copy")
def test_missing_credential_message_ignores_slug_digits() -> None:
    digits = ForgeTarget(
        repo=RepoIdentity(owner="acme", repo="widget-429"),
        credential_env_name="ACME_FORGE_TOKEN",  # noqa: S106  # nosec B106
    )
    result = GhForge().head_sha(7, forge_target=digits)
    assert result.stderr == MISSING_TRANSPORT_STDERR
    assert _forge_errors.classify_forge_failure(
        returncode=result.returncode, stderr=result.stderr,
    ) == "unknown"


@pytest.mark.parametrize("error", _TRANSPORT_FAULTS, ids=["missing-executable", "timeout"])
def test_copy_seam_keeps_oserror_and_timeout_handling(
    error: BaseException, recorder: Recorder, monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets_store.write_secret(TARGET.repo.slug, "transport", TRANSPORT_VALUE)
    monkeypatch.setattr(process_runner, "run_subprocess", _Raiser(error))
    forge = GhForge()
    assert forge.reviews(7, forge_target=TARGET) is None
    assert forge.changed_files(7, forge_target=TARGET) is None
    raised = forge.open_prs(forge_target=TARGET, limit=5, timeout=READ_TIMEOUT)
    assert isinstance(raised, RowsRead)
    assert raised.outcome == "raised"
    assert raised.stderr == str(error)
    with pytest.raises(type(error)):
        forge.read_pr(7, ("number",), forge_target=TARGET, timeout=READ_TIMEOUT)
    assert recorder.calls == []


def test_gh_or_none_branches_on_a_copy(recorder: Recorder) -> None:
    argv = ["gh", "api", "user"]
    refused = _gh_or_none(argv, TARGET)
    assert refused is not None
    assert (refused.returncode, refused.stderr) == (-1, MISSING_TRANSPORT_STDERR)
    secrets_store.write_secret(TARGET.repo.slug, "transport", TRANSPORT_VALUE)
    assert _gh_or_none(argv, TARGET, {"GH_TOKEN": "call-site-value"}) is not None
    assert _gh_or_none(argv, TARGET) is not None
    carried = [kwargs.get("env") for _argv, kwargs in recorder.calls]
    assert carried == [{"GH_TOKEN": TRANSPORT_VALUE}, {"GH_TOKEN": TRANSPORT_VALUE}]
