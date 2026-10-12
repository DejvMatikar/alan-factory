from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

AccentColor = Literal[
    "iris", "violet", "plum", "orchid", "magenta", "rose", "ruby", "crimson",
    "orange", "amber", "tan", "green", "teal", "blue", "slate", "bone",
]

ACCENT_COLORS: tuple[AccentColor, ...] = (
    "iris", "violet", "plum", "orchid", "magenta", "rose", "ruby", "crimson",
    "orange", "amber", "tan", "green", "teal", "blue", "slate", "bone",
)

AccentIntensity = Literal["soft", "standard", "bold"]

ACCENT_INTENSITIES: tuple[AccentIntensity, ...] = ("soft", "standard", "bold")


class AccentChoice(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")

    color: AccentColor | None = None
    intensity: AccentIntensity = "standard"
