from __future__ import annotations

import re
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, cast

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from alan.dep_sweep import AdvisoryPlan, PackageFix, Vulnerability, widen_requirement

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_SAFE_VERSION_RE = re.compile(r"^[A-Za-z0-9._+!-]{1,64}$")
_UNKNOWN_VERSION = "unknown"

LOCK_FILE = "uv.lock"
PYPROJECT_FILE = "pyproject.toml"
FIX_LOG_DOC = "docs/architecture/dep_sweep.md"
FIX_LOG_HEADING = "## Advisory fix log"

FORBIDDEN_ACTIONS: tuple[str, ...] = (
    "never add to or edit `ignore-vuln` or `[tool.pip-audit]` in pyproject.toml — an "
    "advisory with a fixed release is fixed, never suppressed",
    "never change, skip or weaken any CI gate, test, lint rule, coverage threshold or "
    "ratchet baseline to get green",
    f"touch no file beyond {LOCK_FILE}, {PYPROJECT_FILE} and {FIX_LOG_DOC}",
    "do not push branches or open PRs — that is the verification team's job",
)


@dataclass(frozen=True)
class _DirectRequirement:
    location: str
    requirement: str


def safe_token(value: str) -> str | None:
    return value if _SAFE_NAME_RE.match(value) else None


def _safe_version(value: str) -> str:
    return value if _SAFE_VERSION_RE.match(value) else _UNKNOWN_VERSION


def _sanitized_fix(fix: PackageFix) -> PackageFix | None:
    ids = tuple(i for i in fix.advisory_ids if safe_token(i) is not None)
    if safe_token(fix.package) is None or not ids or not _SAFE_VERSION_RE.match(fix.target_version):
        return None
    return PackageFix(fix.package, _safe_version(fix.installed_version), fix.target_version, ids)


def _sanitized_vuln(vuln: Vulnerability) -> Vulnerability | None:
    if safe_token(vuln.id) is None or safe_token(vuln.package) is None:
        return None
    return Vulnerability(vuln.id, vuln.package, _safe_version(vuln.installed_version), ())


def sanitize_plan(plan: AdvisoryPlan) -> AdvisoryPlan:
    fixes = (_sanitized_fix(fix) for fix in plan.fixes)
    vulns = (_sanitized_vuln(vuln) for vuln in plan.unfixable)
    return AdvisoryPlan(
        fixes=tuple(f for f in fixes if f is not None),
        unfixable=tuple(v for v in vulns if v is not None),
    )


def _more(total: int) -> str:
    return f" (+{total - 1} more)" if total > 1 else ""


def fix_title(fixes: Sequence[PackageFix]) -> str:
    first = fixes[0]
    total = sum(len(fix.advisory_ids) for fix in fixes)
    return f"sec/{first.package}: {first.advisory_ids[0]}{_more(total)}"


def no_fix_title(vulns: Sequence[Vulnerability]) -> str:
    first = vulns[0]
    return f"sec/{first.package}: {first.id}{_more(len(vulns))} (no fix)"


def _string_items(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [item for item in cast(list[object], raw) if isinstance(item, str)]


def _table(raw: object) -> dict[str, Any]:
    return cast(dict[str, Any], raw) if isinstance(raw, dict) else {}


def _grouped(location: str, groups: dict[str, Any]) -> list[_DirectRequirement]:
    return [
        _DirectRequirement(f"{location}.{name}", req)
        for name, items in groups.items()
        for req in _string_items(items)
    ]


def _direct_requirements(pyproject_text: str) -> list[_DirectRequirement]:
    try:
        data = tomllib.loads(pyproject_text)
    except tomllib.TOMLDecodeError:
        return []
    project = _table(data.get("project"))
    found = [
        _DirectRequirement("[project].dependencies", req)
        for req in _string_items(project.get("dependencies"))
    ]
    extras = _table(project.get("optional-dependencies"))
    found.extend(_grouped("[project.optional-dependencies]", extras))
    found.extend(_grouped("[dependency-groups]", _table(data.get("dependency-groups"))))
    return found


def _requirement_key(raw: str) -> str | None:
    try:
        return str(canonicalize_name(Requirement(raw).name))
    except InvalidRequirement:
        return None


def _edit_line(item: _DirectRequirement, fix: PackageFix) -> str | None:
    widened = widen_requirement(item.requirement, fix.target_version)
    if widened is None:
        return (
            f"- {item.location}: `{item.requirement}` cannot be widened automatically to admit "
            f"{fix.target_version} — STOP and report this requirement"
        )
    if widened == item.requirement:
        return None
    return f'- {item.location}: replace `"{item.requirement}"` with `"{widened}"`'


def _fix_edits(fix: PackageFix, requirements: list[_DirectRequirement]) -> list[str]:
    key = str(canonicalize_name(fix.package))
    lines = (
        _edit_line(item, fix)
        for item in requirements
        if _requirement_key(item.requirement) == key
    )
    return [line for line in lines if line is not None]


def _range_edits(fixes: Sequence[PackageFix], requirements: list[_DirectRequirement]) -> list[str]:
    return [line for fix in fixes for line in _fix_edits(fix, requirements)]


def _flagged_lines(fixes: Sequence[PackageFix]) -> list[str]:
    return [
        f"- `{fix.package}` {fix.installed_version} -> {fix.target_version} "
        f"(clears {', '.join(fix.advisory_ids)})"
        for fix in fixes
    ]


def _lock_lines(fixes: Sequence[PackageFix]) -> list[str]:
    return [
        f"   - `uv lock --upgrade-package {fix.package}=={fix.target_version}`" for fix in fixes
    ]


def _widen_step(edits: list[str]) -> list[str]:
    if not edits:
        return [
            "1. No declared dependency range excludes a target version; leave "
            f"{PYPROJECT_FILE} untouched."
        ]
    return [f"1. Widen the declared ranges first, by editing {PYPROJECT_FILE}:", *edits]


def _doc_step(edits: list[str], today: date) -> str:
    if not edits:
        return (
            f"3. {PYPROJECT_FILE} is untouched, so no documentation touch is required for the "
            "drift gate."
        )
    return (
        f"3. {PYPROJECT_FILE} changed. If `alan/drift_gate.py` exists in this checkout, add one "
        f"dated line (for example `- {today.isoformat()}: widened <package> to admit <version> "
        f"for <advisory>`) under `{FIX_LOG_HEADING}` in `{FIX_LOG_DOC}`, appending that heading "
        "at the end of the file if it is absent, so the drift gate is satisfied."
    )


def build_fix_brief(
    fixes: Sequence[PackageFix],
    pyproject_text: str,
    *,
    audit_command: str,
    today: date | None = None,
) -> str:
    edits = _range_edits(fixes, _direct_requirements(pyproject_text))
    verify = [f"   `{line}`" for line in audit_command.splitlines()]
    lines = [
        "## Implementation steps",
        "Raise each flagged package to the lowest version that clears all of its advisories "
        "(the target below, never merely the latest release) and change nothing else.",
        "",
        "Flagged packages:",
        *_flagged_lines(fixes),
        "",
        *_widen_step(edits),
        "2. Re-lock, one command per package; if `uv lock` reports that a parent package caps "
        "the dependency, stop and report the resolver output verbatim:",
        *_lock_lines(fixes),
        _doc_step(edits, today if today is not None else datetime.now(UTC).date()),
        "4. Verify with the audit CI runs; both commands must exit 0:",
        "   `uv sync --locked --extra dev`",
        *verify,
        "5. Commit with a Conventional Commits subject such as `fix(deps): raise <package> to "
        "<version> to clear <advisory>`.",
        "",
        "Forbidden:",
        *(f"- {item}" for item in FORBIDDEN_ACTIONS),
    ]
    return "\n".join(lines)


def build_no_fix_message(vulns: Sequence[Vulnerability]) -> str:
    rows = [
        f"- {vuln.id} in `{vuln.package}` (installed {vuln.installed_version}): no fixed "
        "release exists"
        for vuln in vulns
    ]
    return "\n".join(
        [
            "The dependency advisory check found advisories that no released version fixes, so "
            "alan cannot raise a package to clear them. Main's security audit stays red until "
            "you decide:",
            *rows,
            "",
            "Options: replace or remove the package; or, if you accept the risk, add the "
            f"advisory ID to `[tool.pip-audit] ignore-vuln` in {PYPROJECT_FILE} with a rationale "
            "comment and a revisit trigger. alan never writes that suppression itself.",
        ]
    )


def build_posture_message(fixes: Sequence[PackageFix], brief: str) -> str:
    packages = ", ".join(f"{fix.package} {fix.target_version}" for fix in fixes)
    return "\n".join(
        [
            "The dependency advisory check found fixable advisories "
            f"({packages}), but this engine's posture is below `propose`, so no fix was "
            "dispatched. Apply the brief below by hand or raise the engine's posture:",
            "",
            brief,
        ]
    )
