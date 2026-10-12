from __future__ import annotations

import copy
import json
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeAlias, cast

from alan import agent_key, store
from alan.claude_cli import run_agent
from alan.run_summary import RunSummary, derive_run_summary

logger = logging.getLogger("alan.manager_summary")

_JsonDict: TypeAlias = dict[str, object]

_SIDECAR = "manager_summary.json"
_SIDECAR_FILE_MODE = 0o600
_STREAM_FILE = "manager_summary_stream.jsonl"

_SYSTEM_PROMPT = (
    "You are a concise status reporter for an automated software-delivery platform. "
    "Write a single short paragraph (3–5 sentences). No markdown, no bullet points, no headers. "
    "Be specific and direct: what happened, why it stopped if not a clean completion, "
    "and what the operator's concrete next steps are. "
    "Respond with the summary text only; do not use any tools."
)

_STATUS_LABELS: dict[str, str] = {
    "completed": "completed successfully",
    "failed": "failed",
    "awaiting_input": "paused — human input needed",
    "escalated": "escalated — the agent could not proceed autonomously",
}


def _status_label(status: str) -> str:
    return _STATUS_LABELS.get(status, status)


_DOD_EVIDENCE_MAX = 10


def _build_user_prompt(
    title: str,
    status: str,
    synthesis: str | None,
    stop_reason: str | None = None,
    park_reason: str | None = None,
    dod_evidence: list[str] | None = None,
) -> str:
    lines = [f"Task: {title}", f"Final status: {_status_label(status)}"]
    if park_reason:
        lines += ["", f"Park reason: {park_reason}"]
    if stop_reason:
        lines += ["", f"Stop reason: {stop_reason}"]
    if dod_evidence:
        lines += [
            "",
            "Definition-of-done evidence:",
            *[f"- {e}" for e in dod_evidence[:_DOD_EVIDENCE_MAX]],
        ]
    if synthesis:
        lines += ["", "Handover synthesis (truncated to 2000 chars):", synthesis[:2000]]
    lines += ["", "Write the plain-language summary now."]
    return "\n".join(lines)


def _clause_evidence_line(clause: object) -> str | None:
    if not isinstance(clause, dict):
        return None
    clause_d = cast(_JsonDict, clause)
    text = clause_d.get("text")
    evidence_text = clause_d.get("evidence")
    if isinstance(text, str) and isinstance(evidence_text, str) and evidence_text:
        return f"{text}: {evidence_text}"
    return None


def _task_dod_evidence(task: object) -> list[str]:
    if not isinstance(task, dict):
        return []
    clauses = cast(_JsonDict, task).get("dod_clauses")
    if not isinstance(clauses, list):
        return []
    lines = (_clause_evidence_line(c) for c in cast("list[object]", clauses))
    return [line for line in lines if line is not None]


def _dod_evidence_from_tasks(tasks: object) -> list[str]:
    if not isinstance(tasks, list):
        return []
    evidence: list[str] = []
    for task in cast("list[object]", tasks):
        evidence.extend(_task_dod_evidence(task))
    return evidence


def _read_meta_extras(ws_path: Path) -> tuple[str | None, list[str]]:
    try:
        raw = json.loads((ws_path / "meta.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None, []
    if not isinstance(raw, dict):
        return None, []
    meta = cast(_JsonDict, raw)
    park_reason = meta.get("park_reason")
    park_reason_str = park_reason if isinstance(park_reason, str) else None
    return park_reason_str, _dod_evidence_from_tasks(meta.get("tasks"))


def _write_sidecar(ws_path: Path, data: dict[str, object]) -> None:
    store.write_text_atomic(ws_path / _SIDECAR, json.dumps(data), mode=_SIDECAR_FILE_MODE)


def read_manager_summary(ws_path: Path) -> dict[str, object] | None:
    p = ws_path / _SIDECAR
    try:
        return cast(dict[str, object], json.loads(p.read_text()))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def read_manager_summary_written_at(ws_path: Path) -> datetime | None:
    try:
        mtime = (ws_path / _SIDECAR).stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(mtime, tz=UTC)


def _stream_path(ws_path: Path) -> Path:
    return ws_path / _STREAM_FILE


_USAGE_CACHE: dict[Path, tuple[int, int, RunSummary | None]] = {}


def read_manager_summary_usage(ws_path: Path) -> RunSummary | None:
    stream = _stream_path(ws_path)
    try:
        st = stream.stat()
    except OSError:
        return None
    hit = store.stat_cache_hit(_USAGE_CACHE, stream, st)
    if hit is not None:
        return copy.deepcopy(hit[0])
    summary = derive_run_summary(stream, system_prompt=_SYSTEM_PROMPT, model="haiku")
    store.stat_cache_put(_USAGE_CACHE, stream, st, summary)
    return copy.deepcopy(summary)


def _do_generate(
    ws_path: Path,
    workspace_id: str,
    title: str,
    status: str,
    synthesis: str | None,
    on_ready: Callable[[], None],
    stop_reason: str | None = None,
) -> None:
    result: dict[str, object] = {"ok": False, "text": None}
    try:
        _stream_path(ws_path).unlink(missing_ok=True)
    except OSError:
        logger.warning(
            "manager_summary: stream reset failed for %s", workspace_id, exc_info=True
        )
    park_reason, dod_evidence = _read_meta_extras(ws_path)
    try:
        text = run_agent(
            _SYSTEM_PROMPT,
            _build_user_prompt(
                title, status, synthesis, stop_reason, park_reason, dod_evidence
            ),
            model="haiku",
            deny_all_tools=True,
            stream_path=_stream_path(ws_path),
            agent=agent_key.MANAGER_SUMMARY,
        )
        result = {"ok": True, "text": text}
    except Exception:  # noqa: BLE001
        logger.exception("manager_summary: generation failed for %s", workspace_id)
    try:
        _write_sidecar(ws_path, result)
        on_ready()
    except Exception:  # noqa: BLE001
        logger.exception("manager_summary: sidecar/notify failed for %s", workspace_id)


def trigger_manager_summary(
    ws_path: Path,
    workspace_id: str,
    title: str,
    status: str,
    synthesis: str | None,
    on_ready: Callable[[], None],
    *,
    stop_reason: str | None = None,
) -> None:
    threading.Thread(
        target=_do_generate,
        args=(ws_path, workspace_id, title, status, synthesis, on_ready, stop_reason),
        daemon=True,
    ).start()
