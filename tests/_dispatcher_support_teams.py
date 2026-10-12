from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from alan.claude_cli import OnProcStarted
from alan.handover import Handover
from alan.teams._base import Team, TeamEvent
from alan.workflow import PendingTaskSpec
from alan.workspace import Workspace


def _make_handover(task_id: str, from_team: str = "research") -> Handover:
    return Handover(
        handover_id=f"handover_{task_id}_test",
        handover_type="forward",
        task_id=task_id,
        from_team=from_team,
        from_invocation_id="inv_001",
        to_team_suggestions=["implementation_plan"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="ok",
    )


class _NoopTeam:
    name = "research"
    supports_resume = False

    def plan_workflow(
        self,
        _workspace: Workspace,
        _prior: Handover | None,
    ) -> list[PendingTaskSpec]:
        return []

    def run(
        self,
        workspace: Workspace,
        _prior: Handover | None,
        _emit: Callable[[TeamEvent], None],
        *, inv_id: str = "inv_001",  # noqa: ARG002
        resume_inv_id: str | None = None,  # noqa: ARG002
        instructions: str | None = None,  # noqa: ARG002
        on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
        task_id: str | None = None,  # noqa: ARG002
    ) -> Handover:
        return _make_handover(workspace.task_id, from_team=self.name)


class _EmittingTeam:
    name = "research"
    supports_resume = False

    def __init__(self, kind: str = "role_started", role: str = "architect") -> None:
        self._kind = kind
        self._role = role

    def plan_workflow(
        self,
        _workspace: Workspace,
        _prior: Handover | None,
    ) -> list[PendingTaskSpec]:
        return []

    def run(
        self,
        workspace: Workspace,
        _prior: Handover | None,
        emit: Callable[[TeamEvent], None],
        *, inv_id: str = "inv_001",  # noqa: ARG002
        resume_inv_id: str | None = None,  # noqa: ARG002
        instructions: str | None = None,  # noqa: ARG002
        on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
        task_id: str | None = None,  # noqa: ARG002
    ) -> Handover:
        emit(TeamEvent(self._kind, {"role": self._role}))
        return _make_handover(workspace.task_id, from_team=self.name)


class _RaisingTeam:
    name = "research"
    supports_resume = False

    def plan_workflow(
        self,
        _workspace: Workspace,
        _prior: Handover | None,
    ) -> list[PendingTaskSpec]:
        return []

    def run(
        self,
        _workspace: Workspace,
        _prior: Handover | None,
        _emit: Callable[[TeamEvent], None],
        *, inv_id: str = "inv_001",  # noqa: ARG002
        resume_inv_id: str | None = None,  # noqa: ARG002
        instructions: str | None = None,  # noqa: ARG002
        on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
        task_id: str | None = None,  # noqa: ARG002
    ) -> Handover:
        raise RuntimeError("kaboom")


class _HangingTeam:
    name = "research"
    supports_resume = False

    def __init__(self, release: threading.Event) -> None:
        self._release = release

    def plan_workflow(
        self,
        _workspace: Workspace,
        _prior: Handover | None,
    ) -> list[PendingTaskSpec]:
        return []

    def run(
        self,
        workspace: Workspace,
        _prior: Handover | None,
        _emit: Callable[[TeamEvent], None],
        *, inv_id: str = "inv_001",  # noqa: ARG002
        resume_inv_id: str | None = None,  # noqa: ARG002
        instructions: str | None = None,  # noqa: ARG002
        on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
        task_id: str | None = None,  # noqa: ARG002
    ) -> Handover:
        self._release.wait(timeout=10.0)
        return _make_handover(workspace.task_id, from_team=self.name)


_NOOP_TEAMS: dict[str, Team] = {"research": _NoopTeam()}


class _RecordingTeam:
    name = "pr_merge"
    supports_resume = False

    def __init__(self) -> None:
        self.called = 0

    def plan_workflow(
        self, _workspace: Workspace, _prior: Handover | None
    ) -> list[PendingTaskSpec]:
        return []

    def run(
        self,
        workspace: Workspace,
        _prior: Handover | None,
        _emit: Callable[[TeamEvent], None],
        *, inv_id: str = "inv_001",  # noqa: ARG002
        resume_inv_id: str | None = None,  # noqa: ARG002
        instructions: str | None = None,  # noqa: ARG002
        on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
        task_id: str | None = None,  # noqa: ARG002
    ) -> Handover:
        self.called += 1
        return _make_handover(workspace.task_id, from_team="pr_merge")


def _make_resumable_team() -> type:
    class ResumableTeam:
        name = "research"
        supports_resume = True

        def plan_workflow(
            self,
            _workspace: Workspace,
            _prior: Handover | None,
        ) -> list[PendingTaskSpec]:
            return [PendingTaskSpec(id="research", role="research", model="sonnet")]

        def run(
            self,
            workspace: Workspace,
            _prior: Handover | None,
            _emit: Callable[[TeamEvent], None],
            *, inv_id: str = "inv_001",  # noqa: ARG002
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: Any = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            raise RuntimeError("genuine node failure")

    return ResumableTeam


class _PrMergeTerminalTeam:
    name = "pr_merge"
    supports_resume = False

    def plan_workflow(
        self,
        _workspace: Workspace,
        _prior: Handover | None,
    ) -> list[PendingTaskSpec]:
        return []

    def run(
        self,
        workspace: Workspace,
        _prior: Handover | None,
        _emit: Callable[[TeamEvent], None],
        *,
        inv_id: str,
        resume_inv_id: str | None = None,  # noqa: ARG002
        instructions: str | None = None,  # noqa: ARG002
        on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
        task_id: str | None = None,  # noqa: ARG002
    ) -> Handover:
        return Handover(
            handover_id="h_pr_merge_terminal",
            handover_type="terminal",
            task_id=workspace.task_id,
            from_team="pr_merge",
            from_invocation_id=inv_id,
            iteration=1,
            timestamp_utc=datetime.now(UTC),
            full_synthesis="merged",
            pr_number=99,
        )


class _PrMergeEscalationTeam:
    name = "pr_merge"
    supports_resume = False

    def plan_workflow(
        self,
        _workspace: Workspace,
        _prior: Handover | None,
    ) -> list[PendingTaskSpec]:
        return []

    def run(
        self,
        workspace: Workspace,
        _prior: Handover | None,
        _emit: Callable[[TeamEvent], None],
        *,
        inv_id: str,
        resume_inv_id: str | None = None,  # noqa: ARG002
        instructions: str | None = None,  # noqa: ARG002
        on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
        task_id: str | None = None,  # noqa: ARG002
    ) -> Handover:
        return Handover(
            handover_id="h_pr_merge_esc",
            handover_type="escalation",
            task_id=workspace.task_id,
            from_team="pr_merge",
            from_invocation_id=inv_id,
            iteration=1,
            timestamp_utc=datetime.now(UTC),
            full_synthesis="blocked",
        )


class _DeployVerifyNoopTeam:
    name = "deploy_verify"
    supports_resume = False
    restart_rederivable = True

    def plan_workflow(
        self, _workspace: Workspace, _prior: Handover | None
    ) -> list[PendingTaskSpec]:
        return []

    def run(
        self,
        workspace: Workspace,
        _prior: Handover | None,
        _emit: Callable[[TeamEvent], None],
        *,
        inv_id: str,  # noqa: ARG002
        resume_inv_id: str | None = None,  # noqa: ARG002
        instructions: str | None = None,  # noqa: ARG002
        on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
        task_id: str | None = None,  # noqa: ARG002
    ) -> Handover:
        return Handover(
            handover_id="h_dv_noop",
            handover_type="terminal",
            task_id=workspace.task_id,
            from_team="deploy_verify",
            from_invocation_id=inv_id,
            iteration=2,
            timestamp_utc=datetime.now(UTC),
            full_synthesis="confirmed",
        )
