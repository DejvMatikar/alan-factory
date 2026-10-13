from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from alan.self_update import _ops, _run

HEAD = "a" * 40
HEALTH_URL = "http://127.0.0.1:8585/api/health"


@pytest.fixture
def marker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "deploy" / "restarted_sha"
    monkeypatch.setattr(_ops, "marker_path", lambda: path)
    monkeypatch.setattr(_ops, "git", lambda repo, *args: f"{HEAD}\n")
    return path


def test_a_missing_marker_is_seeded_with_head_when_the_service_serves_head(
    marker: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    probed: list[tuple[str, str]] = []

    def health_ok(url: str, target: str) -> bool:
        probed.append((url, target))
        return True

    monkeypatch.setattr(_ops, "health_ok", health_ok)
    _run._backfill_marker(tmp_path, HEALTH_URL)
    assert probed == [(HEALTH_URL, HEAD)]
    assert marker.read_text(encoding="utf-8") == f"{HEAD}\n"


def test_a_missing_marker_stays_missing_when_the_service_does_not_serve_head(
    marker: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(_ops, "health_ok", lambda url, target: False)
    _run._backfill_marker(tmp_path, HEALTH_URL)
    assert not marker.exists()


def test_an_existing_marker_is_never_probed_or_rewritten(
    marker: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    marker.parent.mkdir(parents=True)
    marker.write_text("b" * 40 + "\n", encoding="utf-8")

    def health_ok(url: str, target: str) -> bool:
        raise AssertionError(target)

    monkeypatch.setattr(_ops, "health_ok", health_ok)
    _run._backfill_marker(tmp_path, HEALTH_URL)
    assert marker.read_text(encoding="utf-8") == "b" * 40 + "\n"


@pytest.mark.parametrize(
    "error", [subprocess.CalledProcessError(128, ["git"]), OSError("no git")]
)
def test_a_head_that_cannot_be_read_leaves_the_marker_missing(
    marker: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: Exception
) -> None:
    def git(repo: Path, *args: str) -> str:
        raise error

    monkeypatch.setattr(_ops, "git", git)
    monkeypatch.setattr(_ops, "health_ok", lambda url, target: True)
    _run._backfill_marker(tmp_path, HEALTH_URL)
    assert not marker.exists()
