from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from alan import process_runner
from alan.engine_config import ForgeTarget
from alan.forge import JsonObject, active_forge
from alan.teams import _forge_errors, _pr_audit_log, _pr_body, _pr_body_block
from alan.teams._pr_forge import GH_TIMEOUT_S
from alan.teams._pr_handover import _SALVAGE_IDENTITY_EMAIL  # pyright: ignore[reportPrivateUsage]
from alan.workspace import Workspace

PublishOutcome = Literal["absent", "present", "appended", "split_skipped", "malformed"]

CLOSING_LINKS_MISSING_REASON = "closing_links_missing"

_TRAILER_KEY = "Delivers-Issues:"
_TRAILER_RE = re.compile(r"^Delivers-Issues: (\d+(?:, \d+)*)$")
_CLOSE_KEYWORD_RE = re.compile(
    r"^(?:close|closes|closed|fix|fixes|fixed|resolve|resolves|resolved)[ \t]+#(\d+)$",
    re.IGNORECASE,
)
_FENCE_RUN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_HTML_COMMENT_OPEN = "<!--"
_HTML_COMMENT_CLOSE = "-->"
_MIN_FENCE_RESERVE = 3


@dataclass(frozen=True)
class Trailer:
    issues: tuple[int, ...]
    malformed: bool
    raw_line: str | None = None


@dataclass(frozen=True)
class ComposedBody:
    body: str
    outcome: PublishOutcome
    issues: tuple[int, ...]
    appended: tuple[int, ...]


@dataclass(frozen=True)
class _OpenFence:
    char: str
    length: int


def _comment_state(line: str, in_comment: bool) -> tuple[bool, bool]:
    if in_comment:
        return (_HTML_COMMENT_CLOSE not in line, True)
    if _HTML_COMMENT_OPEN in line:
        after = line.split(_HTML_COMMENT_OPEN, 1)[1]
        return (_HTML_COMMENT_CLOSE not in after, True)
    return (False, False)


def _fence_marker(raw_line: str) -> tuple[str, int, bool] | None:
    match = _FENCE_RUN_RE.match(raw_line)
    if match is None:
        return None
    run = match.group(1)
    char = run[0]
    trailing = raw_line[match.end():]
    if char == "`" and "`" in trailing:
        return None
    return char, len(run), not trailing.strip()


def _advance_fence(
    open_fence: _OpenFence | None, raw_line: str,
) -> tuple[_OpenFence | None, bool]:
    marker = _fence_marker(raw_line)
    if marker is None:
        return open_fence, open_fence is not None
    char, length, is_bare = marker
    if open_fence is None:
        return _OpenFence(char, length), True
    if is_bare and char == open_fence.char and length >= open_fence.length:
        return None, True
    return open_fence, True


def _collect_line_hits(raw_line: str, hits: set[int], trailer_lines: list[str]) -> None:
    stripped = raw_line.rstrip()
    match = _CLOSE_KEYWORD_RE.match(stripped)
    if match:
        hits.add(int(match.group(1)))
    trailer_candidate = raw_line.strip()
    if trailer_candidate.startswith(_TRAILER_KEY):
        trailer_lines.append(trailer_candidate)


def _scan_state(
    body: str,
) -> tuple[_OpenFence | None, bool, frozenset[int], tuple[str, ...]]:
    open_fence: _OpenFence | None = None
    in_comment = False
    hits: set[int] = set()
    trailer_lines: list[str] = []
    for raw_line in body.splitlines():
        open_fence, consumed = _advance_fence(open_fence, raw_line)
        if consumed:
            continue
        in_comment, skip = _comment_state(raw_line.strip(), in_comment)
        if skip:
            continue
        _collect_line_hits(raw_line, hits, trailer_lines)
    return open_fence, in_comment, frozenset(hits), tuple(trailer_lines)


def parse_delivers_trailer(description: str) -> Trailer:
    _, _, _, trailer_lines = _scan_state(description)
    if not trailer_lines:
        return Trailer(issues=(), malformed=False)
    line = trailer_lines[-1]
    match = _TRAILER_RE.match(line)
    if match is None:
        return Trailer(issues=(), malformed=True, raw_line=line)
    numbers = [int(n) for n in match.group(1).split(", ")]
    if any(n <= 0 for n in numbers):
        return Trailer(issues=(), malformed=True, raw_line=line)
    return Trailer(issues=tuple(dict.fromkeys(numbers)), malformed=False)


def _present_issues(body: str, declared: tuple[int, ...]) -> frozenset[int]:
    _, _, hits, _ = _scan_state(body)
    return frozenset(n for n in declared if n in hits)


def _max_fence_run(text: str) -> int:
    longest = _MIN_FENCE_RESERVE
    for raw_line in text.splitlines():
        marker = _fence_marker(raw_line)
        if marker is not None:
            longest = max(longest, marker[1])
    return longest


def _reserve_for(issues: tuple[int, ...], base_text: str) -> int:
    lines = "\n".join(f"Fixes #{n}" for n in issues)
    fence_len = _max_fence_run(base_text)
    return len("\n\n" + lines) + len("\n") + fence_len + len("\n-->")


def _close_open_blocks(body: str) -> str:
    open_fence, in_comment, _, _ = _scan_state(body)
    closed = body
    if in_comment:
        closed += "\n-->"
    if open_fence is not None:
        closed += "\n" + (open_fence.char * open_fence.length)
    return closed


def _append_missing(body: str, missing: tuple[int, ...]) -> str:
    lines = "\n".join(f"Fixes #{n}" for n in missing)
    return f"{_close_open_blocks(body)}\n\n{lines}"


def _plan_single_task(base_text: str, issues: tuple[int, ...]) -> ComposedBody:
    body = _pr_body.build_pr_body(base_text)
    missing = tuple(n for n in issues if n not in _present_issues(body, issues))
    if not missing:
        return ComposedBody(body=body, outcome="present", issues=issues, appended=())
    reserved = _pr_body.build_pr_body(base_text, reserve=_reserve_for(issues, base_text))
    final_missing = tuple(n for n in issues if n not in _present_issues(reserved, issues))
    if not final_missing:
        return ComposedBody(body=reserved, outcome="present", issues=issues, appended=())
    final_body = _append_missing(reserved, final_missing)
    return ComposedBody(
        body=final_body, outcome="appended", issues=issues, appended=final_missing,
    )


def plan_pr_body(ws: Workspace, base_text: str, trailer: Trailer) -> ComposedBody:
    if trailer.malformed:
        return ComposedBody(
            body=_pr_body.build_pr_body(base_text), outcome="malformed",
            issues=(), appended=(),
        )
    if not trailer.issues:
        return ComposedBody(
            body=_pr_body.build_pr_body(base_text), outcome="absent",
            issues=(), appended=(),
        )
    if len(ws.meta.tasks) > 1:
        return ComposedBody(
            body=_pr_body.build_pr_body(base_text), outcome="split_skipped",
            issues=trailer.issues, appended=(),
        )
    return _plan_single_task(base_text, trailer.issues)


def _audit_row(
    ws: Workspace,
    team: str,
    pr_number: int | None,
    composed: ComposedBody,
    *,
    alternative_lengths: tuple[int, ...],
    trailer: Trailer,
) -> dict[str, object]:
    row: dict[str, object] = {
        "task_id": ws.task_id,
        "team": team,
        "pr_number": pr_number,
        "action": "pr_body_publish",
        "outcome": composed.outcome,
        "issues": list(composed.issues),
        "appended": list(composed.appended),
        "body_length": len(composed.body),
        "alternative_lengths": list(alternative_lengths),
    }
    if composed.outcome == "malformed" and trailer.raw_line is not None:
        row["trailer_line"] = trailer.raw_line[:200]
    return row


def _parse_closing_issue_numbers(fields: JsonObject) -> frozenset[int]:
    raw = fields.get("closingIssuesReferences")
    if not isinstance(raw, list):
        return frozenset()
    numbers: set[int] = set()
    for item in cast("list[object]", raw):
        if isinstance(item, dict):
            number = cast(JsonObject, item).get("number")
            if isinstance(number, int):
                numbers.add(number)
    return frozenset(numbers)


def gh_pr_closing_issue_numbers(
    pr_number: int, *, forge_target: ForgeTarget,
) -> frozenset[int] | None:
    try:
        read = active_forge().read_pr(
            pr_number, ("closingIssuesReferences",),
            forge_target=forge_target, timeout=GH_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise _forge_errors.ForgeTransientError(
            f"gh pr view closingIssuesReferences raised {exc}"
        ) from exc
    if not read.ok:
        _forge_errors.raise_for_infra(
            f"gh pr view closingIssuesReferences PR #{pr_number}",
            returncode=read.returncode, stderr=read.stderr,
        )
        return None
    return _parse_closing_issue_numbers(read.fields)


def _commit_author_emails(worktree: Path, base_ref: str) -> list[str]:
    try:
        result = process_runner.run_subprocess(
            ["git", "log", f"{base_ref}..HEAD", "--format=%ae"],
            cwd=str(worktree), timeout=GH_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


def is_salvage_only(worktree: Path, base_ref: str) -> bool:
    emails = _commit_author_emails(worktree, base_ref)
    return bool(emails) and all(email == _SALVAGE_IDENTITY_EMAIL for email in emails)


_SALVAGED_STATEMENT = (
    "**This pull request was salvaged.** Every commit on this branch was "
    "captured by the host recovery path under the alan-salvage identity "
    "after the worker stopped without committing; no worker synthesis is "
    "reflected here, and the worker's own gate result was never observed."
)

_SALVAGED_DIFF_STAT_LINE_CAP = 40


def _salvaged_diff_stat(worktree: Path, base_ref: str) -> str:
    try:
        result = process_runner.run_subprocess(
            ["git", "diff", "--stat", f"{base_ref}..HEAD"],
            cwd=str(worktree), timeout=GH_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    lines = (result.stdout or "").splitlines()
    return "\n".join(lines[:_SALVAGED_DIFF_STAT_LINE_CAP])


def compose_salvaged_pr_body(
    ws: Workspace, worktree: Path, base_ref: str, *, team: str, pr_number: int | None,
) -> str:
    parts = [f"## {(ws.meta.title or ws.task_id).strip()}"]
    description = ws.meta.description.strip()
    if description:
        parts.append(description)
    diff_stat = _salvaged_diff_stat(worktree, base_ref)
    if diff_stat:
        parts.append(f"### Changes\n```\n{diff_stat}\n```")
    parts.append(_SALVAGED_STATEMENT)
    base_text = "\n\n".join(parts)
    return compose_pr_body(ws, base_text, team=team, pr_number=pr_number)


def compose_pr_body(
    ws: Workspace, synthesis: str, *, team: str, pr_number: int | None,
) -> str:
    extraction = _pr_body_block.extract_pr_body_block(synthesis)
    base_text = extraction.body if extraction is not None else synthesis
    trailer = parse_delivers_trailer(ws.meta.description)
    composed = plan_pr_body(ws, base_text, trailer)
    alt_lengths = extraction.alternative_lengths if extraction is not None else ()
    _pr_audit_log.append_audit(
        _audit_row(
            ws, team, pr_number, composed, alternative_lengths=alt_lengths, trailer=trailer,
        )
    )
    return composed.body


__all__ = [
    "CLOSING_LINKS_MISSING_REASON",
    "ComposedBody",
    "PublishOutcome",
    "Trailer",
    "compose_pr_body",
    "compose_salvaged_pr_body",
    "gh_pr_closing_issue_numbers",
    "is_salvage_only",
    "parse_delivers_trailer",
    "plan_pr_body",
]
