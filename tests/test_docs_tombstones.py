from __future__ import annotations

from pathlib import Path

_HEALTH_MD = Path(__file__).parent.parent / "docs" / "architecture" / "health.md"


def test_health_md_contains_remediation_verify_anti_cheat_invariant() -> None:
    text = _HEALTH_MD.read_text()
    assert "## Tombstone — remediation_verify anti-cheat invariant (revived by P3.4)" in text
    assert (
        "DIFF_FINDINGS_CRITICAL: <n>  (n = count of cheat patterns, 0 if none)" in text
    )
    assert "VERDICT: APPROVE  (only when n=0)" in text
    assert "(use VERDICT: REQUEST_CHANGES when n>0)" in text
    assert (
        "if verdict is None or critical_count > 0:\n"
        '        return "REQUEST_CHANGES"' in text
    )
    assert "P3.4" in text
