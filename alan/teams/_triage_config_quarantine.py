from __future__ import annotations

from alan.engine_config import QuarantinedConfig, quarantined_configs
from alan.teams.triage_pregate import ClassifiedSurface

_KIND = "config_quarantine"


def _surface_for(record: QuarantinedConfig) -> ClassifiedSurface:
    identity = record.repo.slug if record.repo is not None else str(record.path)
    return ClassifiedSurface(
        surface_id=f"{_KIND}:{identity}",
        kind=_KIND,
        is_blocked=True,
        blocker_reason=_KIND,
        obs={
            "path": str(record.path),
            "error": record.error,
            "repo": record.repo.slug if record.repo is not None else None,
        },
    )


def config_quarantine_surfaces() -> list[ClassifiedSurface]:
    return [_surface_for(record) for record in quarantined_configs()]
