from __future__ import annotations

import json
import os
import stat
import tempfile
from collections.abc import Hashable, Iterable, Mapping, MutableMapping
from pathlib import Path
from typing import Any, TypeVar, cast

_K = TypeVar("_K", bound=Hashable)
_V = TypeVar("_V")

ListingEntry = tuple[str, int, int]


def write_text_atomic(path: Path, text: str, *, mode: int | None = None) -> None:
    if ".." in path.parts:
        raise ValueError(f"path must not contain '..' components: {path!r}")
    tmp = path.with_suffix(path.suffix + ".tmp")
    replaced = False
    try:
        tmp.write_text(text, encoding="utf-8")
        if mode is not None:
            tmp.chmod(mode)
        tmp.replace(path)
        replaced = True
    finally:
        if not replaced:
            tmp.unlink(missing_ok=True)


def write_text_mkstemp(
    path: Path, text: str, *, prefix: str, suffix: str = ".tmp", mode: int | None = None
) -> None:
    if ".." in path.parts:
        raise ValueError(f"path must not contain '..' components: {path!r}")
    fd, tmp_name = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=path.parent)
    replaced = False
    try:
        try:
            if mode is not None:
                os.fchmod(fd, mode)
            handle = os.fdopen(fd, "w", encoding="utf-8")
        except BaseException:
            os.close(fd)
            raise
        with handle:
            handle.write(text)
        os.replace(tmp_name, path)
        replaced = True
    finally:
        if not replaced:
            Path(tmp_name).unlink(missing_ok=True)


def stat_cache_hit(
    cache: Mapping[_K, tuple[int, int, _V]], key: _K, st: os.stat_result
) -> tuple[_V] | None:
    cached = cache.get(key)
    if cached is not None and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
        return (cached[2],)
    return None


def stat_cache_put(
    cache: MutableMapping[_K, tuple[int, int, _V]], key: _K, st: os.stat_result, value: _V
) -> None:
    cache[key] = (st.st_mtime_ns, st.st_size, value)


def parse_jsonl_row(line: str) -> dict[str, Any] | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        raw: Any = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return cast("dict[str, Any]", raw) if isinstance(raw, dict) else None


def parse_jsonl_rows(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        row = parse_jsonl_row(line)
        if row is not None:
            rows.append(row)
    return rows


def positive_int_env(raw: str | None, default: int) -> int:
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def positive_float_env(raw: str | None, default: float) -> float:
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def read_tail_text(path: Path, max_bytes: int) -> str:
    size = path.stat().st_size
    offset = max(0, size - max_bytes)
    with path.open("rb") as f:
        f.seek(offset)
        return f.read().decode("utf-8", errors="replace")


def stat_signature(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def list_dir(path: Path) -> list[Path]:
    try:
        return sorted(path.iterdir())
    except OSError:
        return []


def listing_entry(path: Path, *, dirs_only: bool = False) -> ListingEntry | None:
    try:
        st = path.stat()
    except OSError:
        return None
    if dirs_only and not stat.S_ISDIR(st.st_mode):
        return None
    return path.name, st.st_ino, st.st_size


def listing_fingerprint(
    paths: Iterable[Path], *, dirs_only: bool = False
) -> tuple[ListingEntry, ...]:
    entries = (listing_entry(path, dirs_only=dirs_only) for path in paths)
    return tuple(entry for entry in entries if entry is not None)
