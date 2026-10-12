from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import cast

from alan import roots, store
from alan.engine_config import ForgeTarget

_FILE_MODE = 0o600
_DIR_MODE = 0o700
_FINGERPRINT_HEX_LEN = 12
_RECORD_FILE_GLOB = "*.json"

EXPIRY_WARNING_DAYS = 7
CREDENTIAL_EXPIRY_AUDIT_FILE = "credential_expiry_audit.jsonl"
_INVALID_EXPIRY_MSG = "expires_at must be an ISO 8601 date (YYYY-MM-DD)"

TRANSPORT_PURPOSE = "transport"
REVIEWER_PURPOSE = "reviewer"
ASKPASS_TOKEN_ENV = "ALAN_GIT_ASKPASS_TOKEN"  # noqa: S105  # nosec B105
GIT_CRED_RESET_ARGS: tuple[str, ...] = ("-c", "credential.helper=")

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ASKPASS_SCRIPT = _REPO_ROOT / "scripts" / "git-askpass-alan.sh"

_Record = dict[str, object]


class SecretStoreError(ValueError):
    pass


class MissingSecretError(SecretStoreError):
    pass


def store_root() -> Path:
    return roots.secrets_store_root()


def _require_nonempty(label: str, value: str) -> None:
    if not value.strip():
        raise SecretStoreError(f"{label} must not be empty")


def record_name(engine: str, purpose: str) -> str:
    _require_nonempty("engine", engine)
    _require_nonempty("purpose", purpose)
    return f"{engine}:{purpose}"


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:_FINGERPRINT_HEX_LEN]


def _key_path(engine: str, purpose: str) -> Path:
    digest = hashlib.sha256(f"{engine}\x00{purpose}".encode()).hexdigest()
    return store_root() / f"{digest}.json"


@dataclass(frozen=True)
class SecretRecordMeta:
    name: str
    fingerprint: str
    last_verified: str | None
    expires_at: str | None = None
    expiry_warned_at: str | None = None


def _write_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
    store.write_text_mkstemp(path, json.dumps(payload), prefix=".secrets_", mode=_FILE_MODE)


def _try_parse_expiry_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _normalize_expires_at(expires_at: str | None) -> str | None:
    if expires_at is None:
        return None
    stripped = expires_at.strip()
    if not stripped:
        return None
    if _try_parse_expiry_date(stripped) is None:
        raise SecretStoreError(_INVALID_EXPIRY_MSG)
    return stripped


def write_secret(
    engine: str, purpose: str, value: str, *, expires_at: str | None = None
) -> SecretRecordMeta:
    _require_nonempty("engine", engine)
    _require_nonempty("purpose", purpose)
    _require_nonempty("value", value)
    normalized_expiry = _normalize_expires_at(expires_at)
    fp = fingerprint(value)
    payload: dict[str, object] = {
        "engine": engine,
        "purpose": purpose,
        "value": value,
        "fingerprint": fp,
        "last_verified": None,
        "expires_at": normalized_expiry,
        "expiry_warned_at": None,
    }
    _write_atomic(_key_path(engine, purpose), payload)
    return SecretRecordMeta(
        name=record_name(engine, purpose),
        fingerprint=fp,
        last_verified=None,
        expires_at=normalized_expiry,
        expiry_warned_at=None,
    )


def delete_secret(engine: str, purpose: str) -> bool:
    _require_nonempty("engine", engine)
    _require_nonempty("purpose", purpose)
    try:
        _key_path(engine, purpose).unlink()
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


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _meta_from_record(data: dict[str, object]) -> SecretRecordMeta | None:
    engine = data.get("engine")
    purpose = data.get("purpose")
    fp = data.get("fingerprint")
    if not isinstance(engine, str) or not isinstance(purpose, str) or not isinstance(fp, str):
        return None
    if not engine.strip() or not purpose.strip():
        return None
    return SecretRecordMeta(
        name=record_name(engine, purpose),
        fingerprint=fp,
        last_verified=_optional_str(data.get("last_verified")),
        expires_at=_optional_str(data.get("expires_at")),
        expiry_warned_at=_optional_str(data.get("expiry_warned_at")),
    )


def list_secrets() -> list[SecretRecordMeta]:
    root = store_root()
    if not root.is_dir():
        return []
    metas: list[SecretRecordMeta] = []
    for path in sorted(root.glob(_RECORD_FILE_GLOB)):
        data = _load_record(path)
        if data is None:
            continue
        meta = _meta_from_record(data)
        if meta is not None:
            metas.append(meta)
    return metas


def _due_expiry_for_record(
    path: Path, now: datetime, warning_days: int
) -> SecretRecordMeta | None:
    data = _load_record(path)
    if data is None:
        return None
    meta = _meta_from_record(data)
    if meta is None or meta.expires_at is None or meta.expiry_warned_at is not None:
        return None
    expiry_date = _try_parse_expiry_date(meta.expires_at)
    if expiry_date is None or now.date() < expiry_date - timedelta(days=warning_days):
        return None
    warned_at = now.isoformat()
    data["expiry_warned_at"] = warned_at
    _write_atomic(path, data)
    return SecretRecordMeta(
        name=meta.name,
        fingerprint=meta.fingerprint,
        last_verified=meta.last_verified,
        expires_at=meta.expires_at,
        expiry_warned_at=warned_at,
    )


def due_expiry_warnings(
    now: datetime, *, warning_days: int = EXPIRY_WARNING_DAYS
) -> list[SecretRecordMeta]:
    root = store_root()
    if not root.is_dir():
        return []
    due: list[SecretRecordMeta] = []
    for path in sorted(root.glob(_RECORD_FILE_GLOB)):
        meta = _due_expiry_for_record(path, now, warning_days)
        if meta is not None:
            due.append(meta)
    return due


def _is_expiring(meta: SecretRecordMeta, now: datetime, warning_days: int) -> bool:
    if meta.expires_at is None:
        return False
    expiry_date = _try_parse_expiry_date(meta.expires_at)
    if expiry_date is None:
        return False
    return now.date() >= expiry_date - timedelta(days=warning_days)


def expiring_secrets(
    now: datetime, *, warning_days: int = EXPIRY_WARNING_DAYS
) -> list[SecretRecordMeta]:
    root = store_root()
    if not root.is_dir():
        return []
    result: list[SecretRecordMeta] = []
    for path in sorted(root.glob(_RECORD_FILE_GLOB)):
        data = _load_record(path)
        if data is None:
            continue
        meta = _meta_from_record(data)
        if meta is not None and _is_expiring(meta, now, warning_days):
            result.append(meta)
    return result


def resolve_secret(engine: str, purpose: str, *, env_fallback: str | None) -> str | None:
    data = _load_record(_key_path(engine, purpose))
    if data is not None:
        value = data.get("value")
        if isinstance(value, str) and value:
            return value
    if env_fallback:
        env_value = os.environ.get(env_fallback)
        if env_value:
            return env_value
    return None


def transport_credential(forge_target: ForgeTarget) -> str | None:
    token = resolve_secret(
        forge_target.repo.slug, TRANSPORT_PURPOSE, env_fallback=forge_target.credential_env_name
    )
    return (token or "").strip() or None


def transport_env(forge_target: ForgeTarget) -> dict[str, str]:
    token = resolve_secret(
        forge_target.repo.slug, TRANSPORT_PURPOSE, env_fallback=forge_target.credential_env_name
    )
    if not token:
        raise MissingSecretError(
            f"no transport credential for {forge_target.repo.slug!r}: secrets store has no "
            f"{TRANSPORT_PURPOSE!r} record and {forge_target.credential_env_name} is unset"
        )
    return {
        "GIT_ASKPASS": str(_ASKPASS_SCRIPT),
        ASKPASS_TOKEN_ENV: token,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",
    }


def verifier_gh_env(forge_target: ForgeTarget) -> dict[str, str]:
    token = (
        resolve_secret(
            forge_target.repo.slug,
            REVIEWER_PURPOSE,
            env_fallback=forge_target.credential_env_name,
        )
        or ""
    ).strip()
    return {"GH_TOKEN": token} if token else {}


class RecordState(StrEnum):
    absent = "absent"
    unreadable = "unreadable"
    present = "present"


@dataclass(frozen=True)
class ReviewerRecordDetail:
    expires_at: str | None
    fingerprint: str | None

    def expired_on(self, today: date) -> bool:
        if self.expires_at is None:
            return False
        expiry_date = _try_parse_expiry_date(self.expires_at)
        return expiry_date is not None and today >= expiry_date

    def expiry_warning_on(self, today: date, days: int) -> bool:
        if self.expires_at is None:
            return False
        expiry_date = _try_parse_expiry_date(self.expires_at)
        if expiry_date is None:
            return False
        return expiry_date - timedelta(days=days) <= today < expiry_date


def _as_record(data: object) -> _Record | None:
    if not isinstance(data, dict):
        return None
    return cast("_Record", data)


def _read_record_file(path: Path) -> tuple[RecordState, _Record | None]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return RecordState.absent, None
    except (OSError, UnicodeDecodeError):
        return RecordState.unreadable, None
    try:
        record = _as_record(json.loads(raw))
    except json.JSONDecodeError:
        return RecordState.unreadable, None
    if record is None:
        return RecordState.unreadable, None
    return RecordState.present, record


def reviewer_record_state(engine: str) -> RecordState:
    state, data = _read_record_file(_key_path(engine, REVIEWER_PURPOSE))
    if data is None:
        return state
    value = data.get("value")
    return RecordState.present if isinstance(value, str) and value.strip() else RecordState.absent


def reviewer_record_detail(engine: str) -> ReviewerRecordDetail:
    raw = _key_path(engine, REVIEWER_PURPOSE).read_text(encoding="utf-8")
    record = _as_record(json.loads(raw))
    if record is None:
        raise SecretStoreError("reviewer record is not a JSON object")
    return ReviewerRecordDetail(
        expires_at=_optional_str(record.get("expires_at")),
        fingerprint=_optional_str(record.get("fingerprint")),
    )
