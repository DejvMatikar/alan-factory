from __future__ import annotations

import re
from typing import Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from alan import store
from alan.block_correction import BlockCorrectionError
from alan.health_repo_metrics import read_audit_tail
from alan.roots import logs_root
from alan.teams import _pr_audit_log, _pr_forge
from alan.workspace import Workspace

_MAX_RENDERED_REFUSALS = 20

ResolutionMode = Literal["fix", "acknowledge", "comment_ack"]

RESOLUTION_BLOCK_RE = re.compile(
    r"```yaml[ \t]+copilot_resolution\.yaml[ \t]*\r?\n(.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)

RESOLUTION_BLOCK_GUIDANCE = (
    "COPILOT RESOLUTION — the host resolves Copilot review threads and "
    "comments on your behalf; you never run a script for this yourself. "
    "For each thread or comment you addressed above, append one row to a "
    "fenced copilot_resolution.yaml block in your final synthesis. Use "
    "this EXACT opening fence line, including the ` copilot_resolution.yaml` "
    "info-string:\n"
    "```yaml copilot_resolution.yaml\n"
    "- thread_id: <thread id>\n"
    "  mode: fix\n"
    "- thread_id: <thread id>\n"
    "  mode: acknowledge\n"
    "  body: <one-line reason it needs no code change>\n"
    "- database_id: <comment database id>\n"
    "  mode: comment_ack\n"
    "  body: <one-line reason it needs no code change>\n"
    "```\n"
    "`mode: fix` — you already committed a code fix for the thread; the "
    "host resolves it and posts no reply. `mode: acknowledge` — the "
    "thread is a nit, false positive, or out of scope; the host posts "
    "`body` as a reply then resolves it. `mode: comment_ack` — for a "
    "PR-level Copilot comment or review body (not a review thread); the "
    "host posts `body` as a reply and records the comment as "
    "acknowledged. Only plain scalar values are accepted per field; do "
    "not nest additional keys under a row — a nested value is rejected "
    "wholesale. Acknowledgements are capped per PR round; a row refused "
    "for exceeding the cap is surfaced back to you on the next "
    "iteration. A `fix` row is only honoured for a conversation the host "
    "listed on this PR and authored by the code reviewer; an `acknowledge` "
    "row on a person's conversation still posts your reply but is never "
    "host-resolved.\n\n"
)

_REFUSAL_MESSAGES: dict[str, str] = {
    "thread_not_listed": (
        "was not among the review conversations shown to you, or is not "
        "the code reviewer's, and will not be host-resolved."
    ),
    "already_handled": "was already acknowledged or resolved in a prior iteration.",
    "ack_cap_exceeded": "exceeded the per-PR acknowledgement cap for this round.",
    "forge_call_failed": "could not be posted or resolved because the forge call failed.",
}


class CopilotResolutionRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    thread_id: str | None = None
    database_id: int | None = None
    mode: ResolutionMode
    body: str | None = None

    @model_validator(mode="after")
    def _validate_required_fields(self) -> CopilotResolutionRow:
        if self.mode in ("fix", "acknowledge") and not self.thread_id:
            raise ValueError(f"mode={self.mode!r} requires a non-empty thread_id")
        if self.mode in ("acknowledge", "comment_ack") and not self.body:
            raise ValueError(f"mode={self.mode!r} requires a non-empty body")
        if self.mode == "comment_ack" and self.database_id is None:
            raise ValueError("mode='comment_ack' requires database_id")
        return self


def _scalar_only_row(raw: dict[object, object]) -> dict[str, object]:
    row: dict[str, object] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise BlockCorrectionError("copilot_resolution.yaml row keys must be strings")
        if isinstance(value, (dict, list)):
            raise BlockCorrectionError(
                f"copilot_resolution.yaml field {key!r} must be a scalar value, "
                "not a nested mapping or list"
            )
        row[key] = value
    return row


def _coerce_row(item: object) -> CopilotResolutionRow:
    if not isinstance(item, dict):
        raise BlockCorrectionError("copilot_resolution.yaml row must be a mapping")
    scalar_row = _scalar_only_row(cast("dict[object, object]", item))
    try:
        return CopilotResolutionRow.model_validate(scalar_row)
    except ValidationError as exc:
        raise BlockCorrectionError(str(exc)) from exc


def parse_copilot_resolution_yaml(text: str) -> tuple[CopilotResolutionRow, ...]:
    match = RESOLUTION_BLOCK_RE.search(text)
    if match is None:
        raise BlockCorrectionError(
            "copilot_resolution.yaml block not found", kind="parse_error"
        )
    try:
        raw = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise BlockCorrectionError(
            f"copilot_resolution.yaml did not parse: {exc}", kind="parse_error"
        ) from exc
    if not isinstance(raw, list) or not raw:
        raise BlockCorrectionError("copilot_resolution.yaml must be a non-empty list of rows")
    return tuple(_coerce_row(item) for item in cast("list[object]", raw))


def resolution_block_attempted(text: str) -> bool:
    return RESOLUTION_BLOCK_RE.search(text) is not None


def _is_refusal_for(row: dict[str, object], pr_number: int, repo_slug: str) -> bool:
    return (
        row.get("action") == "copilot_resolution_refused"
        and row.get("pr_number") == pr_number
        and row.get("repo") == repo_slug
    )


def _refused_rows(pr_number: int, repo_slug: str) -> list[dict[str, object]]:
    text = read_audit_tail(logs_root() / _pr_audit_log.COPILOT_AUDIT_FILE)
    rows: list[dict[str, object]] = []
    for line in text.splitlines():
        row: dict[str, object] | None = store.parse_jsonl_row(line)
        if row is not None and _is_refusal_for(row, pr_number, repo_slug):
            rows.append(row)
    return rows


def refused_resolutions_block(workspace: Workspace, pr_number: int | None) -> str:
    if pr_number is None:
        return ""
    repo_slug = _pr_forge.forge_target_for_ws(workspace).repo.slug
    rows = _refused_rows(pr_number, repo_slug)
    if not rows:
        return ""
    lines = ["\n\n## Recent Copilot resolution refusals"]
    for row in rows[-_MAX_RENDERED_REFUSALS:]:
        target = row.get("thread_id") or row.get("database_id")
        reason = str(row.get("reason"))
        message = _REFUSAL_MESSAGES.get(reason, reason)
        lines.append(f"- `{target}` ({row.get('mode', '?')}): {message}")
    return "\n".join(lines)


__all__ = [
    "RESOLUTION_BLOCK_GUIDANCE",
    "RESOLUTION_BLOCK_RE",
    "CopilotResolutionRow",
    "ResolutionMode",
    "parse_copilot_resolution_yaml",
    "refused_resolutions_block",
    "resolution_block_attempted",
]
