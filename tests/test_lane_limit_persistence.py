from __future__ import annotations

from pathlib import Path

import pytest

from alan import engine_config
from alan.engine_config import EngineConfig, RepoIdentity
from alan.lane_limit import get_limit, set_limit
from alan.queue import Queue

_DEFAULT_ENGINE = RepoIdentity(owner="DejvMatikar", repo="alan")
_FILE_BACKED_ENGINE = RepoIdentity(owner="DejvMatikar", repo="example-repo")


@pytest.fixture(autouse=True)
def _engines_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "engines"))


def _restart(db_path: Path) -> Queue:
    return Queue(db_path=db_path)


def test_lane_limit_survives_restart_for_default_engine(tmp_path: Path) -> None:
    db_path = tmp_path / "queue.db"
    q1 = Queue(db_path=db_path)
    try:
        set_limit(q1, _DEFAULT_ENGINE, 4, source="api")
    finally:
        q1.close()

    q2 = _restart(db_path)
    try:
        assert get_limit(q2, _DEFAULT_ENGINE) == 4
    finally:
        q2.close()


def test_lane_limit_survives_restart_for_file_backed_engine(tmp_path: Path) -> None:
    db_path = tmp_path / "queue.db"
    q1 = Queue(db_path=db_path)
    try:
        set_limit(q1, _FILE_BACKED_ENGINE, 2, source="api")
    finally:
        q1.close()

    q2 = _restart(db_path)
    try:
        assert get_limit(q2, _FILE_BACKED_ENGINE) == 2
    finally:
        q2.close()


def test_lane_limit_clear_survives_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "queue.db"
    q1 = Queue(db_path=db_path)
    try:
        set_limit(q1, _FILE_BACKED_ENGINE, 3, source="api")
        set_limit(q1, _FILE_BACKED_ENGINE, None, source="api")
    finally:
        q1.close()

    q2 = _restart(db_path)
    try:
        assert get_limit(q2, _FILE_BACKED_ENGINE) is None
    finally:
        q2.close()


def test_lane_limits_for_distinct_engines_do_not_collide_across_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "queue.db"
    q1 = Queue(db_path=db_path)
    try:
        set_limit(q1, _DEFAULT_ENGINE, 6, source="api")
        set_limit(q1, _FILE_BACKED_ENGINE, 2, source="api")
    finally:
        q1.close()

    q2 = _restart(db_path)
    try:
        assert get_limit(q2, _DEFAULT_ENGINE) == 6
        assert get_limit(q2, _FILE_BACKED_ENGINE) == 2
    finally:
        q2.close()


def test_lane_limit_survives_engine_boot_loader_rerun(tmp_path: Path) -> None:
    engine_config.register_and_persist(
        EngineConfig(repo=_DEFAULT_ENGINE), default=True, validate_env=False
    )
    engine_config.register_and_persist(EngineConfig(repo=_FILE_BACKED_ENGINE), validate_env=False)
    db_path = tmp_path / "queue.db"
    q1 = Queue(db_path=db_path)
    try:
        set_limit(q1, _DEFAULT_ENGINE, 4, source="api")
        set_limit(q1, _FILE_BACKED_ENGINE, 2, source="api")
    finally:
        q1.close()
    audit_path = tmp_path / "engines" / "lane_limit_audit.jsonl"
    assert audit_path.is_file()

    loaded = engine_config.load_persisted_engines()

    assert {c.repo for c in loaded} == {_DEFAULT_ENGINE, _FILE_BACKED_ENGINE}
    q2 = _restart(db_path)
    try:
        assert get_limit(q2, _DEFAULT_ENGINE) == 4
        assert get_limit(q2, _FILE_BACKED_ENGINE) == 2
    finally:
        q2.close()
