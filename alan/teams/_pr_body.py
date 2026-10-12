from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

from alan import prompt_templates
from alan.engine_config import EngineConfig, ForgeTarget
from alan.forge import active_forge
from alan.teams import _forge_errors, _pr_audit_log, _pr_body_block
from alan.teams._pr_forge import GH_TIMEOUT_S

PR_BODY_MAX_CHARS = 60000

_PREAMBLE_RE = re.compile(r"\AStopping here per instructions[^\n]*\n?(?:[ \t]*\n)*")
_HEADING_RE = re.compile(r"(?m)^#{1,6}[ \t]")
_TRUNCATION_MARKER = (
    "\n\n*(truncated — this body exceeded the PR-body size budget; see the "
    "branch's commit history for the full record.)*"
)


def _strip_preamble(text: str) -> str:
    match = _PREAMBLE_RE.match(text)
    if match is None:
        return text
    remainder = text[match.end():]
    if not remainder.strip():
        return text
    return remainder


def _heading_cut(text: str, budget: int) -> str | None:
    matches = list(_HEADING_RE.finditer(text[:budget]))
    if not matches or matches[-1].start() == 0:
        return None
    return text[:matches[-1].start()].rstrip()


def _word_cut(text: str, budget: int) -> str | None:
    window = text[:budget]
    if text[budget].isspace():
        return window.rstrip() or None
    boundary = max(window.rfind(" "), window.rfind("\n"), window.rfind("\t"))
    if boundary <= 0:
        return None
    return text[:boundary].rstrip()


def _truncate_with_marker(text: str, *, reserve: int = 0) -> str:
    limit = max(PR_BODY_MAX_CHARS - reserve, 0)
    if len(text) <= limit:
        return text
    budget = max(limit - len(_TRUNCATION_MARKER), 0)
    cut = _heading_cut(text, budget) or _word_cut(text, budget) or text[:budget]
    return (cut + _TRUNCATION_MARKER)[:limit]


def build_pr_body(text: str, *, reserve: int = 0) -> str:
    if not text:
        return text
    return _truncate_with_marker(_strip_preamble(text), reserve=reserve)


PR_BODY_BLOCK_INSTRUCTIONS = (
    "PULL REQUEST BODY — the text you write in a ```pr_body block becomes "
    "the PUBLIC pull request body on GitHub: the permanent record that "
    "squash-merge carries into main's commit message. This is a DIFFERENT "
    "artifact with a DIFFERENT audience from your ## Summary / ## Decisions "
    "synthesis above, which only the next internal stage reads — write "
    "both, they serve different readers. Because this codebase forbids "
    "comments in application code, the PR body is where the \"why\" behind "
    "this change has to live for a human reviewer who will never see your "
    "synthesis.\n\n"
    "To author it, emit a fenced block anywhere in your final response "
    "using a backtick run one character longer than the longest backtick "
    "run appearing inside the body text itself (plain ``` is enough unless "
    "your body itself contains a fenced code block):\n"
    "```pr_body\n"
    "<the full PR body, in GitHub-flavored markdown>\n"
    "```\n"
    f"Budget: {PR_BODY_MAX_CHARS} characters; content over budget is "
    "truncated with a marker, so lead with the most important context. "
    "Omitting this block entirely keeps today's default behaviour (a body "
    "derived from your synthesis) — you are never required to supply one. "
    "If you are revising an already-open pull request (implementation_fix), "
    "a supplied block REPLACES the live PR body wholesale — it is not a "
    "patch or an addendum, so include everything the body should say, not "
    "just what changed this round."
)

def render_with_pr_body_instructions(
    cfg: EngineConfig,
    team: str,
    role: str,
    base: str,
    *,
    environment_block: str,
    success_block: str,
    repo_guidance_block: str,
) -> str:
    return prompt_templates.render_prompt(
        cfg,
        team,
        role,
        base,
        environment_block=environment_block,
        success_block=success_block,
        repo_guidance_block=repo_guidance_block,
    ) + "\n\n" + PR_BODY_BLOCK_INSTRUCTIONS


def extract_authored_body(synthesis: str) -> str | None:
    result = _pr_body_block.extract_pr_body_block(synthesis)
    return result.body if result is not None else None


def fetch_pr_body(
    pr_number: int, *, forge_target: ForgeTarget,
) -> str | None:
    try:
        result = active_forge().read_pr(
            pr_number, ("body",), forge_target=forge_target, timeout=GH_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if not result.ok:
        return None
    body = result.fields.get("body")
    return body if isinstance(body, str) else None


@dataclass(frozen=True)
class BodyAmendResult:
    ok: bool
    previous_body: str | None
    new_body: str
    error: str | None = None


def amend_pr_body(
    pr_number: int,
    new_body: str,
    *,
    forge_target: ForgeTarget,
    task_id: str,
    team: str,
) -> BodyAmendResult:
    previous = fetch_pr_body(pr_number, forge_target=forge_target)
    try:
        result = active_forge().update_pr_body(pr_number, new_body, forge_target=forge_target)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise _forge_errors.ForgeTransientError(f"gh pr body amend raised {exc}") from exc
    if not result.ok:
        _forge_errors.raise_for_infra(
            "gh pr body amend", returncode=result.returncode, stderr=result.stderr,
        )
        return BodyAmendResult(
            ok=False, previous_body=previous, new_body=new_body, error=result.stderr[:500],
        )
    _pr_audit_log.append_audit({
        "task_id": task_id,
        "action": "pr_body_amend_ok",
        "pr_number": pr_number,
        "previous_body": previous,
        "new_body": new_body,
        "team": team,
    })
    return BodyAmendResult(ok=True, previous_body=previous, new_body=new_body)
