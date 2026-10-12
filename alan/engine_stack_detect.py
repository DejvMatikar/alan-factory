from __future__ import annotations

import fnmatch
from pathlib import Path

from pydantic import BaseModel

_STACK_MARKERS: dict[str, tuple[str, ...]] = {
    "dotnet": ("*.sln", "*.csproj", "*.fsproj"),
    "node": ("package.json",),
    "python": ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile"),
    "ios": ("Project.swift", "Package.swift"),
}

_PRESET_PRIORITY: tuple[str, ...] = ("dotnet", "node", "python", "ios")


class StackDetection(BaseModel, frozen=True):
    recommended_preset: str
    detected_stacks: tuple[str, ...]
    additional_stacks: tuple[str, ...]


def _depth1_filenames(repo_root: Path) -> list[str]:
    try:
        return [entry.name for entry in repo_root.iterdir() if entry.is_file()]
    except OSError:
        return []


def _matched_stacks(filenames: list[str]) -> dict[str, int]:
    hits: dict[str, int] = {}
    for stack, markers in _STACK_MARKERS.items():
        count = sum(1 for name in filenames if any(fnmatch.fnmatch(name, m) for m in markers))
        if count:
            hits[stack] = count
    return hits


def _pick_recommended(hits: dict[str, int]) -> str:
    if not hits:
        return _PRESET_PRIORITY[0]
    best = max(hits.values())
    return next(stack for stack in _PRESET_PRIORITY if hits.get(stack) == best)


def detect_stack(repo_root: Path) -> StackDetection:
    hits = _matched_stacks(_depth1_filenames(repo_root))
    recommended = _pick_recommended(hits)
    detected = tuple(stack for stack in _PRESET_PRIORITY if stack in hits)
    additional = tuple(stack for stack in detected if stack != recommended)
    return StackDetection(
        recommended_preset=recommended, detected_stacks=detected, additional_stacks=additional
    )
