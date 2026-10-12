from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import cast

from alan import roots, store

_JOURNAL_FILE = "self_update_journal.json"

_LEFTOVER_KEYS = frozenset({
    "phase", "target_sha", "previous_sha", "previous_slot", "restarted",
})


class Phase(StrEnum):
    APPLYING = "applying"
    SOAKING = "soaking"
    LAST_GOOD = "last_good"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    ROLLBACK_FAILED = "rollback_failed"
    ROLLBACK_REFUSED_DIRTY = "rollback_refused_dirty"


LEFTOVER_PHASES = frozenset({
    Phase.APPLYING,
    Phase.SOAKING,
    Phase.ROLLING_BACK,
    Phase.ROLLBACK_FAILED,
    Phase.ROLLBACK_REFUSED_DIRTY,
})


@dataclass(frozen=True)
class Journal:
    phase: Phase
    target_sha: str
    previous_sha: str
    previous_slot: str | None
    restarted: bool

    def as_json(self) -> dict[str, object]:
        return {
            "phase": self.phase.value,
            "target_sha": self.target_sha,
            "previous_sha": self.previous_sha,
            "previous_slot": self.previous_slot,
            "restarted": self.restarted,
        }

    def with_phase(self, phase: Phase) -> Journal:
        return Journal(
            phase, self.target_sha, self.previous_sha, self.previous_slot, self.restarted
        )


def journal_path() -> Path:
    return roots.deploy_marker_root() / _JOURNAL_FILE


def write(journal: Journal) -> None:
    path = journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    store.write_text_atomic(path, json.dumps(journal.as_json(), indent=2) + "\n")


def clear() -> None:
    journal_path().unlink(missing_ok=True)


def _parse(payload: object) -> Journal | None:
    if not isinstance(payload, dict):
        return None
    fields = cast("dict[str, object]", payload)
    if not _LEFTOVER_KEYS <= fields.keys():
        return None
    slot = fields["previous_slot"]
    if slot is not None and not isinstance(slot, str):
        return None
    restarted = fields["restarted"]
    if not isinstance(restarted, bool):
        return None
    try:
        return Journal(
            Phase(str(fields["phase"])),
            str(fields["target_sha"]),
            str(fields["previous_sha"]),
            slot,
            restarted,
        )
    except (ValueError, TypeError):
        return None


def read() -> Journal | None:
    try:
        text = journal_path().read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        payload: object = json.loads(text)
    except ValueError:
        return None
    return _parse(payload)
