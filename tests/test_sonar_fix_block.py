from __future__ import annotations

from datetime import UTC, datetime

from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.handover import Handover
from alan.teams import _pr_sonar
from alan.teams import _sonar_fix_block as block_mod


def _handover(pr_number: int | None) -> Handover:
    return Handover(
        handover_id="handover_verif_001",
        handover_type="forward",
        task_id="sonar-task",
        from_team="verification",
        from_invocation_id="inv_002",
        to_team_suggestions=["implementation_fix"],
        iteration=2,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="## Verdict\nVERDICT: APPROVE",
        pr_number=pr_number,
    )


def _annotation(path: str, line: int, title: str, rule_url: str) -> _pr_sonar.SonarAnnotation:
    return _pr_sonar.SonarAnnotation(path=path, line=line, title=title, rule_url=rule_url)


def test_fetch_without_a_prior_handover_returns_no_findings() -> None:
    assert block_mod.fetch_sonar_findings(None, forge_target=DEFAULT_FORGE_TARGET) == (
        block_mod.NO_SONAR_FINDINGS
    )


def test_fetch_without_a_pr_number_returns_no_findings() -> None:
    findings = block_mod.fetch_sonar_findings(_handover(None), forge_target=DEFAULT_FORGE_TARGET)
    assert findings == block_mod.NO_SONAR_FINDINGS


def test_block_renders_every_finding_with_its_location_title_and_rule_url() -> None:
    findings = block_mod.SonarFindings(
        annotations=(
            _annotation("src/f.ts", 12, "My Title", "https://sonarcloud.io/rules?q=X"),
            _annotation("src/g.ts", 7, "Other Title", ""),
        ),
        authoritative=False,
    )
    block = block_mod.sonar_findings_block(_handover(188), findings)
    assert "## SonarCloud findings on PR #188 (iteration 2)" in block
    assert (
        "### Finding 1 — src/f.ts:12 [SonarCloud]\nMy Title\nhttps://sonarcloud.io/rules?q=X"
        in block
    )
    assert "### Finding 2 — src/g.ts:7 [SonarCloud]\nOther Title" in block
    assert block.endswith("Other Title")


def test_advisory_block_lets_the_fixer_decline_and_forbids_suppressions() -> None:
    findings = block_mod.SonarFindings(
        annotations=(_annotation("src/f.ts", 1, "T", ""),), authoritative=False
    )
    block = block_mod.sonar_findings_block(_handover(7), findings)
    assert "advisory" in block
    assert "never blocks this PR's merge" in block
    assert "one-line reason" in block
    assert "Never add a suppression" in block
    assert "A security finding" in block
    assert "decline it in your summary with the specific reason it is safe here" in block
    assert "clears the gate" not in block


def test_authoritative_block_keeps_the_must_fix_wording() -> None:
    findings = block_mod.SonarFindings(
        annotations=(_annotation("src/f.ts", 1, "T", ""),), authoritative=True
    )
    block = block_mod.sonar_findings_block(_handover(7), findings)
    assert "Fix the code to satisfy the rule" in block
    assert "clears the gate" in block
    assert "advisory" not in block
