from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from alan import default_profile
from alan.engine_registry_store import (
    EngineStoreError,
    _default_origin_path,
    _key_path,
    _load_record,
    _warmup_key_path,
    append_lane_limit_audit,
    append_posture_audit,
    default_origin_marked_at,
    delete_engine,
    engines_root,
    list_engines,
    read_engine,
    read_warmup_seconds,
    write_connected,
    write_engine,
    write_warmup_seconds,
)


@pytest.fixture(autouse=True)
def _engines_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "engines"
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(root))
    return root


@pytest.fixture
def widget_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    state = tmp_path / "profile"
    state.mkdir()
    (state / "default_profile.yaml").write_text("owner: acme\nrepo: widget\nposture: observe\n")
    resolved = default_profile.resolve(state, tmp_path / "no-owner.yaml")
    monkeypatch.setattr(default_profile, "resolved", lambda: resolved)
    return "acme/widget"


def test_engines_root_uses_env_override(tmp_path: Path) -> None:
    assert engines_root() == tmp_path / "engines"


def test_engines_root_falls_back_to_default_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_ENGINES_ROOT", raising=False)
    assert engines_root() == Path.home() / ".alan" / "engines"


def test_engines_root_falls_back_when_env_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_ENGINES_ROOT", "   ")
    assert engines_root() == Path.home() / ".alan" / "engines"


def test_write_engine_persists_file_with_owner_only_permissions() -> None:
    write_engine("acme/widget", {"repo": {"owner": "acme", "repo": "widget"}})
    path = _key_path("acme/widget")
    assert path.is_file()
    assert (path.stat().st_mode & 0o777) == 0o600


def test_write_engine_creates_store_dir_if_missing(tmp_path: Path) -> None:
    root = tmp_path / "engines"
    assert not root.exists()
    write_engine("acme/widget", {"repo": {"owner": "acme", "repo": "widget"}})
    assert root.is_dir()


def test_write_engine_overwrites_existing_key() -> None:
    write_engine("acme/widget", {"posture": "observe"})
    write_engine("acme/widget", {"posture": "autonomous"})
    payloads = list_engines()
    assert len(payloads) == 1
    assert payloads[0]["posture"] == "autonomous"


def test_write_engine_rejects_empty_slug() -> None:
    with pytest.raises(EngineStoreError, match="slug"):
        write_engine("", {"posture": "observe"})


def test_write_engine_leaves_no_tmp_file_behind_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(json, "dumps", _boom)
    with pytest.raises(RuntimeError, match="disk full"):
        write_engine("acme/widget", {"posture": "observe"})
    root = tmp_path / "engines"
    assert list(root.glob("*.tmp")) == []
    assert list(root.glob("*.json")) == []


def test_read_engine_returns_persisted_payload() -> None:
    write_engine("acme/widget", {"repo": {"owner": "acme", "repo": "widget"}})
    assert read_engine("acme/widget") == {"repo": {"owner": "acme", "repo": "widget"}}


def test_read_engine_returns_none_when_never_written() -> None:
    assert read_engine("acme/never-written") is None


def test_read_engine_rejects_empty_slug() -> None:
    with pytest.raises(EngineStoreError, match="slug"):
        read_engine("")


def test_write_warmup_seconds_persists_with_owner_only_permissions() -> None:
    write_warmup_seconds("acme/widget", 12.5)
    path = _warmup_key_path("acme/widget")
    assert path.is_file()
    assert (path.stat().st_mode & 0o777) == 0o600


def test_read_warmup_seconds_roundtrips() -> None:
    write_warmup_seconds("acme/widget", 42.25)
    assert read_warmup_seconds("acme/widget") == 42.25


def test_write_warmup_seconds_overwrites_prior_value() -> None:
    write_warmup_seconds("acme/widget", 10.0)
    write_warmup_seconds("acme/widget", 20.0)
    assert read_warmup_seconds("acme/widget") == 20.0


def test_read_warmup_seconds_returns_none_when_never_measured() -> None:
    assert read_warmup_seconds("acme/never-measured") is None


def test_read_warmup_seconds_returns_none_for_corrupt_file(tmp_path: Path) -> None:
    path = _warmup_key_path("acme/widget")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json{{", encoding="utf-8")
    assert read_warmup_seconds("acme/widget") is None


def test_read_warmup_seconds_returns_none_for_non_numeric_value(tmp_path: Path) -> None:
    path = _warmup_key_path("acme/widget")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"warmup_seconds": "not-a-number"}), encoding="utf-8")
    assert read_warmup_seconds("acme/widget") is None


def test_read_warmup_seconds_returns_none_for_bool_value(tmp_path: Path) -> None:
    path = _warmup_key_path("acme/widget")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"warmup_seconds": True}), encoding="utf-8")
    assert read_warmup_seconds("acme/widget") is None


def test_write_warmup_seconds_rejects_empty_slug() -> None:
    with pytest.raises(EngineStoreError, match="slug"):
        write_warmup_seconds("", 1.0)


def test_warmup_key_path_distinct_from_engine_key_path() -> None:
    assert _warmup_key_path("acme/widget") != _key_path("acme/widget")


def test_list_engines_skips_warmup_sidecar_files() -> None:
    write_engine("acme/widget", {"repo": {"owner": "acme", "repo": "widget"}})
    write_warmup_seconds("acme/widget", 5.0)

    payloads = list_engines()

    assert len(payloads) == 1
    assert payloads[0]["repo"] == {"owner": "acme", "repo": "widget"}


def test_delete_engine_removes_existing_key() -> None:
    write_engine("acme/widget", {"posture": "observe"})
    assert delete_engine("acme/widget") is True
    assert _key_path("acme/widget").exists() is False


def test_delete_engine_returns_false_when_missing() -> None:
    assert delete_engine("acme/widget") is False


def test_delete_engine_rejects_empty_slug() -> None:
    with pytest.raises(EngineStoreError, match="slug"):
        delete_engine("")


def test_delete_engine_also_removes_warmup_sidecar() -> None:
    write_engine("acme/widget", {"posture": "observe"})
    write_warmup_seconds("acme/widget", 12.5)

    assert delete_engine("acme/widget") is True

    assert _warmup_key_path("acme/widget").exists() is False


def test_list_engines_empty_when_store_missing() -> None:
    assert list_engines() == []


def test_list_engines_roundtrips_nested_prompt_overrides_payload() -> None:
    write_engine(
        "acme/widget",
        {
            "repo": {"owner": "acme", "repo": "widget"},
            "prompt_overrides": {
                "implementation/implementer": {
                    "text": "custom {{ENVIRONMENT}} {{SUCCESS}} {{REPO_GUIDANCE}}",
                    "base_fingerprint": "deadbeef",
                }
            },
        },
    )
    payloads = list_engines()
    assert len(payloads) == 1
    entry = payloads[0]["prompt_overrides"]["implementation/implementer"]  # type: ignore[index]
    assert entry["text"] == "custom {{ENVIRONMENT}} {{SUCCESS}} {{REPO_GUIDANCE}}"
    assert entry["base_fingerprint"] == "deadbeef"


def test_list_engines_returns_all_persisted_payloads() -> None:
    write_engine("acme/widget", {"repo": {"owner": "acme", "repo": "widget"}})
    write_engine("acme/gadget", {"repo": {"owner": "acme", "repo": "gadget"}})
    slugs = {
        f"{p['repo']['owner']}/{p['repo']['repo']}"  # type: ignore[index]
        for p in list_engines()
    }
    assert slugs == {"acme/widget", "acme/gadget"}


def test_list_engines_skips_invalid_json_file(tmp_path: Path) -> None:
    root = tmp_path / "engines"
    root.mkdir(parents=True)
    (root / "garbage.json").write_text("not json{{", encoding="utf-8")
    assert list_engines() == []


def test_list_engines_skips_non_dict_json_file(tmp_path: Path) -> None:
    root = tmp_path / "engines"
    root.mkdir(parents=True)
    (root / "array.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert list_engines() == []


def test_load_record_returns_none_for_missing_file(tmp_path: Path) -> None:
    assert _load_record(tmp_path / "does-not-exist.json") is None


def test_append_posture_audit_writes_expected_fields(tmp_path: Path) -> None:
    append_posture_audit("acme/widget", {"from": "observe", "to": "propose", "source": "api"})
    path = tmp_path / "engines" / "posture_audit.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["repo"] == "acme/widget"
    assert entry["from"] == "observe"
    assert entry["to"] == "propose"
    assert entry["source"] == "api"
    assert "timestamp_utc" in entry


def test_append_posture_audit_file_has_owner_only_permissions(tmp_path: Path) -> None:
    append_posture_audit("acme/widget", {"from": "observe", "to": "propose", "source": "api"})
    path = tmp_path / "engines" / "posture_audit.jsonl"
    assert (path.stat().st_mode & 0o777) == 0o600


def test_append_posture_audit_entry_cannot_override_reserved_keys(tmp_path: Path) -> None:
    append_posture_audit(
        "acme/widget",
        {
            "from": "observe",
            "to": "propose",
            "source": "api",
            "repo": "spoofed/repo",
            "timestamp_utc": "1970-01-01T00:00:00+00:00",
        },
    )
    path = tmp_path / "engines" / "posture_audit.jsonl"
    entry = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert entry["repo"] == "acme/widget"
    assert entry["timestamp_utc"] != "1970-01-01T00:00:00+00:00"


def test_append_posture_audit_appends_not_truncates(tmp_path: Path) -> None:
    append_posture_audit("acme/widget", {"from": "observe", "to": "propose", "source": "api"})
    append_posture_audit("acme/widget", {"from": "propose", "to": "self_merge", "source": "api"})
    path = tmp_path / "engines" / "posture_audit.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["to"] == "propose"
    assert json.loads(lines[1])["to"] == "self_merge"


def test_append_posture_audit_mkdir_failure_is_logged_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def _boom(*_a: object, **_kw: object) -> None:
        raise OSError("no space left on device")

    monkeypatch.setattr(Path, "mkdir", _boom)
    with caplog.at_level("WARNING", logger="alan.engine_registry_store"):
        append_posture_audit("acme/widget", {"from": "observe", "to": "propose", "source": "api"})
    assert any("posture audit" in r.message for r in caplog.records)
    assert not (tmp_path / "engines" / "posture_audit.jsonl").exists()


def test_append_posture_audit_open_failure_is_logged_not_raised(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def _boom(*_a: object, **_kw: object) -> int:
        raise OSError("permission denied")

    monkeypatch.setattr(os, "open", _boom)
    with caplog.at_level("WARNING", logger="alan.engine_registry_store"):
        append_posture_audit("acme/widget", {"from": "observe", "to": "propose", "source": "api"})
    assert any("posture audit write failed" in r.message for r in caplog.records)


def test_append_posture_audit_fdopen_failure_is_logged_not_raised(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def _boom(*_a: object, **_kw: object) -> None:
        raise OSError("too many open files")

    monkeypatch.setattr(os, "fdopen", _boom)
    with caplog.at_level("WARNING", logger="alan.engine_registry_store"):
        append_posture_audit("acme/widget", {"from": "observe", "to": "propose", "source": "api"})
    assert any("posture audit write failed" in r.message for r in caplog.records)


class _BoomOnWriteFile:
    def __enter__(self) -> "_BoomOnWriteFile":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def write(self, _data: str) -> int:
        raise OSError("disk full")


def test_append_posture_audit_write_failure_is_logged_not_raised(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(os, "fdopen", lambda *_a, **_kw: _BoomOnWriteFile())
    with caplog.at_level("WARNING", logger="alan.engine_registry_store"):
        append_posture_audit("acme/widget", {"from": "observe", "to": "propose", "source": "api"})
    assert any("posture audit write failed" in r.message for r in caplog.records)


def test_append_lane_limit_audit_writes_expected_fields(tmp_path: Path) -> None:
    append_lane_limit_audit("acme/widget", old=None, new=2, source="api")
    path = tmp_path / "engines" / "lane_limit_audit.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["repo"] == "acme/widget"
    assert entry["from"] is None
    assert entry["to"] == 2
    assert entry["source"] == "api"
    assert "timestamp_utc" in entry


def test_append_lane_limit_audit_file_has_owner_only_permissions(tmp_path: Path) -> None:
    append_lane_limit_audit("acme/widget", old=2, new=3, source="api")
    path = tmp_path / "engines" / "lane_limit_audit.jsonl"
    assert (path.stat().st_mode & 0o777) == 0o600


def test_append_lane_limit_audit_appends_not_truncates(tmp_path: Path) -> None:
    append_lane_limit_audit("acme/widget", old=None, new=2, source="api")
    append_lane_limit_audit("acme/widget", old=2, new=3, source="api")
    path = tmp_path / "engines" / "lane_limit_audit.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["to"] == 2
    assert json.loads(lines[1])["to"] == 3


def test_append_lane_limit_audit_entry_cannot_override_reserved_keys(tmp_path: Path) -> None:
    append_lane_limit_audit("acme/widget", old=None, new=2, source="api")
    path = tmp_path / "engines" / "lane_limit_audit.jsonl"
    entry = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert entry["repo"] == "acme/widget"


def test_append_lane_limit_audit_distinct_from_posture_audit_file(tmp_path: Path) -> None:
    append_posture_audit("acme/widget", {"from": "observe", "to": "propose", "source": "api"})
    append_lane_limit_audit("acme/widget", old=None, new=2, source="api")
    assert (tmp_path / "engines" / "posture_audit.jsonl").is_file()
    assert (tmp_path / "engines" / "lane_limit_audit.jsonl").is_file()


def test_write_engine_stamps_a_utc_marker_when_it_creates_the_default_record(
    widget_default: str,
) -> None:
    write_engine(widget_default, {"posture": "observe"})
    marked_at = default_origin_marked_at(widget_default)
    assert marked_at is not None
    assert datetime.fromisoformat(marked_at).utcoffset() == timedelta(0)


def test_the_default_origin_marker_has_owner_only_permissions(widget_default: str) -> None:
    write_engine(widget_default, {"posture": "observe"})
    assert (_default_origin_path(widget_default).stat().st_mode & 0o777) == 0o600


def test_the_default_origin_marker_is_a_sidecar_keyed_by_the_slug_digest(
    widget_default: str,
) -> None:
    digest = hashlib.sha256(widget_default.encode()).hexdigest()
    assert _default_origin_path(widget_default) == engines_root() / f"{digest}.default-origin"


def test_write_engine_does_not_stamp_a_record_for_another_repository(
    widget_default: str,
) -> None:
    _ = widget_default
    write_engine("acme/gadget", {"posture": "observe"})
    assert default_origin_marked_at("acme/gadget") is None


def test_write_engine_leaves_a_legacy_default_record_unmarked(widget_default: str) -> None:
    write_connected(widget_default, {"posture": "observe"})
    write_engine(widget_default, {"posture": "propose"})
    assert default_origin_marked_at(widget_default) is None
    assert read_engine(widget_default) == {"posture": "propose"}


def test_write_engine_refreshes_a_marker_that_is_present(widget_default: str) -> None:
    _ = widget_default
    write_engine("acme/gadget", {"posture": "observe"})
    stale = "2000-01-01T00:00:00.000000+00:00"
    _default_origin_path("acme/gadget").write_text(stale)
    write_engine("acme/gadget", {"posture": "propose"})
    refreshed = default_origin_marked_at("acme/gadget")
    assert refreshed is not None
    assert datetime.fromisoformat(refreshed) > datetime.fromisoformat(stale)


def test_write_connected_writes_the_record_and_unlinks_the_marker(widget_default: str) -> None:
    write_engine(widget_default, {"posture": "observe"})
    write_connected(widget_default, {"posture": "propose"})
    assert default_origin_marked_at(widget_default) is None
    assert read_engine(widget_default) == {"posture": "propose"}


def test_write_connected_never_stamps_the_default_slug(widget_default: str) -> None:
    write_connected(widget_default, {"posture": "observe"})
    assert default_origin_marked_at(widget_default) is None
    assert _key_path(widget_default).is_file()


def test_write_connected_rejects_empty_slug() -> None:
    with pytest.raises(EngineStoreError, match="slug"):
        write_connected("", {"posture": "observe"})


def test_delete_engine_removes_the_default_origin_marker(widget_default: str) -> None:
    write_engine(widget_default, {"posture": "observe"})
    assert delete_engine(widget_default) is True
    assert _default_origin_path(widget_default).exists() is False


def test_delete_engine_removes_a_marker_left_without_a_record(widget_default: str) -> None:
    write_engine(widget_default, {"posture": "observe"})
    _key_path(widget_default).unlink()
    assert delete_engine(widget_default) is False
    assert _default_origin_path(widget_default).exists() is False


def test_list_engines_ignores_default_origin_markers(widget_default: str) -> None:
    write_engine(widget_default, {"repo": {"owner": "acme", "repo": "widget"}})
    assert _default_origin_path(widget_default).is_file()
    assert list_engines() == [{"repo": {"owner": "acme", "repo": "widget"}}]


def test_default_origin_marked_at_is_none_without_a_marker() -> None:
    assert default_origin_marked_at("acme/never-marked") is None


def test_default_origin_marked_at_raises_when_the_marker_exists_but_cannot_be_read(
    widget_default: str,
) -> None:
    write_engine(widget_default, {"posture": "observe"})
    marker = _default_origin_path(widget_default)
    marker.unlink()
    marker.mkdir()
    with pytest.raises(OSError, match="directory"):
        default_origin_marked_at(widget_default)


def test_default_origin_marked_at_tolerates_undecodable_bytes(widget_default: str) -> None:
    write_engine(widget_default, {"posture": "observe"})
    _default_origin_path(widget_default).write_bytes(b"\xff\xfe")
    assert default_origin_marked_at(widget_default) is not None
