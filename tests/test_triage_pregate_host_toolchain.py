from __future__ import annotations

from pathlib import Path

from alan import worker_liveness
from alan.queue import Queue
from alan.teams.triage_pregate import gather_all, gather_host_toolchain
from tests.fakes.forge import FakeForge
from tests.forge_stubs import always, rows_read


def _probe(
    tool: str, resolved_path: str | None, ok: bool, message: str = ""
) -> worker_liveness.ToolProbe:
    return worker_liveness.ToolProbe(tool, resolved_path, ok, message)


def _healthy_probes() -> list[worker_liveness.ToolProbe]:
    return [
        _probe("git", "/usr/bin/git", True),
        _probe("python3", "/usr/bin/python3", True),
    ]


def _gated_probes() -> list[worker_liveness.ToolProbe]:
    return [
        _probe("git", "/usr/bin/git", False, "sudo xcodebuild -license accept\nxcrun: error"),
        _probe("python3", "/usr/bin/python3", True),
    ]


def test_gather_host_toolchain_healthy_unblocked() -> None:
    surface = gather_host_toolchain(probe=_healthy_probes)
    assert surface.is_blocked is False
    assert surface.kind == "host_toolchain"
    assert surface.surface_id == "host_toolchain"


def test_gather_host_toolchain_gated_reports_blocked_surface() -> None:
    surface = gather_host_toolchain(probe=_gated_probes)
    assert surface.is_blocked is True
    assert surface.kind == "host_toolchain"
    assert surface.blocker_reason == "host_toolchain_gated"
    assert surface.fp_obs is not None
    assert surface.fp_obs["failing_tools"] == ["git"]


def test_gather_host_toolchain_default_arg_identity() -> None:
    assert gather_host_toolchain.__defaults__ == (worker_liveness.probe_host_toolchain,)


def test_gather_all_includes_host_toolchain_surface(
    queue: Queue, ws_root: Path, _active_fake_forge: FakeForge, tmp_path: Path
) -> None:
    always(
        _active_fake_forge, "latest_branch_run",
        rows_read(None, outcome="failed", returncode=1, stderr="err"),
    )
    logs = tmp_path / "logs"
    logs.mkdir()
    surfaces = gather_all(ws_root, queue, logs, 2)
    assert any(s.kind == "host_toolchain" for s in surfaces)
