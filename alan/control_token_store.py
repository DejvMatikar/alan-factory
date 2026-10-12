from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

from alan import store

CONTROL_TOKEN_FILENAME = "control_token"  # noqa: S105  # nosec B105
CONTROL_TOKEN_META_FILENAME = "control_token_meta.json"  # noqa: S105  # nosec B105

_FILE_MODE = 0o600
_DIR_MODE = 0o700
_TOKEN_NBYTES = 32

TOKEN_LIFETIME = timedelta(days=30)
EXPIRY_WARNING_WINDOW = timedelta(days=7)

ControlTokenRecordState = Literal["active", "revoked"]
ControlTokenStatusState = Literal["active", "expired", "revoked"]


@dataclass(frozen=True, slots=True)
class ControlTokenMeta:
    state: ControlTokenRecordState
    token: str | None
    issued_at: datetime | None
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class ControlTokenStatus:
    state: ControlTokenStatusState
    warning: bool
    deadline: str | None


class ControlTokenStoreError(RuntimeError):
    pass


_UNCONFIGURED_META = ControlTokenMeta(state="revoked", token=None, issued_at=None, expires_at=None)
_cached_meta: ControlTokenMeta = _UNCONFIGURED_META


def current_meta() -> ControlTokenMeta:
    return _cached_meta


def reset_cached_meta() -> None:
    _set_cached_meta(_UNCONFIGURED_META)


def _set_cached_meta(meta: ControlTokenMeta) -> None:
    global _cached_meta
    _cached_meta = meta


def _value_path(state_root: Path) -> Path:
    return state_root / CONTROL_TOKEN_FILENAME


def _meta_path(state_root: Path) -> Path:
    return state_root / CONTROL_TOKEN_META_FILENAME


def _write_value(state_root: Path, token: str) -> None:
    state_root.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
    store.write_text_mkstemp(
        _value_path(state_root),
        token,
        prefix=f".{CONTROL_TOKEN_FILENAME}.",
        suffix="",
        mode=_FILE_MODE,
    )


def _read_value(state_root: Path) -> str | None:
    try:
        return _value_path(state_root).read_text(encoding="utf-8")
    except OSError:
        return None


def _tighten_value_permissions(state_root: Path) -> None:
    try:
        os.chmod(_value_path(state_root), _FILE_MODE)
    except OSError:
        pass


def _unlink_value(state_root: Path) -> None:
    _value_path(state_root).unlink(missing_ok=True)


def _meta_payload(meta: ControlTokenMeta) -> dict[str, str | None]:
    return {
        "state": meta.state,
        "token": meta.token,
        "issued_at": meta.issued_at.isoformat() if meta.issued_at is not None else None,
        "expires_at": meta.expires_at.isoformat() if meta.expires_at is not None else None,
    }


def _write_meta(state_root: Path, meta: ControlTokenMeta) -> None:
    state_root.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
    store.write_text_mkstemp(
        _meta_path(state_root),
        json.dumps(_meta_payload(meta)),
        prefix=f".{CONTROL_TOKEN_META_FILENAME}.",
        suffix="",
        mode=_FILE_MODE,
    )


def _optional_datetime(raw: object) -> datetime | None:
    return datetime.fromisoformat(raw) if isinstance(raw, str) else None


def _parse_meta_payload(raw: dict[str, object]) -> ControlTokenMeta | None:
    state_raw = raw.get("state")
    state: ControlTokenRecordState
    if state_raw == "active":
        state = "active"
    elif state_raw == "revoked":
        state = "revoked"
    else:
        return None
    token = raw.get("token")
    return ControlTokenMeta(
        state=state,
        token=token if isinstance(token, str) else None,
        issued_at=_optional_datetime(raw.get("issued_at")),
        expires_at=_optional_datetime(raw.get("expires_at")),
    )


def _read_meta(state_root: Path) -> ControlTokenMeta | None:
    try:
        raw_text = _meta_path(state_root).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        parsed: object = json.loads(raw_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return _parse_meta_payload(cast("dict[str, object]", parsed))


def _generate(state_root: Path, *, now: datetime) -> tuple[str, ControlTokenMeta]:
    token = secrets.token_hex(_TOKEN_NBYTES)
    _write_value(state_root, token)
    meta = ControlTokenMeta(
        state="active", token=token, issued_at=now, expires_at=now + TOKEN_LIFETIME
    )
    _write_meta(state_root, meta)
    return token, meta


def load_or_generate(
    state_root: Path, *, now: datetime | None = None
) -> tuple[str | None, ControlTokenMeta]:
    resolved_now = now if now is not None else datetime.now(UTC)
    value = _read_value(state_root)
    meta = _read_meta(state_root)

    if meta is not None and meta.state == "revoked":
        # Tombstone: honoured across every subsequent boot, never resurrected into a
        # usable token, even if a stale value file still lingers on disk.
        _set_cached_meta(meta)
        return None, meta

    if value is not None and meta is not None:
        _set_cached_meta(meta)
        return value, meta

    if value is not None:
        _tighten_value_permissions(state_root)
        adopted = ControlTokenMeta(
            state="active",
            token=value,
            issued_at=resolved_now,
            expires_at=resolved_now + TOKEN_LIFETIME,
        )
        _write_meta(state_root, adopted)
        _set_cached_meta(adopted)
        return value, adopted

    token, generated = _generate(state_root, now=resolved_now)
    _set_cached_meta(generated)
    return token, generated


def extend_token(state_root: Path, *, now: datetime | None = None) -> ControlTokenMeta:
    resolved_now = now if now is not None else datetime.now(UTC)
    meta = current_meta()
    if meta.state != "active" or meta.token is None:
        raise ControlTokenStoreError("no active control token to extend")
    extended = ControlTokenMeta(
        state="active",
        token=meta.token,
        issued_at=meta.issued_at,
        expires_at=resolved_now + TOKEN_LIFETIME,
    )
    _write_meta(state_root, extended)
    _set_cached_meta(extended)
    return extended


def rotate_token(state_root: Path, *, now: datetime | None = None) -> tuple[str, ControlTokenMeta]:
    resolved_now = now if now is not None else datetime.now(UTC)
    token, meta = _generate(state_root, now=resolved_now)
    _set_cached_meta(meta)
    return token, meta


def revoke_token(state_root: Path) -> ControlTokenMeta:
    _unlink_value(state_root)
    tombstone = ControlTokenMeta(state="revoked", token=None, issued_at=None, expires_at=None)
    _write_meta(state_root, tombstone)
    _set_cached_meta(tombstone)
    return tombstone


def compute_status(meta: ControlTokenMeta, *, now: datetime) -> ControlTokenStatus:
    if meta.state == "revoked" or meta.expires_at is None:
        return ControlTokenStatus(state="revoked", warning=False, deadline=None)
    deadline = meta.expires_at.isoformat()
    if now >= meta.expires_at:
        return ControlTokenStatus(state="expired", warning=True, deadline=deadline)
    warning = now >= meta.expires_at - EXPIRY_WARNING_WINDOW
    return ControlTokenStatus(state="active", warning=warning, deadline=deadline)


def current_status(*, now: datetime | None = None) -> ControlTokenStatus:
    resolved_now = now if now is not None else datetime.now(UTC)
    return compute_status(current_meta(), now=resolved_now)
