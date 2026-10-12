from __future__ import annotations

import enum
import functools
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    ValidationError,
    field_validator,
    model_validator,
)

from alan import roots
from alan.engine_config_validation import CheckAppId, Posture

logger = logging.getLogger(__name__)

PROFILE_FILE_NAME = "default_profile.yaml"
OWNER_FILE = Path(__file__).resolve().parent.parent / "config" / "owner" / PROFILE_FILE_NAME

UNCONFIGURED_OWNER = "unconfigured.invalid"
UNCONFIGURED_REPO = "unconfigured"

OWNER_NAME_PATTERN = r"^[A-Za-z0-9]+(-[A-Za-z0-9]+)*$"
REPO_NAME_PATTERN = r"^\.?[A-Za-z0-9_-][A-Za-z0-9._-]*$"
OWNER_NAME_MAX_LENGTH = 39
REPO_NAME_MAX_LENGTH = 100
BUNDLE_ID_PATTERN = r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$"

_OWNER_NAME = re.compile(OWNER_NAME_PATTERN)
_REPO_NAME = re.compile(REPO_NAME_PATTERN)

FORGE_CREDENTIAL_ENV = "ALAN_VERIFIER_GH_TOKEN"  # noqa: S105  # nosec B105
SONAR_HOST_URL = "https://sonarcloud.io"
SONAR_TOKEN_ENV = "SONAR_TOKEN"  # noqa: S105  # nosec B105


class DefaultProfileError(ValueError):
    pass


class Source(enum.StrEnum):
    profile = "profile"
    owner = "owner"
    unconfigured = "unconfigured"


class ProfileRequirement(enum.StrEnum):
    reviewer_credential = "reviewer_credential"
    code_quality = "code_quality"


class CodeQualityProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    organization: str = Field(min_length=1)
    project_key: str = Field(min_length=1)


def _is_repo_slug(slug: str) -> bool:
    owner, separator, repo = slug.partition("/")
    return (
        bool(separator)
        and len(owner) <= OWNER_NAME_MAX_LENGTH
        and len(repo) <= REPO_NAME_MAX_LENGTH
        and _OWNER_NAME.fullmatch(owner) is not None
        and _REPO_NAME.fullmatch(repo) is not None
    )


class DefaultProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    owner: str = Field(pattern=OWNER_NAME_PATTERN, max_length=OWNER_NAME_MAX_LENGTH)
    repo: str = Field(pattern=REPO_NAME_PATTERN, max_length=REPO_NAME_MAX_LENGTH)
    posture: Posture
    authoritative_checks: frozenset[str] = frozenset()
    authoritative_check_apps: dict[str, CheckAppId] = Field(default_factory=dict)
    enforce_admins: StrictBool = False
    code_quality: CodeQualityProfile | None = None
    main_ci_workflow: str | None = Field(default=None, min_length=1)
    deploy_workflow: str | None = Field(default=None, min_length=1)
    dependency_sweep: StrictBool = False
    triage_sweep: StrictBool = False
    prep_chat_origin: str | None = Field(default=None, min_length=1)
    requirements: list[ProfileRequirement] = Field(default_factory=list[ProfileRequirement])
    never_live_budget_s: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    codeowners_repos: frozenset[str] = frozenset()
    ios_bundle_id: str | None = Field(default=None, pattern=BUNDLE_ID_PATTERN)
    verifier_login: str | None = Field(
        default=None, pattern=OWNER_NAME_PATTERN, max_length=OWNER_NAME_MAX_LENGTH
    )

    @field_validator("codeowners_repos")
    @classmethod
    def _codeowners_repos_are_slugs(cls, value: frozenset[str]) -> frozenset[str]:
        if not all(_is_repo_slug(slug) for slug in value):
            raise ValueError("codeowners_repos entries must be owner/repo slugs")
        return frozenset(slug.casefold() for slug in value)

    @model_validator(mode="after")
    def _deploy_workflow_needs_main_ci_workflow(self) -> DefaultProfile:
        if self.deploy_workflow is not None and self.main_ci_workflow is None:
            raise ValueError("deploy_workflow requires main_ci_workflow")
        return self


UNCONFIGURED_PROFILE = DefaultProfile.model_construct(
    owner=UNCONFIGURED_OWNER, repo=UNCONFIGURED_REPO, posture=Posture.observe
)


def _code_quality_fields(profile: DefaultProfile) -> dict[str, str] | None:
    if profile.code_quality is None:
        return None
    return {
        "organization": profile.code_quality.organization,
        "project_key": profile.code_quality.project_key,
        "host_url": SONAR_HOST_URL,
        "token_env_var": SONAR_TOKEN_ENV,
    }


def _ci_fields(profile: DefaultProfile) -> dict[str, str | None] | None:
    if profile.main_ci_workflow is None:
        return None
    return {
        "main_ci_workflow": profile.main_ci_workflow,
        "deploy_workflow": profile.deploy_workflow,
    }


@dataclass(frozen=True)
class Resolved:
    profile: DefaultProfile
    source: Source
    path: Path | None = None

    def identity_fields(self) -> dict[str, str]:
        return {"owner": self.profile.owner, "repo": self.profile.repo}

    def forge_fields(self) -> dict[str, Any]:
        return {
            "repo": self.identity_fields(),
            "credential_env_name": FORGE_CREDENTIAL_ENV,
            "authoritative_checks": self.profile.authoritative_checks,
            "authoritative_check_apps": self.profile.authoritative_check_apps,
            "enforce_admins": self.profile.enforce_admins,
        }

    def engine_fields(self) -> dict[str, Any]:
        sweep = self.profile.dependency_sweep
        return {
            "repo": self.identity_fields(),
            "forge": None if self.source is Source.unconfigured else self.forge_fields(),
            "code_quality": _code_quality_fields(self.profile),
            "ci": _ci_fields(self.profile),
            "posture": self.profile.posture,
            "dependency_sweep": {"enabled": sweep, "enable_python_vuln_scan": sweep},
        }


def _label(path: Path) -> str:
    return f"{path.parent.name}/{path.name}"


def _describe(error: ValidationError) -> str:
    problems = error.errors(include_url=False, include_context=False, include_input=False)
    return "; ".join(
        f"{'.'.join(str(part) for part in problem['loc']) or 'profile'}: {problem['msg']}"
        for problem in problems
    )


def _read_mapping(path: Path) -> dict[str, Any]:
    label = _label(path)
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise DefaultProfileError(f"{label}: cannot read file") from exc
    except yaml.YAMLError:
        raise DefaultProfileError(f"{label}: invalid YAML") from None
    if not isinstance(raw, dict):
        raise DefaultProfileError(f"{label}: expected mapping at root")
    return cast("dict[str, Any]", raw)


def _parse(path: Path) -> DefaultProfile:
    raw = _read_mapping(path)
    try:
        return DefaultProfile.model_validate(raw)
    except ValidationError as exc:
        raise DefaultProfileError(f"{_label(path)}: {_describe(exc)}") from None


def resolve(state_dir: Path, owner_file: Path) -> Resolved:
    installed = state_dir / PROFILE_FILE_NAME
    if installed.is_file():
        if owner_file.is_file():
            logger.warning(
                "default profile %s overrides the owner profile %s",
                _label(installed),
                _label(owner_file),
            )
        return Resolved(_parse(installed), Source.profile, installed)
    if owner_file.is_file():
        return Resolved(_parse(owner_file), Source.owner, owner_file)
    return Resolved(UNCONFIGURED_PROFILE, Source.unconfigured)


@functools.cache
def resolved() -> Resolved:
    return resolve(roots.engines_root(), OWNER_FILE)


def triage_sweep_enabled() -> bool:
    return resolved().profile.triage_sweep
