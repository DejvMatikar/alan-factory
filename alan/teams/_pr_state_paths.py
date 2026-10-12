from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    ValidatorFunctionWrapHandler,
    WrapValidator,
    model_validator,
)

from alan import store
from alan.engine_config import DEFAULT_FORGE_TARGET, ForgeTarget
from alan.roots import state_root

_T = TypeVar("_T")
_M = TypeVar("_M", bound=BaseModel)
_D = TypeVar("_D")


def state_dir() -> Path:
    return state_root()


def _sanitize_path_component(value: str) -> str:
    cleaned = value.replace("/", "_").replace("\\", "_")
    return cleaned if cleaned not in {"", ".", ".."} else "_"


def _engine_state_dir(forge: ForgeTarget) -> Path:
    return (
        state_dir()
        / _sanitize_path_component(forge.repo.owner)
        / _sanitize_path_component(forge.repo.repo)
    )


def _is_default_forge(forge: ForgeTarget) -> bool:
    return forge.repo.slug == DEFAULT_FORGE_TARGET.repo.slug


def _read_json_with_legacy_fallback(
    namespaced: Path,
    legacy: Path,
    forge: ForgeTarget,
    default: _T,
    parse: Callable[[Any], _T],
) -> _T:
    try:
        raw: Any = json.loads(namespaced.read_text())
    except FileNotFoundError:
        if not _is_default_forge(forge):
            return default
        try:
            raw = json.loads(legacy.read_text())
        except (OSError, json.JSONDecodeError):
            return default
    except (OSError, json.JSONDecodeError):
        return default
    return parse(raw)


def acked_comments_path(
    pr_number: int, forge: ForgeTarget
) -> Path:
    return _engine_state_dir(forge) / f"pr_{pr_number}_acked_comments.json"


def _legacy_acked_comments_path(pr_number: int) -> Path:
    return state_dir() / f"pr_{pr_number}_acked_comments.json"


def _parse_acked_comment_ids(raw: Any) -> frozenset[int]:
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(
        x for x in raw if isinstance(x, int) and not isinstance(x, bool)  # pyright: ignore[reportUnknownVariableType]
    )


def load_acked_comment_ids(
    pr_number: int, forge: ForgeTarget
) -> frozenset[int]:
    return _read_json_with_legacy_fallback(
        acked_comments_path(pr_number, forge),
        _legacy_acked_comments_path(pr_number),
        forge,
        frozenset(),
        _parse_acked_comment_ids,
    )


def save_acked_comment_id(
    pr_number: int, database_id: int, forge: ForgeTarget
) -> None:
    existing = load_acked_comment_ids(pr_number, forge)
    if database_id in existing:
        return
    p = acked_comments_path(pr_number, forge)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sorted([*existing, database_id])))


def release_acked_comment_id(
    pr_number: int, database_id: int, forge: ForgeTarget
) -> None:
    existing = load_acked_comment_ids(pr_number, forge)
    if database_id not in existing:
        return
    p = acked_comments_path(pr_number, forge)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sorted(existing - {database_id})))


def gate_rerun_count_path(
    pr_number: int, forge: ForgeTarget
) -> Path:
    return _engine_state_dir(forge) / f"pr_{pr_number}_gate_reruns.json"


def _legacy_gate_rerun_count_path(pr_number: int) -> Path:
    return state_dir() / f"pr_{pr_number}_gate_reruns.json"


def _parse_nonneg_int(raw: Any) -> int:
    return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0 else 0


def _default_on_error(default: object) -> WrapValidator:
    def validate(value: object, handler: ValidatorFunctionWrapHandler) -> object:
        try:
            return handler(value)
        except ValidationError:
            return default

    return WrapValidator(validate)


def _sorted_list(value: tuple[str, ...]) -> list[str]:
    return sorted(value)


def _is_empty(value: tuple[str, ...]) -> bool:
    return not value


_NonNegInt = Annotated[StrictInt, Field(ge=0)]
_StrList = Annotated[
    tuple[StrictStr, ...],
    _default_on_error(()),
    PlainSerializer(_sorted_list),
    Field(exclude_if=_is_empty),
]
_Stamp = Annotated[
    Annotated[StrictFloat, Field(allow_inf_nan=False)] | None,
    _default_on_error(None),
]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class GateRerunState(_FrozenModel):
    head_sha: StrictStr | None = None
    count: _NonNegInt
    lifetime: _NonNegInt
    checks: _StrList = ()
    specs: _StrList = ()

    @model_validator(mode="before")
    @classmethod
    def _from_bare_count(cls, data: object) -> object:
        if isinstance(data, int) and not isinstance(data, bool):
            return {"count": data, "lifetime": data}
        return data


_DEFAULT_GATE_RERUN_STATE = GateRerunState(head_sha=None, count=0, lifetime=0)


def _model_or_default(model: type[_M], default: _D) -> Callable[[Any], _M | _D]:
    def parse(raw: Any) -> _M | _D:
        try:
            return model.model_validate(raw)
        except ValidationError:
            return default

    return parse


_gate_rerun_or_default = _model_or_default(GateRerunState, _DEFAULT_GATE_RERUN_STATE)


def load_gate_rerun_state(
    pr_number: int, forge: ForgeTarget
) -> GateRerunState:
    return _read_json_with_legacy_fallback(
        gate_rerun_count_path(pr_number, forge),
        _legacy_gate_rerun_count_path(pr_number),
        forge,
        _DEFAULT_GATE_RERUN_STATE,
        _gate_rerun_or_default,
    )


def load_gate_rerun_count(
    pr_number: int, forge: ForgeTarget
) -> int:
    return load_gate_rerun_state(pr_number, forge).count


def _reset_aware_count(state: GateRerunState, head_sha: str | None) -> int:
    if head_sha is None:
        return state.count
    if state.head_sha is None or state.head_sha != head_sha:
        return 0
    return state.count


def _step_gate_rerun_state(
    path: Path, state: GateRerunState, head_sha: str | None, delta: int,
    *, checks: tuple[str, ...] = (), specs: tuple[str, ...] = (),
) -> int:
    count = max(_reset_aware_count(state, head_sha) + delta, 0)
    updated = GateRerunState(
        head_sha=state.head_sha if head_sha is None else head_sha,
        count=count,
        lifetime=max(state.lifetime + delta, 0),
        checks=checks,
        specs=specs,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(updated.model_dump()))
    return count


def gate_rerun_count_for_sha(
    pr_number: int, forge: ForgeTarget,
    *, head_sha: str | None,
) -> int:
    return _reset_aware_count(load_gate_rerun_state(pr_number, forge), head_sha)


def bump_gate_rerun_count(
    pr_number: int, forge: ForgeTarget,
    *, head_sha: str | None, checks: tuple[str, ...] = (), specs: tuple[str, ...] = (),
) -> int:
    return _step_gate_rerun_state(
        gate_rerun_count_path(pr_number, forge),
        load_gate_rerun_state(pr_number, forge),
        head_sha, 1, checks=checks, specs=specs,
    )


def gate_rerun_checks_for_sha(
    pr_number: int, forge: ForgeTarget,
    *, head_sha: str | None,
) -> frozenset[str]:
    state = load_gate_rerun_state(pr_number, forge)
    if head_sha is None or state.head_sha != head_sha:
        return frozenset()
    return frozenset(state.checks)


def gate_rerun_specs_for_sha(
    pr_number: int, forge: ForgeTarget,
    *, head_sha: str | None,
) -> tuple[str, ...]:
    state = load_gate_rerun_state(pr_number, forge)
    if head_sha is None or state.head_sha != head_sha:
        return ()
    return state.specs


def reconcile_attempt_count_path(
    pr_number: int, forge: ForgeTarget
) -> Path:
    return _engine_state_dir(forge) / f"pr_{pr_number}_reconcile_attempts.json"


def _legacy_reconcile_attempt_count_path(pr_number: int) -> Path:
    return state_dir() / f"pr_{pr_number}_reconcile_attempts.json"


def load_reconcile_attempt_state(
    pr_number: int, forge: ForgeTarget
) -> GateRerunState:
    return _read_json_with_legacy_fallback(
        reconcile_attempt_count_path(pr_number, forge),
        _legacy_reconcile_attempt_count_path(pr_number),
        forge,
        _DEFAULT_GATE_RERUN_STATE,
        _gate_rerun_or_default,
    )


def reconcile_attempts_for_sha(
    pr_number: int, forge: ForgeTarget,
    *, head_sha: str | None,
) -> int:
    return _reset_aware_count(load_reconcile_attempt_state(pr_number, forge), head_sha)


def bump_reconcile_attempt_count(
    pr_number: int, forge: ForgeTarget,
    *, head_sha: str | None,
) -> int:
    return _step_gate_rerun_state(
        reconcile_attempt_count_path(pr_number, forge),
        load_reconcile_attempt_state(pr_number, forge),
        head_sha, 1,
    )


def remediation_retry_count_path(
    pr_number: int, forge: ForgeTarget
) -> Path:
    return _engine_state_dir(forge) / f"pr_{pr_number}_remediation_retries.json"


def _legacy_remediation_retry_count_path(pr_number: int) -> Path:
    return state_dir() / f"pr_{pr_number}_remediation_retries.json"


def load_remediation_retry_count(
    pr_number: int, forge: ForgeTarget
) -> int:
    return _read_json_with_legacy_fallback(
        remediation_retry_count_path(pr_number, forge),
        _legacy_remediation_retry_count_path(pr_number),
        forge,
        0,
        _parse_nonneg_int,
    )


def bump_remediation_retry_count(
    pr_number: int, forge: ForgeTarget
) -> int:
    updated = load_remediation_retry_count(pr_number, forge) + 1
    p = remediation_retry_count_path(pr_number, forge)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(updated))
    return updated


def acked_threads_path(
    pr_number: int, forge: ForgeTarget
) -> Path:
    return _engine_state_dir(forge) / f"pr_{pr_number}_acked_threads.json"


def _parse_acked_thread_ids(raw: Any) -> frozenset[str]:
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(x for x in raw if isinstance(x, str) and x)  # pyright: ignore[reportUnknownVariableType]


def _load_acked_thread_ids(pr_number: int, forge: ForgeTarget) -> frozenset[str]:
    try:
        raw: Any = json.loads(acked_threads_path(pr_number, forge).read_text())
    except (OSError, json.JSONDecodeError):
        return frozenset()
    return _parse_acked_thread_ids(raw)


def claim_acked_thread(
    pr_number: int, thread_id: str, forge: ForgeTarget
) -> bool:
    existing = _load_acked_thread_ids(pr_number, forge)
    if thread_id in existing:
        return False
    p = acked_threads_path(pr_number, forge)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sorted([*existing, thread_id])))
    return True


def release_acked_thread(
    pr_number: int, thread_id: str, forge: ForgeTarget
) -> None:
    existing = _load_acked_thread_ids(pr_number, forge)
    if thread_id not in existing:
        return
    p = acked_threads_path(pr_number, forge)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sorted(existing - {thread_id})))


def replied_threads_path(
    pr_number: int, forge: ForgeTarget
) -> Path:
    return _engine_state_dir(forge) / f"pr_{pr_number}_replied_threads.json"


def _load_replied_thread_ids(pr_number: int, forge: ForgeTarget) -> frozenset[str]:
    try:
        raw: Any = json.loads(replied_threads_path(pr_number, forge).read_text())
    except (OSError, json.JSONDecodeError):
        return frozenset()
    return _parse_acked_thread_ids(raw)


def mark_thread_replied(
    pr_number: int, thread_id: str, forge: ForgeTarget
) -> None:
    existing = _load_replied_thread_ids(pr_number, forge)
    if thread_id in existing:
        return
    p = replied_threads_path(pr_number, forge)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sorted([*existing, thread_id])))


def is_thread_replied(
    pr_number: int, thread_id: str, forge: ForgeTarget
) -> bool:
    return thread_id in _load_replied_thread_ids(pr_number, forge)


def resolved_threads_path(
    pr_number: int, forge: ForgeTarget
) -> Path:
    return _engine_state_dir(forge) / f"pr_{pr_number}_resolved_threads.json"


def _load_resolved_thread_ids(pr_number: int, forge: ForgeTarget) -> frozenset[str]:
    try:
        raw: Any = json.loads(resolved_threads_path(pr_number, forge).read_text())
    except (OSError, json.JSONDecodeError):
        return frozenset()
    return _parse_acked_thread_ids(raw)


def mark_thread_resolved(
    pr_number: int, thread_id: str, forge: ForgeTarget
) -> None:
    existing = _load_resolved_thread_ids(pr_number, forge)
    if thread_id in existing:
        return
    p = resolved_threads_path(pr_number, forge)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sorted([*existing, thread_id])))


def is_thread_resolved(
    pr_number: int, thread_id: str, forge: ForgeTarget
) -> bool:
    return thread_id in _load_resolved_thread_ids(pr_number, forge)


def ack_budget_path(
    pr_number: int, forge: ForgeTarget
) -> Path:
    return _engine_state_dir(forge) / f"pr_{pr_number}_ack_budget.json"


def _load_ack_budget_state(pr_number: int, forge: ForgeTarget) -> GateRerunState:
    try:
        raw: Any = json.loads(ack_budget_path(pr_number, forge).read_text())
    except (OSError, json.JSONDecodeError):
        return _DEFAULT_GATE_RERUN_STATE
    return _gate_rerun_or_default(raw)


def load_ack_budget(
    pr_number: int, forge: ForgeTarget, *, head_sha: str | None,
) -> int:
    state = _load_ack_budget_state(pr_number, forge)
    if head_sha is None:
        return state.lifetime
    return _reset_aware_count(state, head_sha)


def bump_ack_budget(
    pr_number: int, forge: ForgeTarget, *, head_sha: str | None,
) -> int:
    return _step_gate_rerun_state(
        ack_budget_path(pr_number, forge),
        _load_ack_budget_state(pr_number, forge),
        head_sha, 1,
    )


def release_ack_budget(
    pr_number: int, forge: ForgeTarget, *, head_sha: str | None,
) -> int:
    return _step_gate_rerun_state(
        ack_budget_path(pr_number, forge),
        _load_ack_budget_state(pr_number, forge),
        head_sha, -1,
    )


class SystemHeadMarker(_FrozenModel):
    head_sha: StrictStr | None = None
    rung: StrictStr
    forwarded: StrictBool = False
    written_at: _Stamp = None


_marker_or_none = _model_or_default(SystemHeadMarker, None)


def system_head_path(
    pr_number: int, forge: ForgeTarget
) -> Path:
    return _engine_state_dir(forge) / f"pr_{pr_number}_system_head.json"


def _usable_marker(marker: SystemHeadMarker | None) -> SystemHeadMarker | None:
    if marker is None or not marker.rung:
        return None
    if marker.head_sha is None:
        return marker if marker.written_at is not None else None
    return marker if marker.head_sha else None


def load_system_head(
    pr_number: int, forge: ForgeTarget
) -> SystemHeadMarker | None:
    try:
        raw: Any = json.loads(system_head_path(pr_number, forge).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return _usable_marker(_marker_or_none(raw))


def _write_system_head_marker(
    pr_number: int, marker: SystemHeadMarker, forge: ForgeTarget
) -> None:
    p = system_head_path(pr_number, forge)
    p.parent.mkdir(parents=True, exist_ok=True)
    store.write_text_mkstemp(p, json.dumps(marker.model_dump()), prefix=".system_head_")


def save_system_head(
    pr_number: int, head_sha: str | None, rung: str, forge: ForgeTarget
) -> None:
    _write_system_head_marker(
        pr_number,
        SystemHeadMarker(head_sha=head_sha, rung=rung, written_at=time.time()),
        forge,
    )


def mark_system_head_forwarded(
    pr_number: int, forge: ForgeTarget
) -> None:
    marker = load_system_head(pr_number, forge)
    if marker is None:
        return
    _write_system_head_marker(pr_number, marker.model_copy(update={"forwarded": True}), forge)


FIX_CYCLE_CEILING = 6
FIX_CYCLE_ESCALATE_AT = 3
FIX_CYCLE_OVERRIDE_GRANT = 3


def fix_cycle_path(
    pr_number: int, forge: ForgeTarget
) -> Path:
    return _engine_state_dir(forge) / f"pr_{pr_number}_fix_cycles.json"


class FixCycleState(_FrozenModel):
    count: _NonNegInt
    allowance: _NonNegInt
    granted_question_ids: _StrList = ()


_DEFAULT_FIX_CYCLE_STATE = FixCycleState(count=0, allowance=0)
_fix_cycle_or_default = _model_or_default(FixCycleState, _DEFAULT_FIX_CYCLE_STATE)
# Bump/grant are both read-modify-write on the same file from dispatcher
# worker threads and operator/answer request threads; serialize them so a
# concurrent pair can't race and lose a cycle past the ceiling.
_fix_cycle_lock = threading.Lock()


def load_fix_cycle_state(
    pr_number: int, forge: ForgeTarget
) -> FixCycleState:
    p = fix_cycle_path(pr_number, forge)
    return _read_json_with_legacy_fallback(
        p, p, forge, _DEFAULT_FIX_CYCLE_STATE, _fix_cycle_or_default,
    )


def _write_fix_cycle_state(
    pr_number: int, state: FixCycleState, forge: ForgeTarget
) -> None:
    p = fix_cycle_path(pr_number, forge)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state.model_dump()))


def bump_fix_cycle_count(
    pr_number: int, forge: ForgeTarget
) -> int:
    with _fix_cycle_lock:
        state = load_fix_cycle_state(pr_number, forge)
        updated = state.model_copy(update={"count": state.count + 1})
        _write_fix_cycle_state(pr_number, updated, forge)
        return updated.count


def fix_cycle_budget(
    pr_number: int, forge: ForgeTarget
) -> int:
    state = load_fix_cycle_state(pr_number, forge)
    return FIX_CYCLE_CEILING + state.allowance


def bump_fix_cycle_count_if_under_ceiling(
    pr_number: int, forge: ForgeTarget
) -> int | None:
    with _fix_cycle_lock:
        state = load_fix_cycle_state(pr_number, forge)
        if state.count >= FIX_CYCLE_CEILING + state.allowance:
            return None
        updated = state.model_copy(update={"count": state.count + 1})
        _write_fix_cycle_state(pr_number, updated, forge)
        return updated.count


def grant_fix_cycle_override(
    pr_number: int, question_id: str, forge: ForgeTarget
) -> bool:
    with _fix_cycle_lock:
        state = load_fix_cycle_state(pr_number, forge)
        if question_id in state.granted_question_ids:
            return False
        updated = state.model_copy(update={
            "allowance": state.allowance + FIX_CYCLE_OVERRIDE_GRANT,
            "granted_question_ids": (*state.granted_question_ids, question_id),
        })
        _write_fix_cycle_state(pr_number, updated, forge)
        return True


def should_escalate_fix_cycle_severity(count: int, job_running: bool) -> bool:
    return job_running and count >= FIX_CYCLE_ESCALATE_AT


def capability_waiver_path(
    pr_number: int, forge: ForgeTarget
) -> Path:
    return _engine_state_dir(forge) / f"pr_{pr_number}_capability_waivers.json"


_capability_waiver_lock = threading.Lock()


def _parse_capability_waiver_ids(raw: Any) -> frozenset[str]:
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(x for x in raw if isinstance(x, str) and x)  # pyright: ignore[reportUnknownVariableType]


def _load_capability_waiver_ids(pr_number: int, forge: ForgeTarget) -> frozenset[str]:
    p = capability_waiver_path(pr_number, forge)
    return _read_json_with_legacy_fallback(
        p, p, forge, frozenset(), _parse_capability_waiver_ids,
    )


def capability_boundary_waiver_recorded(
    pr_number: int, question_id: str, forge: ForgeTarget
) -> bool:
    return question_id in _load_capability_waiver_ids(pr_number, forge)


def record_capability_boundary_waiver(
    pr_number: int, question_id: str, forge: ForgeTarget
) -> bool:
    with _capability_waiver_lock:
        existing = _load_capability_waiver_ids(pr_number, forge)
        if question_id in existing:
            return False
        p = capability_waiver_path(pr_number, forge)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(sorted([*existing, question_id])))
        return True
