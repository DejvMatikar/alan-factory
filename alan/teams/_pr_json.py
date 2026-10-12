from __future__ import annotations

import json
from typing import Any, TypeAlias, cast

_JsonObject: TypeAlias = dict[str, Any]
_JsonList: TypeAlias = list[object]


def _str_field(  # pyright: ignore[reportUnusedFunction]
    item: _JsonObject, *keys: str
) -> str:
    for key in keys:
        value = item.get(key)
        if value:
            return str(value).upper()
    return ""


def safe_json(raw_text: str) -> object:
    stripped = (raw_text or "").strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def _safe_int(value: object) -> int:  # pyright: ignore[reportUnusedFunction]
    return value if isinstance(value, int) else 0


def _safe_list(  # pyright: ignore[reportUnusedFunction]
    value: object,
) -> list[Any]:
    return list(value) if isinstance(value, list) else []  # pyright: ignore[reportUnknownArgumentType]


def _nav_json_path(  # pyright: ignore[reportUnusedFunction]
    stdout: str, *keys: str
) -> _JsonObject | None:
    layer: Any = safe_json(stdout)
    for key in keys:
        if not isinstance(layer, dict):
            return None
        layer = cast(_JsonObject, layer).get(key)
    if not isinstance(layer, dict):
        return None
    return cast(_JsonObject, layer)
