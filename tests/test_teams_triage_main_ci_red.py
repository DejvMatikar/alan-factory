from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any

import pytest

from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan.dep_advisory_seed import seed_advisory_checks
from alan.queue import Queue
from alan.routes.infra import _build_triage_team  # noqa: SLF001
from alan.teams import triage as triage_mod
from alan.teams.triage import (
    ROLE_SPECS,
    RedMainRun,
    TriageTeam,
    _red_main_ci_runs,  # noqa: SLF001
    load_triage_sweep_status,
)
from alan.teams.triage_pregate import ClassifiedSurface


def _main_ci(
    *,
    blocked: bool = True,
    kind: str = "main_ci",
    run_id: object = 555,
    repo: object = "acme/widget",
    sha: object = "abc123",
) -> ClassifiedSurface:
    obs: dict[str, Any] = {
        "run_id": run_id,
        "status": "completed",
        "conclusion": "failure" if blocked else "success",
        "sha": sha,
        "repo": repo,
    }
    return ClassifiedSurface(
        f"{kind}:{repo}", kind, blocked, "ci_failure" if blocked else "", obs
    )


def test_a_blocked_main_ci_surface_produces_a_run_keyed_by_slug_and_run_id() -> None:
    assert _red_main_ci_runs([_main_ci()]) == [RedMainRun("acme/widget", "555", "abc123")]


def test_a_string_run_id_is_kept_verbatim() -> None:
    assert _red_main_ci_runs([_main_ci(run_id="9001")])[0].run_id == "9001"


def test_a_non_string_sha_becomes_none() -> None:
    assert _red_main_ci_runs([_main_ci(sha=None)])[0].sha is None


def test_one_run_per_red_engine_in_surface_order() -> None:
    runs = _red_main_ci_runs([_main_ci(repo="b/two", run_id=2), _main_ci(repo="a/one", run_id=1)])

    assert [(run.slug, run.run_id) for run in runs] == [("b/two", "2"), ("a/one", "1")]


@pytest.mark.parametrize(
    "surface",
    [
        _main_ci(blocked=False),
        _main_ci(kind="deploy"),
        _main_ci(kind="build_task"),
        _main_ci(run_id=None),
        _main_ci(run_id=["x"]),
        _main_ci(repo=None),
        _main_ci(repo=7),
    ],
)
def test_other_surfaces_produce_no_run(surface: ClassifiedSurface) -> None:
    assert _red_main_ci_runs([surface]) == []


def test_no_surfaces_produce_no_runs() -> None:
    assert _red_main_ci_runs([]) == []


def _team(queue: Queue, ws_root: Path, hook: Any) -> TriageTeam:
    return TriageTeam(queue=queue, workspaces_root=ws_root, on_main_ci_red=hook)


def _gather(monkeypatch: pytest.MonkeyPatch, surfaces: list[ClassifiedSurface]) -> None:
    monkeypatch.setattr(triage_mod, "gather_all", lambda *_a, **_k: list(surfaces))


def test_the_hook_receives_the_red_runs_of_the_sweep(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[RedMainRun]] = []
    _gather(monkeypatch, [_main_ci()])

    team = _team(queue, ws_root, lambda runs: seen.append(list(runs)))

    surfaces, _, degraded = team._run_sensors()  # noqa: SLF001

    assert seen == [[RedMainRun("acme/widget", "555", "abc123")]]
    assert degraded == []
    assert any(s.kind == "main_ci" for s in surfaces)


def test_the_hook_fires_again_on_every_consecutive_sweep(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[RedMainRun]] = []
    _gather(monkeypatch, [_main_ci()])
    team = _team(queue, ws_root, lambda runs: seen.append(list(runs)))

    team._run_sensors()  # noqa: SLF001
    team._run_sensors()  # noqa: SLF001

    assert len(seen) == 2
    assert seen[0] == seen[1] != []


def test_a_wired_hook_is_called_with_no_runs_when_main_is_green(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[RedMainRun]] = []
    _gather(monkeypatch, [_main_ci(blocked=False)])

    _team(queue, ws_root, lambda runs: seen.append(list(runs)))._run_sensors()  # noqa: SLF001

    assert seen == [[]]


def test_without_a_hook_the_sweep_is_unchanged(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gather(monkeypatch, [_main_ci()])

    _, _, degraded = TriageTeam(queue=queue, workspaces_root=ws_root)._run_sensors()  # noqa: SLF001

    assert degraded == []


def test_a_raising_hook_marks_the_sweep_degraded_and_the_sweep_continues(
    queue: Queue,
    ws_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _gather(monkeypatch, [_main_ci()])

    def _boom(_runs: object) -> None:
        raise RuntimeError("seed failed")

    team = _team(queue, ws_root, _boom)
    ws = workspace_mod.create("Failure Triage Sweep", "d", root=ws_root, team="triage")
    inv_id = workflow_mod.seed_pending_workflow(ws, team="triage", tasks=list(ROLE_SPECS))

    surfaces, _, degraded = team._run_sensors()  # noqa: SLF001
    team.run(ws, None, lambda _event: None, inv_id=inv_id)

    assert degraded == ["main_ci_red"]
    assert any(s.kind == "main_ci" for s in surfaces)
    status = load_triage_sweep_status()
    assert status is not None
    assert status.outcome == "degraded"


def test_the_production_triage_team_wires_the_advisory_seed(
    queue: Queue, ws_root: Path
) -> None:
    team = _build_triage_team(queue, ws_root)

    hook = team.on_main_ci_red
    assert isinstance(hook, partial)
    assert hook.func is seed_advisory_checks
    assert hook.args == (queue, ws_root)
