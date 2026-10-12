from __future__ import annotations

from dataclasses import dataclass

from alan.engine_config import ForgeTarget
from alan.handover import Handover
from alan.teams import _pr_sonar


@dataclass(frozen=True)
class SonarFindings:
    annotations: tuple[_pr_sonar.SonarAnnotation, ...]
    authoritative: bool


NO_SONAR_FINDINGS = SonarFindings(annotations=(), authoritative=False)

_AUTHORITATIVE_GUIDANCE = (
    "Each finding below is a SonarCloud rule violation flagged on the PR diff "
    "(not a security hotspot). Fix the code to satisfy the rule; the next push "
    "triggers a fresh Sonar analysis which clears the gate automatically — no "
    "resolve script needed, Sonar tracks findings by code identity, not thread ID."
)

_ADVISORY_GUIDANCE = (
    "These findings are advisory: SonarCloud never blocks this PR's merge, and "
    "nothing waits for it to go green. Treat each one as a suggestion, ranked "
    "below the judge review, the PR threads and any failing required check. Fix "
    "a finding only when it points at a real defect or the fix is small and "
    "inside this PR's scope; otherwise leave it and name it in your summary with "
    "a one-line reason. A security finding (injection, path traversal, request "
    "forgery, an unsafe regular expression, logging user-controlled data, or "
    "anything else SonarCloud reports as a vulnerability) always counts: fix it, "
    "or decline it in your summary with the specific reason it is safe here. "
    "Never add a suppression (NOSONAR, an issue acceptance, a Sonar exclusion) "
    "to make a finding go away."
)


def fetch_sonar_findings(
    prior: Handover | None, *, forge_target: ForgeTarget,
) -> SonarFindings:
    if prior is None or prior.pr_number is None:
        return NO_SONAR_FINDINGS
    return SonarFindings(
        annotations=_pr_sonar.list_sonarcloud_annotations(
            prior.pr_number, forge_target=forge_target
        ),
        authoritative=_pr_sonar.SONAR_CHECK_NAME in forge_target.authoritative_checks,
    )


def sonar_findings_block(prior: Handover, findings: SonarFindings) -> str:
    guidance = _AUTHORITATIVE_GUIDANCE if findings.authoritative else _ADVISORY_GUIDANCE
    lines = [
        f"\n\n## SonarCloud findings on PR #{prior.pr_number} "
        f"(iteration {prior.iteration})",
        guidance,
    ]
    for idx, ann in enumerate(findings.annotations, start=1):
        parts = [ann.title]
        if ann.rule_url:
            parts.append(ann.rule_url)
        lines.append(
            f"\n### Finding {idx} — {ann.path}:{ann.line} [SonarCloud]\n"
            + "\n".join(parts)
        )
    return "\n".join(lines)
