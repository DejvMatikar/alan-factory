from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alan import control_token_store

_NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _reset_cache() -> Iterator[None]:
    control_token_store.reset_cached_meta()
    yield
    control_token_store.reset_cached_meta()


def test_load_or_generate_creates_fresh_token_when_nothing_exists(tmp_path: Path) -> None:
    value, meta = control_token_store.load_or_generate(tmp_path, now=_NOW)
    assert value is not None
    assert len(value) == control_token_store._TOKEN_NBYTES * 2  # noqa: SLF001
    assert meta.state == "active"
    assert meta.token == value
    assert meta.issued_at == _NOW
    assert meta.expires_at == _NOW + control_token_store.TOKEN_LIFETIME
    path = tmp_path / control_token_store.CONTROL_TOKEN_FILENAME
    assert path.read_text(encoding="utf-8") == value
    assert (path.stat().st_mode & 0o777) == 0o600


def test_load_or_generate_reuses_persisted_token_across_simulated_restarts(
    tmp_path: Path,
) -> None:
    first_value, first_meta = control_token_store.load_or_generate(tmp_path, now=_NOW)
    control_token_store.reset_cached_meta()
    second_value, second_meta = control_token_store.load_or_generate(
        tmp_path, now=_NOW + timedelta(days=1)
    )
    assert second_value == first_value
    assert second_meta == first_meta


def test_load_or_generate_adopts_pre_existing_value_without_meta(tmp_path: Path) -> None:
    path = tmp_path / control_token_store.CONTROL_TOKEN_FILENAME
    path.write_text("legacy-plaintext-token", encoding="utf-8")
    path.chmod(0o644)

    value, meta = control_token_store.load_or_generate(tmp_path, now=_NOW)

    assert value == "legacy-plaintext-token"
    assert meta.state == "active"
    assert meta.token == "legacy-plaintext-token"  # noqa: S105
    assert meta.issued_at == _NOW
    assert meta.expires_at == _NOW + control_token_store.TOKEN_LIFETIME
    assert path.read_text(encoding="utf-8") == "legacy-plaintext-token"
    assert (path.stat().st_mode & 0o777) == 0o600
    meta_path = tmp_path / control_token_store.CONTROL_TOKEN_META_FILENAME
    assert meta_path.is_file()


def test_load_or_generate_adopt_tolerates_a_chmod_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / control_token_store.CONTROL_TOKEN_FILENAME).write_text(
        "legacy-token", encoding="utf-8"
    )

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("permission denied")

    monkeypatch.setattr(os, "chmod", _boom)

    value, meta = control_token_store.load_or_generate(tmp_path, now=_NOW)

    assert value == "legacy-token"
    assert meta.state == "active"


def test_load_or_generate_adopt_persists_and_is_not_reminted_on_reload(
    tmp_path: Path,
) -> None:
    (tmp_path / control_token_store.CONTROL_TOKEN_FILENAME).write_text(
        "legacy-token", encoding="utf-8"
    )
    control_token_store.load_or_generate(tmp_path, now=_NOW)
    control_token_store.reset_cached_meta()

    value, meta = control_token_store.load_or_generate(tmp_path, now=_NOW + timedelta(days=10))

    assert value == "legacy-token"
    assert meta.issued_at == _NOW
    assert meta.expires_at == _NOW + control_token_store.TOKEN_LIFETIME


def test_load_or_generate_loads_persisted_pair_verbatim(tmp_path: Path) -> None:
    _first_value, first_meta = control_token_store.load_or_generate(tmp_path, now=_NOW)
    control_token_store.reset_cached_meta()

    value, meta = control_token_store.load_or_generate(tmp_path, now=_NOW + timedelta(days=5))

    assert meta == first_meta
    assert value == first_meta.token


def test_load_or_generate_regenerates_when_value_missing_but_meta_orphaned(
    tmp_path: Path,
) -> None:
    first_value, _first_meta = control_token_store.load_or_generate(tmp_path, now=_NOW)
    (tmp_path / control_token_store.CONTROL_TOKEN_FILENAME).unlink()
    control_token_store.reset_cached_meta()

    later = _NOW + timedelta(days=2)
    second_value, second_meta = control_token_store.load_or_generate(tmp_path, now=later)

    assert second_value is not None
    assert second_value != first_value
    assert second_meta.state == "active"
    assert second_meta.issued_at == later
    assert second_meta.expires_at == later + control_token_store.TOKEN_LIFETIME

    control_token_store.reset_cached_meta()
    third_value, third_meta = control_token_store.load_or_generate(
        tmp_path, now=later + timedelta(days=1)
    )
    assert third_value == second_value
    assert third_meta == second_meta


def test_load_or_generate_honours_tombstone_across_boot(tmp_path: Path) -> None:
    control_token_store.load_or_generate(tmp_path, now=_NOW)
    control_token_store.revoke_token(tmp_path)
    control_token_store.reset_cached_meta()

    value, meta = control_token_store.load_or_generate(tmp_path, now=_NOW + timedelta(days=1))

    assert value is None
    assert meta.state == "revoked"
    assert meta.token is None


def test_load_or_generate_tombstone_wins_even_with_a_stray_value_file(tmp_path: Path) -> None:
    control_token_store.load_or_generate(tmp_path, now=_NOW)
    control_token_store.revoke_token(tmp_path)
    (tmp_path / control_token_store.CONTROL_TOKEN_FILENAME).write_text(
        "stray-leftover-value", encoding="utf-8"
    )
    control_token_store.reset_cached_meta()

    value, meta = control_token_store.load_or_generate(tmp_path, now=_NOW + timedelta(days=1))

    assert value is None
    assert meta.state == "revoked"


def test_load_or_generate_treats_malformed_meta_json_as_absent(tmp_path: Path) -> None:
    path = tmp_path / control_token_store.CONTROL_TOKEN_FILENAME
    path.write_text("existing-value", encoding="utf-8")
    (tmp_path / control_token_store.CONTROL_TOKEN_META_FILENAME).write_text(
        "not valid json {", encoding="utf-8"
    )

    value, meta = control_token_store.load_or_generate(tmp_path, now=_NOW)

    assert value == "existing-value"
    assert meta.state == "active"


def test_load_or_generate_treats_non_dict_meta_json_as_absent(tmp_path: Path) -> None:
    path = tmp_path / control_token_store.CONTROL_TOKEN_FILENAME
    path.write_text("existing-value", encoding="utf-8")
    (tmp_path / control_token_store.CONTROL_TOKEN_META_FILENAME).write_text(
        json.dumps("just a string"), encoding="utf-8"
    )

    value, meta = control_token_store.load_or_generate(tmp_path, now=_NOW)

    assert value == "existing-value"
    assert meta.state == "active"


def test_load_or_generate_treats_unrecognized_state_as_absent(tmp_path: Path) -> None:
    path = tmp_path / control_token_store.CONTROL_TOKEN_FILENAME
    path.write_text("existing-value", encoding="utf-8")
    (tmp_path / control_token_store.CONTROL_TOKEN_META_FILENAME).write_text(
        json.dumps({"state": "bogus"}), encoding="utf-8"
    )

    value, meta = control_token_store.load_or_generate(tmp_path, now=_NOW)

    assert value == "existing-value"
    assert meta.state == "active"


def test_load_or_generate_treats_non_string_token_field_as_none(tmp_path: Path) -> None:
    (tmp_path / control_token_store.CONTROL_TOKEN_META_FILENAME).write_text(
        json.dumps(
            {"state": "active", "token": 12345, "issued_at": None, "expires_at": None}
        ),
        encoding="utf-8",
    )

    value, meta = control_token_store.load_or_generate(tmp_path, now=_NOW)

    assert value is not None
    assert meta.token == value


def test_load_or_generate_defaults_now_to_wall_clock(tmp_path: Path) -> None:
    before = datetime.now(UTC)
    _value, meta = control_token_store.load_or_generate(tmp_path)
    after = datetime.now(UTC)
    assert meta.issued_at is not None
    assert before <= meta.issued_at <= after


def test_extend_token_leaves_value_byte_identical_and_moves_expiry(tmp_path: Path) -> None:
    value, _meta = control_token_store.load_or_generate(tmp_path, now=_NOW)

    later = _NOW + timedelta(days=25)
    extended = control_token_store.extend_token(tmp_path, now=later)

    assert extended.token == value
    assert extended.expires_at == later + control_token_store.TOKEN_LIFETIME
    assert extended.issued_at == _NOW

    control_token_store.reset_cached_meta()
    reloaded_value, reloaded_meta = control_token_store.load_or_generate(
        tmp_path, now=later + timedelta(days=1)
    )
    assert reloaded_value == value
    assert reloaded_meta.expires_at == extended.expires_at


def test_extend_token_raises_without_an_active_cached_token(tmp_path: Path) -> None:
    with pytest.raises(control_token_store.ControlTokenStoreError):
        control_token_store.extend_token(tmp_path, now=_NOW)


def test_extend_token_raises_after_revoke(tmp_path: Path) -> None:
    control_token_store.load_or_generate(tmp_path, now=_NOW)
    control_token_store.revoke_token(tmp_path)
    with pytest.raises(control_token_store.ControlTokenStoreError):
        control_token_store.extend_token(tmp_path, now=_NOW)


def test_extend_token_defaults_now_to_wall_clock(tmp_path: Path) -> None:
    control_token_store.load_or_generate(tmp_path, now=_NOW)
    before = datetime.now(UTC)
    extended = control_token_store.extend_token(tmp_path)
    after = datetime.now(UTC)
    assert extended.expires_at is not None
    assert (
        before + control_token_store.TOKEN_LIFETIME
        <= extended.expires_at
        <= after + control_token_store.TOKEN_LIFETIME
    )


def test_rotate_token_mints_a_new_value_invalidating_the_old_one(tmp_path: Path) -> None:
    old_value, _meta = control_token_store.load_or_generate(tmp_path, now=_NOW)

    new_value, new_meta = control_token_store.rotate_token(
        tmp_path, now=_NOW + timedelta(days=3)
    )

    assert new_value != old_value
    assert new_meta.token == new_value
    assert new_meta.state == "active"
    assert control_token_store.current_meta() == new_meta

    control_token_store.reset_cached_meta()
    reloaded_value, _reloaded_meta = control_token_store.load_or_generate(
        tmp_path, now=_NOW + timedelta(days=4)
    )
    assert reloaded_value == new_value


def test_rotate_token_works_from_a_revoked_state(tmp_path: Path) -> None:
    control_token_store.load_or_generate(tmp_path, now=_NOW)
    control_token_store.revoke_token(tmp_path)

    new_value, new_meta = control_token_store.rotate_token(tmp_path, now=_NOW)

    assert new_value is not None
    assert new_meta.state == "active"


def test_rotate_token_defaults_now_to_wall_clock(tmp_path: Path) -> None:
    before = datetime.now(UTC)
    _value, meta = control_token_store.rotate_token(tmp_path)
    after = datetime.now(UTC)
    assert meta.issued_at is not None
    assert before <= meta.issued_at <= after


def test_revoke_token_writes_tombstone_and_unlinks_the_value_file(tmp_path: Path) -> None:
    control_token_store.load_or_generate(tmp_path, now=_NOW)

    tombstone = control_token_store.revoke_token(tmp_path)

    assert tombstone.state == "revoked"
    assert tombstone.token is None
    assert tombstone.issued_at is None
    assert tombstone.expires_at is None
    assert control_token_store.current_meta() == tombstone
    assert not (tmp_path / control_token_store.CONTROL_TOKEN_FILENAME).exists()


def test_revoke_token_is_safe_when_no_value_file_exists(tmp_path: Path) -> None:
    tombstone = control_token_store.revoke_token(tmp_path)
    assert tombstone.state == "revoked"


_ACTIVE_EXPIRES_AT = _NOW + timedelta(days=30)
_ACTIVE_META = control_token_store.ControlTokenMeta(
    state="active", token="t", issued_at=_NOW, expires_at=_ACTIVE_EXPIRES_AT
)


def test_compute_status_revoked_meta_has_no_deadline() -> None:
    tombstone = control_token_store.ControlTokenMeta(
        state="revoked", token=None, issued_at=None, expires_at=None
    )
    status = control_token_store.compute_status(tombstone, now=_NOW)
    assert status.state == "revoked"
    assert status.warning is False
    assert status.deadline is None


def test_compute_status_active_state_with_missing_expiry_is_treated_as_revoked() -> None:
    corrupt = control_token_store.ControlTokenMeta(
        state="active", token="t", issued_at=_NOW, expires_at=None
    )
    status = control_token_store.compute_status(corrupt, now=_NOW)
    assert status.state == "revoked"
    assert status.deadline is None


def test_compute_status_far_from_expiry_is_plain_active() -> None:
    status = control_token_store.compute_status(_ACTIVE_META, now=_NOW)
    assert status.state == "active"
    assert status.warning is False
    assert status.deadline == _ACTIVE_EXPIRES_AT.isoformat()


def test_compute_status_inside_warning_window_is_warned() -> None:
    now = _ACTIVE_EXPIRES_AT - timedelta(days=3)
    status = control_token_store.compute_status(_ACTIVE_META, now=now)
    assert status.state == "active"
    assert status.warning is True


def test_compute_status_at_exact_warning_boundary_is_warned() -> None:
    now = _ACTIVE_EXPIRES_AT - control_token_store.EXPIRY_WARNING_WINDOW
    status = control_token_store.compute_status(_ACTIVE_META, now=now)
    assert status.warning is True


def test_compute_status_just_before_warning_boundary_is_not_warned() -> None:
    now = _ACTIVE_EXPIRES_AT - control_token_store.EXPIRY_WARNING_WINDOW - timedelta(seconds=1)
    status = control_token_store.compute_status(_ACTIVE_META, now=now)
    assert status.warning is False


def test_compute_status_at_exact_expiry_is_expired() -> None:
    status = control_token_store.compute_status(_ACTIVE_META, now=_ACTIVE_EXPIRES_AT)
    assert status.state == "expired"
    assert status.warning is True


def test_compute_status_after_expiry_is_expired() -> None:
    status = control_token_store.compute_status(
        _ACTIVE_META, now=_ACTIVE_EXPIRES_AT + timedelta(days=100)
    )
    assert status.state == "expired"


def test_current_status_reads_the_module_cache() -> None:
    control_token_store.reset_cached_meta()
    assert control_token_store.current_status(now=_NOW).state == "revoked"


def test_current_status_reflects_a_freshly_generated_token(tmp_path: Path) -> None:
    control_token_store.load_or_generate(tmp_path, now=_NOW)
    status = control_token_store.current_status(now=_NOW)
    assert status.state == "active"
    assert status.warning is False


def test_current_status_defaults_now_to_wall_clock(tmp_path: Path) -> None:
    control_token_store.load_or_generate(tmp_path, now=datetime.now(UTC))
    status = control_token_store.current_status()
    assert status.state == "active"


def test_reset_cached_meta_returns_to_the_sentinel(tmp_path: Path) -> None:
    control_token_store.load_or_generate(tmp_path, now=_NOW)
    assert control_token_store.current_meta().state == "active"
    control_token_store.reset_cached_meta()
    sentinel = control_token_store.current_meta()
    assert sentinel.state == "revoked"
    assert sentinel.token is None
