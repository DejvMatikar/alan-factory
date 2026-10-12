from __future__ import annotations

import argparse
import enum
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from alan import roots
from alan.default_profile import (
    PROFILE_FILE_NAME,
    CodeQualityProfile,
    DefaultProfile,
    DefaultProfileError,
    ProfileRequirement,
)
from alan.engine_config_validation import Posture
from alan.store import write_text_atomic

GATE_CHECK = "gate"
GATE_APP_ID = 15368
MAIN_CI_WORKFLOW = "tests.yml"

_IGNORED_WHEN_COMPARING = frozenset({"code_quality", "owner", "repo"})
_SET_FIELDS = ("authoritative_checks", "codeowners_repos")


class WriteOutcome(enum.StrEnum):
    written = "written"
    already = "already"
    kept = "kept"


class ProfileWriterError(DefaultProfileError):
    pass


def build_profile(owner: str, repo: str) -> DefaultProfile:
    try:
        return DefaultProfile(
            owner=owner,
            repo=repo,
            posture=Posture.propose,
            authoritative_checks=frozenset({GATE_CHECK}),
            authoritative_check_apps={GATE_CHECK: GATE_APP_ID},
            enforce_admins=True,
            requirements=[ProfileRequirement.reviewer_credential],
            main_ci_workflow=MAIN_CI_WORKFLOW,
            dependency_sweep=False,
            triage_sweep=False,
        )
    except ValidationError:
        raise ProfileWriterError("invalid owner or repo") from None


def render_profile(profile: DefaultProfile) -> str:
    data: dict[str, Any] = profile.model_dump(mode="json", exclude_none=True)
    for field in _SET_FIELDS:
        data[field] = sorted(data[field])
    return yaml.safe_dump(data, sort_keys=True, default_flow_style=False)


def _parse_text(text: str) -> DefaultProfile:
    try:
        raw: Any = yaml.safe_load(text)
        return DefaultProfile.model_validate(raw)
    except yaml.YAMLError:
        raise ProfileWriterError("profile is not valid YAML") from None
    except ValidationError:
        raise ProfileWriterError("profile fails validation") from None


def _read_existing(target: Path) -> DefaultProfile | None:
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return _parse_text(text)


def _same_repo(existing: DefaultProfile, wanted: DefaultProfile) -> bool:
    return (existing.owner.casefold(), existing.repo.casefold()) == (
        wanted.owner.casefold(),
        wanted.repo.casefold(),
    )


def _comparable(profile: DefaultProfile) -> dict[str, Any]:
    fields = profile.model_dump(exclude=set(_IGNORED_WHEN_COMPARING))
    fields["requirements"] = [
        requirement
        for requirement in fields["requirements"]
        if requirement != ProfileRequirement.code_quality
    ]
    return fields


def _drifted_fields(existing: DefaultProfile, wanted: DefaultProfile) -> list[str]:
    have = _comparable(existing)
    want = _comparable(wanted)
    return sorted(name for name in want if have[name] != want[name])


def requires_code_quality(engines_root: Path, owner: str, repo: str) -> bool:
    existing = _read_existing(engines_root / PROFILE_FILE_NAME)
    if existing is None or not _same_repo(existing, build_profile(owner, repo)):
        return True
    return ProfileRequirement.code_quality in existing.requirements


def _reject_parent_components(engines_root: Path) -> None:
    if ".." in engines_root.parts:
        raise ProfileWriterError("engines root must not contain '..' components")


def write_profile(engines_root: Path, owner: str, repo: str) -> WriteOutcome:
    _reject_parent_components(engines_root)
    profile = build_profile(owner, repo)
    text = render_profile(profile)
    _parse_text(text)
    target = engines_root / PROFILE_FILE_NAME
    existing = _read_existing(target)
    if existing is None:
        engines_root.mkdir(parents=True, exist_ok=True)
        write_text_atomic(target, text)
        return WriteOutcome.written
    if not _same_repo(existing, profile):
        raise ProfileWriterError("an installed profile names a different repository")
    drifted = _drifted_fields(existing, profile)
    if drifted:
        print(
            f"default_profile_writer: kept the installed profile; differs in: {', '.join(drifted)}",
            file=sys.stderr,
        )
        return WriteOutcome.kept
    return WriteOutcome.already


def _code_quality_block(organization: str, project_key: str) -> CodeQualityProfile:
    try:
        return CodeQualityProfile(organization=organization, project_key=project_key)
    except ValidationError:
        raise ProfileWriterError("invalid code quality organization or project key") from None


def _installed_for(target: Path, owner: str, repo: str) -> DefaultProfile:
    existing = _read_existing(target)
    if existing is None:
        raise ProfileWriterError("no installed profile to add the code quality block to")
    if not _same_repo(existing, build_profile(owner, repo)):
        raise ProfileWriterError("an installed profile names a different repository")
    return existing


def installed_code_quality(engines_root: Path, owner: str, repo: str) -> CodeQualityProfile | None:
    existing = _read_existing(engines_root / PROFILE_FILE_NAME)
    if existing is None or not _same_repo(existing, build_profile(owner, repo)):
        return None
    return existing.code_quality


def write_code_quality(
    engines_root: Path,
    owner: str,
    repo: str,
    organization: str,
    project_key: str,
    *,
    before_write: Callable[[], None],
) -> WriteOutcome:
    _reject_parent_components(engines_root)
    target = engines_root / PROFILE_FILE_NAME
    existing = _installed_for(target, owner, repo)
    wanted = _code_quality_block(organization, project_key)
    if existing.code_quality == wanted:
        return WriteOutcome.already
    text = render_profile(existing.model_copy(update={"code_quality": wanted}))
    _parse_text(text)
    before_write()
    write_text_atomic(target, text)
    return WriteOutcome.written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m alan.default_profile_writer")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repo", required=True)
    args = parser.parse_args(argv)
    try:
        outcome = write_profile(roots.engines_root(), args.owner, args.repo)
    except (ValueError, OSError) as exc:
        print(f"default_profile_writer: {exc}", file=sys.stderr)
        return 1
    print(outcome.value)
    return 0


if __name__ == "__main__":
    sys.exit(main())
