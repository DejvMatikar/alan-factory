from __future__ import annotations

from pathlib import Path

import pytest

import alan.teams.implementation as impl_mod
from alan.teams.implementation import (
    _BACKSTOP_USER_TEXT,
    ENVIRONMENT_BLOCK,
    _drift_gate_check,
)


def test_drift_gate_check_returns_false_and_warns_when_base_unresolved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        "alan.teams.implementation._base_ref.resolve", lambda *_a, **_kw: None
    )
    with caplog.at_level("WARNING"):
        assert _drift_gate_check(tmp_path) is False
    assert "base ref unresolved" in caplog.text


def test_drift_gate_check_returns_false_when_changed_paths_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "alan.teams.implementation._base_ref.resolve", lambda *_a, **_kw: "origin/main"
    )

    def _raise(*_a: object, **_kw: object) -> object:
        raise RuntimeError("git diff failed")

    monkeypatch.setattr(impl_mod, "_drift_gate_changed_paths", _raise)
    assert _drift_gate_check(tmp_path) is False


def test_drift_gate_check_uses_resolved_base_for_changed_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "alan.teams.implementation._base_ref.resolve", lambda *_a, **_kw: "origin/main"
    )
    captured: dict[str, object] = {}

    def _fake_changed_paths(base_ref: str, cwd: Path) -> list[str]:
        captured["base_ref"] = base_ref
        captured["cwd"] = cwd
        return ["alan/server.py"]

    monkeypatch.setattr(impl_mod, "_drift_gate_changed_paths", _fake_changed_paths)
    assert _drift_gate_check(tmp_path) is True
    assert captured["base_ref"] == "origin/main"
    assert captured["cwd"] == tmp_path


def test_drift_gate_check_returns_false_when_no_load_bearing_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "alan.teams.implementation._base_ref.resolve", lambda *_a, **_kw: "origin/main"
    )
    monkeypatch.setattr(
        impl_mod, "_drift_gate_changed_paths", lambda *_a, **_kw: ["alan/foo.py"]
    )
    assert _drift_gate_check(tmp_path) is False


def test_prompt_fetches_before_diffing_changed_e2e_specs() -> None:
    assert (
        "git fetch origin main && git diff --name-only origin/main...HEAD -- "
        "alan/web/e2e" in ENVIRONMENT_BLOCK
    )


def test_backstop_user_text_names_architecture_docs_first() -> None:
    assert _BACKSTOP_USER_TEXT.index("docs/architecture/") < _BACKSTOP_USER_TEXT.index(
        "CLAUDE.md"
    )


def test_environment_block_drift_gate_names_architecture_docs_first() -> None:
    assert ENVIRONMENT_BLOCK.index("docs/architecture/*.md") < ENVIRONMENT_BLOCK.index(
        "CLAUDE.md"
    )
