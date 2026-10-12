from __future__ import annotations

from pathlib import Path

import pytest

from tests.forge_patch_sites import (
    FAMILY_MODULES,
    RECORDED_PATCH_SITES,
    PatchSiteCount,
    count_family_patch_sites,
    count_patch_sites,
)


def test_recorded_patch_site_count_equals_the_parsed_count() -> None:
    measured = count_family_patch_sites()
    assert measured == RECORDED_PATCH_SITES, (
        f"parsed forge-family patch sites are {measured}; set RECORDED_PATCH_SITES in "
        "tests/forge_patch_sites.py to that figure in the same change"
    )


def test_family_is_the_eleven_modules_the_item_is_measured_over() -> None:
    assert len(FAMILY_MODULES) == 11
    assert all(name.startswith("_pr_") for name in FAMILY_MODULES)


@pytest.mark.parametrize(
    "source",
    [
        "mp.setattr(_pr_branch, 'run_subprocess', fake)",
        "m.setattr(_pr_post, 'fetch_review_stream', fake, raising=False)",
        "monkeypatch.setattr(\n    _pr_ready,\n    'readiness_or_unavailable',\n    fake,\n)",
        "mp.setattr(alan.teams._pr_flake_ledger, 'record', fake)",
        "mp.setattr('alan.teams._pr_branch.run_subprocess', fake)",
        "mp.setattr('alan._dispatcher_selfheal._pr_ready.readiness_or_unavailable', fake)",
        "def t(mp):\n    mp.setattr(_pr_sonar, 'x', 1)",
    ],
)
def test_predicate_counts_wrapped_attribute_and_dotted_path_targets(source: str) -> None:
    assert count_patch_sites(source) == 1


@pytest.mark.parametrize(
    "source",
    [
        "mp.setattr(subprocess, 'run', fake)",
        "mp.setattr(_pr_branch_support, 'x', fake)",
        "mp.setattr(_pr_branch.subprocess, 'run', fake)",
        "mp.setattr('_pr_branch', fake)",
        "mp.setattr('alan.teams._pr_branch_support.x', fake)",
        "mp.setattr()",
        "mp.setenv('_pr_branch', 'x')",
        "setattr(_pr_branch, 'x', 1)",
        "name = '_pr_branch'",
    ],
)
def test_predicate_ignores_non_family_targets_and_other_calls(source: str) -> None:
    assert count_patch_sites(source) == 0


def test_two_calls_in_one_source_count_twice() -> None:
    source = "mp.setattr(_pr_post, 'a', f)\nmp.setattr(\n    _pr_threads, 'b', f,\n)"
    assert count_patch_sites(source) == 2


def test_count_walks_tests_recursively_and_counts_patched_files(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    (tests_dir / "fakes").mkdir(parents=True)
    (tests_dir / "__pycache__").mkdir()
    (tests_dir / "a.py").write_text("mp.setattr(_pr_post, 'a', f)\nmp.setattr(_pr_push, 'b', f)\n")
    (tests_dir / "fakes" / "b.py").write_text("mp.setattr(_pr_ready, 'c', f)\n")
    (tests_dir / "c.py").write_text("mp.setattr(subprocess, 'run', f)\n")
    (tests_dir / "__pycache__" / "d.py").write_text("mp.setattr(_pr_post, 'e', f)\n")
    assert count_family_patch_sites(tmp_path) == PatchSiteCount(sites=3, files=2)


def test_count_of_a_tree_without_patch_sites_is_zero(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    assert count_family_patch_sites(tmp_path) == PatchSiteCount(sites=0, files=0)
