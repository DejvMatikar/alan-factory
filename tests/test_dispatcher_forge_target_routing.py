from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from alan import engine_config
from alan import workspace as workspace_mod
from alan.dispatcher import Dispatcher
from alan.engine_config import (
    DEFAULT_FORGE_TARGET,
    EngineConfig,
    ForgeConfig,
    ForgeTarget,
    RepoIdentity,
    forge_target_for_workspace,
)
from alan.loop_guard import REVISIT_CAP
from alan.merge_gate import MergeGateBlocked
from alan.merge_recovery import MergeBlockClass
from alan.queue import Queue
from alan.router import RouterDecision
from alan.teams import _pr_state_paths
from alan.teams._pr_ready import PrReadiness

_PR = 42
_RETRY_FILE = f"pr_{_PR}_remediation_retries.json"
_SECOND_ENGINE_SLUG = "DejvMatikar/example-repo"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        with contextlib.suppress(Exception):
            q.close()


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state_dir = tmp_path / "pr-state"
    monkeypatch.setenv("ALAN_STATE_ROOT", str(state_dir))
    return state_dir


@pytest.fixture
def second_engine_target() -> ForgeTarget:
    repo = RepoIdentity(owner="DejvMatikar", repo="example-repo")
    engine_config.register(
        EngineConfig(
            repo=repo,
            forge=ForgeConfig(repo=repo, credential_env_name="SECOND_ENGINE_GH_TOKEN"),
        ),
        validate_env=False,
    )
    return forge_target_for_workspace(_SECOND_ENGINE_SLUG, None)


def _seed(path: Path, count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(count))


def _stored(path: Path) -> int:
    return int(json.loads(path.read_text()))


def _retry(queue: Queue, root: Path, target: ForgeTarget) -> RouterDecision | None:
    d = Dispatcher(queue, {}, workspaces_root=root)
    ws = workspace_mod.create("forge target routing", "d", root=root)
    readiness = PrReadiness(
        ok=False, merge_state_status="CLEAN", review_decision="APPROVED",
        failed_reasons=("stale_review",),
    )
    attempt = MergeGateBlocked(readiness, MergeBlockClass.STALE_REVIEW, None, target)
    return d._pr_merge_conflict_or_retry(  # noqa: SLF001
        ws, _PR, "reason", "stale_review", [], attempt, {},
    )


def test_retry_counter_written_under_non_default_engine_state_dir(
    queue: Queue, root: Path, state: Path, second_engine_target: ForgeTarget
) -> None:
    decision = _retry(queue, root, second_engine_target)
    assert decision is not None
    assert decision.team == "pr_merge"
    assert "attempt 1" in (decision.reason or "")
    assert _stored(state / "DejvMatikar" / "example-repo" / _RETRY_FILE) == 1
    assert not (state / "DejvMatikar" / "alan" / _RETRY_FILE).exists()
    assert not (state / _RETRY_FILE).exists()


def test_non_default_engine_ignores_counts_in_alan_state_dir(
    queue: Queue, root: Path, state: Path, second_engine_target: ForgeTarget
) -> None:
    alan_namespaced = state / "DejvMatikar" / "alan" / _RETRY_FILE
    alan_legacy = state / _RETRY_FILE
    _seed(alan_namespaced, 2)
    _seed(alan_legacy, 2)
    decision = _retry(queue, root, second_engine_target)
    assert decision is not None
    assert "attempt 1" in (decision.reason or "")
    assert _stored(state / "DejvMatikar" / "example-repo" / _RETRY_FILE) == 1
    assert _stored(alan_namespaced) == 2
    assert _stored(alan_legacy) == 2


def test_non_default_engine_retry_stops_at_cap(
    queue: Queue, root: Path, state: Path, second_engine_target: ForgeTarget
) -> None:
    second_engine_file = state / "DejvMatikar" / "example-repo" / _RETRY_FILE
    _seed(second_engine_file, REVISIT_CAP)
    assert _retry(queue, root, second_engine_target) is None
    assert _stored(second_engine_file) == REVISIT_CAP


def test_default_engine_retry_reads_legacy_unscoped_file(
    queue: Queue, root: Path, state: Path
) -> None:
    _seed(state / _RETRY_FILE, 2)
    decision = _retry(queue, root, DEFAULT_FORGE_TARGET)
    assert decision is not None
    assert "attempt 3" in (decision.reason or "")
    default_path = _pr_state_paths.remediation_retry_count_path(_PR, DEFAULT_FORGE_TARGET)
    assert default_path == state / "DejvMatikar" / "alan" / _RETRY_FILE
    assert _stored(default_path) == 3


def test_default_engine_retry_uses_same_namespaced_file(
    queue: Queue, root: Path, state: Path
) -> None:
    default_path = state / "DejvMatikar" / "alan" / _RETRY_FILE
    _seed(default_path, 1)
    decision = _retry(queue, root, DEFAULT_FORGE_TARGET)
    assert decision is not None
    assert "attempt 2" in (decision.reason or "")
    assert _stored(default_path) == 2
