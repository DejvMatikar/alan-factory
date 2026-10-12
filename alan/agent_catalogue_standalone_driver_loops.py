from __future__ import annotations

from collections.abc import Sequence

from alan import agent_key, driver_loop_registry
from alan.agent_catalogue_helpers import ResolvedAgent, ResolverGroup, ValueSource, runtime_slot
from alan.agent_catalogue_models import AgentTag, SessionOut, TurnLimitOut
from alan.agent_catalogue_standalone_common import ALLOWLISTED_ENVIRONMENT_NOTE, group
from alan.driver_loop_cmd import build_spec
from alan.driver_loop_registry import DriverLoopEntry
from alan.engine_config import EngineConfig
from alan.launch_spec import LaunchSpec

TEMPLATE_NAME = "template"

_MODEL_SOURCE = ValueSource(
    kind="builtin",
    detail=(
        "Set only when the driver loop's own registry entry names a model; otherwise the CLI "
        "default applies. It is never an engine role."
    ),
)

_EFFORT_SOURCE = ValueSource(
    kind="cli_default", detail="Driver loops never pass an effort; the CLI default applies."
)

_TURN_LIMIT = TurnLimitOut(
    max_turns=None,
    note="Driver loops pass no turn limit; the run ends at its timeout or when the command ends.",
)

_SESSION = SessionOut(
    behaviour="none",
    detail="Every wake is a fresh headless run; nothing is resumed between wakes.",
)

_TIMEOUTS_DETAIL = (
    "No idle timeout; the run is stopped when the entry's timeout_seconds have passed."
)

_PROCESS_DETAIL = (
    "Runs in its own process session so it survives a service restart, started with an "
    "explicit allow-listed environment."
)

_TEMPLATE_WORKING_DIRECTORY = (
    "The directory named in the registry entry, a dedicated clone when the entry asks for one."
)

_TEMPLATE_WHEN_RUNS = (
    "No driver loop is registered, so this is a template: it shows what a registered loop "
    "would launch with. Values shown as placeholders come from the registry entry."
)


def _template_entry() -> DriverLoopEntry:
    return DriverLoopEntry(
        name=TEMPLATE_NAME,
        command=runtime_slot("DRIVER_LOOP_COMMAND", "The slash command the loop runs"),
        cwd=runtime_slot("DRIVER_LOOP_CWD", "Absolute path of the loop's working directory"),
        self_paced=True,
    )


def _schedule_text(entry: DriverLoopEntry) -> str:
    if entry.interval_seconds is not None:
        return f"every {entry.interval_seconds} seconds"
    return "self-paced: the loop decides when it wakes next"


def _when_runs(entry: DriverLoopEntry, spec: LaunchSpec) -> str:
    state = "enabled" if entry.enabled else "disabled"
    families = ", ".join(spec.tool_gate)
    return (
        f"On its schedule ({_schedule_text(entry)}) while {state}. Each wake is a fresh "
        f"headless run limited to these tool families: {families}."
    )


def _working_directory(entry: DriverLoopEntry, spec: LaunchSpec) -> str:
    if entry.dedicated_clone:
        return f"{spec.process.cwd}, a dedicated clone brought up to date before each run."
    return f"{spec.process.cwd}, the directory named in the registry entry."


def _driver_loop(
    entry: DriverLoopEntry,
    spec: LaunchSpec,
    *,
    when_runs: str,
    working_directory: str,
    tags: tuple[AgentTag, ...],
) -> ResolvedAgent:
    return ResolvedAgent(
        key=agent_key.driver_loop(entry.name),
        name=f"Driver loop / {entry.name}",
        when_runs=when_runs,
        scope="machine",
        spec=spec,
        user_message=str(spec.prompt_arg),
        model_source=_MODEL_SOURCE,
        effort_source=_EFFORT_SOURCE,
        turn_limit=_TURN_LIMIT,
        session=_SESSION,
        working_directory=working_directory,
        stored_tools=entry.allowed_tools,
        tags=tags,
        cwd_is_runtime=False,
        timeouts_detail=_TIMEOUTS_DETAIL,
        process_detail=_PROCESS_DETAIL,
        environment_note=ALLOWLISTED_ENVIRONMENT_NOTE,
    )


def _registered(entry: DriverLoopEntry) -> ResolvedAgent:
    spec = build_spec(entry)
    return _driver_loop(
        entry,
        spec,
        when_runs=_when_runs(entry, spec),
        working_directory=_working_directory(entry, spec),
        tags=("driver_loop",),
    )


def _template() -> ResolvedAgent:
    entry = _template_entry()
    return _driver_loop(
        entry,
        build_spec(entry),
        when_runs=_TEMPLATE_WHEN_RUNS,
        working_directory=_TEMPLATE_WORKING_DIRECTORY,
        tags=("driver_loop", "template"),
    )


def _resolve(_cfg: EngineConfig) -> Sequence[ResolvedAgent]:
    entries, _quarantined = driver_loop_registry.load_registry()
    if entries:
        return [_registered(entry) for entry in entries]
    return [_template()]


GROUPS: tuple[ResolverGroup, ...] = (
    group(agent_key.driver_loop(TEMPLATE_NAME), "Driver loops", _resolve),
)

__all__ = ["GROUPS", "TEMPLATE_NAME"]
