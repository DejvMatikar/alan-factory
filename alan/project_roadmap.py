from __future__ import annotations

import json
import re
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, TypeAdapter, ValidationError

import alan.project as project_mod
from alan import store

_ROADMAP_FILE = "roadmap.json"
_ITEM_ID_PREFIX = "itm_"
_ITEM_ID_TOKEN_BYTES = 9

_H2_RE = re.compile(r"(?m)^##(?!#)(?P<title>.*)$")

_lock = threading.Lock()


class RoadmapItem(BaseModel):
    id: str
    title: str
    text: str


class RoadmapError(RuntimeError):
    pass


class RoadmapAlreadyImportedError(RoadmapError):
    pass


class RoadmapItemNotFoundError(RoadmapError):
    pass


class RoadmapReorderMismatchError(RoadmapError):
    pass


@dataclass(frozen=True)
class ParsedRoadmapItem:
    title: str
    text: str


_RoadmapItemsAdapter = TypeAdapter(list[RoadmapItem])


def parse_roadmap_markdown(markdown: str) -> list[ParsedRoadmapItem]:
    matches = list(_H2_RE.finditer(markdown))
    items: list[ParsedRoadmapItem] = []
    for i, match in enumerate(matches):
        title = match.group("title").strip()
        if not title:
            continue
        end = match.end()
        body_start = end + 1 if markdown[end : end + 1] == "\n" else end
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        items.append(ParsedRoadmapItem(title=title, text=markdown[body_start:body_end].strip("\n")))
    return items


def _mint_item_id() -> str:
    return f"{_ITEM_ID_PREFIX}{secrets.token_urlsafe(_ITEM_ID_TOKEN_BYTES)}"


def _roadmap_path(project_id: str, *, root: Path | None = None) -> Path:
    return project_mod.project_dir(project_id, root=root) / _ROADMAP_FILE


def read_items(project_id: str, *, root: Path | None = None) -> list[RoadmapItem]:
    path = _roadmap_path(project_id, root=root)
    if not path.is_file():
        return []
    raw = path.read_text(encoding="utf-8")
    try:
        return _RoadmapItemsAdapter.validate_json(raw)
    except ValidationError as exc:
        raise RoadmapError(f"{_ROADMAP_FILE} malformed for project {project_id}") from exc


def seed_items(
    project_id: str, parsed: list[ParsedRoadmapItem], *, root: Path | None = None
) -> list[RoadmapItem]:
    path = _roadmap_path(project_id, root=root)
    with _lock:
        if path.is_file():
            raise RoadmapAlreadyImportedError(
                f"roadmap already imported for project {project_id}"
            )
        items = [RoadmapItem(id=_mint_item_id(), title=p.title, text=p.text) for p in parsed]
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_items(path, items)
    return items


def import_roadmap(
    project_id: str, markdown: str, *, root: Path | None = None
) -> list[RoadmapItem]:
    return seed_items(project_id, parse_roadmap_markdown(markdown), root=root)


def reorder_items(
    project_id: str, ordered_ids: list[str], *, root: Path | None = None
) -> list[RoadmapItem]:
    path = _roadmap_path(project_id, root=root)
    with _lock:
        current = read_items(project_id, root=root)
        by_id = {item.id: item for item in current}
        if len(ordered_ids) != len(current) or set(ordered_ids) != set(by_id):
            raise RoadmapReorderMismatchError(
                "ordered_ids must match the current roadmap item id set exactly"
            )
        reordered = [by_id[item_id] for item_id in ordered_ids]
        _write_items(path, reordered)
    return reordered


def edit_item(
    project_id: str,
    item_id: str,
    *,
    title: str | None = None,
    text: str | None = None,
    root: Path | None = None,
) -> list[RoadmapItem]:
    path = _roadmap_path(project_id, root=root)
    with _lock:
        current = read_items(project_id, root=root)
        if not any(item.id == item_id for item in current):
            raise RoadmapItemNotFoundError(f"roadmap item not found: {item_id}")
        updates: dict[str, str] = {}
        if title is not None:
            updates["title"] = title
        if text is not None:
            updates["text"] = text
        updated = [
            item.model_copy(update=updates) if item.id == item_id else item
            for item in current
        ]
        _write_items(path, updated)
    return updated


def _write_items(path: Path, items: list[RoadmapItem]) -> None:
    store.write_text_atomic(
        path,
        json.dumps(
            [item.model_dump(mode="json") for item in items], indent=2, sort_keys=False
        ),
    )


__all__ = [
    "ParsedRoadmapItem",
    "RoadmapAlreadyImportedError",
    "RoadmapError",
    "RoadmapItem",
    "RoadmapItemNotFoundError",
    "RoadmapReorderMismatchError",
    "edit_item",
    "import_roadmap",
    "parse_roadmap_markdown",
    "read_items",
    "reorder_items",
    "seed_items",
]
