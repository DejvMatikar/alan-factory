from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path

from alan import roots
from alan.claude_cli_grants import alan_secret_files

_GROUP_WORLD_BITS = 0o077

HOST_READ_MODES_HEADING = "## HOST-READ SECRET FILE PERMISSIONS"


@dataclass(frozen=True)
class SecretFileMode:
    label: str
    mode: int | None

    @property
    def exposed(self) -> bool:
        return self.mode is not None and bool(self.mode & _GROUP_WORLD_BITS)


def _store_entries(store: Path) -> list[tuple[str, Path]]:
    if not store.is_dir():
        return []
    entries = sorted(entry for entry in store.rglob("*") if entry.is_file())
    return [(f"{store.name}/{entry.relative_to(store).as_posix()}", entry) for entry in entries]


def _labelled_secret_paths() -> list[tuple[str, Path]]:
    present = [(path.name, path) for path in alan_secret_files() if path.is_file()]
    return [*present, *_store_entries(roots.secrets_store_root())]


def _mode(path: Path) -> int | None:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None


def secret_file_modes() -> list[SecretFileMode]:
    return [SecretFileMode(label, _mode(path)) for label, path in _labelled_secret_paths()]


def _render(entry: SecretFileMode) -> str:
    if entry.mode is None:
        return f"- {entry.label}: unreadable"
    suffix = " GROUP/WORLD-ACCESSIBLE" if entry.exposed else ""
    return f"- {entry.label}: {entry.mode:03o}{suffix}"


def secret_permission_block() -> str:
    entries = secret_file_modes()
    body = [_render(entry) for entry in entries] or ["- (no secret files present)"]
    header = (
        f"{HOST_READ_MODES_HEADING}\n"
        "The host read these permission bits itself; the secret paths are denied to "
        "this session. Labels only, never absolute paths."
    )
    return "\n".join([header, *body]) + "\n\n"
