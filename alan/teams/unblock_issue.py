from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass

from alan.engine_config import ForgeTarget
from alan.forge import JsonObject, active_forge

logger = logging.getLogger(__name__)

LABEL = "unblocker"
_LABEL_COLOR = "0e8a16"
_LABEL_DESCRIPTION = "Filed by alan's recipe unblocker: one issue per park shape per engine"
_ISSUE_NUMBER_RE = re.compile(r"/issues/(\d+)\s*$")


@dataclass(frozen=True)
class IssueLookup:
    ok: bool
    number: int | None


def issue_url(slug: str, number: int) -> str:
    return f"https://github.com/{slug}/issues/{number}"


def shape_issue_title(shape_title: str, slug: str) -> str:
    return f"Unblocker: {shape_title} ({slug})"


def _matching_number(rows: tuple[JsonObject, ...], title: str) -> int | None:
    for entry in rows:
        number = entry.get("number")
        if entry.get("title") == title and isinstance(number, int):
            return number
    return None


def find_open_issue(title: str, *, forge_target: ForgeTarget) -> IssueLookup:
    read = active_forge().open_issues_by_title(title, forge_target=forge_target, label=LABEL)
    if read.outcome == "raised":
        logger.warning("unblock_issue: issue list raised %s", read.stderr)
    if read.outcome != "ok":
        return IssueLookup(ok=False, number=None)
    return IssueLookup(ok=True, number=_matching_number(read.rows or (), title))


def ensure_label(*, forge_target: ForgeTarget) -> None:
    active_forge().create_label(
        LABEL, forge_target=forge_target, color=_LABEL_COLOR, description=_LABEL_DESCRIPTION,
    )


def create_issue(title: str, body: str, *, forge_target: ForgeTarget) -> int | None:
    try:
        result = active_forge().create_numbered_issue(
            title, body, forge_target=forge_target, label=LABEL,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("unblock_issue: issue create raised %s", exc)
        return None
    if not result.ok:
        return None
    match = _ISSUE_NUMBER_RE.search(result.stdout)
    return int(match.group(1)) if match else None


def ensure_shape_issue(title: str, body: str, *, forge_target: ForgeTarget) -> int | None:
    lookup = find_open_issue(title, forge_target=forge_target)
    if not lookup.ok:
        return None
    if lookup.number is not None:
        return lookup.number
    ensure_label(forge_target=forge_target)
    return create_issue(title, body, forge_target=forge_target)


def comment(number: int, body: str, *, forge_target: ForgeTarget) -> bool:
    try:
        return active_forge().comment_on_issue(number, body, forge_target=forge_target).ok
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("unblock_issue: comment on #%s raised %s", number, exc)
        return False


__all__ = [
    "LABEL",
    "IssueLookup",
    "comment",
    "create_issue",
    "ensure_label",
    "ensure_shape_issue",
    "find_open_issue",
    "issue_url",
    "shape_issue_title",
]
