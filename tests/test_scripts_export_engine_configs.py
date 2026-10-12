from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import export_engine_configs as export_mod  # type: ignore[import-not-found]  # noqa: E402

from alan.engine_config import EngineConfig, PromptOverride, RepoIdentity  # noqa: E402
from alan.engine_registry_store import write_engine  # noqa: E402


@pytest.fixture(autouse=True)
def _engines_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "engines"
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(root))
    return root


def test_load_live_config_raises_when_never_persisted() -> None:
    with pytest.raises(SystemExit, match="no persisted engine record"):
        export_mod.load_live_config("acme/widget")


def test_load_live_config_returns_persisted_record() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="acme", repo="widget"))
    write_engine(cfg.repo.slug, cfg.model_dump(mode="json"))
    loaded = export_mod.load_live_config("acme/widget")
    assert loaded == cfg


def test_render_yaml_round_trips_through_engine_config() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="acme", repo="widget"))
    text = export_mod.render_yaml(cfg)
    assert EngineConfig.model_validate(yaml.safe_load(text)) == cfg


def test_render_yaml_preserves_field_order_not_alphabetical() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="acme", repo="widget"))
    text = export_mod.render_yaml(cfg)
    assert text.index("repo:") < text.index("posture:")


def test_render_yaml_omits_prompt_overrides_even_when_populated() -> None:
    cfg = EngineConfig(
        repo=RepoIdentity(owner="acme", repo="widget"),
        prompt_overrides={
            "implementation/implementer": PromptOverride(text="custom prompt", base_fingerprint="x")
        },
    )
    text = export_mod.render_yaml(cfg)
    assert "prompt_overrides" not in text
    assert "custom prompt" not in text
    reloaded = EngineConfig.model_validate(yaml.safe_load(text))
    assert reloaded.prompt_overrides == {}


def test_parse_args_requires_slug() -> None:
    with pytest.raises(SystemExit):
        export_mod.parse_args([])


def test_parse_args_accepts_slug() -> None:
    args = export_mod.parse_args(["acme/widget"])
    assert args.slug == "acme/widget"


def test_main_writes_rendered_yaml_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="acme", repo="widget"))
    write_engine(cfg.repo.slug, cfg.model_dump(mode="json"))
    exit_code = export_mod.main(["acme/widget"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert EngineConfig.model_validate(yaml.safe_load(out)) == cfg
