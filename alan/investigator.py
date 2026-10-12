from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, cast

import yaml

from alan import agent_key
from alan.block_correction import BlockCorrectionError
from alan.claude_cli import TurnCeilingReached, run_agent_streaming
from alan.queue import Queue
from alan.rca_store import (
    Citation,
    RcaDraft,
    RcaSchemaError,
    TimelineEntry,
    get_rca_draft,
    save_rca_draft,
)
from alan.teams._block_correction_audit import record_unreachable_attempt

logger = logging.getLogger("alan.investigator")

_STREAM_FILE = "investigator_stream.jsonl"
_MAX_TURNS = 40

_INVESTIGATOR_ALLOWED_TOOLS: tuple[str, ...] = (
    "Read",
    "Grep",
    "Glob",
    "Bash(grep:*)",
    "Bash(cat:*)",
    "Bash(git log:*)",
    "Bash(gh pr view:*)",
    "Bash(gh issue view:*)",
    "Bash(gh run view:*)",
)

_REPLAY_ALLOWLIST: frozenset[str] = frozenset({
    "grep",
    "cat",
    "git log",
    "gh pr view",
    "gh issue view",
    "gh run view",
})

_SYSTEM_PROMPT = (
    "You are a read-only root-cause investigator for an automated "
    "software-delivery platform. You only diagnose — you never edit files, "
    "never run a write git/gh command, and never propose or trigger a fix. "
    "Investigate the incident described below using the read-only tools "
    "available to you (file reads, grep, cat, git log, gh pr/issue/run "
    "view), then write a root-cause draft.\n\n"
    "FAIL-CLOSED INVARIANT: this draft is only useful if every step in your "
    "timeline can be independently re-confirmed against its source. A "
    "calling system will re-resolve each citation by grepping the exact "
    "file you name for the exact pattern you name, or by checking a replay "
    "command's argv against a read-only allowlist. A citation that does "
    "not resolve is silently dropped and the draft is marked NOT verified "
    "— never present a guess as a confirmed fact. If you cannot find a "
    "real, concrete locator (a file path plus either a JSON path, a grep "
    "pattern, or the exact argv of a read-only command you actually ran) "
    "for a step, omit that step rather than inventing one. A short honest "
    "timeline beats a long fabricated one.\n\n"
    "OUTPUT — after your prose analysis, end your response with a "
    "`## RCA_DRAFT_YAML` section containing a single fenced ```yaml block "
    "with these keys:\n"
    "proposed_disposition: one of the valid dispositions listed below, or "
    "omit if unsure\n"
    "falsification_test: a concrete check a human could run to confirm or "
    "refute this diagnosis (required)\n"
    "recurrence_signature: a short stable label for this failure mode\n"
    "recipe: prose remediation notes\n"
    "timeline: an ordered list of {description, citation} entries, where "
    "each citation is one of:\n"
    "  {kind: audit_row, file: <path>, json_path: <locator>}\n"
    "  {kind: log_line, file: <path>, grep_pattern: <exact substring>}\n"
    "  {kind: replay_command, argv: [<read-only command>, ...]}\n"
    "Do not emit a VERDICT line — verification is computed mechanically "
    "from your citations, not asserted by you."
)


def _build_user_prompt(
    fingerprint: str, evidence: dict[str, Any], valid_dispositions: frozenset[str]
) -> str:
    lines = [
        f"Incident fingerprint: {fingerprint}",
        f"Valid dispositions: {', '.join(sorted(valid_dispositions))}",
        "",
        "Evidence:",
        json.dumps(evidence, indent=2, sort_keys=True, default=str),
        "",
        "Investigate using only the read-only tools available, then emit "
        "the ## RCA_DRAFT_YAML block as instructed.",
    ]
    return "\n".join(lines)


def _compute_recurrence_signal(evidence: dict[str, Any]) -> str:
    return json.dumps(evidence, sort_keys=True, default=str)


def _stream_path(ws_path: Path) -> Path:
    return ws_path / _STREAM_FILE


_YAML_BLOCK_RE = re.compile(
    r"##[ \t]+RCA_DRAFT_YAML[ \t]*\n+[ \t]*```(?:ya?ml)?[ \t]*\n(.*?)\n```", re.DOTALL
)


def _parse_rca_yaml(text: str) -> dict[str, Any] | None:
    match = _YAML_BLOCK_RE.search(text)
    if match is None:
        return None
    try:
        loaded: Any = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None
    return cast(dict[str, Any], loaded) if isinstance(loaded, dict) else None


def _opt_str(v: Any) -> str | None:
    return str(v) if v is not None else None


def _str_or_default(v: Any) -> str:
    return v if isinstance(v, str) else ""


def _citation_from_raw(raw: Any) -> Citation | None:
    if not isinstance(raw, dict):
        return None
    data = cast(dict[str, Any], raw)
    kind = data.get("kind")
    argv_raw = data.get("argv")
    argv = (
        tuple(str(a) for a in cast("list[object]", argv_raw))
        if isinstance(argv_raw, list)
        else None
    )
    try:
        return Citation(
            kind=str(kind) if kind is not None else "",
            file=_opt_str(data.get("file")),
            json_path=_opt_str(data.get("json_path")),
            grep_pattern=_opt_str(data.get("grep_pattern")),
            argv=argv,
        )
    except RcaSchemaError:
        return None


def _timeline_entry_from_raw(raw: Any) -> TimelineEntry | None:
    if not isinstance(raw, dict):
        return None
    data = cast(dict[str, Any], raw)
    citation = _citation_from_raw(data.get("citation"))
    if citation is None:
        return None
    return TimelineEntry(
        description=_str_or_default(data.get("description")), citation=citation
    )


_FILE_SCAN_CHUNK_SIZE = 1 << 16


def _file_contains(file_path: str, needle: str) -> bool:
    overlap = max(len(needle) - 1, 0)
    try:
        with Path(file_path).open(encoding="utf-8", errors="replace") as f:
            carry = ""
            while True:
                chunk = f.read(_FILE_SCAN_CHUNK_SIZE)
                window = carry + chunk
                if needle in window:
                    return True
                if not chunk:
                    return False
                carry = window[-overlap:] if overlap else ""
    except OSError:
        return False


def _replay_command_resolves(argv: tuple[str, ...] | None) -> bool:
    if not argv:
        return False
    prefixes = (tuple(entry.split()) for entry in _REPLAY_ALLOWLIST)
    return any(argv[: len(prefix)] == prefix for prefix in prefixes)


def _citation_resolves(citation: Citation) -> bool:
    if citation.kind == "audit_row":
        return _file_contains(cast(str, citation.file), cast(str, citation.json_path))
    if citation.kind == "log_line":
        return _file_contains(cast(str, citation.file), cast(str, citation.grep_pattern))
    return _replay_command_resolves(citation.argv)


def _resolve_timeline(
    raw_items: list[object],
) -> tuple[tuple[TimelineEntry, ...], bool]:
    entries = tuple(
        e for e in (_timeline_entry_from_raw(item) for item in raw_items) if e is not None
    )
    resolved = tuple(e for e in entries if _citation_resolves(e.citation))
    all_resolved = len(raw_items) > 0 and len(resolved) == len(raw_items)
    return resolved, all_resolved


def _build_draft(
    parsed: dict[str, Any],
    recurrence_signal: str,
    valid_dispositions: frozenset[str],
    workspace_key: str | None,
    created_at: float,
) -> RcaDraft | None:
    falsification_test = parsed.get("falsification_test")
    if not isinstance(falsification_test, str) or not falsification_test:
        return None
    raw_timeline = parsed.get("timeline")
    raw_items = (
        cast("list[object]", raw_timeline) if isinstance(raw_timeline, list) else []
    )
    resolved_entries, all_resolved = _resolve_timeline(raw_items)
    proposed = parsed.get("proposed_disposition")
    proposed_disposition = (
        proposed if isinstance(proposed, str) and proposed in valid_dispositions else None
    )
    return RcaDraft(
        source="investigator",
        proposed_disposition=proposed_disposition,
        timeline=resolved_entries,
        falsification_test=falsification_test,
        recurrence_signature=_str_or_default(parsed.get("recurrence_signature")),
        recipe=_str_or_default(parsed.get("recipe")),
        recurrence_signal=recurrence_signal,
        verified=all_resolved,
        workspace_key=workspace_key,
        created_at=created_at,
    )


def _generate_rca_text(
    ws_path: Path,
    fingerprint: str,
    evidence: dict[str, Any],
    valid_dispositions: frozenset[str],
) -> str | None:
    try:
        result = run_agent_streaming(
            _SYSTEM_PROMPT,
            _build_user_prompt(fingerprint, evidence, valid_dispositions),
            model="sonnet",
            allowed_tools=list(_INVESTIGATOR_ALLOWED_TOOLS),
            add_dirs=[str(ws_path)],
            max_turns=_MAX_TURNS,
            stream_path=_stream_path(ws_path),
            agent=agent_key.INVESTIGATOR,
        )
    except TurnCeilingReached:
        logger.exception(
            "investigator: turn ceiling of %d reached for fingerprint %s",
            _MAX_TURNS,
            fingerprint,
        )
        return None
    except Exception:  # noqa: BLE001
        logger.exception("investigator: generation failed for fingerprint %s", fingerprint)
        return None
    return result.result_text


def _validate_rca_block(text: str) -> dict[str, Any]:
    parsed = _parse_rca_yaml(text)
    if parsed is None:
        raise BlockCorrectionError(
            "RCA_DRAFT_YAML block missing or malformed", kind="parse_error"
        )
    return parsed


def _do_investigate(
    ws_path: Path,
    fingerprint: str,
    evidence: dict[str, Any],
    valid_dispositions: frozenset[str],
    queue: Queue,
    workspace_key: str | None,
) -> None:
    recurrence_signal = _compute_recurrence_signal(evidence)
    existing = get_rca_draft(queue, fingerprint)
    if existing is not None and existing.recurrence_signal == recurrence_signal:
        return
    try:
        _stream_path(ws_path).unlink(missing_ok=True)
    except OSError:
        logger.warning(
            "investigator: stream reset failed for fingerprint %s", fingerprint, exc_info=True
        )
    result_text = _generate_rca_text(ws_path, fingerprint, evidence, valid_dispositions)
    if result_text is None:
        return
    parsed = record_unreachable_attempt(
        site="RCA_DRAFT_YAML",
        role="investigator",
        validate=_validate_rca_block,
        text=result_text,
    )
    if parsed is None:
        logger.warning(
            "investigator: no parseable RCA_DRAFT_YAML for fingerprint %s", fingerprint
        )
        return
    draft = _build_draft(parsed, recurrence_signal, valid_dispositions, workspace_key, time.time())
    if draft is None:
        logger.warning("investigator: malformed RCA draft for fingerprint %s", fingerprint)
        return
    save_rca_draft(queue, fingerprint, draft)


def trigger_investigator(
    ws_path: Path,
    fingerprint: str,
    evidence: dict[str, Any],
    valid_dispositions: frozenset[str],
    queue: Queue,
    workspace_key: str | None,
) -> None:
    threading.Thread(
        target=_do_investigate,
        args=(ws_path, fingerprint, evidence, valid_dispositions, queue, workspace_key),
        daemon=True,
    ).start()


__all__ = [
    "trigger_investigator",
]
