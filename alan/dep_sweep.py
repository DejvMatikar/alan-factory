from __future__ import annotations

import os
import re
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from enum import Enum

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, Specifier
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

_ENV_MERGE = "ALAN_DEP_SWEEP_MERGE"


class ProposalClass(str, Enum):
    MERGE_READY = "merge_ready"
    NEEDS_REBASE = "needs_rebase"
    UPSTREAM_BLOCKED = "upstream_blocked"
    SKIP = "skip"


@dataclass(frozen=True)
class PeerCapBlock:
    dependency: str
    capping_package: str
    peer_range: str


@dataclass(frozen=True)
class Proposal:
    number: int
    state: str
    mergeable: str
    lockfiles: frozenset[str]
    upstream_block: PeerCapBlock | None = None


@dataclass(frozen=True)
class ProposalBatch:
    lockfiles: frozenset[str]
    proposal_numbers: tuple[int, ...]


@dataclass(frozen=True)
class Vulnerability:
    id: str
    package: str
    installed_version: str
    fix_versions: tuple[str, ...]


@dataclass(frozen=True)
class PackageFix:
    package: str
    installed_version: str
    target_version: str
    advisory_ids: tuple[str, ...]


@dataclass(frozen=True)
class AdvisoryPlan:
    fixes: tuple[PackageFix, ...]
    unfixable: tuple[Vulnerability, ...]


_CONFLICT_RE = re.compile(r"Conflicting peer dependency:\s+(\S+)")
_PEER_RE = re.compile(r'peer\s+(\S+?)@"([^"]+)"\s+from\s+(\S+)')


def _dep_name(token: str) -> str:
    return token.rsplit("@", 1)[0]


def parse_peer_cap(log_text: str) -> PeerCapBlock | None:
    conflict = _CONFLICT_RE.search(log_text)
    if conflict is None:
        return None
    dependency = _dep_name(conflict.group(1))
    for peer in _PEER_RE.finditer(log_text):
        if peer.group(1) == dependency:
            return PeerCapBlock(
                dependency=dependency,
                capping_package=peer.group(3),
                peer_range=peer.group(2),
            )
    return None


def classify_proposal(proposal: Proposal) -> ProposalClass:
    if proposal.state != "open":
        return ProposalClass.SKIP
    if proposal.upstream_block is not None:
        return ProposalClass.UPSTREAM_BLOCKED
    if proposal.mergeable == "CONFLICTING":
        return ProposalClass.NEEDS_REBASE
    return ProposalClass.MERGE_READY


def _overlapping_indices(
    lockfiles: frozenset[str], groups: list[ProposalBatch]
) -> frozenset[int]:
    return frozenset(i for i, g in enumerate(groups) if g.lockfiles & lockfiles)


def _merge_into_groups(
    groups: list[ProposalBatch],
    indices: frozenset[int],
    new_lockfiles: frozenset[str],
    new_number: int,
) -> list[ProposalBatch]:
    combined_lf = new_lockfiles
    combined_nums: tuple[int, ...] = ()
    result: list[ProposalBatch] = []
    for i, g in enumerate(groups):
        if i in indices:
            combined_lf |= g.lockfiles
            combined_nums += g.proposal_numbers
        else:
            result.append(g)
    result.append(
        ProposalBatch(
            lockfiles=combined_lf,
            proposal_numbers=tuple(sorted(combined_nums + (new_number,))),
        )
    )
    return result


def group_proposals(proposals: list[Proposal]) -> list[ProposalBatch]:
    groups: list[ProposalBatch] = []
    for proposal in proposals:
        matching = _overlapping_indices(proposal.lockfiles, groups)
        if not matching:
            groups.append(
                ProposalBatch(lockfiles=proposal.lockfiles, proposal_numbers=(proposal.number,))
            )
        else:
            groups = _merge_into_groups(groups, matching, proposal.lockfiles, proposal.number)
    return sorted(groups, key=lambda b: min(b.proposal_numbers))


def _parse_version(raw: str) -> Version | None:
    try:
        return Version(raw)
    except InvalidVersion:
        return None


def _stable_fixes(vuln: Vulnerability) -> tuple[Version, ...]:
    parsed = (_parse_version(raw) for raw in vuln.fix_versions)
    return tuple(v for v in parsed if v is not None and not v.is_prerelease)


def _candidates(vuln: Vulnerability) -> tuple[Version, ...]:
    installed = _parse_version(vuln.installed_version)
    return tuple(v for v in _stable_fixes(vuln) if installed is None or v > installed)


def _clears(candidate: Version, fixes: tuple[Version, ...]) -> bool:
    if not fixes:
        return False
    if candidate >= max(fixes):
        return True
    return any(f <= candidate and f.major == candidate.major for f in fixes)


def lowest_clearing_version(vulns: Sequence[Vulnerability]) -> str | None:
    pool = sorted({c for vuln in vulns for c in _candidates(vuln)})
    per_advisory = [_stable_fixes(vuln) for vuln in vulns]
    for candidate in pool:
        if all(_clears(candidate, fixes) for fixes in per_advisory):
            return str(candidate)
    return None


def _group_by_package(vulns: Iterable[Vulnerability]) -> list[list[Vulnerability]]:
    grouped: dict[str, list[Vulnerability]] = {}
    for vuln in vulns:
        grouped.setdefault(str(canonicalize_name(vuln.package)), []).append(vuln)
    return [grouped[key] for key in sorted(grouped)]


def _package_fix(fixable: list[Vulnerability], target: str) -> PackageFix:
    return PackageFix(
        package=fixable[0].package,
        installed_version=fixable[0].installed_version,
        target_version=target,
        advisory_ids=tuple(sorted({v.id for v in fixable})),
    )


def _plan_package(group: list[Vulnerability]) -> tuple[PackageFix | None, list[Vulnerability]]:
    fixable = [v for v in group if _candidates(v)]
    target = lowest_clearing_version(fixable)
    if target is None:
        return None, group
    return _package_fix(fixable, target), [v for v in group if not _candidates(v)]


def plan_fixes(vulns: Iterable[Vulnerability], suppressed: Collection[str]) -> AdvisoryPlan:
    live = [v for v in vulns if v.id not in suppressed]
    fixes: list[PackageFix] = []
    unfixable: list[Vulnerability] = []
    for group in _group_by_package(live):
        fix, stuck = _plan_package(group)
        if fix is not None:
            fixes.append(fix)
        unfixable.extend(stuck)
    return AdvisoryPlan(fixes=tuple(fixes), unfixable=tuple(unfixable))


_SPEC_START_RE = re.compile(r"(?=[<>=!~(])")


def _ceiling(version: Version) -> str:
    return f"0.{version.minor + 1}" if version.major == 0 else str(version.major + 1)


def _widen_clause(clause: Specifier, target: Version) -> list[str] | None:
    if clause.contains(target, prereleases=True):
        return [str(clause)]
    if clause.operator in {"==", "==="}:
        return [f"=={target}"]
    if clause.operator in {"<", "<="}:
        return [f"<{_ceiling(target)}"]
    if clause.operator == "~=":
        return [f">={clause.version}", f"<{_ceiling(target)}"]
    if clause.operator == "!=":
        return []
    return None


def _widened_clauses(spec_text: str, target: Version) -> list[str] | None:
    widened: list[str] = []
    for raw in spec_text.split(","):
        pieces = _widen_clause(Specifier(raw.strip()), target)
        if pieces is None:
            return None
        widened.extend(piece for piece in pieces if piece not in widened)
    return widened


def _render_requirement(prefix: str, clauses: list[str], marker: str | None) -> str:
    tail = "" if marker is None else f"; {marker.strip()}"
    return f"{prefix.rstrip()}{','.join(clauses)}{tail}"


def widen_requirement(requirement: str, target: str) -> str | None:
    try:
        parsed = Requirement(requirement)
        version = Version(target)
        if parsed.specifier.contains(version, prereleases=True):
            return requirement
        head, separator, marker = requirement.partition(";")
        prefix, *spec_text = _SPEC_START_RE.split(head, maxsplit=1)
        clauses = _widened_clauses("".join(spec_text), version)
    except (InvalidRequirement, InvalidVersion, InvalidSpecifier):
        return None
    if clauses is None:
        return None
    return _render_requirement(prefix, clauses, marker if separator else None)


def _merge_enabled() -> bool:
    return os.environ.get(_ENV_MERGE, "1") != "0"


def merge_target(proposal_class: ProposalClass) -> str | None:
    if proposal_class != ProposalClass.MERGE_READY:
        return None
    return "pr_merge" if _merge_enabled() else None
