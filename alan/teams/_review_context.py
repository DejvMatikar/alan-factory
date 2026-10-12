from __future__ import annotations

from pathlib import Path

from alan.engine_config import ForgeTarget
from alan.teams import (
    _fence,
    _pr_audit_log,
    _pr_body,
    _pr_flake_ledger,
    _ratchet_direction,
    _verification_prompts,
)
from alan.teams._base import UNTRUSTED_CONTENT_NOTICE
from alan.teams._pr_forge import forge_target_for_ws
from alan.workspace import Workspace


def _audit_ratchet_loosening(
    pr_number: int, task_id: str, findings: tuple[_ratchet_direction.Loosening, ...]
) -> None:
    _pr_audit_log.append_audit({
        "task_id": task_id,
        "action": "ratchet_loosening_detected",
        "pr_number": pr_number,
        "paths": [f.path for f in findings],
    })


_PR_EVIDENCE_HEADER = "## PR EVIDENCE (host-supplied, authoritative)"
_PR_EVIDENCE_PREAMBLE = (
    "The pull request's live description and diff below were fetched by "
    "the host at review time — this is your diff of record. Do not "
    "attempt to fetch either yourself (no `gh pr diff`, no `gh pr "
    "view`, no other forge read); the worker environment carries no "
    "forge credential to do so with."
)
_DIFF_UNAVAILABLE_LINE = (
    "PR diff: unavailable from the host fetch. A missing or truncated "
    "diff is never itself a finding — do not attempt to recover it "
    "yourself (no `git fetch`, no `git diff` against a remote ref: this "
    "checkout carries no forge credential and the worker environment "
    "grants no command that mutates or refreshes it). Note the "
    "limitation and judge correctness from the PR description above and "
    "the implementation handover instead."
)


def _pr_description_lines(body: str | None) -> list[str]:
    text = body if body else "(empty)"
    fenced = _fence.fence(text, info="pr-description")
    return ["### PR description", UNTRUSTED_CONTENT_NOTICE, fenced]


def _pr_diff_lines(diff: str | None) -> list[str]:
    if diff is None:
        return ["### PR diff", _DIFF_UNAVAILABLE_LINE]
    return ["### PR diff", UNTRUSTED_CONTENT_NOTICE, _fence.fence(diff, info="diff")]


def pr_evidence_block(pr_number: int, *, forge_target: ForgeTarget) -> str:
    body = _pr_body.fetch_pr_body(pr_number, forge_target=forge_target)
    diff = _pr_flake_ledger.fetch_pr_diff_patch(pr_number, forge_target=forge_target)
    lines = [_PR_EVIDENCE_HEADER, "", _PR_EVIDENCE_PREAMBLE, ""]
    lines.extend(_pr_description_lines(body))
    lines.append("")
    lines.extend(_pr_diff_lines(diff))
    return "\n".join(lines)


def checkout_has_ui_surface(checkout: Path | None) -> bool:
    if checkout is None:
        return False
    return (checkout / "alan" / "web").is_dir() and (
        checkout / "docs" / "DESIGN_SYSTEM.md"
    ).is_file()


def ui_blocks(checkout: Path | None) -> str:
    if not checkout_has_ui_surface(checkout):
        return ""
    return _verification_prompts.UI_CONSISTENCY_BLOCK + _verification_prompts.RENDERED_SCREEN_BLOCK


def targeted_test_scope_block(checkout: Path | None) -> str:
    if checkout_has_ui_surface(checkout):
        return _verification_prompts.TARGETED_TEST_SCOPE_BLOCK
    return _verification_prompts.TARGETED_TEST_SCOPE_NEUTRAL


def diff_reviewer_prefix(
    pr_number: int, workspace: Workspace, task_id: str, *, checkout: Path,
) -> str | None:
    findings = _ratchet_direction.detect_ratchet_loosening(checkout)
    if findings:
        _audit_ratchet_loosening(pr_number, task_id, findings)
    ratchet_block = _ratchet_direction.render_ratchet_block(findings)
    forge_target = forge_target_for_ws(workspace)
    evidence_block = pr_evidence_block(pr_number, forge_target=forge_target)
    ui_block = ui_blocks(checkout) or None
    scope_block = targeted_test_scope_block(checkout)
    parts = [
        p for p in (ratchet_block, evidence_block, ui_block, scope_block)
        if p is not None
    ]
    return "\n\n".join(parts) if parts else None


_MANIFEST_DEVIATION_HEADER = "## Manifest deviation (advisory)"
_MANIFEST_DEVIATION_PREAMBLE = (
    "This subtask's diff touches files outside its declared manifest. This "
    "is advisory only, not a mechanical block — weigh whether it is "
    "genuine scope-creep (may justify REQUEST_CHANGES) or a legitimate "
    "addition (e.g. a new test, a shared helper) that does not warrant "
    "rejection on its own."
)


def _format_manifest_deviation_block(  # pyright: ignore[reportUnusedFunction]
    out_of_manifest: list[str],
) -> str:
    lines = [_MANIFEST_DEVIATION_HEADER, _MANIFEST_DEVIATION_PREAMBLE, ""]
    lines.extend(f"- {f}" for f in out_of_manifest)
    return "\n".join(lines)
