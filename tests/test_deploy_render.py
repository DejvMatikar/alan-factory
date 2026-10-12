from __future__ import annotations

from pathlib import Path

import pytest

from tests._deploy_render_support import (
    EXAMPLE_ORIGIN,
    NODE22_ENTRY,
    plist_string_value,
    rendered_target,
    run_render,
    scratch_checkout,
    scratch_home,
)

_SENTINEL = b"sentinel-target-bytes\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return scratch_checkout(tmp_path, EXAMPLE_ORIGIN)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return scratch_home(tmp_path)


def test_render_creates_parent_dir_and_regular_file(repo: Path, home: Path) -> None:
    result = run_render(repo, home)
    assert result.returncode == 0, result.stderr
    target = rendered_target(home)
    assert target.is_file()
    assert not target.is_symlink()


def test_render_substitutes_every_token(repo: Path, home: Path) -> None:
    result = run_render(repo, home)
    assert result.returncode == 0, result.stderr
    content = rendered_target(home).read_text(encoding="utf-8")
    assert "__ALAN_REPO_DIR__" not in content
    assert "__ALAN_HOME__" not in content
    assert "__ALAN_SLOT_REPO_URL__" not in content
    assert str(repo) in content
    assert str(home) in content


def test_render_is_idempotent(repo: Path, home: Path) -> None:
    run_render(repo, home)
    first = rendered_target(home).read_bytes()
    run_render(repo, home)
    second = rendered_target(home).read_bytes()
    assert first == second


def test_render_replaces_symlink_without_writing_through(repo: Path, home: Path) -> None:
    template = repo / "deploy" / "ai.alan.plist"
    target = rendered_target(home)
    target.parent.mkdir(parents=True)
    target.symlink_to(template)
    template_before = template.read_bytes()

    result = run_render(repo, home)

    assert result.returncode == 0, result.stderr
    assert target.is_file()
    assert not target.is_symlink()
    assert template.read_bytes() == template_before


def test_render_refuses_when_home_unset(repo: Path) -> None:
    result = run_render(repo, None)
    assert result.returncode != 0


def test_render_places_node22_directly_before_homebrew_bin(repo: Path, home: Path) -> None:
    result = run_render(repo, home)
    assert result.returncode == 0, result.stderr
    path_value = plist_string_value(rendered_target(home).read_text(encoding="utf-8"), "PATH")
    assert f"{NODE22_ENTRY}:/opt/homebrew/bin:" in path_value


@pytest.mark.parametrize("target_present", [True, False], ids=["target-present", "target-absent"])
@pytest.mark.parametrize("char", ["#", "&", "\\"], ids=["hash", "ampersand", "backslash"])
@pytest.mark.parametrize("location", ["repository", "home"])
def test_render_rejects_unsafe_path_character(
    tmp_path: Path, location: str, char: str, target_present: bool
) -> None:
    repo = scratch_checkout(
        tmp_path, EXAMPLE_ORIGIN, name=f"re{char}po" if location == "repository" else "repo"
    )
    home = scratch_home(tmp_path, name=f"ho{char}me" if location == "home" else "home")
    target = rendered_target(home)
    if target_present:
        target.parent.mkdir(parents=True)
        target.write_bytes(_SENTINEL)

    result = run_render(repo, home)

    assert result.returncode != 0
    assert f"{location} directory" in result.stderr
    assert list(home.rglob(".ai.alan.plist.*")) == []
    if target_present:
        assert target.read_bytes() == _SENTINEL
    else:
        assert not target.exists()
        assert not (home / "Library").exists()
