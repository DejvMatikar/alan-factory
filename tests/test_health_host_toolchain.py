from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from alan.health import (
    _ACTION_DEEP_LINK,  # pyright: ignore[reportPrivateUsage]
    _OBS_DESCRIBERS,  # pyright: ignore[reportPrivateUsage]
    _SIMPLE_SEVERITY_ACTION,  # pyright: ignore[reportPrivateUsage]
    HealthFinding,
    compute_health_state,
)
from alan.routes.infra import _suppress_worker_pool  # pyright: ignore[reportPrivateUsage]
from alan.teams.triage_pregate import ClassifiedSurface


def test_severity_action_and_describer_registered_for_host_toolchain() -> None:
    assert _SIMPLE_SEVERITY_ACTION["host_toolchain"] == ("critical", _ACTION_DEEP_LINK)
    assert "host_toolchain" in _OBS_DESCRIBERS


def test_describe_host_toolchain_full_obs() -> None:
    describer = _OBS_DESCRIBERS["host_toolchain"]
    obs: dict[str, Any] = {
        "resolved_paths": {"git": "/usr/bin/git", "python3": "/usr/bin/python3"},
        "failing_tools": ["git"],
        "failing_tool": "git",
        "message": "sudo xcodebuild -license accept",
        "recovery_command": "sudo xcodebuild -license accept",
    }
    desc = describer(obs)
    assert "/usr/bin/git" in desc
    assert "/usr/bin/python3" in desc
    assert "sudo xcodebuild -license accept" in desc
    assert "clears" in desc


def test_describe_host_toolchain_missing_resolved_paths_does_not_raise() -> None:
    describer = _OBS_DESCRIBERS["host_toolchain"]
    desc = describer({"resolved_paths": "not-a-dict"})
    assert isinstance(desc, str)
    assert "unresolved" in desc


def test_compute_health_state_host_toolchain_finding() -> None:
    surface = ClassifiedSurface(
        surface_id="host_toolchain",
        kind="host_toolchain",
        is_blocked=True,
        blocker_reason="host_toolchain_gated",
        obs={
            "resolved_paths": {"git": "/usr/bin/git", "python3": "/usr/bin/python3"},
            "failing_tools": ["git"],
            "failing_tool": "git",
            "message": "sudo xcodebuild -license accept",
            "recovery_command": "sudo xcodebuild -license accept",
        },
    )
    state = compute_health_state([surface], datetime.now(UTC))
    f = state.findings[0]
    assert f.kind == "host_toolchain"
    assert f.severity == "critical"
    assert f.action == "deep_link"


def test_suppress_worker_pool_does_not_drop_host_toolchain_finding() -> None:
    finding = HealthFinding(
        finding_id="host_toolchain",
        kind="host_toolchain",
        severity="critical",
        description="Host toolchain gated",
        workspace_id=None,
        pr_number=None,
        blocker_flags=["host_toolchain_gated"],
        action="deep_link",
    )
    result = _suppress_worker_pool([finding], dispatcher=None)
    assert result == [finding]
