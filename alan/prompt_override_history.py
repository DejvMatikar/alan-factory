from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

from alan import engine_registry_store, store

_HISTORY_SUBDIR = "prompt_override_history"
_ENTRY_FILENAME_TEMPLATE = "{version:06d}.json"
_MAX_VERSION = 999_999
_DIR_MODE = 0o700
_FILE_MODE = 0o600


class PromptOverrideHistoryError(RuntimeError):
    pass


class HistoryEntry(BaseModel, frozen=True):
    version: int
    text: str
    base_fingerprint: str
    saved_at: str


def _history_dir(repo_slug: str, team: str, role: str) -> Path:
    digest = hashlib.sha256(f"{repo_slug}\n{team}\n{role}".encode()).hexdigest()
    return engine_registry_store.engines_root() / _HISTORY_SUBDIR / digest


def _claim_slot(directory: Path) -> tuple[Path, int]:
    # Mirrors alan.handover's O_EXCL immutable-slot claim: scan upward from 1,
    # the first version number that doesn't already exist is ours — races on
    # the same version are resolved by O_EXCL, the loser just continues
    # scanning rather than clobbering the winner's entry.
    directory.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
    for version in range(1, _MAX_VERSION + 1):
        candidate = directory / _ENTRY_FILENAME_TEMPLATE.format(version=version)
        try:
            fd = os.open(str(candidate), os.O_WRONLY | os.O_CREAT | os.O_EXCL, _FILE_MODE)
        except FileExistsError:
            continue
        os.close(fd)
        return candidate, version
    raise PromptOverrideHistoryError(f"no free prompt-override-history slot under {directory}")


def _write_atomic(path: Path, payload: dict[str, object]) -> None:
    store.write_text_mkstemp(path, json.dumps(payload), prefix=".prompt_override_", mode=_FILE_MODE)


def append_entry(
    repo_slug: str, team: str, role: str, text: str, base_fingerprint: str
) -> HistoryEntry:
    directory = _history_dir(repo_slug, team, role)
    path, version = _claim_slot(directory)
    entry = HistoryEntry(
        version=version,
        text=text,
        base_fingerprint=base_fingerprint,
        saved_at=datetime.now(UTC).isoformat(),
    )
    try:
        _write_atomic(path, entry.model_dump(mode="json"))
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return entry


def _load_entry(path: Path) -> HistoryEntry | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    try:
        return HistoryEntry.model_validate(data)
    except ValidationError:
        return None


def list_entries(repo_slug: str, team: str, role: str) -> list[HistoryEntry]:
    # Ascending by version (oldest first); callers wanting newest-first reverse it.
    directory = _history_dir(repo_slug, team, role)
    if not directory.is_dir():
        return []
    entries: list[HistoryEntry] = []
    for path in sorted(directory.glob("*.json")):
        entry = _load_entry(path)
        if entry is not None:
            entries.append(entry)
    return entries


def get_entry(repo_slug: str, team: str, role: str, version: int) -> HistoryEntry | None:
    path = _history_dir(repo_slug, team, role) / _ENTRY_FILENAME_TEMPLATE.format(version=version)
    return _load_entry(path)


__all__ = [
    "HistoryEntry",
    "PromptOverrideHistoryError",
    "append_entry",
    "get_entry",
    "list_entries",
]
