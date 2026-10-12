from __future__ import annotations

import pytest

from alan.obligation_direction import classify, direction_preserved


@pytest.mark.parametrize(
    "text",
    [
        "The endpoint must return 200 on success.",
        "Add a new button to the toolbar.",
        "Users can log in with email.",
    ],
)
def test_classify_positive_phrasing_is_must(text: str) -> None:
    assert classify(text) == "must"


@pytest.mark.parametrize(
    "text",
    [
        "The change must not downgrade an existing gate.",
        "A worker should not delete a prior requirement.",
        "The service shall not expose secrets in logs.",
        "Do not commit directly to main.",
        "This can not be skipped.",
        "We will not ship without tests.",
        "Never bypass the acceptance gate.",
        "Don't widen the allowlist.",
        "Won't accept an empty payload.",
        "Avoid hardcoding credentials.",
        "This behavior is no longer allowed.",
        "That approach is not allowed here.",
        "No secrets may be logged.",
    ],
)
def test_classify_negative_phrasing_is_must_not(text: str) -> None:
    assert classify(text) == "must_not"


def test_classify_standalone_no_does_not_false_positive_on_substring_words() -> None:
    assert classify("Known issues are tracked separately.") == "must"


def test_classify_is_case_insensitive() -> None:
    assert classify("MUST NOT reverse this clause.") == "must_not"


def test_direction_preserved_true_for_same_direction() -> None:
    assert direction_preserved(
        "The endpoint must return 200.", "The endpoint must always return 200 on success."
    )


def test_direction_preserved_false_when_negation_introduced() -> None:
    assert not direction_preserved(
        "The endpoint must return 200.", "The endpoint must not return 200."
    )


def test_direction_preserved_false_when_negation_removed() -> None:
    assert not direction_preserved(
        "The service must not log secrets.", "The service must log secrets."
    )


def test_direction_preserved_false_for_standalone_no_reversal() -> None:
    assert not direction_preserved(
        "No secrets may be logged.", "Secrets may be logged."
    )
