from __future__ import annotations

from pathlib import Path

from alan.engine_config import QuarantinedConfig, RepoIdentity, set_quarantined_configs
from alan.teams._triage_config_quarantine import config_quarantine_surfaces


def test_no_quarantined_configs_yields_no_surfaces() -> None:
    set_quarantined_configs([])
    assert config_quarantine_surfaces() == []


def test_resolved_quarantine_yields_surface_keyed_by_repo_slug() -> None:
    repo = RepoIdentity(owner="acme", repo="widget")
    set_quarantined_configs([
        QuarantinedConfig(path=Path("config/engines.d/widget.yaml"), error="boom", repo=repo)
    ])
    surfaces = config_quarantine_surfaces()
    assert len(surfaces) == 1
    surface = surfaces[0]
    assert surface.kind == "config_quarantine"
    assert surface.is_blocked is True
    assert surface.blocker_reason == "config_quarantine"
    assert surface.surface_id == "config_quarantine:acme/widget"
    assert surface.obs == {
        "path": "config/engines.d/widget.yaml",
        "error": "boom",
        "repo": "acme/widget",
    }


def test_unresolved_quarantine_yields_surface_keyed_by_path() -> None:
    set_quarantined_configs([
        QuarantinedConfig(path=Path("config/engines.d/mystery.yaml"), error="boom", repo=None)
    ])
    surfaces = config_quarantine_surfaces()
    assert len(surfaces) == 1
    surface = surfaces[0]
    assert surface.surface_id == "config_quarantine:config/engines.d/mystery.yaml"
    assert surface.obs["repo"] is None


def test_multiple_quarantined_configs_yield_one_surface_each() -> None:
    repo = RepoIdentity(owner="acme", repo="widget")
    set_quarantined_configs([
        QuarantinedConfig(path=Path("widget.yaml"), error="boom", repo=repo),
        QuarantinedConfig(path=Path("mystery.yaml"), error="bang", repo=None),
    ])
    surfaces = config_quarantine_surfaces()
    assert len(surfaces) == 2
    assert {s.surface_id for s in surfaces} == {
        "config_quarantine:acme/widget",
        "config_quarantine:mystery.yaml",
    }
