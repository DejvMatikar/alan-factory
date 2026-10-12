from __future__ import annotations

import alan.teams._inline_findings as inline_mod
import alan.teams._pr_post as _pr_post

_DEFAULT_JUDGE_OUTPUT = (
    "## Verdict\n\n"
    "VERDICT: APPROVE\n"
    "DIFF_FINDINGS_CRITICAL: 0\n\n"
    "## Rationale\nLooks good."
)

_INLINE_JUDGE_OUTPUT = (
    "## Verdict\n\n"
    "VERDICT: REQUEST_CHANGES\n"
    "DIFF_FINDINGS_CRITICAL: 2\n\n"
    "## Findings\n"
    "see inline comments\n\n"
    "## INLINE_FINDINGS_YAML\n"
    "```yaml\n"
    "- path: alan/foo.py\n"
    "  line: 10\n"
    "  body: |\n"
    "    SQL injection in query()\n"
    "- path: alan/bar.py\n"
    "  line: 20\n"
    "  body: |\n"
    "    Missing null check\n"
    "```\n"
)


def test_parse_inline_findings_extracts_entries() -> None:
    findings = inline_mod.parse_inline_findings(_INLINE_JUDGE_OUTPUT)
    assert findings == [
        _pr_post.InlineFinding(path="alan/foo.py", line=10, body="SQL injection in query()"),
        _pr_post.InlineFinding(path="alan/bar.py", line=20, body="Missing null check"),
    ]


def test_strip_inline_findings_block_removes_yaml() -> None:
    body = inline_mod.strip_inline_findings_block(_INLINE_JUDGE_OUTPUT)
    assert "INLINE_FINDINGS_YAML" not in body
    assert "```" not in body
    assert "VERDICT: REQUEST_CHANGES" in body
    assert body.endswith("see inline comments\n")


def test_strip_inline_findings_block_no_block_returns_input() -> None:
    body = inline_mod.strip_inline_findings_block(_DEFAULT_JUDGE_OUTPUT)
    assert body.rstrip() == _DEFAULT_JUDGE_OUTPUT.rstrip()


_CONFIDENCE_FOOTER = (
    "---\n"
    "AGENT_STATUS: complete\n"
    "CONFIDENCE: 0.9\n"
    "COMPLETENESS: 1.0\n"
    "GAPS: none\n"
    "---\n"
)


def test_strip_confidence_footer_removes_trailing_footer() -> None:
    body = (
        "## Verdict\n\nVERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n\n"
        "## Rationale\n\nLooks good.\n\n" + _CONFIDENCE_FOOTER
    )
    out = inline_mod.strip_confidence_footer(body)
    assert "AGENT_STATUS" not in out
    assert "CONFIDENCE" not in out
    assert "VERDICT: APPROVE" in out
    assert "Looks good." in out


def test_strip_confidence_footer_noop_when_trailing_block_lacks_markers() -> None:
    body = (
        "## Rationale\n\nSome prose.\n\n"
        "---\n"
        "FOO: bar\n"
        "BAZ: qux\n"
        "---\n"
    )
    assert inline_mod.strip_confidence_footer(body) == body


def test_strip_confidence_footer_noop_when_absent() -> None:
    body = (
        "## Rationale\n\n"
        "Some prose.\n\n"
        "---\n\n"
        "More prose after a divider.\n"
    )
    assert inline_mod.strip_confidence_footer(body) == body


def test_strip_confidence_footer_preserves_legit_rule_alongside_real_footer() -> None:
    body = (
        "## Verdict\n\nVERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n\n"
        "## Rationale\n\n"
        "Some context.\n\n"
        "---\n\n"
        "More rationale after a divider.\n\n" + _CONFIDENCE_FOOTER
    )
    out = inline_mod.strip_confidence_footer(body)
    assert "AGENT_STATUS" not in out
    assert "CONFIDENCE" not in out
    assert "More rationale after a divider." in out
    assert out.count("---") == 1


def test_strip_confidence_footer_order_independent() -> None:
    body = (
        "## Rationale\n\nLooks good.\n\n"
        "---\n"
        "CONFIDENCE: 0.9\n"
        "AGENT_STATUS: complete\n"
        "COMPLETENESS: 1.0\n"
        "GAPS: none\n"
        "---\n"
    )
    out = inline_mod.strip_confidence_footer(body)
    assert "AGENT_STATUS" not in out
    assert "CONFIDENCE" not in out
    assert "Looks good." in out


def test_strip_confidence_footer_composed_with_inline_findings_strip() -> None:
    body = _INLINE_JUDGE_OUTPUT + "\n" + _CONFIDENCE_FOOTER
    composed = inline_mod.strip_confidence_footer(
        inline_mod.strip_inline_findings_block(body)
    )
    assert "INLINE_FINDINGS_YAML" not in composed
    assert "AGENT_STATUS" not in composed
    assert "CONFIDENCE" not in composed
    assert "VERDICT: REQUEST_CHANGES" in composed


def test_parse_inline_findings_no_block() -> None:
    assert inline_mod.parse_inline_findings(_DEFAULT_JUDGE_OUTPUT) == []


def test_parse_inline_findings_malformed_yaml() -> None:
    bad = "## INLINE_FINDINGS_YAML\n```yaml\n- path: foo\n  line: : ::\n```\n"
    assert inline_mod.parse_inline_findings(bad) == []


def test_parse_inline_findings_non_list_payload() -> None:
    bad = "## INLINE_FINDINGS_YAML\n```yaml\npath: foo\nline: 5\n```\n"
    assert inline_mod.parse_inline_findings(bad) == []


def test_parse_inline_findings_skips_invalid_entries() -> None:
    text = (
        "## INLINE_FINDINGS_YAML\n```yaml\n"
        "- path: alan/ok.py\n  line: 7\n  body: real finding\n"
        "- not-a-dict\n"
        "- path: ''\n  line: 1\n  body: empty path\n"
        "- path: foo\n  line: 0\n  body: bad line\n"
        "- path: foo\n  line: 5\n  body: ''\n"
        "- path: foo\n  line: true\n  body: bool line\n"
        "- path: 5\n  line: 5\n  body: int path\n"
        "- path: foo\n  line: 5\n  body: 99\n"
        "```\n"
    )
    findings = inline_mod.parse_inline_findings(text)
    assert findings == [
        _pr_post.InlineFinding(path="alan/ok.py", line=7, body="real finding")
    ]
