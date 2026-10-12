from __future__ import annotations

import re
from hashlib import sha256

from alan.handover import Handover, OpenQuestion
from alan.teams import _pr_forge, _pr_state_paths
from alan.workspace import Workspace

CAPABILITY_BOUNDARY_PREFIX = "NO_FLEET_ROLE_CAN_FIX:"
CAPABILITY_BOUNDARY_QUESTION_PREFIX = "verif-capability-boundary-"
NO_FLEET_ROLE_PHRASE = "unresolvable by any fleet role"

_CAPABILITY_BOUNDARY_RE = re.compile(
    rf"^{re.escape(CAPABILITY_BOUNDARY_PREFIX)}[ \t]*(\S.*)$", re.MULTILINE
)


def declared_capability_boundary(judge_output: str) -> str | None:
    match = _CAPABILITY_BOUNDARY_RE.search(judge_output)
    if match is None:
        return None
    return match.group(1).strip()


def is_capability_boundary_question(question: OpenQuestion) -> bool:
    return question.id.startswith(CAPABILITY_BOUNDARY_QUESTION_PREFIX)


def capability_boundary_question_text(reason: str) -> str:
    return f"Verifier declared this finding {NO_FLEET_ROLE_PHRASE}: {reason}"


def _waiver_key(question_text: str) -> str:
    return sha256(question_text.encode()).hexdigest()[:16]


def capability_boundary_waiver_key_for_question(question: OpenQuestion) -> str:
    return _waiver_key(question.question)


def capability_boundary_waived(
    workspace: Workspace, prior: Handover | None, reason: str,
) -> bool:
    if prior is None or prior.pr_number is None:
        return False
    forge_target = _pr_forge.forge_target_for_ws(workspace)
    key = _waiver_key(capability_boundary_question_text(reason))
    return _pr_state_paths.capability_boundary_waiver_recorded(
        prior.pr_number, key, forge_target,
    )


__all__ = [
    "CAPABILITY_BOUNDARY_PREFIX",
    "CAPABILITY_BOUNDARY_QUESTION_PREFIX",
    "NO_FLEET_ROLE_PHRASE",
    "capability_boundary_question_text",
    "capability_boundary_waived",
    "capability_boundary_waiver_key_for_question",
    "declared_capability_boundary",
    "is_capability_boundary_question",
]
