from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _forge_errors, _pr_ready
from alan.teams._pr_forge import GH_TIMEOUT_S
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, pr_read, readiness_fields

_SHA = "b" * 40
_GREEN = [{"name": "ci", "conclusion": "SUCCESS", "status": "COMPLETED"}]
_RATE_LIMIT = "You have exceeded a secondary rate limit (HTTP 403)"


@pytest.fixture(autouse=True)
def _isolated_audit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


def test_readiness_parses_the_port_fields(forge: ForgeScript) -> None:
    fields = readiness_fields(rollup=_GREEN, head_sha=_SHA, headRefName="feature-x")
    forge.queue("read_pr", pr_read(fields))
    readiness = _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.ok is True
    assert (readiness.head_sha, readiness.head_ref_name) == (_SHA, "feature-x")
    assert readiness.passing_checks == ("ci",)


def test_readiness_requests_the_field_tuple_from_the_port(forge: ForgeScript) -> None:
    forge.queue("read_pr", pr_read(readiness_fields(rollup=_GREEN)))
    _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    (args, kwargs), = forge.calls("read_pr")
    assert args == (42, _pr_ready._READINESS_FIELDS)
    assert isinstance(args[1], tuple)
    assert kwargs["timeout"] == GH_TIMEOUT_S


def test_empty_fields_fall_back_to_the_unparseable_json_transient(forge: ForgeScript) -> None:
    forge.queue("read_pr", pr_read({}))
    readiness = _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.ReadinessUnavailable)
    assert readiness.transient_reason == "gh pr view returned unparseable JSON"


def test_nonzero_exit_with_infra_stderr_raises(forge: ForgeScript) -> None:
    forge.queue("read_pr", pr_read(returncode=1, stderr=_RATE_LIMIT))
    with pytest.raises(_forge_errors.ForgeRateLimited):
        _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)


def test_nonzero_exit_with_other_stderr_is_a_transient_readiness(forge: ForgeScript) -> None:
    forge.queue("read_pr", pr_read(returncode=1, stderr="boom"))
    readiness = _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.ReadinessUnavailable)
    assert readiness.transient_reason == "gh returncode=1: boom"


@pytest.mark.parametrize(
    "error", [FileNotFoundError("gh"), subprocess.TimeoutExpired("gh", 60.0)],
)
def test_spawn_failure_raises_forge_transient(forge: ForgeScript, error: BaseException) -> None:
    forge.queue("read_pr", error)
    with pytest.raises(_forge_errors.ForgeTransientError):
        _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)


def test_verifier_variant_requests_latest_reviews_and_blocks_only_on_dirty(
    forge: ForgeScript,
) -> None:
    fields = readiness_fields("DIRTY", "CHANGES_REQUESTED", rollup=_GREEN, latestReviews=[])
    forge.queue("read_pr", pr_read(fields))
    readiness = _pr_ready.check_pr_verifier_blockers(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET
    )
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.failed_reasons == ("mergeStateStatus=DIRTY",)
    (args, _), = forge.calls("read_pr")
    assert args[1] == _pr_ready._VERIFIER_READINESS_FIELDS
    assert args[1][-1] == "latestReviews"


def test_base_sha_is_read_through_the_port_and_stripped(forge: ForgeScript) -> None:
    forge.queue("read_pr", pr_read({"baseRefOid": f"{_SHA}\n"}))
    assert _pr_ready._fetch_pr_base_sha(42, forge_target=DEFAULT_FORGE_TARGET) == _SHA
    (args, _), = forge.calls("read_pr")
    assert args == (42, ("baseRefOid",))


@pytest.mark.parametrize(
    "result",
    [
        pr_read({}),
        pr_read({"baseRefOid": None}),
        pr_read({"baseRefOid": "  "}),
        pr_read({"baseRefOid": _SHA}, returncode=1),
        FileNotFoundError("gh"),
        subprocess.TimeoutExpired("gh", 60.0),
    ],
)
def test_base_sha_is_absent_when_the_port_cannot_supply_one(
    forge: ForgeScript, result: object
) -> None:
    forge.queue("read_pr", result)
    assert _pr_ready._fetch_pr_base_sha(42, forge_target=DEFAULT_FORGE_TARGET) is None


@pytest.fixture
def known_flake_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_pr_ready, "_KNOWN_FLAKE_REGISTRY", {"ci": frozenset({"docs/*"})})


@pytest.mark.usefixtures("known_flake_registry")
@pytest.mark.parametrize("changed", [frozenset({"alan/x.py"}), None])
def test_quarantine_inputs_carry_the_changed_files_from_the_port(
    forge: ForgeScript, changed: frozenset[str] | None
) -> None:
    forge.queue("read_pr", pr_read(returncode=1))
    forge.queue("changed_files", changed)
    assert _pr_ready._gather_quarantine_inputs(
        42, forge_target=DEFAULT_FORGE_TARGET
    ) == (None, changed)
    assert [args for args, _ in forge.calls("changed_files")] == [(42,)]


def test_quarantine_inputs_skip_the_changed_files_read_without_a_registry(
    forge: ForgeScript,
) -> None:
    forge.queue("read_pr", pr_read(returncode=1))
    assert _pr_ready._gather_quarantine_inputs(
        42, forge_target=DEFAULT_FORGE_TARGET
    ) == (None, None)
    assert forge.calls("changed_files") == []
