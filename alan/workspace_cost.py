from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from itertools import chain
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from alan import store
from alan.manager_summary import read_manager_summary_usage
from alan.run_summary import RunSummary, derive_run_summary
from alan.workflow import WorkflowRowSummary

logger = logging.getLogger("alan.workspace_cost")

_CHAT_DIR_NAME = "chat"
_PREP_CHAT_DIR_NAME = "prep-chat"
_STREAM_FILE = "stream.jsonl"
_COLLISION_STREAM_FILE = "collision_stream.jsonl"
_INVESTIGATOR_STREAM_FILE = "investigator_stream.jsonl"
_SESSION_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

_DISCUSSION_MODEL = "opus"
_COLLISION_MODEL = "haiku"
_INVESTIGATOR_MODEL = "sonnet"
_COST_ONLY_SYSTEM_PROMPT = ""
_CLEARED_COST_DIR_NAME = "cleared-chat-cost"


class WorkspaceCostTotals(BaseModel):
    model_config = ConfigDict(frozen=True)

    team_runs_usd: float | None = None
    status_summary_usd: float | None = None
    discussion_usd: float | None = None
    collision_check_usd: float | None = None
    investigator_usd: float | None = None
    preparation_usd: float | None = None


class ClearedSessionCost(BaseModel):
    model_config = ConfigDict(frozen=True)

    discussion_usd: float | None = None
    collision_check_usd: float | None = None


def _sum_optional(values: Iterable[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def total_cost_usd(totals: WorkspaceCostTotals) -> float | None:
    return _sum_optional(
        (
            totals.team_runs_usd,
            totals.status_summary_usd,
            totals.discussion_usd,
            totals.collision_check_usd,
            totals.investigator_usd,
            totals.preparation_usd,
        )
    )


class WorkspaceCostBreakdownEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    run_summary: RunSummary | None


_BREAKDOWN_MODEL = "aggregate"

_BREAKDOWN_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("team_runs", "Team & agent runs"),
    ("status_summary", "Status summary"),
    ("discussion", "Discussion chat"),
    ("collision_check", "Collision check"),
    ("investigator", "Investigator"),
    ("preparation", "Preparation chat"),
)


def _breakdown_run_summary(cost: float | None) -> RunSummary | None:
    if cost is None:
        return None
    return RunSummary(model=_BREAKDOWN_MODEL, effort=None, system_prompt="", total_cost_usd=cost)


def synthesize_cost_breakdown(
    totals: WorkspaceCostTotals,
) -> list[WorkspaceCostBreakdownEntry]:
    costs = (
        totals.team_runs_usd,
        totals.status_summary_usd,
        totals.discussion_usd,
        totals.collision_check_usd,
        totals.investigator_usd,
        totals.preparation_usd,
    )
    return [
        WorkspaceCostBreakdownEntry(key=key, label=label, run_summary=_breakdown_run_summary(cost))
        for (key, label), cost in zip(_BREAKDOWN_CATEGORIES, costs, strict=True)
    ]


def _session_listing(root: Path) -> tuple[store.ListingEntry, ...]:
    candidates = (
        entry for entry in store.list_dir(root) if _SESSION_ID_RE.fullmatch(entry.name)
    )
    return store.listing_fingerprint(candidates, dirs_only=True)


def _session_names(listing: tuple[store.ListingEntry, ...]) -> tuple[str, ...]:
    return tuple(name for name, _, _ in listing)


def _cleared_record_paths(cleared_dir: Path) -> list[Path]:
    return [entry for entry in store.list_dir(cleared_dir) if entry.name.endswith(".json")]


def _drop_gone_sessions(sessions: dict[str, _SessionCost], session_ids: tuple[str, ...]) -> None:
    for gone in set(sessions) - set(session_ids):
        del sessions[gone]


@dataclass
class _SessionCost:
    stream_sig: tuple[int, int] | None = None
    stream_cost: float | None = None
    collision_sig: tuple[int, int] | None = None
    collision_cost: float | None = None


@dataclass
class ChatCostGuards:
    chat_listing: tuple[store.ListingEntry, ...] | None = None
    session_ids: tuple[str, ...] = ()
    sessions: dict[str, _SessionCost] = field(default_factory=dict[str, _SessionCost])
    investigator_sig: tuple[int, int] | None = None
    investigator_cost: float | None = None
    prep_listing: tuple[store.ListingEntry, ...] | None = None
    prep_session_ids: tuple[str, ...] = ()
    prep_sessions: dict[str, _SessionCost] = field(default_factory=dict[str, _SessionCost])
    cleared_listing: tuple[store.ListingEntry, ...] | None = None
    cleared_costs: dict[str, ClearedSessionCost] = field(
        default_factory=dict[str, ClearedSessionCost]
    )


def _maybe_update_cost(
    sig_before: tuple[int, int] | None, path: Path, *, model: str
) -> tuple[tuple[int, int] | None, float | None, bool]:
    sig_after = store.stat_signature(path)
    if sig_after == sig_before:
        return sig_before, None, False
    summary = (
        derive_run_summary(path, system_prompt=_COST_ONLY_SYSTEM_PROMPT, model=model)
        if sig_after is not None
        else None
    )
    return sig_after, (summary.total_cost_usd if summary is not None else None), True


def _refresh_session_ids(guards: ChatCostGuards, chat_dir: Path) -> None:
    listing = _session_listing(chat_dir)
    if listing == guards.chat_listing:
        return
    guards.chat_listing = listing
    guards.session_ids = _session_names(listing)
    _drop_gone_sessions(guards.sessions, guards.session_ids)


def _refresh_session(sess: _SessionCost, session_dir: Path) -> None:
    sig, cost, changed = _maybe_update_cost(
        sess.stream_sig, session_dir / _STREAM_FILE, model=_DISCUSSION_MODEL
    )
    if changed:
        sess.stream_sig, sess.stream_cost = sig, cost
    sig, cost, changed = _maybe_update_cost(
        sess.collision_sig, session_dir / _COLLISION_STREAM_FILE, model=_COLLISION_MODEL
    )
    if changed:
        sess.collision_sig, sess.collision_cost = sig, cost


def _refresh_investigator(guards: ChatCostGuards, ws_path: Path) -> None:
    sig, cost, changed = _maybe_update_cost(
        guards.investigator_sig, ws_path / _INVESTIGATOR_STREAM_FILE, model=_INVESTIGATOR_MODEL
    )
    if changed:
        guards.investigator_sig, guards.investigator_cost = sig, cost


def _update_chat_cost_guards(guards: ChatCostGuards, ws_path: Path) -> None:
    chat_dir = ws_path / _CHAT_DIR_NAME
    _refresh_session_ids(guards, chat_dir)
    for session_id in guards.session_ids:
        sess = guards.sessions.setdefault(session_id, _SessionCost())
        _refresh_session(sess, chat_dir / session_id)
    _refresh_investigator(guards, ws_path)


def _load_cleared_costs(paths: Iterable[Path]) -> dict[str, ClearedSessionCost]:
    costs: dict[str, ClearedSessionCost] = {}
    for entry in paths:
        try:
            costs[entry.stem] = ClearedSessionCost.model_validate_json(
                entry.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError):
            logger.warning("workspace_cost: cleared-cost tombstone unreadable: %s", entry)
    return costs


def _refresh_cleared_costs(guards: ChatCostGuards, ws_path: Path) -> None:
    paths = _cleared_record_paths(ws_path / _CLEARED_COST_DIR_NAME)
    listing = store.listing_fingerprint(paths)
    if listing == guards.cleared_listing:
        return
    guards.cleared_listing = listing
    guards.cleared_costs = _load_cleared_costs(paths)


def _cleared_values(
    guards: ChatCostGuards, pick: Callable[[ClearedSessionCost], float | None]
) -> Iterable[float | None]:
    live = set(guards.session_ids)
    return (pick(cost) for sid, cost in guards.cleared_costs.items() if sid not in live)


def _discussion_cost_usd(guards: ChatCostGuards) -> float | None:
    live_values = (sess.stream_cost for sess in guards.sessions.values())
    return _sum_optional(chain(live_values, _cleared_values(guards, lambda c: c.discussion_usd)))


def _collision_cost_usd(guards: ChatCostGuards) -> float | None:
    live_values = (sess.collision_cost for sess in guards.sessions.values())
    return _sum_optional(
        chain(live_values, _cleared_values(guards, lambda c: c.collision_check_usd))
    )


def _refresh_prep_session_ids(guards: ChatCostGuards, prep_dir: Path) -> None:
    listing = _session_listing(prep_dir)
    if listing == guards.prep_listing:
        return
    guards.prep_listing = listing
    guards.prep_session_ids = _session_names(listing)
    _drop_gone_sessions(guards.prep_sessions, guards.prep_session_ids)


def _update_prep_chat_cost_guards(guards: ChatCostGuards, ws_path: Path) -> None:
    prep_dir = ws_path / _PREP_CHAT_DIR_NAME
    _refresh_prep_session_ids(guards, prep_dir)
    for session_id in guards.prep_session_ids:
        sess = guards.prep_sessions.setdefault(session_id, _SessionCost())
        _refresh_session(sess, prep_dir / session_id)


def _preparation_cost_usd(guards: ChatCostGuards) -> float | None:
    parts = (
        value
        for sess in guards.prep_sessions.values()
        for value in (sess.stream_cost, sess.collision_cost)
    )
    return _sum_optional(parts)


def _safe_status_summary_cost(ws_path: Path) -> float | None:
    try:
        usage = read_manager_summary_usage(ws_path)
    except Exception:  # noqa: BLE001
        logger.warning(
            "workspace_cost: status-summary usage read failed for %s", ws_path, exc_info=True
        )
        return None
    return usage.total_cost_usd if usage is not None else None


def derive_cost_totals(
    *,
    inv_summaries: Mapping[str, WorkflowRowSummary],
    ws_path: Path,
    chat_guards: ChatCostGuards,
    include_new_categories: bool = True,
) -> WorkspaceCostTotals:
    if include_new_categories:
        _update_chat_cost_guards(chat_guards, ws_path)
        _update_prep_chat_cost_guards(chat_guards, ws_path)
        _refresh_cleared_costs(chat_guards, ws_path)
    return WorkspaceCostTotals(
        team_runs_usd=_sum_optional(s.cost for s in inv_summaries.values()),
        status_summary_usd=_safe_status_summary_cost(ws_path),
        discussion_usd=_discussion_cost_usd(chat_guards) if include_new_categories else None,
        collision_check_usd=(
            _collision_cost_usd(chat_guards) if include_new_categories else None
        ),
        investigator_usd=chat_guards.investigator_cost if include_new_categories else None,
        preparation_usd=_preparation_cost_usd(chat_guards) if include_new_categories else None,
    )
