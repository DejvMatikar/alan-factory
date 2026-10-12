from __future__ import annotations

import re

from alan.handover import OpenQuestion, QuestionClass

BLOCKING_MARK = "[BLOCKING]"

QUESTION_CLASS: QuestionClass = "fix_escalation"

HAND_RESOLVE_OPTION = (
    "Resolve the declared blocker by hand, then re-dispatch implementation_fix "
    "to resume the fix now that the blocker is cleared."
)
BLOCKER_UNFOUNDED_OPTION = (
    "The blocker does not hold — re-dispatch verification directly without "
    "further implementation_fix work."
)
WORKER_STEP_OPTION = (
    "The declared step is the worker's to do: widen scope to the files it needs and do it."
)

DECLARED_BLOCKER_GUIDANCE = (
    "DECLARED BLOCKERS — if an item in your ## Open questions section must "
    "stop this workspace until a human decides (not a routine caveat), start "
    f"that bullet with the literal mark `{BLOCKING_MARK}`: "
    f"`- {BLOCKING_MARK} <one-line description of what needs a human "
    "decision>`. The host parses only bullets carrying this mark from the "
    "Open questions section and routes them to a human before the workspace "
    "continues; every other bullet there is read later but does not stop "
    "the workspace. Use the mark sparingly — only for a genuine blocker.\n\n"
)

_OPEN_QUESTIONS_HEADING_RE = re.compile(
    r"^##[ \t]+Open[ \t]+questions\b[^\n]*\n", re.IGNORECASE | re.MULTILINE
)
_NEXT_MARKDOWN_H2_RE = re.compile(r"\n##[ \t]")
_BULLET_RE = re.compile(r"^-(.*)$", re.MULTILINE)
_BULLET_MAX_CHARS = 300
_MAX_BULLETS = 5
_NOOP_TOKEN_RE = re.compile(r"(none|n/?a|no|nothing|-)[.!]?", re.IGNORECASE)


def _is_noop_blocker_text(text: str) -> bool:
    return _NOOP_TOKEN_RE.fullmatch(text.strip()) is not None


def _open_questions_section(synthesis: str) -> str:
    match = _OPEN_QUESTIONS_HEADING_RE.search(synthesis)
    if match is None:
        return ""
    start = match.end()
    next_match = _NEXT_MARKDOWN_H2_RE.search(synthesis, start)
    end = next_match.start() if next_match else len(synthesis)
    return synthesis[start:end]


def _marked_bullets(section: str) -> list[str]:
    bullets: list[str] = []
    for match in _BULLET_RE.finditer(section):
        text = match.group(1).strip()
        if not text.startswith(BLOCKING_MARK):
            continue
        remainder = text[len(BLOCKING_MARK) :].strip()
        if not remainder or _is_noop_blocker_text(remainder):
            continue
        bullets.append(remainder[:_BULLET_MAX_CHARS])
        if len(bullets) >= _MAX_BULLETS:
            break
    return bullets


def _aggregated_question_text(bullets: list[str]) -> str:
    if len(bullets) == 1:
        return bullets[0]
    return "\n".join(f"- {b}" for b in bullets)


def _declared_blocker_question(inv_id: str, bullets: list[str]) -> OpenQuestion:
    return OpenQuestion(
        id=f"declared_blocker_{inv_id[:8]}",
        question=_aggregated_question_text(bullets),
        proposed_answer_space=[HAND_RESOLVE_OPTION, BLOCKER_UNFOUNDED_OPTION, WORKER_STEP_OPTION],
        recommended=HAND_RESOLVE_OPTION,
        question_class=QUESTION_CLASS,
        resolver_role="human",
        blocking=True,
        option_resolvers={
            HAND_RESOLVE_OPTION: "implementation_fix",
            BLOCKER_UNFOUNDED_OPTION: "verification",
            WORKER_STEP_OPTION: "implementation_fix",
        },
    )


def parse_declared_blocker(synthesis: str, inv_id: str) -> list[OpenQuestion]:
    section = _open_questions_section(synthesis)
    if not section:
        return []
    bullets = _marked_bullets(section)
    if not bullets:
        return []
    return [_declared_blocker_question(inv_id, bullets)]


__all__ = [
    "BLOCKER_UNFOUNDED_OPTION",
    "BLOCKING_MARK",
    "DECLARED_BLOCKER_GUIDANCE",
    "HAND_RESOLVE_OPTION",
    "QUESTION_CLASS",
    "WORKER_STEP_OPTION",
    "parse_declared_blocker",
]
