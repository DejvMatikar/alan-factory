from __future__ import annotations

from enum import Enum


class SizeClass(str, Enum):
    XS = "XS"
    S = "S"
    M = "M"
    L = "L"
    XL = "XL"


_THRESHOLDS = (3, 6, 10, 16)


def size_class_from_file_count(count: int) -> SizeClass:
    if count <= _THRESHOLDS[0]:
        return SizeClass.XS
    if count <= _THRESHOLDS[1]:
        return SizeClass.S
    if count <= _THRESHOLDS[2]:
        return SizeClass.M
    if count <= _THRESHOLDS[3]:
        return SizeClass.L
    return SizeClass.XL


def derive_size_class(
    *,
    plan_files_to_edit: list[str] | None,
    architecture_files_to_edit: list[str] | None,
    dag_task_count: int,
) -> SizeClass | None:
    if plan_files_to_edit is not None:
        return size_class_from_file_count(len(set(plan_files_to_edit)))
    if architecture_files_to_edit is not None:
        return size_class_from_file_count(len(set(architecture_files_to_edit)))
    if dag_task_count > 0:
        return size_class_from_file_count(dag_task_count)
    return None
