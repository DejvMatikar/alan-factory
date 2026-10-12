from __future__ import annotations

from pathlib import Path

from alan.config_files import (
    QuarantinedEngineFile,
    engines_config_dir,
    load_tracked_configs,
    resolve_quarantined_identity,
)
from alan.engine_config import RepoIdentity


def test_engines_config_dir_is_checkout_relative() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    assert engines_config_dir() == repo_root / "config" / "engines.d"


def test_load_tracked_configs_empty_dir_returns_nothing(tmp_path: Path) -> None:
    configs, quarantined = load_tracked_configs(tmp_path)
    assert configs == {}
    assert quarantined == []


def test_load_tracked_configs_missing_dir_returns_nothing(tmp_path: Path) -> None:
    configs, quarantined = load_tracked_configs(tmp_path / "does-not-exist")
    assert configs == {}
    assert quarantined == []


def test_load_tracked_configs_parses_valid_yaml(tmp_path: Path) -> None:
    (tmp_path / "widget.yaml").write_text(
        "repo:\n  owner: acme\n  repo: widget\nposture: observe\n", encoding="utf-8"
    )
    configs, quarantined = load_tracked_configs(tmp_path)
    assert quarantined == []
    identity = RepoIdentity(owner="acme", repo="widget")
    assert configs[identity].posture.value == "observe"


def test_load_tracked_configs_ignores_non_yaml_files(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("not an engine config", encoding="utf-8")
    configs, quarantined = load_tracked_configs(tmp_path)
    assert configs == {}
    assert quarantined == []


def test_load_tracked_configs_quarantines_invalid_yaml_syntax(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("repo: [unterminated", encoding="utf-8")
    configs, quarantined = load_tracked_configs(tmp_path)
    assert configs == {}
    assert len(quarantined) == 1
    assert quarantined[0].path == path


def test_load_tracked_configs_quarantines_schema_violation(tmp_path: Path) -> None:
    path = tmp_path / "bogus.yaml"
    path.write_text(
        "repo:\n  owner: acme\n  repo: widget\nnot_a_real_field: true\n", encoding="utf-8"
    )
    configs, quarantined = load_tracked_configs(tmp_path)
    assert configs == {}
    assert len(quarantined) == 1
    assert "not_a_real_field" in quarantined[0].error or quarantined[0].path == path


def test_load_tracked_configs_quarantines_duplicate_repo_keeps_first(tmp_path: Path) -> None:
    (tmp_path / "a-first.yaml").write_text(
        "repo:\n  owner: acme\n  repo: widget\nposture: observe\n", encoding="utf-8"
    )
    (tmp_path / "b-second.yaml").write_text(
        "repo:\n  owner: acme\n  repo: widget\nposture: propose\n", encoding="utf-8"
    )
    configs, quarantined = load_tracked_configs(tmp_path)
    identity = RepoIdentity(owner="acme", repo="widget")
    assert configs[identity].posture.value == "observe"
    assert len(quarantined) == 1
    assert "duplicate" in quarantined[0].error
    assert quarantined[0].path.name == "b-second.yaml"


def test_load_tracked_configs_multiple_valid_files(tmp_path: Path) -> None:
    (tmp_path / "widget.yaml").write_text(
        "repo:\n  owner: acme\n  repo: widget\n", encoding="utf-8"
    )
    (tmp_path / "gadget.yaml").write_text(
        "repo:\n  owner: acme\n  repo: gadget\n", encoding="utf-8"
    )
    configs, quarantined = load_tracked_configs(tmp_path)
    assert quarantined == []
    assert {r.slug for r in configs} == {"acme/widget", "acme/gadget"}


def test_load_tracked_configs_one_bad_file_does_not_block_the_others(tmp_path: Path) -> None:
    (tmp_path / "widget.yaml").write_text(
        "repo:\n  owner: acme\n  repo: widget\nposture: observe\n", encoding="utf-8"
    )
    (tmp_path / "gadget.yaml").write_text(
        "repo:\n  owner: acme\n  repo: gadget\nposture: propose\n", encoding="utf-8"
    )
    path = tmp_path / "broken.yaml"
    path.write_text("repo: [unterminated", encoding="utf-8")
    configs, quarantined = load_tracked_configs(tmp_path)
    assert {r.slug for r in configs} == {"acme/widget", "acme/gadget"}
    assert len(quarantined) == 1
    assert quarantined[0].path == path


def test_quarantined_engine_file_is_frozen_dataclass() -> None:
    record = QuarantinedEngineFile(path=Path("x.yaml"), error="boom")
    assert record.path == Path("x.yaml")
    assert record.error == "boom"


def test_resolve_quarantined_identity_matches_stem() -> None:
    widget = RepoIdentity(owner="acme", repo="widget")
    gadget = RepoIdentity(owner="acme", repo="gadget")
    resolved = resolve_quarantined_identity(
        Path("config/engines.d/widget.yaml"), [widget, gadget]
    )
    assert resolved == widget


def test_resolve_quarantined_identity_no_match_returns_none() -> None:
    widget = RepoIdentity(owner="acme", repo="widget")
    resolved = resolve_quarantined_identity(Path("config/engines.d/unknown.yaml"), [widget])
    assert resolved is None


def test_resolve_quarantined_identity_empty_candidates_returns_none() -> None:
    resolved = resolve_quarantined_identity(Path("x.yaml"), [])
    assert resolved is None
