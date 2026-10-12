from __future__ import annotations

from collections.abc import Mapping, Sequence


def _normalize(path: str) -> str:
    return path.strip().removeprefix("./").strip("/")


def _declared_paths(manifest: Sequence[str]) -> list[str]:
    return [d for m in manifest if (d := _normalize(m))]


def _is_covered(diff_file: str, declared: str) -> bool:
    return diff_file == declared or diff_file.startswith(f"{declared}/")


def _covered_by_any(diff_file: str, declared_paths: Sequence[str]) -> bool:
    return any(_is_covered(diff_file, declared) for declared in declared_paths)


def find_out_of_manifest(
    diff_files: Sequence[str], manifest: Sequence[str]
) -> list[str]:
    declared_paths = _declared_paths(manifest)
    if not declared_paths:
        return []
    return [f for f in diff_files if not _covered_by_any(_normalize(f), declared_paths)]


def find_sibling_overlap(
    diff_files: Sequence[str], sibling_manifests: Mapping[str, Sequence[str]]
) -> dict[str, list[str]]:
    overlaps: dict[str, list[str]] = {}
    for sibling_id, manifest in sibling_manifests.items():
        declared_paths = _declared_paths(manifest)
        if not declared_paths:
            continue
        hits = [f for f in diff_files if _covered_by_any(_normalize(f), declared_paths)]
        if hits:
            overlaps[sibling_id] = hits
    return overlaps
