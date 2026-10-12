from __future__ import annotations

import pytest
from pydantic import ValidationError

from alan.repo_accent import (
    ACCENT_COLORS,
    ACCENT_INTENSITIES,
    AccentChoice,
)


def test_accent_colors_is_exactly_the_curated_sixteen() -> None:
    assert ACCENT_COLORS == (
        "iris", "violet", "plum", "orchid", "magenta", "rose", "ruby", "crimson",
        "orange", "amber", "tan", "green", "teal", "blue", "slate", "bone",
    )
    assert len(set(ACCENT_COLORS)) == 16


def test_accent_colors_are_bare_lowercase_names() -> None:
    for color in ACCENT_COLORS:
        assert color.isalpha()
        assert color.islower()
    assert "lavender" not in ACCENT_COLORS


def test_accent_intensities_is_the_named_ladder() -> None:
    assert ACCENT_INTENSITIES == ("soft", "standard", "bold")


def test_accent_choice_defaults_to_no_color_and_standard_intensity() -> None:
    choice = AccentChoice()
    assert choice.color is None
    assert choice.intensity == "standard"


@pytest.mark.parametrize("color", ACCENT_COLORS)
def test_accent_choice_accepts_every_curated_color(color: str) -> None:
    choice = AccentChoice(color=color)  # type: ignore[arg-type]
    assert choice.color == color


@pytest.mark.parametrize("intensity", ["soft", "standard", "bold"])
def test_accent_choice_accepts_every_named_intensity(intensity: str) -> None:
    choice = AccentChoice(color="iris", intensity=intensity)  # type: ignore[arg-type]
    assert choice.intensity == intensity


def test_accent_choice_rejects_unknown_color() -> None:
    with pytest.raises(ValidationError):
        AccentChoice(color="lavender")  # type: ignore[arg-type]


def test_accent_choice_rejects_unknown_intensity() -> None:
    with pytest.raises(ValidationError):
        AccentChoice(color="iris", intensity="loud")  # type: ignore[arg-type]


def test_accent_choice_rejects_bounded_integer_intensity() -> None:
    with pytest.raises(ValidationError):
        AccentChoice(color="iris", intensity=2)  # type: ignore[arg-type]


def test_accent_choice_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        AccentChoice(color="iris", extra="nope")  # type: ignore[call-arg]


def test_accent_choice_is_frozen() -> None:
    choice = AccentChoice(color="iris")
    with pytest.raises(ValidationError):
        choice.color = "violet"  # type: ignore[misc]
