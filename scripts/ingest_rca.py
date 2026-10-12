"""Ingest a written markdown post-mortem into rca_store as a human gold-label RcaDraft.

Usage:
    uv run python scripts/ingest_rca.py [--queue-db PATH] SEED [SEED ...]

This is the LLM-free counterpart to the (future) investigator sidecar: it never infers
anything, it only mechanically lifts fields a human already wrote down. Every seed must
declare its own fingerprint explicitly — this script never computes one from live incident
state, so infra-liveness post-mortems that the workspace-only investigator trigger can never
fingerprint-collide with (e.g. watcher-heartbeat deaths) still join the same rca_drafts_v1
store as a first-class gold label.

Seed format — the ledger format seeded by
docs/audits/incident-2026-07-03-triage-slot-exhaustion.md:

    ## Record

    | Field | Value |
    |---|---|
    | fingerprint | `explicit-stable-id` |
    | disposition | `root_caused` ... |
    | recurrence_signature | ... |

    ## Fix (this PR)
    - bullet
    - bullet

    ## Timeline (evidence-linked)
    - prose description {cite kind=log_line file=alan.log grep_pattern=ERROR}
    - prose description with no citation tag (contributes no evidence link)

Record-table field lookup rules (all keys matched case-insensitively):
  - fingerprint: read from a `fingerprint` row, falling back to `incident_id` (the field
    name the existing ledger-format seed already declares explicitly). Required — a seed
    missing both is rejected.
  - disposition: optional; the first backtick-quoted token is taken as proposed_disposition.
  - falsification_test: optional explicit row; if absent, derived mechanically as "re-run and
    confirm the recurrence_signature does not reappear" — so a seed needs to declare at least
    one of falsification_test / recurrence_signature, or it is rejected.
  - recipe: optional explicit row; if absent, derived from a "## Fix" section's bullets.

Timeline bullets carry evidence only when they end in a `{cite kind=... ...}` tag (audit_row
needs file+json_path, log_line needs file+grep_pattern, replay_command needs argv); bullets
without a tag, or with a tag that fails rca_store's own locator/kind validation, contribute no
TimelineEntry — an empty timeline is a valid draft, never a rejected one.

A seed ingested here is source="human": a human already confirmed the incident end-to-end, so
it is marked verified=True unconditionally (unlike the future investigator's LLM-authored
drafts, which are the ones that need the deterministic citation resolver's fail-closed check).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from alan.queue import QUEUE_DB, Queue
from alan.rca_store import Citation, RcaDraft, RcaSchemaError, TimelineEntry, save_rca_draft

_QUEUE_DB_ENV = "ALAN_QUEUE_DB_PATH"
_FINGERPRINT_KEYS = ("fingerprint", "incident_id")
_TABLE_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|\s*(.+?)\s*\|\s*$")
_HEADING_RE = re.compile(r"^#{2,3}\s+(.+?)\s*$")
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_CITE_TAG_RE = re.compile(r"\{cite\s+([^}]*)\}\s*$")
_CITE_ATTR_RE = re.compile(r'(\w+)=("[^"]*"|\S+)')


class IngestError(ValueError):
    pass


def _parse_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in text.splitlines():
        heading = _HEADING_RE.match(line)
        if heading:
            current = []
            sections[heading.group(1).strip().lower()] = current
            continue
        if current is not None:
            current.append(line)
    return sections


def _find_section(sections: dict[str, list[str]], prefix: str) -> list[str] | None:
    for heading, lines in sections.items():
        if heading.startswith(prefix):
            return lines
    return None


def _parse_record_table(lines: list[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in lines:
        match = _TABLE_ROW_RE.match(line.strip())
        if not match:
            continue
        key = match.group(1).strip().lower()
        if key == "field" or not key.strip("-"):
            continue
        fields[key] = match.group(2).strip()
    return fields


def _strip_backticks(value: str) -> str:
    match = _BACKTICK_RE.search(value)
    return match.group(1) if match else value


def _unwrap_if_pure_backtick_span(value: str) -> str:
    """Unwrap a field value that is *entirely* one backtick span, e.g. `` `fd-exhaustion` ``.

    Left raw otherwise, since a prose recurrence_signature legitimately embeds several
    backtick-quoted tokens interspersed with free text (e.g. the ledger-format seed's
    "`seed_triage_failed` rows in `triage_audit.jsonl`; ...") and unwrapping the first span
    there would silently discard the rest of the declared signature.
    """
    if len(value) >= 2 and value.startswith("`") and value.endswith("`") and "`" not in value[1:-1]:
        return value[1:-1]
    return value


def _first_present(fields: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = fields.get(key)
        if value:
            return value
    return None


def _derive_fingerprint(fields: dict[str, str]) -> str:
    raw = _first_present(fields, _FINGERPRINT_KEYS)
    if raw is None:
        raise IngestError(
            "seed's Record table declares neither 'fingerprint' nor 'incident_id'"
        )
    return _strip_backticks(raw)


def _derive_disposition(fields: dict[str, str]) -> str | None:
    raw = fields.get("disposition")
    return _strip_backticks(raw) if raw else None


def _is_top_level_bullet(line: str, stripped: str) -> bool:
    return stripped.startswith("-") and line == line.lstrip()


def _logical_bullets(lines: list[str]) -> list[str]:
    """Join a markdown bullet list into logical bullets, folding indented continuation lines.

    A top-level bullet is a line starting with "-" with no leading indentation; any
    subsequent non-blank line that does not itself start a new top-level bullet is
    treated as a wrapped continuation of the current bullet and folded in (space-joined)
    rather than dropped. Blank lines are skipped.
    """
    bullets: list[str] = []
    current: list[str] | None = None
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if _is_top_level_bullet(line, stripped):
            if current is not None:
                bullets.append(" ".join(current))
            current = [stripped[1:].strip()]
        elif current is not None:
            current.append(stripped)
    if current is not None:
        bullets.append(" ".join(current))
    return bullets


def _derive_recipe(fields: dict[str, str], sections: dict[str, list[str]]) -> str:
    explicit = fields.get("recipe")
    if explicit:
        return explicit
    fix_lines = _find_section(sections, "fix")
    if fix_lines is None:
        return ""
    return " ".join(_logical_bullets(fix_lines))


def _derive_falsification_test(fields: dict[str, str]) -> str:
    explicit = fields.get("falsification_test")
    if explicit:
        return explicit
    recurrence = fields.get("recurrence_signature")
    if recurrence:
        clean_recurrence = _unwrap_if_pure_backtick_span(recurrence)
        return (
            f"Re-run the fix and confirm the recurrence signature ({clean_recurrence}) "
            "does not reappear."
        )
    raise IngestError(
        "seed declares neither 'falsification_test' nor 'recurrence_signature' in its "
        "Record table; cannot derive a falsification test"
    )


def _parse_citation_tag(tag_body: str) -> Citation | None:
    attrs = {m.group(1): m.group(2).strip('"') for m in _CITE_ATTR_RE.finditer(tag_body)}
    kind = attrs.get("kind")
    if kind is None:
        return None
    try:
        if kind == "replay_command":
            argv_raw = attrs.get("argv", "")
            argv = tuple(argv_raw.split()) if argv_raw else None
            return Citation(kind=kind, argv=argv)
        return Citation(
            kind=kind,
            file=attrs.get("file"),
            json_path=attrs.get("json_path"),
            grep_pattern=attrs.get("grep_pattern"),
        )
    except RcaSchemaError:
        return None


def _parse_timeline(sections: dict[str, list[str]]) -> tuple[TimelineEntry, ...]:
    lines = _find_section(sections, "timeline")
    if lines is None:
        return ()
    entries: list[TimelineEntry] = []
    for bullet in _logical_bullets(lines):
        tag_match = _CITE_TAG_RE.search(bullet)
        if not tag_match:
            continue
        citation = _parse_citation_tag(tag_match.group(1))
        if citation is None:
            continue
        description = bullet[: tag_match.start()].strip()
        entries.append(TimelineEntry(description=description, citation=citation))
    return tuple(entries)


def parse_seed(text: str) -> tuple[str, RcaDraft]:
    sections = _parse_sections(text)
    record_lines = _find_section(sections, "record")
    if record_lines is None:
        raise IngestError("seed has no '## Record' section")
    fields = _parse_record_table(record_lines)
    fingerprint = _derive_fingerprint(fields)
    draft = RcaDraft(
        source="human",
        proposed_disposition=_derive_disposition(fields),
        timeline=_parse_timeline(sections),
        falsification_test=_derive_falsification_test(fields),
        recurrence_signature=_unwrap_if_pure_backtick_span(fields.get("recurrence_signature", "")),
        recipe=_derive_recipe(fields, sections),
        recurrence_signal=fields.get("recurrence_signal", ""),
        verified=True,
    )
    return fingerprint, draft


def ingest_seed_file(path: Path, queue: Queue) -> tuple[str, RcaDraft]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise IngestError(f"seed file not found: {path}") from exc
    except OSError as exc:
        raise IngestError(f"seed file could not be read: {path}: {exc}") from exc
    fingerprint, draft = parse_seed(text)
    save_rca_draft(queue, fingerprint, draft)
    return fingerprint, draft


def _resolve_queue_db(cli_value: str | None) -> Path:
    if cli_value:
        return Path(cli_value)
    override = os.environ.get(_QUEUE_DB_ENV)
    return Path(override) if override else QUEUE_DB


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("seeds", nargs="+", help="Markdown post-mortem seed file(s)")
    parser.add_argument(
        "--queue-db",
        default=None,
        help="Path to the queue sqlite db (default: ALAN_QUEUE_DB_PATH env or ~/.alan/queue.db)",
    )
    args = parser.parse_args(argv)

    queue = Queue(db_path=_resolve_queue_db(args.queue_db))
    failures = 0
    try:
        for raw_path in args.seeds:
            path = Path(raw_path)
            try:
                fingerprint, draft = ingest_seed_file(path, queue)
            except IngestError as exc:
                print(f"SKIP {raw_path}: {exc}", file=sys.stderr)
                failures += 1
                continue
            print(
                f"INGESTED {fingerprint} <- {raw_path} "
                f"(disposition={draft.proposed_disposition})"
            )
    finally:
        queue.close()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
