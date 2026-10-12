from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict

from alan import worker_records

HEARTBEAT_TTL_S: float = 180.0
_PROBE_TIMEOUT_S = 5.0
_PROBED_TOOLS: tuple[str, ...] = ("git", "python3")
_LICENSE_RECOVERY_FALLBACK = "sudo xcodebuild -license accept"


def _representable_epoch(value: float) -> float:
    try:
        datetime.fromtimestamp(value, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError(f"observed_at {value!r} is not a representable epoch") from exc
    return value


class DispatchLoopReading(BaseModel):
    model_config = ConfigDict(frozen=True)

    live_loops: int
    configured_loops: int
    observed_at: Annotated[float, AfterValidator(_representable_epoch)]


def encode(reading: DispatchLoopReading) -> str:
    return json.dumps(reading.model_dump())


def parse(raw: str | None) -> DispatchLoopReading | None:
    if raw is None:
        return None
    try:
        return DispatchLoopReading.model_validate(json.loads(raw))
    except ValueError:
        return None


def is_fresh(reading: DispatchLoopReading, *, now: float) -> bool:
    age = now - reading.observed_at
    return 0.0 <= age <= HEARTBEAT_TTL_S


def probe_active_workers() -> int:
    this_service = worker_records.this_service_identity()
    survivors = 0
    for _path, record in worker_records.load_all():
        if record is None:
            continue
        verdict = worker_records.probe(record, this_service=this_service)
        if worker_records.is_detached_survivor(record, verdict):
            survivors += 1
    return survivors


def classify(
    reading: DispatchLoopReading | None,
    *,
    now: float,
    running_jobs: int,
    probe_active_workers: Callable[[], int],
) -> tuple[bool, str]:
    if reading is None:
        return False, ""
    if not is_fresh(reading, now=now):
        return False, ""
    if reading.live_loops >= reading.configured_loops:
        return False, ""
    if running_jobs > 0:
        return False, ""
    if probe_active_workers() > 0:
        return False, ""
    deficit = reading.configured_loops - reading.live_loops
    return True, f"worker_deficit:{deficit}"


@dataclass(frozen=True)
class ToolProbe:
    tool: str
    resolved_path: str | None
    ok: bool
    message: str


def _failure_message(result: subprocess.CompletedProcess[str]) -> str:
    stderr = (result.stderr or "").strip()
    if stderr:
        return stderr
    stdout = (result.stdout or "").strip()
    if stdout:
        return stdout
    return f"exit {result.returncode}"


def probe_tool(tool: str) -> ToolProbe:
    resolved_path = shutil.which(tool)
    if resolved_path is None:
        return ToolProbe(tool, None, False, f"{tool} not found on PATH")
    try:
        result = subprocess.run(  # noqa: S603
            [resolved_path, "--version"],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ToolProbe(tool, resolved_path, False, str(exc))
    if result.returncode != 0:
        return ToolProbe(tool, resolved_path, False, _failure_message(result))
    return ToolProbe(tool, resolved_path, True, "")


def probe_host_toolchain() -> list[ToolProbe]:
    return [probe_tool(tool) for tool in _PROBED_TOOLS]


def recovery_command(message: str) -> str:
    for line in message.splitlines():
        stripped = line.strip()
        if stripped.startswith("sudo "):
            return stripped
    return _LICENSE_RECOVERY_FALLBACK


def classify_host_toolchain(
    probes: list[ToolProbe],
) -> tuple[bool, str, dict[str, Any], dict[str, Any]]:
    resolved_paths = {p.tool: p.resolved_path for p in probes}
    failing = [p for p in probes if not p.ok]
    failing_tools = [p.tool for p in failing]
    if not failing:
        obs: dict[str, Any] = {"resolved_paths": resolved_paths}
        fp_obs: dict[str, Any] = {
            "resolved_paths": resolved_paths, "failing_tools": [], "first_line": "",
        }
        return False, "", obs, fp_obs
    message = failing[0].message
    obs = {
        "resolved_paths": resolved_paths,
        "failing_tools": failing_tools,
        "failing_tool": failing[0].tool,
        "message": message,
        "recovery_command": recovery_command(message),
    }
    fp_obs = {
        "resolved_paths": resolved_paths,
        "failing_tools": failing_tools,
        "first_line": message.splitlines()[0] if message else "",
    }
    return True, "host_toolchain_gated", obs, fp_obs


__all__ = [
    "HEARTBEAT_TTL_S",
    "DispatchLoopReading",
    "ToolProbe",
    "classify",
    "classify_host_toolchain",
    "encode",
    "is_fresh",
    "parse",
    "probe_active_workers",
    "probe_host_toolchain",
    "probe_tool",
    "recovery_command",
]
