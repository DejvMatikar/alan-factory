from __future__ import annotations

from alan.engine_config import EngineConfig

_IOS_PRESET = "ios"
_FIX_TEAMS = frozenset({"implementation_fix"})

INNER_LOOP_GATE_GUIDANCE = (
    "INNER-LOOP GATE RUNS — while you iterate, the narrowest targeted "
    "form of this repository's own documented gate is "
    "`bash scripts/gate.sh --stages build,test_unit,test_ui` (the stage "
    "list is comma-separated with no spaces). Use that for each "
    "fix-and-check cycle. It deliberately reports no verdict; the "
    "repository's full default gate command, run once at the end before "
    "you push, is the only run that does."
)


def inner_loop_gate_block(cfg: EngineConfig, team: str) -> str:
    if cfg.preset != _IOS_PRESET:
        return ""
    if team not in _FIX_TEAMS:
        return ""
    return INNER_LOOP_GATE_GUIDANCE


__all__ = ["INNER_LOOP_GATE_GUIDANCE", "inner_loop_gate_block"]
