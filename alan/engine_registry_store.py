from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from alan import default_profile, roots, store

logger = logging.getLogger(__name__)

_FILE_MODE = 0o600
_DIR_MODE = 0o700
_POSTURE_AUDIT_FILENAME = "posture_audit.jsonl"
_LANE_LIMIT_AUDIT_FILENAME = "lane_limit_audit.jsonl"
_DEFAULT_ORIGIN_SUFFIX = ".default-origin"


class EngineStoreError(ValueError):
    pass


def engines_root() -> Path:
    return roots.engines_root()


def _require_nonempty(label: str, value: str) -> None:
    if not value.strip():
        raise EngineStoreError(f"{label} must not be empty")


def _digest(slug: str) -> str:
    return hashlib.sha256(slug.encode()).hexdigest()


def _key_path(slug: str) -> Path:
    return engines_root() / f"{_digest(slug)}.json"


def _warmup_key_path(slug: str) -> Path:
    return engines_root() / f"warmup_{_digest(slug)}.json"


def _default_origin_path(slug: str) -> Path:
    return engines_root() / f"{_digest(slug)}{_DEFAULT_ORIGIN_SUFFIX}"


def _write_atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
    store.write_text_mkstemp(path, text, prefix=".engine_registry_", mode=_FILE_MODE)


def _write_atomic(path: Path, payload: dict[str, object]) -> None:
    _write_atomic_text(path, json.dumps(payload))


def _resolved_default_slug() -> str:
    profile = default_profile.resolved().profile
    return f"{profile.owner}/{profile.repo}"


def _stamp_default_origin(slug: str) -> None:
    stamp = datetime.now(UTC).isoformat(timespec="microseconds")
    _write_atomic_text(_default_origin_path(slug), stamp)


def default_origin_marked_at(slug: str) -> str | None:
    try:
        return _default_origin_path(slug).read_text(encoding="utf-8", errors="replace").strip()
    except FileNotFoundError:
        return None


def write_engine(slug: str, payload: dict[str, object]) -> None:
    _require_nonempty("slug", slug)
    path = _key_path(slug)
    created_for_default = not path.exists() and slug == _resolved_default_slug()
    if created_for_default or _default_origin_path(slug).exists():
        _stamp_default_origin(slug)
    _write_atomic(path, payload)


def write_connected(slug: str, payload: dict[str, object]) -> None:
    _require_nonempty("slug", slug)
    _default_origin_path(slug).unlink(missing_ok=True)
    _write_atomic(_key_path(slug), payload)


def write_warmup_seconds(slug: str, seconds: float) -> None:
    _require_nonempty("slug", slug)
    _write_atomic(_warmup_key_path(slug), {"warmup_seconds": seconds})


def read_warmup_seconds(slug: str) -> float | None:
    data = _load_record(_warmup_key_path(slug))
    if data is None:
        return None
    value = data.get("warmup_seconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def read_engine(slug: str) -> dict[str, object] | None:
    _require_nonempty("slug", slug)
    return _load_record(_key_path(slug))


def delete_engine(slug: str) -> bool:
    _require_nonempty("slug", slug)
    _warmup_key_path(slug).unlink(missing_ok=True)
    _default_origin_path(slug).unlink(missing_ok=True)
    try:
        _key_path(slug).unlink()
    except FileNotFoundError:
        return False
    return True


def _load_record(path: Path) -> dict[str, object] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data: object = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return cast("dict[str, object]", data)


def _append_audit_line(filename: str, label: str, repo_slug: str, entry: dict[str, object]) -> None:
    path = engines_root() / filename
    payload = {**entry, "timestamp_utc": datetime.now(UTC).isoformat(), "repo": repo_slug}
    write_failed = f"{label} write failed: %s"
    try:
        path.parent.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("%s dir mkdir failed: %s", label, exc)
        return
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, _FILE_MODE)
    except OSError as exc:
        logger.warning(write_failed, exc)
        return
    try:
        fh = os.fdopen(fd, "a", encoding="utf-8")
    except OSError as exc:
        os.close(fd)
        logger.warning(write_failed, exc)
        return
    try:
        with fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning(write_failed, exc)


def append_posture_audit(repo_slug: str, entry: dict[str, object]) -> None:
    _append_audit_line(_POSTURE_AUDIT_FILENAME, "posture audit", repo_slug, entry)


def append_lane_limit_audit(
    repo_slug: str, *, old: int | None, new: int | None, source: str
) -> None:
    entry: dict[str, object] = {"from": old, "to": new, "source": source}
    _append_audit_line(_LANE_LIMIT_AUDIT_FILENAME, "lane limit audit", repo_slug, entry)


def list_engines() -> list[dict[str, object]]:
    root = engines_root()
    if not root.is_dir():
        return []
    payloads: list[dict[str, object]] = []
    for path in sorted(root.glob("*.json")):
        if path.name.startswith("warmup_"):
            continue
        data = _load_record(path)
        if data is not None:
            payloads.append(data)
    return payloads
