from __future__ import annotations

from alan.prompt_templates import (
    DIFF_FINDINGS_CRITICAL_MARKER,
    REQUIRED_VERIFICATION_MARKERS,
    VERDICT_MARKER,
)
from alan.teams._verdict import (
    DIFF_FINDINGS_CRITICAL_PREFIX,
    VERDICT_PREFIX,
)


def test_verdict_marker_matches_verification_module() -> None:
    assert VERDICT_MARKER == VERDICT_PREFIX


def test_diff_findings_critical_marker_matches_verification_module() -> None:
    assert DIFF_FINDINGS_CRITICAL_MARKER == DIFF_FINDINGS_CRITICAL_PREFIX


def test_required_verification_markers_matches_both_verification_module_prefixes() -> None:
    assert REQUIRED_VERIFICATION_MARKERS == {VERDICT_PREFIX, DIFF_FINDINGS_CRITICAL_PREFIX}
