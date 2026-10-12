from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SiteKind = Literal["fleet_reachable", "interactive_reachable", "unreachable_author", "mixed"]

DECLARATION_MESSAGE = (
    "undeclared host-parsed block found in alan/ source "
    "— declare this host-parsed block in tests/host_parsed_blocks.py"
)


@dataclass(frozen=True)
class HostParsedBlock:
    name: str
    site: str
    kind: SiteKind
    discovery: str | None


HOST_PARSED_BLOCKS: tuple[HostParsedBlock, ...] = (
    HostParsedBlock(
        name="decisions.yaml",
        site="alan/teams/_static_pipeline.py (architecture.py, implementation_plan.py)",
        kind="fleet_reachable",
        discovery=r"```yaml\s+decisions\.yaml",
    ),
    HostParsedBlock(
        name="decomposition.yaml",
        site="alan/teams/decomposer.py",
        kind="fleet_reachable",
        discovery=r"```yaml\s+decomposition\.yaml",
    ),
    HostParsedBlock(
        name="test_scenarios.yaml",
        site=(
            "alan/teams/implementation_plan.py (test_scenarios: key nested in "
            "decisions.yaml); alan/teams/_fix_non_actionable.py via "
            "alan/teams/_fix_forward.py (named fence, heading, and the "
            "salvage-only bare-yaml fallback)"
        ),
        kind="mixed",
        discovery=r"```yaml\s+test_scenarios\.yaml",
    ),
    HostParsedBlock(
        name="brief_refine.yaml",
        site="alan/teams/brief_refine.py",
        kind="fleet_reachable",
        discovery=r"```yaml\s+brief_refine\.yaml",
    ),
    HostParsedBlock(
        name="EXTRACT_DISPATCH_YAML",
        site="alan/routes/_chat_router.py (interactive dispatch proposal flow)",
        kind="interactive_reachable",
        discovery=r"EXTRACT_DISPATCH_YAML",
    ),
    HostParsedBlock(
        name="scenario_rebind.yaml",
        site="alan/_dispatcher_scenario_rebind.py",
        kind="unreachable_author",
        discovery=r"```yaml\s+scenario_rebind\.yaml",
    ),
    HostParsedBlock(
        name="non_actionable_feedback.yaml",
        site=(
            "alan/teams/implementation_fix.py (emits and parses); "
            "alan/teams/_fix_non_actionable.py (audits)"
        ),
        kind="unreachable_author",
        discovery=r"```yaml\s+non_actionable_feedback\.yaml",
    ),
    HostParsedBlock(
        name="INLINE_FINDINGS_YAML",
        site="alan/teams/_verification_review_post.py",
        kind="unreachable_author",
        discovery=r"INLINE_FINDINGS_YAML",
    ),
    HostParsedBlock(
        name="RCA_DRAFT_YAML",
        site="alan/investigator.py",
        kind="unreachable_author",
        discovery=r"RCA_DRAFT_YAML",
    ),
    HostParsedBlock(
        name="copilot_resolution.yaml",
        site=(
            "alan/teams/implementation_fix.py (emits); "
            "alan/teams/_copilot_resolution_block.py (parses); "
            "alan/teams/_copilot_resolution.py (executes)"
        ),
        kind="unreachable_author",
        discovery=r"```yaml\s+copilot_resolution\.yaml",
    ),
)


__all__ = ["DECLARATION_MESSAGE", "HOST_PARSED_BLOCKS", "HostParsedBlock", "SiteKind"]
