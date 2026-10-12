from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import pytest

from alan import default_profile, engine_config, secrets_store
from alan import engine_preflight as ep
from alan import workspace as workspace_mod
from alan.branch_protection_compat import Finding
from alan.default_profile import (
    CodeQualityProfile,
    DefaultProfile,
    ProfileRequirement,
    Resolved,
    Source,
)
from alan.dispatcher import Dispatcher
from alan.engine_config import (
    EngineConfig,
    ForgeConfig,
    RepoIdentity,
    default_engine_config,
    forge_target_for_workspace,
)
from alan.engine_config_validation import Posture
from alan.events import PubSub
from alan.queue import Job, Queue
from alan.workspace import Workspace
from tests._dispatcher_support_teams import _NoopTeam
from tests.fake_clock import FakeClock

OWNER = "acme"
REPO = "widget"
ORGANIZATION = "acme-org"
PROJECT_KEY = "acme-org_widget"
DEFAULT_SLUG = f"{OWNER}/{REPO}"
REVIEWER_TOKEN = "reviewer-fixture-value"  # noqa: S105
REVIEWER_FINGERPRINT = secrets_store.fingerprint(REVIEWER_TOKEN)
CONNECTED_SLUG = "other-org/service"
REVIEWER = ProfileRequirement.reviewer_credential
CODE_QUALITY = ProfileRequirement.code_quality
Verdict = Literal["pass", "fail", "unknown"]


def profile(*requirements: ProfileRequirement, code_quality: bool = True) -> DefaultProfile:
    project = CodeQualityProfile(organization=ORGANIZATION, project_key=PROJECT_KEY)
    return DefaultProfile(
        owner=OWNER,
        repo=REPO,
        posture=Posture.autonomous,
        code_quality=project if code_quality else None,
        requirements=list(requirements),
    )


@dataclass
class Install:
    monkeypatch: pytest.MonkeyPatch

    @classmethod
    def isolated(cls, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Install:
        monkeypatch.setenv("ALAN_SECRETS_STORE_ROOT", str(tmp_path / "secrets"))
        monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "engines"))
        monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
        monkeypatch.setattr(ep, "_last_seen", {})
        return cls(monkeypatch)

    def require(self, *requirements: ProfileRequirement, code_quality: bool = True) -> None:
        resolved = Resolved(profile(*requirements, code_quality=code_quality), Source.profile)
        self.monkeypatch.setattr(default_profile, "resolved", lambda: resolved)

    def leave_unconfigured(self) -> None:
        resolved = Resolved(default_profile.UNCONFIGURED_PROFILE, Source.unconfigured)
        self.monkeypatch.setattr(default_profile, "resolved", lambda: resolved)

    def default_cfg(self) -> EngineConfig:
        return default_engine_config()

    def connect(self, slug: str = CONNECTED_SLUG) -> EngineConfig:
        owner, repo = slug.split("/")
        identity = RepoIdentity(owner=owner, repo=repo)
        cfg = default_engine_config().model_copy(
            update={
                "repo": identity,
                "forge": ForgeConfig(repo=identity, credential_env_name="CONNECTED_GH_TOKEN"),
            }
        )
        engine_config.register(cfg, validate_env=False)
        return cfg

    def store_reviewer(self, slug: str = DEFAULT_SLUG, *, expires_at: str | None = None) -> None:
        secrets_store.write_secret(
            slug, secrets_store.REVIEWER_PURPOSE, REVIEWER_TOKEN, expires_at=expires_at
        )

    def save_preflight(
        self,
        distinct: Verdict,
        *,
        fingerprint: str | None = REVIEWER_FINGERPRINT,
        cfg: EngineConfig | None = None,
        checked_at: str | None = None,
    ) -> ep.PreflightResult:
        target = cfg if cfg is not None else self.default_cfg()
        result = ep.PreflightResult(
            slug=target.repo.slug,
            checked_at=checked_at or datetime.now(UTC).isoformat(),
            overall=distinct,
            findings=(
                Finding(check="reviewer_distinct_from_author", status=distinct, message="stub"),
            ),
            reviewer_fingerprint=fingerprint,
        )
        ep._persist(forge_target_for_workspace(None, target), result)  # noqa: SLF001
        return result

    def record_latch(
        self, organization: str = ORGANIZATION, project_key: str = PROJECT_KEY
    ) -> None:
        ep.write_code_quality_latch(organization, project_key)

    def satisfy_everything(self) -> None:
        self.require(REVIEWER, CODE_QUALITY)
        self.store_reviewer()
        self.save_preflight("pass")
        self.record_latch()


def yesterday() -> str:
    return (datetime.now(UTC).date() - timedelta(days=1)).isoformat()


def _missing_reviewer(install: Install) -> None:
    install.require(REVIEWER)


def _expired_reviewer(install: Install) -> None:
    install.require(REVIEWER)
    install.store_reviewer(expires_at=yesterday())


def _identical_reviewer(install: Install) -> None:
    install.require(REVIEWER)
    install.store_reviewer()
    install.save_preflight("fail")


def _sonar_never_reported(install: Install) -> None:
    install.require(CODE_QUALITY)


def _latch_for_another_project(install: Install) -> None:
    install.require(CODE_QUALITY)
    install.record_latch(project_key="someone-else_widget")


def _unconfigured_install(install: Install) -> None:
    install.leave_unconfigured()


NOT_READY_CASES: dict[str, tuple[Callable[[Install], None], str]] = {
    "missing_reviewer": (_missing_reviewer, "reviewer_credential"),
    "expired_reviewer": (_expired_reviewer, "reviewer_credential"),
    "reviewer_identical_to_author": (_identical_reviewer, "reviewer_credential"),
    "sonar_never_reported": (_sonar_never_reported, "code_quality"),
    "latch_for_another_project": (_latch_for_another_project, "code_quality"),
    "unconfigured_install": (_unconfigured_install, ep.DEFAULT_ENGINE_REQUIREMENT),
}


class SweepTeam(_NoopTeam):
    name = "dependency_sweep"

    def __init__(self) -> None:
        self.runs = 0

    def run(self, *args: Any, **kwargs: Any) -> Any:
        self.runs += 1
        return super().run(*args, **kwargs)


SEEDED_COUNTERS = {
    "interruption_count": 4,
    "requeue_count": 5,
    "server_error_retry_count": 2,
    "resume_inv_id": "resume-fixture",
}


def enqueue_sweep_job(
    queue: Queue, root: Path, *, team_name: str = "dependency_sweep"
) -> tuple[Workspace, Job]:
    ws = workspace_mod.create("sweep", "d", root=root)
    job = queue.enqueue(ws.task_id, team_name)
    queue.run(
        "UPDATE jobs SET interruption_count = ?, requeue_count = ?, "
        "server_error_retry_count = ?, resume_inv_id = ? WHERE id = ?",
        (*SEEDED_COUNTERS.values(), job.id),
    )
    stored = queue.get_job(job.id)
    assert stored is not None
    return ws, stored


def make_dispatcher(
    queue: Queue,
    root: Path,
    team: SweepTeam,
    *,
    clock: FakeClock,
    pubsub: PubSub | None = None,
    team_name: str = "dependency_sweep",
) -> Dispatcher:
    return Dispatcher(
        queue, {team_name: team}, workspaces_root=root, clock=clock, pubsub=pubsub
    )
