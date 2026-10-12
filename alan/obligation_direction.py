from __future__ import annotations

from typing import Literal

ObligationDirection = Literal["must", "must_not"]

_NEGATION_MARKERS: tuple[str, ...] = (
    "must not",
    "must never",
    "should not",
    "shall not",
    "cannot ",
    "can not",
    "never ",
    "do not ",
    "don't ",
    "won't ",
    "will not",
    "avoid ",
    "not allowed",
    "no longer",
    " no ",
)


def classify(text: str) -> ObligationDirection:
    lowered = f" {text.strip().lower()} "
    if any(marker in lowered for marker in _NEGATION_MARKERS):
        return "must_not"
    return "must"


def direction_preserved(original_text: str, candidate_text: str) -> bool:
    return classify(original_text) == classify(candidate_text)


__all__ = ["ObligationDirection", "classify", "direction_preserved"]
