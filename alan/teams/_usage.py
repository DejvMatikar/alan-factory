from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from alan.teams._env import float_or_none, int_or_none
from alan.workflow import NodeUsage


def node_usage_from_dict(raw: Mapping[str, Any]) -> NodeUsage | None:
    if not raw:
        return None
    return NodeUsage(
        input_tokens=int_or_none(raw.get("input_tokens")),
        output_tokens=int_or_none(raw.get("output_tokens")),
        cache_creation_input_tokens=int_or_none(raw.get("cache_creation_input_tokens")),
        cache_read_input_tokens=int_or_none(raw.get("cache_read_input_tokens")),
        total_cost_usd=float_or_none(raw.get("total_cost_usd")),
        usage_raw=raw,
    )
