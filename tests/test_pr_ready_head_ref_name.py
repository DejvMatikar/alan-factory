from __future__ import annotations

import pytest

from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _pr_ready
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, pr_read, readiness_fields


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


def test_readiness_fields_requests_head_ref_name() -> None:
    assert "headRefName" in _pr_ready._READINESS_FIELDS  # noqa: SLF001


def test_verifier_readiness_fields_requests_head_ref_name() -> None:
    assert "headRefName" in _pr_ready._VERIFIER_READINESS_FIELDS  # noqa: SLF001


def test_check_pr_merge_readiness_parses_head_ref_name(forge: ForgeScript) -> None:
    fields = readiness_fields(
        "DIRTY", "APPROVED", head_sha="deadbeef", headRefName="teams/foo-inv_1",
    )
    forge.queue("read_pr", pr_read(fields))
    readiness = _pr_ready.check_pr_merge_readiness(42, "task-1", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert forge.calls("read_pr")[0][0] == (42, _pr_ready._READINESS_FIELDS)  # noqa: SLF001
    assert readiness.head_ref_name == "teams/foo-inv_1"
    assert readiness.head_sha == "deadbeef"


def test_check_pr_merge_readiness_defaults_head_ref_name_when_absent(
    forge: ForgeScript,
) -> None:
    fields = readiness_fields("DIRTY", "APPROVED", head_sha="deadbeef", headRefName=None)
    forge.queue("read_pr", pr_read(fields))
    readiness = _pr_ready.check_pr_merge_readiness(42, "task-1", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.head_ref_name == ""
