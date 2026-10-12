from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from alan import engine_config as engine_config_mod
from alan import onboarding as onboarding_mod
from alan import project as project_mod
from alan import secrets_store
from alan.branch_protection_compat import Finding
from alan.dispatcher import Dispatcher
from alan.engine_config import EngineConfig, RepoIdentity
from alan.engine_preflight import PreflightResult
from alan.onboarding import (
    _ATTEMPTS,
    _IN_FLIGHT,
    Attempt,
    OnboardingError,
    _activate_project,
    _create_and_bind_project,
    _delete_project_best_effort,
    _dispose_pool_best_effort,
    _ensure_secret,
    _finding_message,
    _gate_preflight,
    _read_provisioning_project_or_error,
    _reconstruct_attempt,
    _register_engine,
    _reserve_in_flight,
    _reserve_slug,
    _spawn_warm_task,
    _teardown_attempt,
    _warm_and_activate,
    create_attempt,
    rollback_attempt,
)
from alan.project import ProjectStatus
from alan.queue import Queue
from alan.slot_pool import PoolBusyError, PoolState, SlotPool


@pytest.fixture(autouse=True)
def _isolate_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_SECRETS_STORE_ROOT", str(tmp_path / "secrets"))
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "engines"))


@pytest.fixture(autouse=True)
def _reset_attempts() -> Iterator[None]:
    _ATTEMPTS.clear()
    _IN_FLIGHT.clear()
    yield
    _ATTEMPTS.clear()
    _IN_FLIGHT.clear()


class _RecordingPoolFactory:
    def __init__(self, root: Path) -> None:
        self._root = root

    def __call__(self, cfg: EngineConfig) -> SlotPool:
        return SlotPool(
            root=self._root / cfg.repo.slug.replace("/", "_"),
            size=cfg.pool_size or 2,
            repo_root=str(self._root),
        )


@pytest.fixture
def dispatcher_queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "dispatcher-queue.db")
    try:
        yield q
    finally:
        q.close()


@pytest.fixture
def dispatcher(dispatcher_queue: Queue, tmp_path: Path) -> Dispatcher:
    return Dispatcher(
        dispatcher_queue, {}, pool_factory=_RecordingPoolFactory(tmp_path / "pools")
    )


_REPO = RepoIdentity(owner="acme", repo="widget")


def _resolve_transport(slug: str) -> str | None:
    return secrets_store.resolve_secret(slug, secrets_store.TRANSPORT_PURPOSE, env_fallback=None)


def _pass_preflight(cfg: EngineConfig) -> PreflightResult:
    return PreflightResult(
        slug=cfg.repo.slug, checked_at="2026-07-22T00:00:00+00:00", overall="pass", findings=()
    )


def _fail_preflight(cfg: EngineConfig) -> PreflightResult:
    return PreflightResult(
        slug=cfg.repo.slug,
        checked_at="2026-07-22T00:00:00+00:00",
        overall="fail",
        findings=(
            Finding(check="repo_clonable", status="fail", message="no access", remedy="fix token"),
            Finding(check="author_token_valid", status="fail", message="missing"),
        ),
    )


def _unknown_preflight(cfg: EngineConfig) -> PreflightResult:
    return PreflightResult(
        slug=cfg.repo.slug,
        checked_at="2026-07-22T00:00:00+00:00",
        overall="unknown",
        findings=(
            Finding(
                check="reviewer_distinct_from_author",
                status="unknown",
                message="cannot resolve reviewer",
            ),
        ),
    )


class _FakePoolStateDispatcher:
    def __init__(self, states_sequence: list[dict[str, PoolState]]) -> None:
        self._states_sequence = states_sequence
        self._calls = 0

    def pool_states(self) -> dict[str, PoolState]:
        idx = min(self._calls, len(self._states_sequence) - 1)
        self._calls += 1
        return self._states_sequence[idx]


def _pool_state(slug: str, *, warm: bool) -> PoolState:
    return PoolState(
        slug=slug, size=2, free_count=2, leased_count=0, warm=warm,
        quiescing=False, disk_usage_bytes=0,
    )


def test_reserve_slug_passes_when_repo_not_registered() -> None:
    _reserve_slug(_REPO)


def test_reserve_slug_raises_409_when_repo_already_registered() -> None:
    engine_config_mod.register_and_persist(EngineConfig(repo=_REPO), validate_env=False)
    with pytest.raises(OnboardingError) as exc_info:
        _reserve_slug(_REPO)
    assert exc_info.value.status_code == 409


def test_ensure_secret_writes_new_secret_and_registers_rollback() -> None:
    with contextlib.ExitStack() as stack:
        created = _ensure_secret(_REPO, "ghp_abc", stack)
        assert created is True
        assert _resolve_transport(_REPO.slug) == "ghp_abc"
    assert _resolve_transport(_REPO.slug) is None


def test_ensure_secret_reuses_existing_secret_without_rollback() -> None:
    secrets_store.write_secret(_REPO.slug, secrets_store.TRANSPORT_PURPOSE, "original")
    with contextlib.ExitStack() as stack:
        created = _ensure_secret(_REPO, "new-value-ignored", stack)
        assert created is False
    assert _resolve_transport(_REPO.slug) == "original"


def test_register_engine_unknown_template_raises_422() -> None:
    with contextlib.ExitStack() as stack, pytest.raises(OnboardingError) as exc_info:
        _register_engine(_REPO, "cobol", stack)
    assert exc_info.value.status_code == 422


def test_register_engine_registers_and_rolls_back_on_exit() -> None:
    with contextlib.ExitStack() as stack:
        cfg = _register_engine(_REPO, "python", stack)
        assert cfg.repo == _REPO
        assert engine_config_mod.lookup_by_repo(_REPO) is not None
    assert engine_config_mod.lookup_by_repo(_REPO) is None


def test_register_engine_duplicate_raises_409(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(cfg: EngineConfig, *, validate_env: bool = True) -> None:
        raise engine_config_mod.DuplicateEngineError("boom")

    monkeypatch.setattr(engine_config_mod, "register_and_persist", _boom)
    with contextlib.ExitStack() as stack, pytest.raises(OnboardingError) as exc_info:
        _register_engine(_REPO, "python", stack)
    assert exc_info.value.status_code == 409


def test_finding_message_includes_remedy_when_present() -> None:
    f = Finding(check="x", status="fail", message="bad", remedy="fix it")
    assert _finding_message(f) == "x: bad (remedy: fix it)"


def test_finding_message_omits_remedy_when_absent() -> None:
    f = Finding(check="x", status="fail", message="bad")
    assert _finding_message(f) == "x: bad"


def test_gate_preflight_passes_silently(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(onboarding_mod, "run_preflight", _pass_preflight)
    _gate_preflight(EngineConfig(repo=_REPO))


def test_gate_preflight_raises_422_naming_failing_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(onboarding_mod, "run_preflight", _fail_preflight)
    with pytest.raises(OnboardingError) as exc_info:
        _gate_preflight(EngineConfig(repo=_REPO))
    assert exc_info.value.status_code == 422
    assert "repo_clonable: no access (remedy: fix token)" in exc_info.value.detail
    assert "author_token_valid: missing" in exc_info.value.detail


def test_gate_preflight_allows_unknown_overall_and_logs_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(onboarding_mod, "run_preflight", _unknown_preflight)
    with caplog.at_level("WARNING"):
        _gate_preflight(EngineConfig(repo=_REPO))
    assert "reviewer_distinct_from_author" in caplog.text


class _RaisingDispatcher:
    def dispose_pool(self, slug: str) -> None:
        raise PoolBusyError("busy")


def test_dispose_pool_best_effort_swallows_pool_busy_error() -> None:
    _dispose_pool_best_effort(_RaisingDispatcher(), _REPO.slug)  # type: ignore[arg-type]


def test_dispose_pool_best_effort_disposes_normally(dispatcher: Dispatcher) -> None:
    dispatcher.register_pool(EngineConfig(repo=_REPO))
    _dispose_pool_best_effort(dispatcher, _REPO.slug)
    assert _REPO.slug not in dispatcher._slot_pools_by_engine  # noqa: SLF001


def test_delete_project_best_effort_deletes_existing() -> None:
    project = project_mod.create("Delete Me")
    _delete_project_best_effort(project.id)
    assert project_mod.list_projects() == []


def test_delete_project_best_effort_swallows_missing_project() -> None:
    _delete_project_best_effort("does-not-exist")


def test_create_and_bind_project_sets_provisioning_and_engine_key() -> None:
    with contextlib.ExitStack() as stack:
        project = _create_and_bind_project("My Proj", _REPO, stack)
        assert project.status == ProjectStatus.PROVISIONING
        assert project.engine_key == "acme/widget"
        stack.pop_all()
    assert project_mod.read(project.id).status == ProjectStatus.PROVISIONING


def test_create_and_bind_project_leaves_intended_engine_key_unstamped() -> None:
    with contextlib.ExitStack() as stack:
        project = _create_and_bind_project("My Proj", _REPO, stack)
        assert project.intended_engine_key is None
        stack.pop_all()
    assert project_mod.read(project.id).intended_engine_key is None


def test_create_and_bind_project_rolls_back_on_exit() -> None:
    with contextlib.ExitStack() as stack:
        project = _create_and_bind_project("My Proj", _REPO, stack)
    with pytest.raises(project_mod.ProjectError):
        project_mod.read(project.id)


def test_create_attempt_success_end_to_end(
    dispatcher: Dispatcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding_mod, "run_preflight", _pass_preflight)
    spawned = []
    monkeypatch.setattr(
        onboarding_mod, "_spawn_warm_task", lambda d, pid, slug: spawned.append((d, pid, slug))
    )

    project = create_attempt(
        dispatcher=dispatcher,
        title="My Project",
        owner="acme",
        repo="widget",
        template="python",
        token="ghp_abc",
    )

    assert project.status == ProjectStatus.PROVISIONING
    assert project.engine_key == "acme/widget"
    assert _resolve_transport("acme/widget") == "ghp_abc"
    cfg = engine_config_mod.lookup_by_repo(_REPO)
    assert cfg is not None
    assert cfg.forge is not None
    assert cfg.forge.repo.slug == "acme/widget"
    assert "acme/widget" in dispatcher._slot_pools_by_engine  # noqa: SLF001
    assert spawned == [(dispatcher, project.id, "acme/widget")]
    attempt = _ATTEMPTS[project.id]
    assert attempt.repo == _REPO
    assert attempt.secret_created is True


def test_create_attempt_reuses_pre_existing_secret(
    dispatcher: Dispatcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding_mod, "run_preflight", _pass_preflight)
    monkeypatch.setattr(onboarding_mod, "_spawn_warm_task", lambda *a: None)
    secrets_store.write_secret("acme/widget", secrets_store.TRANSPORT_PURPOSE, "pre-existing")

    project = create_attempt(
        dispatcher=dispatcher,
        title="My Project",
        owner="acme",
        repo="widget",
        template="python",
        token="ignored-new-value",
    )

    assert _resolve_transport("acme/widget") == "pre-existing"
    assert _ATTEMPTS[project.id].secret_created is False


def test_reserve_in_flight_raises_409_when_already_reserved() -> None:
    _reserve_in_flight(_REPO)
    with pytest.raises(OnboardingError) as exc_info:
        _reserve_in_flight(_REPO)
    assert exc_info.value.status_code == 409


def test_concurrent_same_slug_returns_409(
    dispatcher: Dispatcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding_mod, "run_preflight", _pass_preflight)
    monkeypatch.setattr(onboarding_mod, "_spawn_warm_task", lambda *a: None)

    entered_secret_write = threading.Event()
    release_secret_write = threading.Event()
    original_write_secret = secrets_store.write_secret

    def _blocking_write_secret(slug: str, purpose: str, value: str) -> None:
        entered_secret_write.set()
        release_secret_write.wait(timeout=5)
        original_write_secret(slug, purpose, value)

    monkeypatch.setattr(secrets_store, "write_secret", _blocking_write_secret)

    first_result: list[object] = []

    def _run_first() -> None:
        try:
            first_result.append(
                create_attempt(
                    dispatcher=dispatcher,
                    title="First",
                    owner="acme",
                    repo="widget",
                    template="python",
                    token="ghp_first",
                )
            )
        except OnboardingError as exc:
            first_result.append(exc)

    first_thread = threading.Thread(target=_run_first)
    first_thread.start()
    assert entered_secret_write.wait(timeout=5)

    with pytest.raises(OnboardingError) as exc_info:
        create_attempt(
            dispatcher=dispatcher,
            title="Second",
            owner="acme",
            repo="widget",
            template="python",
            token="ghp_second",
        )
    assert exc_info.value.status_code == 409

    release_secret_write.set()
    first_thread.join(timeout=5)

    assert len(first_result) == 1
    assert isinstance(first_result[0], project_mod.Project)
    assert _resolve_transport("acme/widget") == "ghp_first"
    assert engine_config_mod.lookup_by_repo(_REPO) is not None


def test_create_attempt_repo_already_onboarded_returns_409_zero_writes(
    dispatcher: Dispatcher,
) -> None:
    engine_config_mod.register_and_persist(EngineConfig(repo=_REPO), validate_env=False)

    with pytest.raises(OnboardingError) as exc_info:
        create_attempt(
            dispatcher=dispatcher,
            title="My Project",
            owner="acme",
            repo="widget",
            template="python",
            token="ghp_abc",
        )

    assert exc_info.value.status_code == 409
    assert secrets_store.list_secrets() == []
    assert project_mod.list_projects() == []


def test_create_attempt_unknown_template_rolls_back_secret(dispatcher: Dispatcher) -> None:
    with pytest.raises(OnboardingError) as exc_info:
        create_attempt(
            dispatcher=dispatcher,
            title="My Project",
            owner="acme",
            repo="widget",
            template="cobol",
            token="ghp_abc",
        )

    assert exc_info.value.status_code == 422
    assert secrets_store.list_secrets() == []
    assert engine_config_mod.lookup_by_repo(_REPO) is None
    assert project_mod.list_projects() == []


def test_create_attempt_preflight_failure_rolls_back_engine_and_secret(
    dispatcher: Dispatcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding_mod, "run_preflight", _fail_preflight)

    with pytest.raises(OnboardingError) as exc_info:
        create_attempt(
            dispatcher=dispatcher,
            title="My Project",
            owner="acme",
            repo="widget",
            template="python",
            token="ghp_abc",
        )

    assert exc_info.value.status_code == 422
    assert secrets_store.list_secrets() == []
    assert engine_config_mod.lookup_by_repo(_REPO) is None
    assert "acme/widget" not in dispatcher._slot_pools_by_engine  # noqa: SLF001
    assert project_mod.list_projects() == []


def test_create_attempt_preflight_failure_preserves_pre_existing_secret(
    dispatcher: Dispatcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding_mod, "run_preflight", _fail_preflight)
    secrets_store.write_secret("acme/widget", secrets_store.TRANSPORT_PURPOSE, "pre-existing")

    with pytest.raises(OnboardingError):
        create_attempt(
            dispatcher=dispatcher,
            title="My Project",
            owner="acme",
            repo="widget",
            template="python",
            token="ignored",
        )

    assert _resolve_transport("acme/widget") == "pre-existing"


def test_create_attempt_pool_provision_failure_rolls_back_engine_and_secret(
    dispatcher: Dispatcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding_mod, "run_preflight", _pass_preflight)

    def _boom(cfg: EngineConfig) -> None:
        raise RuntimeError("pool boom")

    monkeypatch.setattr(dispatcher, "register_pool", _boom)

    with pytest.raises(RuntimeError, match="pool boom"):
        create_attempt(
            dispatcher=dispatcher,
            title="My Project",
            owner="acme",
            repo="widget",
            template="python",
            token="ghp_abc",
        )

    assert secrets_store.list_secrets() == []
    assert engine_config_mod.lookup_by_repo(_REPO) is None
    assert project_mod.list_projects() == []


def test_create_attempt_project_bind_failure_rolls_back_everything(
    dispatcher: Dispatcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding_mod, "run_preflight", _pass_preflight)

    def _boom(project: object, updates: object, *, root: object = None) -> None:
        raise RuntimeError("bind boom")

    monkeypatch.setattr(project_mod, "update", _boom)

    with pytest.raises(RuntimeError, match="bind boom"):
        create_attempt(
            dispatcher=dispatcher,
            title="My Project",
            owner="acme",
            repo="widget",
            template="python",
            token="ghp_abc",
        )

    assert secrets_store.list_secrets() == []
    assert engine_config_mod.lookup_by_repo(_REPO) is None
    assert "acme/widget" not in dispatcher._slot_pools_by_engine  # noqa: SLF001
    assert project_mod.list_projects() == []


def test_activate_project_flips_provisioning_to_active() -> None:
    project = project_mod.create("Act Proj")
    project_mod.update(project, {"status": ProjectStatus.PROVISIONING})
    _activate_project(project.id)
    assert project_mod.read(project.id).status == ProjectStatus.ACTIVE


def test_activate_project_noop_when_not_provisioning() -> None:
    project = project_mod.create("Act Proj 2")
    _activate_project(project.id)
    assert project_mod.read(project.id).status == ProjectStatus.ACTIVE


def test_activate_project_removes_attempt_record() -> None:
    project = project_mod.create("Act Proj 3")
    project_mod.update(project, {"status": ProjectStatus.PROVISIONING})
    _ATTEMPTS[project.id] = Attempt(project_id=project.id, repo=None, secret_created=False)

    _activate_project(project.id)

    assert project.id not in _ATTEMPTS


def test_activate_project_noop_when_missing() -> None:
    _activate_project("does-not-exist")


def test_warm_and_activate_activates_after_becoming_warm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(onboarding_mod, "_WARM_POLL_INTERVAL_S", 0.001)
    fake = _FakePoolStateDispatcher([
        {"acme/widget": _pool_state("acme/widget", warm=False)},
        {"acme/widget": _pool_state("acme/widget", warm=True)},
    ])
    project = project_mod.create("Warm Proj")
    project_mod.update(project, {"engine_key": "acme/widget", "status": ProjectStatus.PROVISIONING})

    _warm_and_activate(fake, project.id, "acme/widget")  # type: ignore[arg-type]

    assert project_mod.read(project.id).status == ProjectStatus.ACTIVE


def test_warm_and_activate_times_out_and_leaves_provisioning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(onboarding_mod, "_WARM_TIMEOUT_S", 0.03)
    monkeypatch.setattr(onboarding_mod, "_WARM_POLL_INTERVAL_S", 0.01)
    fake = _FakePoolStateDispatcher([{}])
    project = project_mod.create("Never Warm Proj")
    project_mod.update(project, {"engine_key": "acme/widget", "status": ProjectStatus.PROVISIONING})

    _warm_and_activate(fake, project.id, "acme/widget")  # type: ignore[arg-type]

    assert project_mod.read(project.id).status == ProjectStatus.PROVISIONING


def test_spawn_warm_task_starts_background_thread_that_activates(
    dispatcher: Dispatcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding_mod, "_WARM_POLL_INTERVAL_S", 0.01)
    dispatcher.register_pool(EngineConfig(repo=_REPO))
    project = project_mod.create("Spawn Proj")
    project_mod.update(project, {"engine_key": "acme/widget", "status": ProjectStatus.PROVISIONING})

    _spawn_warm_task(dispatcher, project.id, "acme/widget")

    deadline = time.monotonic() + 5.0
    status = project_mod.read(project.id).status
    while time.monotonic() < deadline and status != ProjectStatus.ACTIVE:
        time.sleep(0.02)
        status = project_mod.read(project.id).status
    assert status == ProjectStatus.ACTIVE


def test_read_provisioning_project_missing_raises_404() -> None:
    with pytest.raises(OnboardingError) as exc_info:
        _read_provisioning_project_or_error("no-such-id")
    assert exc_info.value.status_code == 404


def test_read_provisioning_project_not_provisioning_raises_409() -> None:
    project = project_mod.create("Active Proj")
    with pytest.raises(OnboardingError) as exc_info:
        _read_provisioning_project_or_error(project.id)
    assert exc_info.value.status_code == 409


def test_read_provisioning_project_returns_when_provisioning() -> None:
    project = project_mod.create("Prov Proj")
    project_mod.update(project, {"status": ProjectStatus.PROVISIONING})
    result = _read_provisioning_project_or_error(project.id)
    assert result.id == project.id


def test_reconstruct_attempt_parses_engine_key() -> None:
    project = project_mod.create("Recon Proj")
    project = project_mod.update(
        project, {"engine_key": "acme/widget", "status": ProjectStatus.PROVISIONING}
    )
    attempt = _reconstruct_attempt(project.id, project)
    assert attempt.repo == _REPO
    assert attempt.secret_created is False


def test_reconstruct_attempt_handles_missing_engine_key() -> None:
    project = project_mod.create("Recon Proj 2")
    project = project_mod.update(project, {"status": ProjectStatus.PROVISIONING})
    attempt = _reconstruct_attempt(project.id, project)
    assert attempt.repo is None


def test_teardown_attempt_with_repo_and_created_secret_deletes_everything(
    dispatcher: Dispatcher,
) -> None:
    secrets_store.write_secret(_REPO.slug, secrets_store.TRANSPORT_PURPOSE, "tok")
    engine_config_mod.register_and_persist(EngineConfig(repo=_REPO), validate_env=False)
    dispatcher.register_pool(EngineConfig(repo=_REPO))
    project = project_mod.create("Teardown Proj")
    project = project_mod.update(
        project, {"engine_key": _REPO.slug, "status": ProjectStatus.PROVISIONING}
    )
    attempt = Attempt(project_id=project.id, repo=_REPO, secret_created=True)

    _teardown_attempt(dispatcher, attempt)

    assert _resolve_transport(_REPO.slug) is None
    assert engine_config_mod.lookup_by_repo(_REPO) is None
    assert _REPO.slug not in dispatcher._slot_pools_by_engine  # noqa: SLF001
    with pytest.raises(project_mod.ProjectError):
        project_mod.read(project.id)


def test_teardown_attempt_preserves_secret_when_not_created(dispatcher: Dispatcher) -> None:
    secrets_store.write_secret(_REPO.slug, secrets_store.TRANSPORT_PURPOSE, "keep-me")
    engine_config_mod.register_and_persist(EngineConfig(repo=_REPO), validate_env=False)
    project = project_mod.create("Teardown Proj 2")
    project = project_mod.update(
        project, {"engine_key": _REPO.slug, "status": ProjectStatus.PROVISIONING}
    )
    attempt = Attempt(project_id=project.id, repo=_REPO, secret_created=False)

    _teardown_attempt(dispatcher, attempt)

    assert _resolve_transport(_REPO.slug) == "keep-me"
    assert engine_config_mod.lookup_by_repo(_REPO) is None


def test_teardown_attempt_with_no_repo_only_deletes_project(dispatcher: Dispatcher) -> None:
    project = project_mod.create("Teardown Proj 3")
    project = project_mod.update(project, {"status": ProjectStatus.PROVISIONING})
    attempt = Attempt(project_id=project.id, repo=None, secret_created=False)

    _teardown_attempt(dispatcher, attempt)

    with pytest.raises(project_mod.ProjectError):
        project_mod.read(project.id)


def test_rollback_attempt_unknown_project_raises_404(dispatcher: Dispatcher) -> None:
    with pytest.raises(OnboardingError) as exc_info:
        rollback_attempt(dispatcher, "no-such-id")
    assert exc_info.value.status_code == 404


def test_rollback_attempt_refuses_non_provisioning_project(dispatcher: Dispatcher) -> None:
    project = project_mod.create("Active Proj")
    with pytest.raises(OnboardingError) as exc_info:
        rollback_attempt(dispatcher, project.id)
    assert exc_info.value.status_code == 409


def test_rollback_attempt_uses_in_memory_record_when_present(
    dispatcher: Dispatcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding_mod, "run_preflight", _pass_preflight)
    monkeypatch.setattr(onboarding_mod, "_spawn_warm_task", lambda *a: None)
    project = create_attempt(
        dispatcher=dispatcher,
        title="Rollback Proj",
        owner="acme",
        repo="widget",
        template="python",
        token="ghp_abc",
    )
    assert project.id in _ATTEMPTS

    rollback_attempt(dispatcher, project.id)

    assert project.id not in _ATTEMPTS
    assert secrets_store.list_secrets() == []
    assert engine_config_mod.lookup_by_repo(_REPO) is None
    with pytest.raises(project_mod.ProjectError):
        project_mod.read(project.id)


def test_rollback_attempt_reconstructs_after_restart_preserves_secret(
    dispatcher: Dispatcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding_mod, "run_preflight", _pass_preflight)
    monkeypatch.setattr(onboarding_mod, "_spawn_warm_task", lambda *a: None)
    project = create_attempt(
        dispatcher=dispatcher,
        title="Restart Proj",
        owner="acme",
        repo="widget",
        template="python",
        token="ghp_abc",
    )
    _ATTEMPTS.clear()

    rollback_attempt(dispatcher, project.id)

    assert _resolve_transport("acme/widget") == "ghp_abc"
    assert engine_config_mod.lookup_by_repo(_REPO) is None
    with pytest.raises(project_mod.ProjectError):
        project_mod.read(project.id)
