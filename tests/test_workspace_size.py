from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from alan.workspace_size import SizeClass, derive_size_class, size_class_from_file_count

_SIZE_DISTRIBUTION_FIXTURE = Path(__file__).parent / "fixtures" / "size_distribution_sample.json"

_SYNTHETIC_AREAS = (
    "quota_ledger",
    "intake_router",
    "label_sync",
    "digest_mailer",
    "artifact_index",
    "snooze_rules",
    "tag_registry",
    "webhook_relay",
)


def _synthetic_area_paths(area: str) -> set[str]:
    camel = area.title().replace("_", "")
    kebab = area.replace("_", "-")
    return {
        f"alan/{area}.py",
        f"tests/test_{area}.py",
        f"alan/routes/{area}.py",
        f"tests/test_routes_{area}.py",
        f"alan/web/src/components/{camel}Panel.tsx",
        f"alan/web/src/components/{camel}Panel.test.tsx",
        f"alan/web/e2e/{kebab}.spec.ts",
        f"docs/architecture/{area}.md",
    }


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (0, SizeClass.XS),
        (3, SizeClass.XS),
        (4, SizeClass.S),
        (6, SizeClass.S),
        (7, SizeClass.M),
        (10, SizeClass.M),
        (11, SizeClass.L),
        (16, SizeClass.L),
        (17, SizeClass.XL),
        (100, SizeClass.XL),
    ],
)
def test_size_class_from_file_count_ladder(count: int, expected: SizeClass) -> None:
    assert size_class_from_file_count(count) == expected


def test_derive_size_class_uses_plan_files_to_edit_when_present() -> None:
    result = derive_size_class(
        plan_files_to_edit=["a.py", "b.py"],
        architecture_files_to_edit=["a", "b", "c", "d", "e", "f", "g"],
        dag_task_count=99,
    )
    assert result == SizeClass.XS


def test_derive_size_class_dedupes_plan_files_to_edit() -> None:
    result = derive_size_class(
        plan_files_to_edit=["a.py", "a.py", "b.py"],
        architecture_files_to_edit=None,
        dag_task_count=0,
    )
    assert result == SizeClass.XS


def test_derive_size_class_empty_plan_files_to_edit_is_xs_not_fallback() -> None:
    result = derive_size_class(
        plan_files_to_edit=[],
        architecture_files_to_edit=["a", "b", "c", "d", "e", "f", "g"],
        dag_task_count=99,
    )
    assert result == SizeClass.XS


def test_derive_size_class_falls_back_to_architecture_files_to_edit() -> None:
    result = derive_size_class(
        plan_files_to_edit=None,
        architecture_files_to_edit=["a", "b", "c", "d"],
        dag_task_count=99,
    )
    assert result == SizeClass.S


def test_derive_size_class_dedupes_architecture_files_to_edit() -> None:
    result = derive_size_class(
        plan_files_to_edit=None,
        architecture_files_to_edit=["a", "a", "a"],
        dag_task_count=99,
    )
    assert result == SizeClass.XS


def test_derive_size_class_falls_back_to_dag_task_count() -> None:
    result = derive_size_class(
        plan_files_to_edit=None, architecture_files_to_edit=None, dag_task_count=5
    )
    assert result == SizeClass.S


def test_derive_size_class_none_when_no_signal_present() -> None:
    result = derive_size_class(
        plan_files_to_edit=None, architecture_files_to_edit=None, dag_task_count=0
    )
    assert result is None


def test_size_distribution_sample_spreads_across_every_bucket() -> None:
    samples = json.loads(_SIZE_DISTRIBUTION_FIXTURE.read_text(encoding="utf-8"))
    sizes = [
        size_class_from_file_count(len(set(sample["files_to_edit"]))) for sample in samples
    ]
    counts = Counter(sizes)
    assert set(counts) == set(SizeClass)
    largest_bucket_fraction = max(counts.values()) / len(sizes)
    assert largest_bucket_fraction <= 0.5


def test_size_distribution_sample_task_ids_are_a_synthetic_sequence() -> None:
    samples = json.loads(_SIZE_DISTRIBUTION_FIXTURE.read_text(encoding="utf-8"))
    assert [sample["task_id"] for sample in samples] == [
        f"sample-{index:03d}" for index in range(1, 149)
    ]


def test_size_distribution_sample_paths_are_distinct_within_each_entry() -> None:
    samples = json.loads(_SIZE_DISTRIBUTION_FIXTURE.read_text(encoding="utf-8"))
    for sample in samples:
        paths = sample["files_to_edit"]
        assert len(set(paths)) == len(paths)


def test_size_distribution_sample_paths_all_come_from_the_synthetic_pool() -> None:
    samples = json.loads(_SIZE_DISTRIBUTION_FIXTURE.read_text(encoding="utf-8"))
    pool = set().union(*(_synthetic_area_paths(area) for area in _SYNTHETIC_AREAS))
    used = {path for sample in samples for path in sample["files_to_edit"]}
    assert len(pool) == 64
    assert used <= pool
