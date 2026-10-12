from __future__ import annotations

import logging
from pathlib import Path

import pytest

import alan.config_files as config_files_mod
import alan.default_engine_boot as default_engine_boot_mod
import alan.engine_config as engine_config_mod
import alan.routes.infra as infra_mod
from alan.config_files import QuarantinedEngineFile
from alan.routes.infra import _load_file_backed_engines


def test_valid_file_backed_engine_registers_and_gets_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = engine_config_mod.RepoIdentity(owner="acme", repo="widget")
    cfg = engine_config_mod.EngineConfig(repo=repo, posture=engine_config_mod.Posture.propose)
    monkeypatch.setattr(config_files_mod, "load_tracked_configs", lambda: ({repo: cfg}, []))
    _load_file_backed_engines()
    assert engine_config_mod.lookup_by_repo(repo) == cfg
    assert engine_config_mod.is_file_backed(repo) is True
    assert engine_config_mod.file_ceiling(repo) == engine_config_mod.Posture.propose


def test_quarantined_parse_failure_is_logged_not_registered(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    record = QuarantinedEngineFile(path=Path("bad.yaml"), error="boom")
    monkeypatch.setattr(config_files_mod, "load_tracked_configs", lambda: ({}, [record]))
    with caplog.at_level(logging.WARNING, logger="alan.server"):
        _load_file_backed_engines()
    assert any("bad.yaml" in r.message for r in caplog.records)


def test_registration_failure_is_quarantined_with_restrictive_ceiling(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    repo = engine_config_mod.RepoIdentity(owner="acme", repo="widget")
    bad_cfg = engine_config_mod.EngineConfig(repo=repo, cache_env={"PATH": "x"})
    monkeypatch.setattr(config_files_mod, "load_tracked_configs", lambda: ({repo: bad_cfg}, []))
    with caplog.at_level(logging.WARNING, logger="alan.server"):
        _load_file_backed_engines()
    assert engine_config_mod.lookup_by_repo(repo) is None
    assert engine_config_mod.is_file_backed(repo) is True
    assert engine_config_mod.file_ceiling(repo) == engine_config_mod.Posture.observe
    assert any("acme/widget" in r.message for r in caplog.records)


def test_registration_failure_recorded_in_quarantined_configs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = engine_config_mod.RepoIdentity(owner="acme", repo="widget")
    bad_cfg = engine_config_mod.EngineConfig(repo=repo, cache_env={"PATH": "x"})
    monkeypatch.setattr(config_files_mod, "load_tracked_configs", lambda: ({repo: bad_cfg}, []))
    _load_file_backed_engines()
    records = engine_config_mod.quarantined_configs()
    assert len(records) == 1
    assert records[0].repo == repo
    assert records[0].path.name == "widget.yaml"
    assert records[0].error


def test_parse_failure_resolves_via_stem_match_to_registered_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = engine_config_mod.RepoIdentity(owner="acme", repo="widget")
    engine_config_mod.register(engine_config_mod.EngineConfig(repo=repo))
    record = QuarantinedEngineFile(path=Path("config/engines.d/widget.yaml"), error="boom")
    monkeypatch.setattr(config_files_mod, "load_tracked_configs", lambda: ({}, [record]))
    _load_file_backed_engines()
    assert engine_config_mod.is_file_backed(repo) is True
    assert engine_config_mod.file_ceiling(repo) == engine_config_mod.Posture.observe
    records = engine_config_mod.quarantined_configs()
    assert len(records) == 1
    assert records[0].repo == repo
    assert records[0].path == record.path
    assert records[0].error == "boom"
    with pytest.raises(engine_config_mod.PostureCeilingError):
        engine_config_mod.set_posture_and_persist(
            repo, engine_config_mod.Posture.propose, source="api"
        )


def test_unresolved_parse_failure_sets_global_restrictive_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = QuarantinedEngineFile(path=Path("config/engines.d/mystery.yaml"), error="boom")
    monkeypatch.setattr(config_files_mod, "load_tracked_configs", lambda: ({}, [record]))
    _load_file_backed_engines()
    other = engine_config_mod.RepoIdentity(owner="acme", repo="totally-unregistered")
    assert engine_config_mod.is_file_backed(other) is False
    assert engine_config_mod.file_ceiling(other) == engine_config_mod.Posture.observe
    records = engine_config_mod.quarantined_configs()
    assert len(records) == 1
    assert records[0].repo is None


def test_broken_file_engine_restricted_while_fileless_engine_stays_permissive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken_repo = engine_config_mod.RepoIdentity(owner="acme", repo="widget")
    engine_config_mod.register(engine_config_mod.EngineConfig(repo=broken_repo))
    record = QuarantinedEngineFile(path=Path("config/engines.d/widget.yaml"), error="boom")
    monkeypatch.setattr(config_files_mod, "load_tracked_configs", lambda: ({}, [record]))
    _load_file_backed_engines()
    fileless_repo = engine_config_mod.RepoIdentity(owner="acme", repo="totally-unknown")
    assert engine_config_mod.is_file_backed(broken_repo) is True
    assert engine_config_mod.file_ceiling(broken_repo) == engine_config_mod.Posture.observe
    assert engine_config_mod.is_file_backed(fileless_repo) is False
    assert engine_config_mod.file_ceiling(fileless_repo) == engine_config_mod.Posture.autonomous


def test_one_broken_file_does_not_block_sibling_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good_repo = engine_config_mod.RepoIdentity(owner="acme", repo="gadget")
    good_cfg = engine_config_mod.EngineConfig(
        repo=good_repo, posture=engine_config_mod.Posture.propose
    )
    record = QuarantinedEngineFile(path=Path("config/engines.d/widget.yaml"), error="boom")
    monkeypatch.setattr(
        config_files_mod, "load_tracked_configs", lambda: ({good_repo: good_cfg}, [record])
    )
    _load_file_backed_engines()
    assert engine_config_mod.lookup_by_repo(good_repo) == good_cfg
    assert engine_config_mod.file_ceiling(good_repo) == engine_config_mod.Posture.propose


def test_duplicate_identity_quarantine_does_not_clobber_clean_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = engine_config_mod.RepoIdentity(owner="acme", repo="widget")
    cfg = engine_config_mod.EngineConfig(repo=repo, posture=engine_config_mod.Posture.propose)
    duplicate = QuarantinedEngineFile(
        path=Path("config/engines.d/widget.yaml"),
        error="duplicate repo identity 'acme/widget'",
    )
    monkeypatch.setattr(
        config_files_mod, "load_tracked_configs", lambda: ({repo: cfg}, [duplicate])
    )
    _load_file_backed_engines()
    assert engine_config_mod.file_ceiling(repo) == engine_config_mod.Posture.propose


def test_load_engines_imports_legacy_then_merges_saved_records_then_runs_the_file_backed_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    monkeypatch.setattr(
        infra_mod, "_import_legacy_engines_once", lambda: order.append("legacy")
    )
    monkeypatch.setattr(
        default_engine_boot_mod, "merge_saved_engines", lambda: order.append("merge")
    )
    monkeypatch.setattr(
        infra_mod, "_load_file_backed_engines", lambda: order.append("file_backed")
    )
    monkeypatch.setattr(
        default_engine_boot_mod, "publish_default_ceiling", lambda: order.append("publish")
    )
    infra_mod._load_engines()
    assert order == ["legacy", "merge", "file_backed", "publish"]


def test_merges_projection_from_already_registered_stored_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = engine_config_mod.RepoIdentity(owner="acme", repo="widget")
    stored = engine_config_mod.EngineConfig(repo=repo, posture=engine_config_mod.Posture.observe)
    engine_config_mod.register(stored)
    file_cfg = engine_config_mod.EngineConfig(
        repo=repo, posture=engine_config_mod.Posture.autonomous, base_tools=["Read", "Write"]
    )
    monkeypatch.setattr(config_files_mod, "load_tracked_configs", lambda: ({repo: file_cfg}, []))
    _load_file_backed_engines()
    merged = engine_config_mod.lookup_by_repo(repo)
    assert merged is not None
    assert merged.posture == engine_config_mod.Posture.observe
    assert merged.base_tools == ["Read", "Write"]


def test_default_repo_file_is_registered_as_default(monkeypatch: pytest.MonkeyPatch) -> None:
    default_cfg = engine_config_mod.default_engine_config()
    monkeypatch.setattr(
        config_files_mod,
        "load_tracked_configs",
        lambda: ({default_cfg.repo: default_cfg}, []),
    )
    _load_file_backed_engines()
    assert engine_config_mod.get_registered() == default_cfg


def test_no_tracked_files_leaves_ceilings_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_files_mod, "load_tracked_configs", lambda: ({}, []))
    _load_file_backed_engines()
    repo = engine_config_mod.RepoIdentity(owner="acme", repo="widget")
    assert engine_config_mod.is_file_backed(repo) is False


def test_boot_survives_a_persisted_record_for_the_default_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.engine_registry_store import write_engine
    from alan.repo_accent import AccentChoice

    file_cfg = engine_config_mod.default_engine_config()
    repo = file_cfg.repo
    accent = AccentChoice(color="iris", intensity="standard")
    write_engine(repo.slug, file_cfg.model_copy(update={"accent": accent}).model_dump(mode="json"))
    monkeypatch.setattr(config_files_mod, "load_tracked_configs", lambda: ({repo: file_cfg}, []))

    infra_mod._load_engines()

    registered = engine_config_mod.lookup_by_repo(repo)
    assert registered is not None
    assert registered.accent == accent
    assert engine_config_mod.get_registered() == registered
