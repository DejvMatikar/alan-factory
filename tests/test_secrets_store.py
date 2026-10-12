from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from alan.engine_config import DEFAULT_FORGE_TARGET, ForgeTarget, RepoIdentity
from alan.secrets_store import (
    EXPIRY_WARNING_DAYS,
    REVIEWER_PURPOSE,
    TRANSPORT_PURPOSE,
    RecordState,
    ReviewerRecordDetail,
    SecretRecordMeta,
    SecretStoreError,
    _key_path,
    _load_record,
    _meta_from_record,
    delete_secret,
    due_expiry_warnings,
    expiring_secrets,
    fingerprint,
    list_secrets,
    record_name,
    resolve_secret,
    reviewer_record_detail,
    reviewer_record_state,
    store_root,
    transport_credential,
    write_secret,
)


@pytest.fixture(autouse=True)
def _store_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "secrets"
    monkeypatch.setenv("ALAN_SECRETS_STORE_ROOT", str(root))
    return root


def test_store_root_uses_env_override(tmp_path: Path) -> None:
    assert store_root() == tmp_path / "secrets"


def test_store_root_falls_back_to_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_SECRETS_STORE_ROOT", raising=False)
    assert store_root() == Path.home() / ".alan" / "secrets"


def test_store_root_falls_back_when_env_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_SECRETS_STORE_ROOT", "   ")
    assert store_root() == Path.home() / ".alan" / "secrets"


def test_reviewer_purpose_constant_value() -> None:
    assert REVIEWER_PURPOSE == "reviewer"


def test_record_name_joins_engine_and_purpose() -> None:
    assert record_name("DejvMatikar/alan", "transport") == "DejvMatikar/alan:transport"


def test_record_name_rejects_empty_engine() -> None:
    with pytest.raises(SecretStoreError, match="engine"):
        record_name("", "transport")


def test_record_name_rejects_whitespace_only_purpose() -> None:
    with pytest.raises(SecretStoreError, match="purpose"):
        record_name("engine", "   ")


def test_fingerprint_is_truncated_sha256_hex() -> None:
    value = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    expected = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    assert fingerprint(value) == expected
    assert len(fingerprint(value)) == 12


def test_fingerprint_is_not_a_prefix_of_the_value() -> None:
    value = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    fp = fingerprint(value)
    assert fp != value[: len(fp)]


def test_fingerprint_differs_for_different_values() -> None:
    assert fingerprint("value-one") != fingerprint("value-two")


def test_fingerprint_deterministic() -> None:
    assert fingerprint("stable") == fingerprint("stable")


def test_write_secret_returns_meta_without_value() -> None:
    meta = write_secret("engine-a", "transport", "topsecret")
    assert meta == SecretRecordMeta(
        name="engine-a:transport", fingerprint=fingerprint("topsecret"), last_verified=None
    )


def test_write_secret_persists_file_with_owner_only_permissions(tmp_path: Path) -> None:
    write_secret("engine-a", "transport", "topsecret")
    path = _key_path("engine-a", "transport")
    assert path.is_file()
    assert (path.stat().st_mode & 0o777) == 0o600


def test_write_secret_creates_store_dir_if_missing(tmp_path: Path) -> None:
    root = tmp_path / "secrets"
    assert not root.exists()
    write_secret("engine-a", "transport", "topsecret")
    assert root.is_dir()


def test_write_secret_overwrites_existing_key() -> None:
    write_secret("engine-a", "transport", "first-value")
    meta = write_secret("engine-a", "transport", "second-value")
    assert meta.fingerprint == fingerprint("second-value")
    assert resolve_secret("engine-a", "transport", env_fallback=None) == "second-value"


def test_write_secret_rejects_empty_engine() -> None:
    with pytest.raises(SecretStoreError, match="engine"):
        write_secret("", "transport", "value")


def test_write_secret_rejects_empty_purpose() -> None:
    with pytest.raises(SecretStoreError, match="purpose"):
        write_secret("engine-a", "", "value")


def test_write_secret_rejects_empty_value() -> None:
    with pytest.raises(SecretStoreError, match="value"):
        write_secret("engine-a", "transport", "")


def test_write_secret_rejects_whitespace_only_value() -> None:
    with pytest.raises(SecretStoreError, match="value"):
        write_secret("engine-a", "transport", "   ")


def test_write_secret_leaves_no_tmp_file_behind_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(json, "dumps", _boom)
    with pytest.raises(RuntimeError, match="disk full"):
        write_secret("engine-a", "transport", "value")
    root = tmp_path / "secrets"
    assert list(root.glob("*.tmp")) == []
    assert list(root.glob("*.json")) == []


def test_delete_secret_removes_existing_key() -> None:
    write_secret("engine-a", "transport", "value")
    assert delete_secret("engine-a", "transport") is True
    assert _key_path("engine-a", "transport").exists() is False


def test_delete_secret_returns_false_when_missing() -> None:
    assert delete_secret("engine-a", "transport") is False


def test_delete_secret_rejects_empty_engine() -> None:
    with pytest.raises(SecretStoreError, match="engine"):
        delete_secret("", "transport")


def test_delete_secret_rejects_empty_purpose() -> None:
    with pytest.raises(SecretStoreError, match="purpose"):
        delete_secret("engine-a", "")


def test_list_secrets_empty_when_store_missing(tmp_path: Path) -> None:
    assert list_secrets() == []


def test_list_secrets_returns_created_records_without_value() -> None:
    write_secret("engine-a", "transport", "value-a")
    write_secret("engine-b", "code_quality", "value-b")
    names = {m.name for m in list_secrets()}
    assert names == {"engine-a:transport", "engine-b:code_quality"}
    for meta in list_secrets():
        assert not hasattr(meta, "value")


def test_list_secrets_skips_invalid_json_file(tmp_path: Path) -> None:
    root = tmp_path / "secrets"
    root.mkdir(parents=True)
    (root / "garbage.json").write_text("not json{{", encoding="utf-8")
    assert list_secrets() == []


def test_list_secrets_skips_non_dict_json_file(tmp_path: Path) -> None:
    root = tmp_path / "secrets"
    root.mkdir(parents=True)
    (root / "array.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert list_secrets() == []


def test_list_secrets_skips_record_with_wrong_field_types(tmp_path: Path) -> None:
    root = tmp_path / "secrets"
    root.mkdir(parents=True)
    (root / "bad.json").write_text(
        json.dumps({"engine": 123, "purpose": "x", "fingerprint": "abc"}), encoding="utf-8"
    )
    assert list_secrets() == []


def test_list_secrets_reflects_last_verified_when_set(tmp_path: Path) -> None:
    root = tmp_path / "secrets"
    root.mkdir(parents=True)
    (root / "verified.json").write_text(
        json.dumps({
            "engine": "engine-a",
            "purpose": "transport",
            "value": "v",
            "fingerprint": "abc",
            "last_verified": "2026-07-20T00:00:00+00:00",
        }),
        encoding="utf-8",
    )
    metas = list_secrets()
    assert len(metas) == 1
    assert metas[0].last_verified == "2026-07-20T00:00:00+00:00"


def test_resolve_secret_prefers_store_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    write_secret("engine-a", "transport", "store-value")
    monkeypatch.setenv("ENGINE_A_TOKEN", "env-value")
    assert resolve_secret("engine-a", "transport", env_fallback="ENGINE_A_TOKEN") == "store-value"


def test_resolve_secret_falls_back_to_env_when_store_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENGINE_A_TOKEN", "env-value")
    assert resolve_secret("engine-a", "transport", env_fallback="ENGINE_A_TOKEN") == "env-value"


def test_resolve_secret_none_when_store_empty_and_no_fallback() -> None:
    assert resolve_secret("engine-a", "transport", env_fallback=None) is None


def test_resolve_secret_none_when_fallback_env_var_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENGINE_A_TOKEN", raising=False)
    assert resolve_secret("engine-a", "transport", env_fallback="ENGINE_A_TOKEN") is None


def test_resolve_secret_default_engine_env_fallback_keeps_working(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_VERIFIER_GH_TOKEN", "ghp_ambient")
    resolved = resolve_secret(
        "DejvMatikar/alan", "transport", env_fallback="ALAN_VERIFIER_GH_TOKEN"
    )
    assert resolved == "ghp_ambient"


def test_resolve_secret_falls_through_when_stored_value_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "secrets"
    root.mkdir(parents=True)
    path = _key_path("engine-a", "transport")
    path.write_text(
        json.dumps({
            "engine": "engine-a", "purpose": "transport", "value": "",
            "fingerprint": "abc", "last_verified": None,
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("ENGINE_A_TOKEN", "env-value")
    assert resolve_secret("engine-a", "transport", env_fallback="ENGINE_A_TOKEN") == "env-value"


_TRANSPORT_TARGET = ForgeTarget(
    repo=RepoIdentity(owner="acme", repo="widget"),
    credential_env_name="ACME_GH_TOKEN",  # noqa: S106  # nosec B106
)


def test_transport_credential_none_when_neither_store_nor_env_has_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ACME_GH_TOKEN", raising=False)
    assert transport_credential(_TRANSPORT_TARGET) is None


def test_transport_credential_none_when_env_value_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACME_GH_TOKEN", "")
    assert transport_credential(_TRANSPORT_TARGET) is None


def test_transport_credential_none_when_stored_value_is_whitespace_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ACME_GH_TOKEN", raising=False)
    path = _key_path("acme/widget", "transport")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "engine": "acme/widget", "purpose": "transport", "value": "   \t\n",
            "fingerprint": "abc", "last_verified": None,
        }),
        encoding="utf-8",
    )
    assert transport_credential(_TRANSPORT_TARGET) is None


def test_transport_credential_none_when_env_value_is_whitespace_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACME_GH_TOKEN", "   ")
    assert transport_credential(_TRANSPORT_TARGET) is None


def test_transport_credential_strips_surrounding_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACME_GH_TOKEN", "  ghp_padded\n")
    assert transport_credential(_TRANSPORT_TARGET) == "ghp_padded"


def test_transport_credential_prefers_the_store_over_the_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_secret("acme/widget", "transport", "store-value")
    monkeypatch.setenv("ACME_GH_TOKEN", "env-value")
    assert transport_credential(_TRANSPORT_TARGET) == "store-value"


def test_transport_credential_falls_back_to_the_env_when_the_store_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACME_GH_TOKEN", "env-value")
    assert transport_credential(_TRANSPORT_TARGET) == "env-value"


def test_transport_credential_reads_the_default_forge_target_env_when_passed_explicitly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(DEFAULT_FORGE_TARGET.credential_env_name, "default-env")
    assert transport_credential(DEFAULT_FORGE_TARGET) == "default-env"


def test_load_record_returns_none_for_missing_file(tmp_path: Path) -> None:
    assert _load_record(tmp_path / "does-not-exist.json") is None


def test_meta_from_record_none_when_fingerprint_not_string() -> None:
    assert _meta_from_record({"engine": "a", "purpose": "b", "fingerprint": 1}) is None


def test_meta_from_record_none_when_engine_whitespace_only() -> None:
    assert _meta_from_record({"engine": "  ", "purpose": "b", "fingerprint": "abc"}) is None


def test_meta_from_record_none_when_purpose_whitespace_only() -> None:
    assert _meta_from_record({"engine": "a", "purpose": "  ", "fingerprint": "abc"}) is None


def test_list_secrets_skips_record_with_whitespace_only_engine(tmp_path: Path) -> None:
    root = tmp_path / "secrets"
    root.mkdir(parents=True)
    (root / "bad.json").write_text(
        json.dumps({"engine": "   ", "purpose": "transport", "fingerprint": "abc"}),
        encoding="utf-8",
    )
    assert list_secrets() == []


def test_write_secret_records_expires_at() -> None:
    meta = write_secret("engine-a", "transport", "value", expires_at="2099-01-01")
    assert meta.expires_at == "2099-01-01"
    assert meta.expiry_warned_at is None


def test_write_secret_defaults_expires_at_to_none() -> None:
    meta = write_secret("engine-a", "transport", "value")
    assert meta.expires_at is None
    assert meta.expiry_warned_at is None


def test_write_secret_treats_blank_expires_at_as_none() -> None:
    meta = write_secret("engine-a", "transport", "value", expires_at="   ")
    assert meta.expires_at is None


def test_write_secret_rejects_malformed_expires_at() -> None:
    with pytest.raises(SecretStoreError, match="expires_at"):
        write_secret("engine-a", "transport", "value", expires_at="not-a-date")


def test_write_secret_persists_expires_at_across_reload() -> None:
    write_secret("engine-a", "transport", "value", expires_at="2099-06-15")
    metas = list_secrets()
    assert len(metas) == 1
    assert metas[0].expires_at == "2099-06-15"
    assert metas[0].expiry_warned_at is None


def test_write_secret_overwrite_resets_expiry_warned_at() -> None:
    write_secret("engine-a", "transport", "first-value", expires_at="2099-01-01")
    path = _key_path("engine-a", "transport")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["expiry_warned_at"] = "2026-01-01T00:00:00+00:00"
    path.write_text(json.dumps(raw), encoding="utf-8")

    meta = write_secret("engine-a", "transport", "second-value", expires_at="2099-02-02")
    assert meta.expires_at == "2099-02-02"
    assert meta.expiry_warned_at is None


def test_list_secrets_reflects_expiry_fields_when_set(tmp_path: Path) -> None:
    root = tmp_path / "secrets"
    root.mkdir(parents=True)
    (root / "expiring.json").write_text(
        json.dumps({
            "engine": "engine-a",
            "purpose": "transport",
            "value": "v",
            "fingerprint": "abc",
            "last_verified": None,
            "expires_at": "2099-03-03",
            "expiry_warned_at": "2026-01-01T00:00:00+00:00",
        }),
        encoding="utf-8",
    )
    metas = list_secrets()
    assert len(metas) == 1
    assert metas[0].expires_at == "2099-03-03"
    assert metas[0].expiry_warned_at == "2026-01-01T00:00:00+00:00"


def test_meta_from_record_expiry_fields_default_none_when_absent() -> None:
    meta = _meta_from_record({"engine": "a", "purpose": "b", "fingerprint": "abc"})
    assert meta is not None
    assert meta.expires_at is None
    assert meta.expiry_warned_at is None


def test_meta_from_record_ignores_non_string_expiry_fields() -> None:
    meta = _meta_from_record({
        "engine": "a", "purpose": "b", "fingerprint": "abc",
        "expires_at": 1, "expiry_warned_at": 2,
    })
    assert meta is not None
    assert meta.expires_at is None
    assert meta.expiry_warned_at is None


def test_due_expiry_warnings_empty_when_store_missing() -> None:
    assert due_expiry_warnings(datetime.now(UTC)) == []


def test_due_expiry_warnings_skips_record_without_expiry() -> None:
    write_secret("engine-a", "transport", "value")
    assert due_expiry_warnings(datetime.now(UTC)) == []


def test_due_expiry_warnings_skips_record_outside_window() -> None:
    now = datetime.now(UTC)
    expires_at = (now.date() + timedelta(days=30)).isoformat()
    write_secret("engine-a", "transport", "value", expires_at=expires_at)
    assert due_expiry_warnings(now) == []


def test_due_expiry_warnings_returns_record_inside_window_and_marks_it_warned() -> None:
    now = datetime.now(UTC)
    expires_at = (now.date() + timedelta(days=5)).isoformat()
    write_secret("engine-a", "transport", "value", expires_at=expires_at)

    due = due_expiry_warnings(now)
    assert len(due) == 1
    assert due[0].name == "engine-a:transport"
    assert due[0].expires_at == expires_at
    assert due[0].expiry_warned_at == now.isoformat()

    persisted = list_secrets()
    assert persisted[0].expiry_warned_at == now.isoformat()


def test_due_expiry_warnings_fires_exactly_once() -> None:
    now = datetime.now(UTC)
    expires_at = (now.date() + timedelta(days=2)).isoformat()
    write_secret("engine-a", "transport", "value", expires_at=expires_at)

    first = due_expiry_warnings(now)
    assert len(first) == 1

    second = due_expiry_warnings(now + timedelta(hours=1))
    assert second == []


def test_due_expiry_warnings_includes_already_expired_record() -> None:
    now = datetime.now(UTC)
    expires_at = (now.date() - timedelta(days=10)).isoformat()
    write_secret("engine-a", "transport", "value", expires_at=expires_at)

    due = due_expiry_warnings(now)
    assert len(due) == 1
    assert due[0].expires_at == expires_at


def test_due_expiry_warnings_respects_custom_warning_days() -> None:
    now = datetime.now(UTC)
    expires_at = (now.date() + timedelta(days=5)).isoformat()
    write_secret("engine-a", "transport", "value", expires_at=expires_at)

    assert due_expiry_warnings(now, warning_days=1) == []
    assert len(due_expiry_warnings(now, warning_days=10)) == 1


def test_due_expiry_warnings_default_window_is_seven_days() -> None:
    assert EXPIRY_WARNING_DAYS == 7


def test_due_expiry_warnings_skips_corrupted_expiry_date_string(tmp_path: Path) -> None:
    root = tmp_path / "secrets"
    root.mkdir(parents=True)
    (root / "bad.json").write_text(
        json.dumps({
            "engine": "engine-a",
            "purpose": "transport",
            "value": "v",
            "fingerprint": "abc",
            "expires_at": "not-a-date",
            "expiry_warned_at": None,
        }),
        encoding="utf-8",
    )
    assert due_expiry_warnings(datetime.now(UTC)) == []


def test_due_expiry_warnings_skips_unreadable_json_file(tmp_path: Path) -> None:
    root = tmp_path / "secrets"
    root.mkdir(parents=True)
    (root / "garbage.json").write_text("not json{{", encoding="utf-8")
    assert due_expiry_warnings(datetime.now(UTC)) == []


def test_expiring_secrets_empty_when_store_missing() -> None:
    assert expiring_secrets(datetime.now(UTC)) == []


def test_expiring_secrets_excludes_record_outside_window() -> None:
    now = datetime.now(UTC)
    expires_at = (now.date() + timedelta(days=30)).isoformat()
    write_secret("engine-a", "transport", "value", expires_at=expires_at)
    assert expiring_secrets(now) == []


def test_expiring_secrets_includes_record_inside_window() -> None:
    now = datetime.now(UTC)
    expires_at = (now.date() + timedelta(days=5)).isoformat()
    write_secret("engine-a", "transport", "value", expires_at=expires_at)

    result = expiring_secrets(now)

    assert len(result) == 1
    assert result[0].name == "engine-a:transport"
    assert result[0].expires_at == expires_at


def test_expiring_secrets_repeated_calls_return_same_result(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    expires_at = (now.date() + timedelta(days=2)).isoformat()
    write_secret("engine-a", "transport", "value", expires_at=expires_at)

    first = expiring_secrets(now)
    second = expiring_secrets(now + timedelta(hours=1))

    assert len(first) == 1
    assert len(second) == 1
    assert first[0].name == second[0].name


def test_expiring_secrets_does_not_modify_expiry_warned_at(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    expires_at = (now.date() + timedelta(days=2)).isoformat()
    write_secret("engine-a", "transport", "value", expires_at=expires_at)

    expiring_secrets(now)

    persisted = list_secrets()
    assert persisted[0].expiry_warned_at is None


def test_expiring_secrets_respects_custom_warning_days() -> None:
    now = datetime.now(UTC)
    expires_at = (now.date() + timedelta(days=5)).isoformat()
    write_secret("engine-a", "transport", "value", expires_at=expires_at)

    assert expiring_secrets(now, warning_days=1) == []
    assert len(expiring_secrets(now, warning_days=10)) == 1


def test_expiring_secrets_skips_record_without_expiry() -> None:
    write_secret("engine-a", "transport", "value")
    assert expiring_secrets(datetime.now(UTC)) == []


def test_expiring_secrets_skips_unreadable_json_file(tmp_path: Path) -> None:
    root = tmp_path / "secrets"
    root.mkdir(parents=True)
    (root / "garbage.json").write_text("not json{{", encoding="utf-8")
    assert expiring_secrets(datetime.now(UTC)) == []


def test_expiring_secrets_skips_corrupted_expiry_date_string(tmp_path: Path) -> None:
    root = tmp_path / "secrets"
    root.mkdir(parents=True)
    (root / "bad.json").write_text(
        json.dumps({
            "engine": "engine-a",
            "purpose": "transport",
            "value": "v",
            "fingerprint": "abc",
            "expires_at": "not-a-date",
            "expiry_warned_at": None,
        }),
        encoding="utf-8",
    )
    assert expiring_secrets(datetime.now(UTC)) == []


def test_due_expiry_warnings_only_reports_each_engine_once() -> None:
    now = datetime.now(UTC)
    expires_at = (now.date() + timedelta(days=3)).isoformat()
    write_secret("engine-a", "transport", "value-a", expires_at=expires_at)
    write_secret("engine-b", "reviewer", "value-b", expires_at=expires_at)
    write_secret("engine-c", "transport", "value-c")

    due = due_expiry_warnings(now)
    assert {m.name for m in due} == {"engine-a:transport", "engine-b:reviewer"}


_ENGINE = "acme/widget"


def _reviewer_record_path() -> Path:
    return _key_path(_ENGINE, REVIEWER_PURPOSE)


def test_reviewer_record_state_is_absent_when_nothing_is_stored() -> None:
    assert reviewer_record_state(_ENGINE) is RecordState.absent


def test_reviewer_record_state_is_present_once_a_reviewer_secret_is_written() -> None:
    write_secret(_ENGINE, REVIEWER_PURPOSE, "value")
    assert reviewer_record_state(_ENGINE) is RecordState.present


def test_reviewer_record_state_ignores_a_transport_record() -> None:
    write_secret(_ENGINE, TRANSPORT_PURPOSE, "value")
    assert reviewer_record_state(_ENGINE) is RecordState.absent


def test_reviewer_record_state_is_scoped_to_the_engine() -> None:
    write_secret("acme/other", REVIEWER_PURPOSE, "value")
    assert reviewer_record_state(_ENGINE) is RecordState.absent


def test_reviewer_record_state_never_infers_a_reviewer_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_VERIFIER_GH_TOKEN", "signed-in-account")
    assert reviewer_record_state(_ENGINE) is RecordState.absent


@pytest.mark.parametrize("value", ["", "   ", None, 5])
def test_reviewer_record_state_is_absent_when_the_record_holds_no_usable_value(
    value: object,
) -> None:
    write_secret(_ENGINE, REVIEWER_PURPOSE, "value")
    _reviewer_record_path().write_text(json.dumps({"value": value}), encoding="utf-8")
    assert reviewer_record_state(_ENGINE) is RecordState.absent


def test_reviewer_record_state_keeps_an_unreadable_file_distinct_from_an_absent_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_secret(_ENGINE, REVIEWER_PURPOSE, "value")
    target = _reviewer_record_path()
    real_read_text = Path.read_text

    def _refuse(self: Path, *args: object, **kwargs: object) -> str:
        if self == target:
            raise PermissionError(13, "denied", str(self))
        return real_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", _refuse)
    assert reviewer_record_state(_ENGINE) is RecordState.unreadable


def test_reviewer_record_state_treats_a_directory_at_the_record_path_as_unreadable() -> None:
    _reviewer_record_path().mkdir(parents=True)
    assert reviewer_record_state(_ENGINE) is RecordState.unreadable


@pytest.mark.parametrize("raw", [b"\xff\xfe\x00", b"not json", b"[1, 2]", b'"text"'])
def test_reviewer_record_state_treats_a_malformed_record_as_unreadable(raw: bytes) -> None:
    path = _reviewer_record_path()
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    assert reviewer_record_state(_ENGINE) is RecordState.unreadable


def test_reviewer_record_detail_carries_the_expiry_and_the_fingerprint() -> None:
    write_secret(_ENGINE, REVIEWER_PURPOSE, "value", expires_at="2031-02-03")
    detail = reviewer_record_detail(_ENGINE)
    assert detail == ReviewerRecordDetail(expires_at="2031-02-03", fingerprint=fingerprint("value"))


def test_reviewer_record_detail_has_no_expiry_when_none_was_recorded() -> None:
    write_secret(_ENGINE, REVIEWER_PURPOSE, "value")
    assert reviewer_record_detail(_ENGINE).expires_at is None


def test_reviewer_record_detail_ignores_a_non_string_expiry_and_fingerprint() -> None:
    write_secret(_ENGINE, REVIEWER_PURPOSE, "value")
    _reviewer_record_path().write_text(
        json.dumps({"value": "v", "expires_at": 5, "fingerprint": 7}), encoding="utf-8"
    )
    assert reviewer_record_detail(_ENGINE) == ReviewerRecordDetail(None, None)


def test_reviewer_record_detail_raises_when_the_record_is_absent() -> None:
    with pytest.raises(FileNotFoundError):
        reviewer_record_detail(_ENGINE)


def test_reviewer_record_detail_raises_on_a_record_that_is_not_a_json_object() -> None:
    write_secret(_ENGINE, REVIEWER_PURPOSE, "value")
    _reviewer_record_path().write_text("[1]", encoding="utf-8")
    with pytest.raises(SecretStoreError):
        reviewer_record_detail(_ENGINE)


def test_reviewer_record_detail_raises_on_invalid_json() -> None:
    write_secret(_ENGINE, REVIEWER_PURPOSE, "value")
    _reviewer_record_path().write_text("{", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        reviewer_record_detail(_ENGINE)


@pytest.mark.parametrize(
    ("expires_at", "today", "expected"),
    [
        (None, datetime(2031, 2, 3, tzinfo=UTC).date(), False),
        ("2031-02-03", datetime(2031, 2, 2, tzinfo=UTC).date(), False),
        ("2031-02-03", datetime(2031, 2, 3, tzinfo=UTC).date(), True),
        ("2031-02-03", datetime(2031, 2, 4, tzinfo=UTC).date(), True),
        ("garbage", datetime(2031, 2, 4, tzinfo=UTC).date(), False),
    ],
)
def test_expired_on_means_on_or_after_the_recorded_date(
    expires_at: str | None, today: date, expected: bool
) -> None:
    detail = ReviewerRecordDetail(expires_at=expires_at, fingerprint=None)
    assert detail.expired_on(today) is expected


_EXPIRY_WARNING_EXPIRY = date(2031, 2, 3)


@pytest.mark.parametrize(
    ("days_before_expiry", "expected"),
    [(15, False), (14, True), (1, True), (0, False), (-1, False)],
)
def test_expiry_warning_on_window_boundaries(days_before_expiry: int, expected: bool) -> None:
    today = _EXPIRY_WARNING_EXPIRY - timedelta(days=days_before_expiry)
    detail = ReviewerRecordDetail(expires_at=_EXPIRY_WARNING_EXPIRY.isoformat(), fingerprint=None)
    assert detail.expiry_warning_on(today, 14) is expected


def test_expiry_warning_on_false_without_a_recorded_expiry() -> None:
    detail = ReviewerRecordDetail(expires_at=None, fingerprint=None)
    assert detail.expiry_warning_on(_EXPIRY_WARNING_EXPIRY, 14) is False


def test_expiry_warning_on_false_for_an_unparseable_expiry() -> None:
    detail = ReviewerRecordDetail(expires_at="garbage", fingerprint=None)
    assert detail.expiry_warning_on(_EXPIRY_WARNING_EXPIRY, 14) is False


def test_expiry_warning_on_and_expired_on_never_both_true() -> None:
    detail = ReviewerRecordDetail(expires_at=_EXPIRY_WARNING_EXPIRY.isoformat(), fingerprint=None)
    for offset in range(-3, 20):
        today = _EXPIRY_WARNING_EXPIRY - timedelta(days=offset)
        assert not (detail.expiry_warning_on(today, 14) and detail.expired_on(today))


def test_expiry_warning_on_and_expired_on_cover_every_day_from_the_window_start_onward() -> None:
    detail = ReviewerRecordDetail(expires_at=_EXPIRY_WARNING_EXPIRY.isoformat(), fingerprint=None)
    for offset in range(-3, 15):
        today = _EXPIRY_WARNING_EXPIRY - timedelta(days=offset)
        assert detail.expiry_warning_on(today, 14) or detail.expired_on(today)
