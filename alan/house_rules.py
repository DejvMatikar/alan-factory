from __future__ import annotations

from pydantic import BaseModel, Field

from alan.obligation_direction import ObligationDirection

HOUSE_RULES_VERSION = 2


class HouseRule(BaseModel, frozen=True):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    direction: ObligationDirection


HOUSE_RULES: tuple[HouseRule, ...] = (
    HouseRule(
        id="house-tests-required",
        text=(
            "Changed or new behavior must be covered by automated tests "
            "before the work is considered done."
        ),
        direction="must",
    ),
    HouseRule(
        id="house-no-gate-downgrade",
        text=(
            "A change must not downgrade, skip, or bypass an existing "
            "quality gate in order to land."
        ),
        direction="must_not",
    ),
    HouseRule(
        id="house-no-secrets",
        text=(
            "A change must not introduce hardcoded secrets, credentials, "
            "or other sensitive material."
        ),
        direction="must_not",
    ),
    HouseRule(
        id="house-no-ratchet-widening",
        text=(
            "A change must not widen a tightening-only ratchet baseline "
            "in the pull request that ratchet is blocking."
        ),
        direction="must_not",
    ),
)

__all__ = ["HOUSE_RULES", "HOUSE_RULES_VERSION", "HouseRule"]
