from __future__ import annotations

import hashlib
import re
from collections.abc import Generator
from contextlib import contextmanager

from alan.claude_cli import get_active_cache_env, set_active_cache_env
from alan.teams._pr_forge import governing_config_for_ws
from alan.workspace import Workspace

DERIVED_DATA_ENV = "DERIVED_DATA_PATH"
_SLOT_DERIVED_DATA_DIR = "slot-deriveddata"
_UNSAFE_SEGMENT_CHARS = re.compile(r"[^A-Za-z0-9_.-]")


def engine_dirname(slug: str) -> str:
    sanitized = _UNSAFE_SEGMENT_CHARS.sub("_", slug)
    digest = hashlib.sha256(slug.encode("utf-8")).hexdigest()[:8]
    return f"{sanitized}-{digest}"


def slot_segment(slug: str, slot_index: int) -> str:
    return f"{_SLOT_DERIVED_DATA_DIR}/{engine_dirname(slug)}/slot_{slot_index}"


@contextmanager
def per_slot_derived_data(
    workspace: Workspace, slot_index: int | None
) -> Generator[None, None, None]:
    prior = get_active_cache_env()
    if slot_index is None or prior is None or DERIVED_DATA_ENV not in prior:
        yield
        return
    slug = governing_config_for_ws(workspace).repo.slug
    set_active_cache_env({**prior, DERIVED_DATA_ENV: slot_segment(slug, slot_index)})
    try:
        yield
    finally:
        set_active_cache_env(prior)


__all__ = ["DERIVED_DATA_ENV", "engine_dirname", "per_slot_derived_data", "slot_segment"]
