from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import swap_web_dist  # type: ignore[import-not-found]  # noqa: E402

_SHA = "a" * 40


def _make_build(web_dir: Path, slot: str) -> Path:
    slot_dir = web_dir / slot
    slot_dir.mkdir(parents=True, exist_ok=True)
    (slot_dir / "index.html").write_text(f"<html>{slot}</html>", encoding="utf-8")
    return slot_dir


def test_next_slot_is_dist_a_when_dist_absent(tmp_path: Path) -> None:
    assert swap_web_dist.next_slot(tmp_path) == "dist.a"


def test_next_slot_alternates_a_then_b_then_a(tmp_path: Path) -> None:
    _make_build(tmp_path, "dist.a")
    swap_web_dist.activate(tmp_path, "dist.a", _SHA)
    assert swap_web_dist.next_slot(tmp_path) == "dist.b"

    _make_build(tmp_path, "dist.b")
    swap_web_dist.activate(tmp_path, "dist.b", _SHA)
    assert swap_web_dist.next_slot(tmp_path) == "dist.a"


def test_activate_symlinks_dist_to_slot(tmp_path: Path) -> None:
    _make_build(tmp_path, "dist.a")
    swap_web_dist.activate(tmp_path, "dist.a", _SHA)
    dist = tmp_path / "dist"
    assert dist.is_symlink()
    assert (dist / "index.html").is_file()


def test_activate_writes_deploy_sha_before_symlink_rename(tmp_path: Path) -> None:
    _make_build(tmp_path, "dist.a")
    swap_web_dist.activate(tmp_path, "dist.a", _SHA)
    assert (tmp_path / "dist.a" / ".deploy_sha").read_text(encoding="utf-8") == _SHA
    assert (tmp_path / "dist" / ".deploy_sha").read_text(encoding="utf-8") == _SHA


def test_activate_refuses_when_slot_has_no_index_html_writes_no_sha(tmp_path: Path) -> None:
    (tmp_path / "dist.a").mkdir()
    with pytest.raises(SystemExit):
        swap_web_dist.activate(tmp_path, "dist.a", _SHA)
    assert not (tmp_path / "dist.a" / ".deploy_sha").exists()


def test_activate_first_run_migrates_real_directory(tmp_path: Path) -> None:
    real_dist = tmp_path / "dist"
    real_dist.mkdir()
    (real_dist / "index.html").write_text("<html>legacy</html>", encoding="utf-8")
    assert (real_dist / "index.html").is_file()

    _make_build(tmp_path, "dist.a")
    swap_web_dist.activate(tmp_path, "dist.a", _SHA)

    dist = tmp_path / "dist"
    assert dist.is_symlink()
    assert (dist / "index.html").is_file()
    assert (dist / "index.html").read_text(encoding="utf-8") == "<html>dist.a</html>"
    assert not (tmp_path / "dist.legacy").exists()


def test_activate_refuses_when_slot_has_no_index_html(tmp_path: Path) -> None:
    (tmp_path / "dist.a").mkdir()
    with pytest.raises(SystemExit):
        swap_web_dist.activate(tmp_path, "dist.a", _SHA)
    assert not (tmp_path / "dist").exists()


def test_activate_cleans_stale_dist_tmp(tmp_path: Path) -> None:
    stale = tmp_path / "dist.tmp"
    stale.symlink_to("dist.b")
    _make_build(tmp_path, "dist.a")
    swap_web_dist.activate(tmp_path, "dist.a", _SHA)
    dist = tmp_path / "dist"
    assert dist.is_symlink()
    assert (dist / "index.html").is_file()


def test_dist_never_resolves_to_nonexistent_path_across_reactivation(tmp_path: Path) -> None:
    _make_build(tmp_path, "dist.a")
    swap_web_dist.activate(tmp_path, "dist.a", _SHA)
    dist = tmp_path / "dist"
    assert (dist / "index.html").is_file()

    second_sha = "b" * 40
    _make_build(tmp_path, "dist.b")
    swap_web_dist.activate(tmp_path, "dist.b", second_sha)
    assert (dist / "index.html").is_file()
    assert (dist / ".deploy_sha").read_text(encoding="utf-8") == second_sha


def test_source_uses_os_rename_and_no_ln_sfn() -> None:
    source = Path(swap_web_dist.__file__).read_text(encoding="utf-8")
    assert "os.rename" in source
    assert "ln -sfn" not in source


def test_main_next_slot_accepts_web_dir_before_subcommand(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = swap_web_dist._main(["--web-dir", str(tmp_path), "next-slot"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "dist.a"


def test_main_activate_accepts_web_dir_before_subcommand(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _make_build(tmp_path, "dist.a")
    rc = swap_web_dist._main(
        ["--web-dir", str(tmp_path), "activate", "--slot", "dist.a", "--sha", _SHA]
    )
    assert rc == 0
    dist = tmp_path / "dist"
    assert dist.is_symlink()
    assert (dist / "index.html").is_file()
    assert (dist / ".deploy_sha").read_text(encoding="utf-8") == _SHA


def test_main_activate_requires_sha(tmp_path: Path) -> None:
    _make_build(tmp_path, "dist.a")
    with pytest.raises(SystemExit):
        swap_web_dist._main(["--web-dir", str(tmp_path), "activate", "--slot", "dist.a"])
