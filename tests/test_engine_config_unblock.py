from __future__ import annotations

from alan.engine_config import EngineConfig, RepoIdentity, apply_stored_projection


def _cfg(**overrides: object) -> EngineConfig:
    return EngineConfig(repo=RepoIdentity(owner="a", repo="b"), **overrides)  # type: ignore[arg-type]


def test_unblock_enabled_defaults_to_true() -> None:
    assert _cfg().unblock_enabled is True


def test_unblock_enabled_is_filled_for_a_persisted_blob_without_the_key() -> None:
    cfg = EngineConfig.model_validate({"repo": {"owner": "a", "repo": "b"}})
    assert cfg.unblock_enabled is True


def test_unblock_enabled_roundtrips_through_model_dump() -> None:
    cfg = _cfg(unblock_enabled=False)
    assert EngineConfig.model_validate(cfg.model_dump()).unblock_enabled is False


def test_stored_projection_carries_the_stored_unblock_flag() -> None:
    projected = apply_stored_projection(_cfg(), _cfg(unblock_enabled=False))
    assert projected.unblock_enabled is False


def test_stored_projection_without_a_stored_config_keeps_the_file_flag() -> None:
    assert apply_stored_projection(_cfg(unblock_enabled=False), None).unblock_enabled is False
