from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from alan import agent_key, store
from alan.claude_cli import ClaudeCliError, ProcessConfig, run_agent_streaming
from alan.handover import (
    PENDING_EXTERNAL_NOTICE,
    Handover,
    HandoverError,
    OpenQuestion,
    handover_paths,
    load_handover,
    recorded_answers_for_chain,
)
from alan.workspace import Workspace

logger = logging.getLogger("alan.compression")

DEFAULT_MAX_CHARS = 2500
_DECISIONS_HEADER = "Decisions:"
_QUESTIONS_HEADER = "Open questions:"
_SUMMARY_HEADER = "Summary:"
_FORBIDDEN_HEADER = "Forbidden:"
_PRIOR_HEADER_TEMPLATE = "Prior handover from {team} (iteration {iteration}):"

_LLM_COMPACT_THRESHOLD = 1500
_COMPACTOR_TARGET_CHARS = 1200
_COMPACT_TIMEOUT_SECONDS = 15
_LLM_COMPACT_DISABLE_ENV = "ALAN_LLM_COMPACT_DISABLE"

_COMPACTOR_SYSTEM_PROMPT = (
    "You are a concise technical summarizer. "
    f"Compress the handover synthesis to under {_COMPACTOR_TARGET_CHARS} characters. "
    "Preserve verbatim: file paths, commands, error messages and gate names, "
    "PR/issue numbers, unresolved blockers, and the concrete next step. Prefer "
    "dropping narrative and adjectives over dropping any identifier. If you "
    "must cut, cut oldest progress detail first. "
    "Output ONLY the compressed summary — no preamble, no headings."
)
_COMPACTOR_VERSION = hashlib.sha256(_COMPACTOR_SYSTEM_PROMPT.encode()).hexdigest()[:8]


def summarize_handover(
    handover: Handover,
    max_chars: int = DEFAULT_MAX_CHARS,
    *,
    workspace: Workspace | None = None,
) -> str:
    return _assemble(
        handover, include_summary=True, max_chars=max_chars, workspace=workspace
    )


def summarize_handover_decisions_only(
    handover: Handover, max_chars: int = DEFAULT_MAX_CHARS
) -> str:
    return _assemble(handover, include_summary=False, max_chars=max_chars)


def _try_load_handover(path: Path) -> Handover | None:
    try:
        return load_handover(path)
    except (HandoverError, OSError):
        logger.warning(
            "load_cross_team_prior_outputs: skipping unreadable %s", path.name, exc_info=True
        )
        return None


def load_cross_team_prior_outputs(
    workspace: Workspace,
    target_team: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    *,
    decisions_only: bool = False,
) -> str:
    blocks: list[str] = []
    remaining = max_chars
    for path in reversed(handover_paths(workspace)):
        if remaining <= 0:
            break
        handover = _try_load_handover(path)
        if handover is None or target_team not in handover.to_team_suggestions:
            continue
        block = _format_prior_block(
            handover, remaining, decisions_only=decisions_only, workspace=workspace
        )
        if not block:
            continue
        blocks.append(block)
        remaining -= len(block) + 2  # +2 accounts for the joining "\n\n"
    return "\n\n".join(blocks)


def _format_prior_block(
    handover: Handover,
    remaining: int,
    *,
    decisions_only: bool = False,
    workspace: Workspace | None = None,
) -> str:
    header = _PRIOR_HEADER_TEMPLATE.format(
        team=handover.from_team, iteration=handover.iteration
    )
    body = _assemble(
        handover,
        include_summary=not decisions_only,
        max_chars=max(0, remaining - len(header) - 1),
        workspace=workspace,
    )
    if not body:
        return ""
    return f"{header}\n{body}"


def _assemble(
    handover: Handover,
    *,
    include_summary: bool,
    max_chars: int,
    workspace: Workspace | None = None,
) -> str:
    sections: list[str] = []
    decisions = _format_decisions(handover)
    if decisions:
        sections.append(decisions)
    questions = _format_questions(handover, workspace)
    if questions:
        sections.append(questions)
    forbidden = _format_forbidden(handover)
    if forbidden:
        sections.append(forbidden)
    if include_summary:
        summary = _compact_synthesis(handover, workspace)
        if summary:
            sections.append(f"{_SUMMARY_HEADER} {summary}")
    if handover.pr_number is not None:
        sections.append(f"PR: #{handover.pr_number}")
    text = "\n\n".join(sections)
    return text[:max_chars]


def _compact_synthesis(handover: Handover, workspace: Workspace | None) -> str:
    raw = handover.full_synthesis.strip()
    if workspace is None:
        return raw
    _disable_val = os.environ.get(_LLM_COMPACT_DISABLE_ENV, "").lower()
    if _disable_val in ("1", "true", "yes"):
        return raw
    if len(raw) <= _LLM_COMPACT_THRESHOLD:
        return raw
    cached = _read_compact_cache(workspace, handover.handover_id)
    if cached is not None:
        return cached
    return _run_compact(handover, workspace)


def _safe_cache_name(handover_id: str) -> str:
    return handover_id.replace("/", "_").replace("\\", "_").replace("..", "_")


def _read_compact_cache(workspace: Workspace, handover_id: str) -> str | None:
    path = workspace.path / "handovers" / ".compact" / f"{_safe_cache_name(handover_id)}.txt"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    header, _, rest = text.partition("\n")
    expected = f"# v={_COMPACTOR_VERSION} target={_COMPACTOR_TARGET_CHARS}"
    if header != expected:
        return None
    stripped = rest.strip()
    return stripped if stripped else None


def _write_compact_cache(workspace: Workspace, handover_id: str, body: str) -> None:
    cache_dir = workspace.path / "handovers" / ".compact"
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{_safe_cache_name(handover_id)}.txt"
    header = f"# v={_COMPACTOR_VERSION} target={_COMPACTOR_TARGET_CHARS}"
    store.write_text_atomic(path, f"{header}\n{body}")


def _call_compactor_llm(synthesis: str) -> str:
    result = run_agent_streaming(
        _COMPACTOR_SYSTEM_PROMPT,
        synthesis,
        model="haiku",
        max_turns=1,
        deny_all_tools=True,
        proc_config=ProcessConfig(timeout_seconds=_COMPACT_TIMEOUT_SECONDS),
        agent=agent_key.HANDOVER_COMPACTOR,
    )
    return result.result_text[:_COMPACTOR_TARGET_CHARS]


def _run_compact(handover: Handover, workspace: Workspace) -> str:
    raw = handover.full_synthesis.strip()
    try:
        compacted = _call_compactor_llm(raw)
    except ClaudeCliError:
        logger.warning(
            "LLM compactor failed for handover %s; falling back to lexical",
            handover.handover_id,
            exc_info=True,
        )
        return raw
    _write_compact_cache(workspace, handover.handover_id, compacted)
    return compacted


def _format_decisions(handover: Handover) -> str:
    if not handover.decisions:
        return ""
    bullets = [
        f"- {d.statement.strip()} (because: {d.rationale.strip()})"
        for d in handover.decisions
    ]
    return f"{_DECISIONS_HEADER}\n" + "\n".join(bullets)


def _format_questions(handover: Handover, workspace: Workspace | None) -> str:
    blocking = [q for q in handover.open_questions if q.blocking]
    if not blocking:
        return ""
    answers_by_id = _recorded_answers_or_empty(workspace, handover.handover_chain_id)
    bullets = [_question_bullet(q, answers_by_id) for q in blocking]
    return f"{_QUESTIONS_HEADER}\n" + "\n".join(bullets)


def _recorded_answers_or_empty(
    workspace: Workspace | None, handover_chain_id: str
) -> dict[str, str]:
    if workspace is None:
        return {}
    return recorded_answers_for_chain(workspace, handover_chain_id)


def _question_bullet(question: OpenQuestion, answers_by_id: dict[str, str]) -> str:
    base = f"- {question.question.strip()} (resolver: {question.resolver_role})"
    effective = (
        question.answer if question.answer is not None else answers_by_id.get(question.id)
    )
    if effective is None:
        return base
    if question.option_effects.get(effective) == "pending_external":
        return f"{base} [{PENDING_EXTERNAL_NOTICE}]"
    return f"{base} [ANSWERED: {effective}]"


def _format_forbidden(handover: Handover) -> str:
    if not handover.forbidden_actions:
        return ""
    bullets = [f"- {item.strip()}" for item in handover.forbidden_actions if item.strip()]
    if not bullets:
        return ""
    return f"{_FORBIDDEN_HEADER}\n" + "\n".join(bullets)


__all__ = [
    "DEFAULT_MAX_CHARS",
    "load_cross_team_prior_outputs",
    "summarize_handover",
    "summarize_handover_decisions_only",
    "_call_compactor_llm",
]
