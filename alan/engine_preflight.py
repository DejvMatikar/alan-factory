from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from alan import default_profile, roots, secrets_store, store
from alan.branch_protection_compat import Finding, compare_branch_protection
from alan.default_profile import DefaultProfile, ProfileRequirement, Source
from alan.engine_config import (
    EngineConfig,
    ForgeTarget,
    canonical_scope_key,
    default_engine_config,
    forge_target_for_workspace,
    get_registered,
    registered_engines,
)
from alan.engine_config import (
    _validate_before_register as _validate_engine_config,  # pyright: ignore[reportPrivateUsage]
)
from alan.engine_config_validation import FlowGraphValidationError, PostureOrthogonalityError
from alan.errors import AppError
from alan.forge import active_forge
from alan.teams._base import render_allowed_tools
from alan.teams._pr_state_paths import (
    _engine_state_dir as _engine_state_dir,  # pyright: ignore[reportPrivateUsage]
)

logger = logging.getLogger(__name__)

_PREFLIGHT_FILE = "engine_preflight.json"
_GH_USER_TIMEOUT_S = 30.0
_GIT_LS_REMOTE_TIMEOUT_S = 30.0
_GATE_TEAM_NAME = "implementation"

_BASH_PREFIX_RE = re.compile(r"^Bash\((.+):\*\)$")


class PreflightResult(BaseModel, frozen=True):
    slug: str
    checked_at: str
    overall: Literal["pass", "fail", "unknown"]
    findings: tuple[Finding, ...]
    reviewer_fingerprint: str | None = None


def _worst(statuses: Iterable[str]) -> Literal["pass", "fail", "unknown"]:
    statuses = list(statuses)
    if "fail" in statuses:
        return "fail"
    if "unknown" in statuses:
        return "unknown"
    return "pass"


def _check_author_token(forge: ForgeTarget) -> Finding:
    try:
        secrets_store.transport_env(forge)
    except secrets_store.MissingSecretError as exc:
        return Finding(
            check="author_token_valid",
            status="fail",
            message=str(exc),
            remedy=(
                "write a transport secret for this repo via the secrets store, "
                "or set its credential_env_name env var"
            ),
        )
    return Finding(
        check="author_token_valid", status="pass", message="transport credential resolvable"
    )


def _author_login(forge: ForgeTarget) -> str | None:
    result = active_forge().viewer_login(
        forge_target=forge, as_verifier=False, timeout=_GH_USER_TIMEOUT_S,
    )
    return result.login if result.outcome == "ok" else None


def _reviewer_login(forge: ForgeTarget) -> str | None:
    result = active_forge().viewer_login(
        forge_target=forge, as_verifier=True, timeout=_GH_USER_TIMEOUT_S,
    )
    return result.login if result.outcome == "ok" else None


def _check_reviewer_distinctness(forge: ForgeTarget) -> Finding:
    author_login = _author_login(forge)
    reviewer_login = _reviewer_login(forge)
    if author_login is None or reviewer_login is None:
        return Finding(
            check="reviewer_distinct_from_author",
            status="unknown",
            message="could not resolve both author and reviewer identity",
            remedy=(
                f"ensure both the transport/author credential and the reviewer "
                f"credential are configured and valid for this repo — write a "
                f"transport and/or reviewer secret via the secrets store, or set "
                f"{forge.credential_env_name}; each must resolve a login via "
                f"`gh api user`, and the two must differ"
            ),
        )
    if author_login == reviewer_login:
        return Finding(
            check="reviewer_distinct_from_author",
            status="fail",
            message=f"author and reviewer both resolve to {author_login!r}",
            remedy=(
                f"write a reviewer secret for this repo via the secrets store "
                f"(or set {forge.credential_env_name}) that resolves to a login "
                f"distinct from the transport/author credential"
            ),
        )
    return Finding(
        check="reviewer_distinct_from_author",
        status="pass",
        message=f"author={author_login!r} reviewer={reviewer_login!r}",
    )


def _check_repo_clonable(forge: ForgeTarget) -> Finding:
    try:
        env = secrets_store.transport_env(forge)
    except secrets_store.MissingSecretError as exc:
        return Finding(
            check="repo_clonable",
            status="fail",
            message=str(exc),
            remedy="configure a transport credential for this repo",
        )
    remote_url = f"https://github.com/{forge.repo.slug}.git"
    try:
        result = subprocess.run(
            ["git", "ls-remote", remote_url, "HEAD"],
            capture_output=True,
            text=True,
            timeout=_GIT_LS_REMOTE_TIMEOUT_S,
            env={**os.environ, **env},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Finding(
            check="repo_clonable",
            status="unknown",
            message=f"git ls-remote raised: {exc}",
            remedy="retry once network/git access is restored",
        )
    if result.returncode != 0:
        return Finding(
            check="repo_clonable",
            status="fail",
            message=(result.stderr or "")[:300],
            remedy="verify the transport credential has read access to this repository",
        )
    return Finding(
        check="repo_clonable", status="pass", message="repository reachable via git ls-remote"
    )


def _bash_allowlist_prefixes(tools: tuple[str, ...]) -> list[tuple[str, ...]]:
    prefixes: list[tuple[str, ...]] = []
    for entry in tools:
        if entry == "Bash":
            prefixes.append(())
            continue
        match = _BASH_PREFIX_RE.match(entry)
        if match:
            prefixes.append(tuple(match.group(1).split()))
    return prefixes


def _command_reachable(argv: list[str], prefixes: list[tuple[str, ...]]) -> bool:
    return any(tuple(argv[: len(prefix)]) == prefix for prefix in prefixes)


def _check_tools_reachable(cfg: EngineConfig) -> Finding:
    commands = [*cfg.gate_commands, *cfg.warmup_commands]
    if any(not cmd for cmd in commands):
        return Finding(
            check="gate_warmup_tools_reachable",
            status="fail",
            message="a gate or warmup command is empty",
            remedy="remove or fix the empty command entry in gate_commands/warmup_commands",
        )
    prefixes = _bash_allowlist_prefixes(render_allowed_tools(cfg, _GATE_TEAM_NAME))
    unreachable = [cmd for cmd in commands if not _command_reachable(cmd, prefixes)]
    if unreachable:
        rendered = "; ".join(" ".join(cmd) for cmd in unreachable)
        return Finding(
            check="gate_warmup_tools_reachable",
            status="fail",
            message=(
                f"commands not reachable from the {_GATE_TEAM_NAME!r} tool allowlist: {rendered}"
            ),
            remedy=(
                "add a matching Bash(...) allowlist entry for these commands to "
                "base_tools or team_tools['implementation']"
            ),
        )
    return Finding(
        check="gate_warmup_tools_reachable",
        status="pass",
        message="all gate/warmup commands are reachable",
    )


_DEPENDABOT_CONFIG_PATH = ".github/dependabot.yml"
_DEPENDABOT_CHECK_TIMEOUT_S = 30.0
_DEP_SWEEP_REQUIRED_ROSTER: frozenset[str] = frozenset({"pr_merge", "implementation_fix"})


def _check_dependabot_config(forge: ForgeTarget) -> Finding:
    result = active_forge().repo_file(
        _DEPENDABOT_CONFIG_PATH, forge_target=forge, timeout=_DEPENDABOT_CHECK_TIMEOUT_S,
    )
    if result.outcome == "raised":
        return Finding(
            check="dependabot_config_present",
            status="unknown",
            message=f"gh api raised: {result.stderr}",
            remedy=(
                f"retry once network/gh access is restored; add {_DEPENDABOT_CONFIG_PATH} "
                "to the repository if it is missing"
            ),
        )
    if result.outcome == "ok":
        return Finding(
            check="dependabot_config_present",
            status="pass",
            message=f"{_DEPENDABOT_CONFIG_PATH} found in {forge.repo.slug}",
        )
    return Finding(
        check="dependabot_config_present",
        status="fail",
        message=f"{_DEPENDABOT_CONFIG_PATH} not found in {forge.repo.slug}",
        remedy=(
            f"add a {_DEPENDABOT_CONFIG_PATH} file to the repository to configure "
            "Dependabot updates before enabling the dependency sweep"
        ),
    )


def _transition_graph_roster(graph: dict[str, list[str]]) -> set[str]:
    nodes: set[str] = set(graph)
    for destinations in graph.values():
        nodes.update(destinations)
    return nodes


def _check_dependency_sweep_roster(cfg: EngineConfig) -> Finding:
    graph = cfg.transition_graph
    if not graph:
        return Finding(
            check="dependency_sweep_roster_present",
            status="pass",
            message="no transition_graph declared",
        )
    missing = _DEP_SWEEP_REQUIRED_ROSTER - _transition_graph_roster(graph)
    if missing:
        return Finding(
            check="dependency_sweep_roster_present",
            status="fail",
            message=(
                "transition_graph is missing required team(s): "
                + ", ".join(sorted(missing))
            ),
            remedy=(
                "add the missing team(s) to transition_graph so the dependency sweep "
                "can dispatch fix/merge work"
            ),
        )
    return Finding(
        check="dependency_sweep_roster_present", status="pass", message="roster complete"
    )


def check_dependency_sweep_enable(cfg: EngineConfig) -> Finding | None:
    forge = forge_target_for_workspace(cfg.repo.slug, cfg)
    dependabot = _check_dependabot_config(forge)
    if dependabot.status != "pass":
        return dependabot
    roster = _check_dependency_sweep_roster(cfg)
    if roster.status != "pass":
        return roster
    return None


def _check_flow_graph(cfg: EngineConfig) -> Finding:
    try:
        _validate_engine_config(cfg, validate_env=False)
    except (FlowGraphValidationError, PostureOrthogonalityError) as exc:
        return Finding(
            check="flow_graph_and_posture_valid",
            status="fail",
            message=str(exc),
            remedy="fix the transition_graph/flow_policy per the validation error",
        )
    return Finding(
        check="flow_graph_and_posture_valid",
        status="pass",
        message="flow graph and posture orthogonality valid",
    )


def _preflight_path(forge: ForgeTarget) -> Path:
    return _engine_state_dir(forge) / _PREFLIGHT_FILE


def _persist(forge: ForgeTarget, result: PreflightResult) -> None:
    path = _preflight_path(forge)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        store.write_text_mkstemp(path, result.model_dump_json(), prefix=".engine_preflight_")
    except OSError:
        pass


def load_last_preflight(forge: ForgeTarget) -> PreflightResult | None:
    try:
        raw = _preflight_path(forge).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return PreflightResult.model_validate_json(raw)
    except ValidationError:
        return None


ReadinessState = Literal["not_ready", "unknown", "warning"]
_Verdict = Literal["pass", "fail", "unknown"]

CODE_QUALITY_LATCH_FILE = "code_quality.latch"
DEFAULT_ENGINE_REQUIREMENT = "default_engine"
SETUP_FIX = "./setup.sh"
REVIEWER_EXPIRY_WARNING_DAYS = 14
_LATCH_MODE = 0o600
_DISTINCTNESS_CHECK = "reviewer_distinct_from_author"
_READINESS_CHECK = "dispatch_readiness"
_REVIEWER = ProfileRequirement.reviewer_credential
_CODE_QUALITY = ProfileRequirement.code_quality


class ReadinessIssue(BaseModel, frozen=True):
    engine: str
    requirement: str
    state: ReadinessState
    message: str
    fix: str


class CodeQualityLatch(BaseModel, frozen=True, extra="forbid"):
    version: Literal[1]
    organization: str = Field(min_length=1)
    project_key: str = Field(min_length=1)
    recorded_at: str

    @field_validator("recorded_at")
    @classmethod
    def _recorded_at_is_a_utc_timestamp(cls, value: str) -> str:
        if datetime.fromisoformat(value).utcoffset() != timedelta(0):
            raise ValueError("recorded_at must be an ISO-8601 UTC timestamp")
        return value


@dataclass(frozen=True)
class _Subject:
    cfg: EngineConfig
    profile: DefaultProfile
    preflight: PreflightResult | None


_Checker = Callable[[_Subject], tuple[ReadinessIssue, ...]]
_Signature = tuple[tuple[str, str], ...]

_seen_lock = threading.Lock()
_last_seen: dict[str, _Signature] = {}


def _is_default_engine(cfg: EngineConfig) -> bool:
    return cfg.repo == canonical_scope_key(None)


def readiness_fix(requirement: str, cfg: EngineConfig) -> str:
    if requirement == ProfileRequirement.reviewer_credential and not _is_default_engine(cfg):
        return f"store a reviewer credential for {cfg.repo.slug}"
    return SETUP_FIX


def _issue(
    cfg: EngineConfig, requirement: str, state: ReadinessState, message: str
) -> ReadinessIssue:
    return ReadinessIssue(
        engine=cfg.repo.slug,
        requirement=str(requirement),
        state=state,
        message=message,
        fix=readiness_fix(requirement, cfg),
    )


def code_quality_latch_path() -> Path:
    return roots.engines_root() / CODE_QUALITY_LATCH_FILE


def read_code_quality_latch() -> CodeQualityLatch | None:
    try:
        raw = code_quality_latch_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return CodeQualityLatch.model_validate_json(raw)


def write_code_quality_latch(organization: str, project_key: str) -> CodeQualityLatch:
    latch = CodeQualityLatch(
        version=1,
        organization=organization,
        project_key=project_key,
        recorded_at=datetime.now(UTC).isoformat(),
    )
    path = code_quality_latch_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    store.write_text_mkstemp(
        path, latch.model_dump_json(), prefix=".code_quality_latch_", mode=_LATCH_MODE
    )
    return latch


def _reviewer_slug(cfg: EngineConfig) -> str:
    return forge_target_for_workspace(None, cfg).repo.slug


def _reviewer_missing(subject: _Subject) -> ReadinessIssue | None:
    state = secrets_store.reviewer_record_state(_reviewer_slug(subject.cfg))
    if state is secrets_store.RecordState.absent:
        return _issue(subject.cfg, _REVIEWER, "not_ready", "no reviewer credential is stored")
    if state is secrets_store.RecordState.unreadable:
        return _issue(
            subject.cfg, _REVIEWER, "unknown", "the stored reviewer credential cannot be read"
        )
    return None


def _saved_preflight(subject: _Subject) -> PreflightResult | None:
    if subject.preflight is not None:
        return subject.preflight
    return load_last_preflight(forge_target_for_workspace(None, subject.cfg))


def _distinctness_verdict(saved: PreflightResult) -> _Verdict:
    finding = next((f for f in saved.findings if f.check == _DISTINCTNESS_CHECK), None)
    return "unknown" if finding is None else finding.status


def _saved_distinctness(
    subject: _Subject, detail: secrets_store.ReviewerRecordDetail
) -> _Verdict:
    saved = _saved_preflight(subject)
    if saved is None or detail.fingerprint is None:
        return "unknown"
    if saved.reviewer_fingerprint != detail.fingerprint:
        return "unknown"
    return _distinctness_verdict(saved)


def _reviewer_distinctness(
    subject: _Subject, detail: secrets_store.ReviewerRecordDetail
) -> ReadinessIssue | None:
    verdict = _saved_distinctness(subject, detail)
    if verdict == "fail":
        return _issue(
            subject.cfg,
            _REVIEWER,
            "not_ready",
            "the reviewer resolves to the same account as the author",
        )
    if verdict == "unknown":
        return _issue(
            subject.cfg,
            _REVIEWER,
            "unknown",
            "no current engine preflight confirms the reviewer differs from the author",
        )
    return None


def _expiry_warning(
    subject: _Subject, detail: secrets_store.ReviewerRecordDetail
) -> ReadinessIssue | None:
    if not detail.expiry_warning_on(datetime.now(UTC).date(), REVIEWER_EXPIRY_WARNING_DAYS):
        return None
    return _issue(
        subject.cfg,
        _REVIEWER,
        "warning",
        f"the reviewer credential expires on {detail.expires_at}",
    )


def _check_reviewer_credential(subject: _Subject) -> tuple[ReadinessIssue, ...]:
    missing = _reviewer_missing(subject)
    if missing is not None:
        return (missing,)
    detail = secrets_store.reviewer_record_detail(_reviewer_slug(subject.cfg))
    if detail.expired_on(datetime.now(UTC).date()):
        return (
            _issue(subject.cfg, _REVIEWER, "not_ready", "the reviewer credential has expired"),
        )
    distinctness = _reviewer_distinctness(subject, detail)
    warning = _expiry_warning(subject, detail)
    return tuple(issue for issue in (distinctness, warning) if issue is not None)


def _check_code_quality(subject: _Subject) -> ReadinessIssue | None:
    project = subject.profile.code_quality
    if project is None:
        return _issue(
            subject.cfg, _CODE_QUALITY, "not_ready", "the profile names no SonarCloud project"
        )
    latch = read_code_quality_latch()
    if latch is None:
        return _issue(
            subject.cfg, _CODE_QUALITY, "not_ready", "SonarCloud has not reported for this install"
        )
    if (latch.organization, latch.project_key) != (project.organization, project.project_key):
        return _issue(
            subject.cfg,
            _CODE_QUALITY,
            "not_ready",
            "the recorded SonarCloud setup is for a different organization or project",
        )
    return None


def _present(checker: Callable[[_Subject], ReadinessIssue | None]) -> _Checker:
    def _wrapped(subject: _Subject) -> tuple[ReadinessIssue, ...]:
        issue = checker(subject)
        return () if issue is None else (issue,)

    return _wrapped


_CHECKERS: Mapping[ProfileRequirement, _Checker] = {
    ProfileRequirement.reviewer_credential: _check_reviewer_credential,
    ProfileRequirement.code_quality: _present(_check_code_quality),
}


def _run_checker(requirement: ProfileRequirement, subject: _Subject) -> tuple[ReadinessIssue, ...]:
    try:
        return _CHECKERS[requirement](subject)
    except Exception as exc:  # noqa: BLE001
        return (
            _issue(
                subject.cfg,
                requirement,
                "unknown",
                f"could not be evaluated ({type(exc).__name__})",
            ),
        )


def _run_checkers(subject: _Subject) -> tuple[ReadinessIssue, ...]:
    outcomes = (_run_checker(req, subject) for req in dict.fromkeys(subject.profile.requirements))
    return tuple(issue for group in outcomes for issue in group)


def _evaluate(cfg: EngineConfig, preflight: PreflightResult | None) -> tuple[ReadinessIssue, ...]:
    resolved = default_profile.resolved()
    if resolved.source is Source.unconfigured:
        message = "no default profile is installed"
        return (_issue(cfg, DEFAULT_ENGINE_REQUIREMENT, "not_ready", message),)
    if not resolved.profile.requirements:
        return ()
    return _run_checkers(_Subject(cfg, resolved.profile, preflight))


def _describe(issues: tuple[ReadinessIssue, ...]) -> str:
    return "; ".join(f"{i.requirement}={i.state} ({i.message})" for i in issues)


def _note_readiness(slug: str, issues: tuple[ReadinessIssue, ...]) -> None:
    signature = tuple((i.requirement, i.state) for i in issues)
    with _seen_lock:
        previous = _last_seen.get(slug, ())
        _last_seen[slug] = signature
    if signature == previous:
        return
    if not signature:
        logger.info("engine %s is ready to dispatch", slug)
        return
    logger.warning("engine %s dispatch readiness changed: %s", slug, _describe(issues))


def evaluate_readiness(
    cfg: EngineConfig, *, preflight: PreflightResult | None = None
) -> tuple[ReadinessIssue, ...]:
    try:
        issues = _evaluate(cfg, preflight)
    except Exception as exc:  # noqa: BLE001
        message = f"could not be evaluated ({type(exc).__name__})"
        issues = (_issue(cfg, DEFAULT_ENGINE_REQUIREMENT, "unknown", message),)
    _note_readiness(cfg.repo.slug, issues)
    return issues


def blocking_issues(cfg: EngineConfig) -> tuple[ReadinessIssue, ...]:
    return tuple(issue for issue in evaluate_readiness(cfg) if issue.state == "not_ready")


def _refusal(cfg: EngineConfig, issues: tuple[ReadinessIssue, ...]) -> str:
    reasons = "; ".join(f"{i.requirement}: {i.message} (fix: {i.fix})" for i in issues)
    return f"engine {cfg.repo.slug} is not ready to dispatch: {reasons}"


def assert_engine_ready(cfg: EngineConfig) -> None:
    issues = blocking_issues(cfg)
    if issues:
        raise AppError(status_code=409, detail=_refusal(cfg, issues))


def _report_engines() -> list[EngineConfig]:
    default = get_registered() or default_engine_config()
    return list({cfg.repo.slug: cfg for cfg in (default, *registered_engines())}.values())


def readiness_report() -> list[ReadinessIssue]:
    return [issue for cfg in _report_engines() for issue in evaluate_readiness(cfg)]


_STATE_TO_VERDICT: Mapping[ReadinessState, _Verdict] = {
    "not_ready": "fail",
    "unknown": "unknown",
    "warning": "pass",
}


def _readiness_finding(issues: tuple[ReadinessIssue, ...]) -> Finding:
    if not issues:
        return Finding(
            check=_READINESS_CHECK, status="pass", message="dispatch requirements are satisfied"
        )
    status = _worst(_STATE_TO_VERDICT[issue.state] for issue in issues)
    return Finding(
        check=_READINESS_CHECK,
        status=status,
        message="; ".join(f"{i.requirement}: {i.message}" for i in issues),
        remedy="; ".join(dict.fromkeys(i.fix for i in issues)),
    )


def _reviewer_fingerprint(forge: ForgeTarget) -> str | None:
    try:
        return secrets_store.reviewer_record_detail(forge.repo.slug).fingerprint
    except (OSError, ValueError):
        return None


def _with_readiness(cfg: EngineConfig, result: PreflightResult) -> PreflightResult:
    finding = _readiness_finding(evaluate_readiness(cfg, preflight=result))
    return result.model_copy(update={"findings": (*result.findings, finding)})


def run_preflight(cfg: EngineConfig) -> PreflightResult:
    forge = forge_target_for_workspace(None, cfg)
    findings: list[Finding] = [
        _check_author_token(forge),
        _check_reviewer_distinctness(forge),
        _check_repo_clonable(forge),
        _check_tools_reachable(cfg),
        _check_flow_graph(cfg),
    ]
    findings.extend(compare_branch_protection(forge))
    result = PreflightResult(
        slug=cfg.repo.slug,
        checked_at=datetime.now(UTC).isoformat(),
        overall=_worst(f.status for f in findings),
        findings=tuple(findings),
        reviewer_fingerprint=_reviewer_fingerprint(forge),
    )
    result = _with_readiness(cfg, result)
    _persist(forge, result)
    return result
