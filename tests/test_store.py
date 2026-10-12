from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import IO

import pytest

from alan import store


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_write_text_atomic_creates_file_with_content(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    store.write_text_atomic(path, "hello")
    assert path.read_text(encoding="utf-8") == "hello"
    assert not (tmp_path / "x.json.tmp").exists()


def test_write_text_atomic_tmp_name_appends_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "x.json"
    seen: list[Path] = []
    original_replace = Path.replace

    def spy(self: Path, target: Path) -> Path:
        seen.append(self)
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", spy)
    store.write_text_atomic(path, "hi")
    assert seen == [tmp_path / "x.json.tmp"]


def test_write_text_atomic_mode_none_never_calls_chmod(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    original_chmod = Path.chmod

    def spy(self: Path, mode: int) -> None:
        calls.append(mode)
        original_chmod(self, mode)

    monkeypatch.setattr(Path, "chmod", spy)
    store.write_text_atomic(tmp_path / "x.json", "hi")
    assert calls == []


def test_write_text_atomic_mode_set_chmods_before_replace(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    store.write_text_atomic(path, "hi", mode=0o600)
    assert _mode(path) == 0o600


def test_write_text_atomic_write_failure_unlinks_tmp_and_reraises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "x.json"

    def boom(*_a: object, **_kw: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", boom)
    with pytest.raises(OSError, match="disk full"):
        store.write_text_atomic(path, "hi")
    assert not (tmp_path / "x.json.tmp").exists()
    assert not path.exists()


def test_write_text_atomic_chmod_failure_unlinks_tmp_and_reraises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "x.json"

    def boom(*_a: object, **_kw: object) -> None:
        raise OSError("chmod refused")

    monkeypatch.setattr(Path, "chmod", boom)
    with pytest.raises(OSError, match="chmod refused"):
        store.write_text_atomic(path, "hi", mode=0o600)
    assert not (tmp_path / "x.json.tmp").exists()
    assert not path.exists()


def test_write_text_atomic_replace_failure_unlinks_tmp_leaves_target_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "x.json"
    path.write_text("original", encoding="utf-8")

    def boom(*_a: object, **_kw: object) -> None:
        raise OSError("replace refused")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError, match="replace refused"):
        store.write_text_atomic(path, "new", mode=0o600)
    assert not (tmp_path / "x.json.tmp").exists()
    assert path.read_text(encoding="utf-8") == "original"


def test_write_text_atomic_overwrites_stale_tmp_from_prior_crash(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    (tmp_path / "x.json.tmp").write_text("stale crash leftover", encoding="utf-8")
    store.write_text_atomic(path, "fresh")
    assert path.read_text(encoding="utf-8") == "fresh"
    assert not (tmp_path / "x.json.tmp").exists()


def test_write_text_atomic_replace_uses_tmp_file_mode_not_prior_target_mode(
    tmp_path: Path,
) -> None:
    path = tmp_path / "x.json"
    path.write_text("original", encoding="utf-8")
    path.chmod(0o644)
    store.write_text_atomic(path, "new", mode=0o600)
    assert _mode(path) == 0o600


def test_write_text_atomic_unicode_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    text = "café 日本語"
    store.write_text_atomic(path, text)
    assert path.read_text(encoding="utf-8") == text


def test_write_text_atomic_empty_string(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    store.write_text_atomic(path, "")
    assert path.read_text(encoding="utf-8") == ""


def test_write_text_atomic_rejects_parent_traversal_component(tmp_path: Path) -> None:
    path = tmp_path / ".." / "x.json"
    with pytest.raises(ValueError, match=r"\.\."):
        store.write_text_atomic(path, "hi")
    assert not (tmp_path / ".." / "x.json.tmp").exists()


def test_write_text_mkstemp_creates_file_with_content(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    store.write_text_mkstemp(path, "hello", prefix=".x.")
    assert path.read_text(encoding="utf-8") == "hello"


def test_write_text_mkstemp_threads_prefix_and_empty_suffix_into_tmp_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "x.json"
    seen: list[str] = []
    monkeypatch.setattr(os, "replace", lambda src, _dst: seen.append(str(src)))
    store.write_text_mkstemp(path, "hi", prefix=".marker.", suffix="")
    name = Path(seen[0]).name
    assert name.startswith(".marker.")
    assert not name.endswith(".tmp")


def test_write_text_mkstemp_mode_none_never_calls_fchmod(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(os, "fchmod", lambda _fd, mode: calls.append(mode))
    store.write_text_mkstemp(tmp_path / "x.json", "hi", prefix=".x.")
    assert calls == []


def test_write_text_mkstemp_mode_set_chmods_before_replace(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    store.write_text_mkstemp(path, "hi", prefix=".x.", mode=0o600)
    assert _mode(path) == 0o600


def test_write_text_mkstemp_fchmod_happens_before_first_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "x.json"
    order: list[str] = []
    original_fchmod = os.fchmod
    original_fdopen = os.fdopen

    def spy_fchmod(fd: int, mode: int) -> None:
        order.append("fchmod")
        original_fchmod(fd, mode)

    def spy_fdopen(fd: int, mode: str, *, encoding: str | None = None) -> IO[str]:
        handle: IO[str] = original_fdopen(fd, mode, encoding=encoding)
        original_write = handle.write

        def spy_write(data: str) -> int:
            order.append("write")
            return original_write(data)

        handle.write = spy_write  # type: ignore[method-assign]
        return handle

    monkeypatch.setattr(os, "fchmod", spy_fchmod)
    monkeypatch.setattr(os, "fdopen", spy_fdopen)
    store.write_text_mkstemp(path, "hi", prefix=".x.", mode=0o600)
    assert order == ["fchmod", "write"]


def test_write_text_mkstemp_fchmod_failure_closes_descriptor_and_reraises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "x.json"
    closed: list[int] = []
    original_close = os.close

    def spy_close(fd: int) -> None:
        closed.append(fd)
        original_close(fd)

    def boom(_fd: int, _mode: int) -> None:
        raise OSError("fchmod refused")

    monkeypatch.setattr(os, "fchmod", boom)
    monkeypatch.setattr(os, "close", spy_close)
    with pytest.raises(OSError, match="fchmod refused"):
        store.write_text_mkstemp(path, "hi", prefix=".x.", mode=0o600)
    assert closed
    assert not list(tmp_path.glob(".x.*"))
    assert not path.exists()


def test_write_text_mkstemp_overwrites_existing_target(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    path.write_text("old", encoding="utf-8")
    store.write_text_mkstemp(path, "new", prefix=".x.")
    assert path.read_text(encoding="utf-8") == "new"


def test_write_text_mkstemp_write_failure_unlinks_tmp_and_reraises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "x.json"

    def boom(*_a: object, **_kw: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "fdopen", boom)
    with pytest.raises(OSError, match="disk full"):
        store.write_text_mkstemp(path, "hi", prefix=".x.")
    assert not list(tmp_path.glob(".x.*"))
    assert not path.exists()


def test_write_text_mkstemp_replace_failure_unlinks_tmp_leaves_target_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "x.json"
    path.write_text("original", encoding="utf-8")

    def boom(*_a: object, **_kw: object) -> None:
        raise OSError("replace refused")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="replace refused"):
        store.write_text_mkstemp(path, "new", prefix=".x.")
    assert not list(tmp_path.glob(".x.*"))
    assert path.read_text(encoding="utf-8") == "original"


def test_write_text_mkstemp_unicode_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    text = "café 日本語"
    store.write_text_mkstemp(path, text, prefix=".x.")
    assert path.read_text(encoding="utf-8") == text


def test_write_text_mkstemp_rejects_parent_traversal_component(tmp_path: Path) -> None:
    path = tmp_path / ".." / "x.json"
    with pytest.raises(ValueError, match=r"\.\."):
        store.write_text_mkstemp(path, "hi", prefix=".x.")


def test_stat_cache_hit_returns_none_when_key_absent(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("x", encoding="utf-8")
    st = path.stat()
    cache: dict[Path, tuple[int, int, str]] = {}
    assert store.stat_cache_hit(cache, path, st) is None


def test_stat_cache_hit_returns_none_when_mtime_differs(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("x", encoding="utf-8")
    st = path.stat()
    cache: dict[Path, tuple[int, int, str]] = {path: (st.st_mtime_ns - 1, st.st_size, "stale")}
    assert store.stat_cache_hit(cache, path, st) is None


def test_stat_cache_hit_returns_none_when_size_differs(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("x", encoding="utf-8")
    st = path.stat()
    cache: dict[Path, tuple[int, int, str]] = {path: (st.st_mtime_ns, st.st_size + 1, "stale")}
    assert store.stat_cache_hit(cache, path, st) is None


def test_stat_cache_hit_returns_boxed_value_on_signature_match(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("x", encoding="utf-8")
    st = path.stat()
    cache: dict[Path, tuple[int, int, str]] = {path: (st.st_mtime_ns, st.st_size, "fresh")}
    assert store.stat_cache_hit(cache, path, st) == ("fresh",)


def test_stat_cache_hit_boxes_a_cached_none_value(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("x", encoding="utf-8")
    st = path.stat()
    cache: dict[Path, tuple[int, int, str | None]] = {path: (st.st_mtime_ns, st.st_size, None)}
    assert store.stat_cache_hit(cache, path, st) == (None,)


def test_stat_cache_hit_accepts_tuple_key(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("x", encoding="utf-8")
    st = path.stat()
    key = (path, "extra")
    cache: dict[tuple[Path, str], tuple[int, int, str]] = {key: (st.st_mtime_ns, st.st_size, "v")}
    assert store.stat_cache_hit(cache, key, st) == ("v",)


def test_stat_cache_put_writes_mtime_size_value_triple(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("x", encoding="utf-8")
    st = path.stat()
    cache: dict[Path, tuple[int, int, str]] = {}
    store.stat_cache_put(cache, path, st, "value")
    assert cache[path] == (st.st_mtime_ns, st.st_size, "value")


def test_stat_cache_put_overwrites_existing_entry(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("x", encoding="utf-8")
    st = path.stat()
    cache: dict[Path, tuple[int, int, str]] = {path: (0, 0, "old")}
    store.stat_cache_put(cache, path, st, "new")
    assert cache[path] == (st.st_mtime_ns, st.st_size, "new")


def test_parse_jsonl_row_blank_line_returns_none() -> None:
    assert store.parse_jsonl_row("") is None
    assert store.parse_jsonl_row("  ") is None


def test_parse_jsonl_row_invalid_json_returns_none() -> None:
    assert store.parse_jsonl_row("{not json}") is None


def test_parse_jsonl_row_scalar_returns_none() -> None:
    assert store.parse_jsonl_row("42") is None


def test_parse_jsonl_row_list_returns_none() -> None:
    assert store.parse_jsonl_row("[1, 2, 3]") is None


def test_parse_jsonl_row_null_returns_none() -> None:
    assert store.parse_jsonl_row("null") is None


def test_parse_jsonl_row_valid_dict_returned_whole_unfiltered() -> None:
    result = store.parse_jsonl_row(
        '{"action": "merge_ok", "pr_number": 7, "extra_key": "kept"}'
    )
    assert result == {"action": "merge_ok", "pr_number": 7, "extra_key": "kept"}


def test_parse_jsonl_rows_empty_text_returns_empty_list() -> None:
    assert store.parse_jsonl_rows("") == []


def test_parse_jsonl_rows_mixed_valid_blank_malformed_preserves_order() -> None:
    text = "\n".join(['{"n": 1}', "", "  ", "{not json}", "[1, 2]", '{"n": 2}'])
    assert store.parse_jsonl_rows(text) == [{"n": 1}, {"n": 2}]


def test_parse_jsonl_rows_trailing_newline_adds_no_phantom_row() -> None:
    assert store.parse_jsonl_rows('{"n": 1}\n') == [{"n": 1}]


def test_parse_jsonl_rows_returns_a_list_instance() -> None:
    assert isinstance(store.parse_jsonl_rows('{"n": 1}'), list)


def test_positive_int_env_none_uses_default() -> None:
    assert store.positive_int_env(None, 7) == 7


def test_positive_int_env_blank_uses_default() -> None:
    assert store.positive_int_env("   ", 7) == 7


def test_positive_int_env_unparsable_uses_default() -> None:
    assert store.positive_int_env("not-a-number", 7) == 7


def test_positive_int_env_non_positive_uses_default() -> None:
    assert store.positive_int_env("0", 7) == 7
    assert store.positive_int_env("-3", 7) == 7


def test_positive_int_env_valid_returns_parsed_value() -> None:
    assert store.positive_int_env("42", 7) == 42


def test_positive_float_env_none_uses_default() -> None:
    assert store.positive_float_env(None, 1.5) == 1.5


def test_positive_float_env_blank_uses_default() -> None:
    assert store.positive_float_env("", 1.5) == 1.5


def test_positive_float_env_unparsable_uses_default() -> None:
    assert store.positive_float_env("nope", 1.5) == 1.5


def test_positive_float_env_non_positive_uses_default() -> None:
    assert store.positive_float_env("0.0", 1.5) == 1.5
    assert store.positive_float_env("-2.5", 1.5) == 1.5


def test_positive_float_env_valid_returns_parsed_value() -> None:
    assert store.positive_float_env("2.5", 1.5) == 2.5


def test_read_tail_text_under_budget_returns_whole_file(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("hello", encoding="utf-8")
    assert store.read_tail_text(path, 1024) == "hello"


def test_read_tail_text_over_budget_returns_only_the_tail(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("0123456789", encoding="utf-8")
    assert store.read_tail_text(path, 4) == "6789"


def test_read_tail_text_invalid_utf8_is_replaced(tmp_path: Path) -> None:
    path = tmp_path / "f.bin"
    path.write_bytes(b"\xff\xfe\x00abc")
    result = store.read_tail_text(path, 1024)
    assert "abc" in result
    assert "�" in result


def test_read_tail_text_missing_path_raises_oserror(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        store.read_tail_text(tmp_path / "nope.txt", 1024)


def test_stat_signature_existing_file_returns_mtime_and_size(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("hello", encoding="utf-8")
    st = path.stat()
    assert store.stat_signature(path) == (st.st_mtime_ns, st.st_size)


def test_stat_signature_missing_path_returns_none(tmp_path: Path) -> None:
    assert store.stat_signature(tmp_path / "nope.txt") is None


def test_list_dir_returns_sorted_paths(tmp_path: Path) -> None:
    for name in ("b", "c", "a"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    assert store.list_dir(tmp_path) == [tmp_path / "a", tmp_path / "b", tmp_path / "c"]


def test_list_dir_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert store.list_dir(tmp_path / "nope") == []


def test_list_dir_regular_file_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("x", encoding="utf-8")
    assert store.list_dir(path) == []


def test_list_dir_returns_list_not_iterator(tmp_path: Path) -> None:
    (tmp_path / "a").write_text("a", encoding="utf-8")
    listing = store.list_dir(tmp_path)
    assert type(listing) is list


def test_listing_entry_returns_name_inode_size(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("hello", encoding="utf-8")
    st = path.stat()
    assert store.listing_entry(path) == ("f.txt", st.st_ino, 5)


def test_listing_entry_missing_path_returns_none(tmp_path: Path) -> None:
    assert store.listing_entry(tmp_path / "nope.txt") is None


def test_listing_entry_dirs_only_rejects_regular_file(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("hello", encoding="utf-8")
    assert store.listing_entry(path, dirs_only=True) is None


def test_listing_entry_dirs_only_accepts_directory(tmp_path: Path) -> None:
    directory = tmp_path / "d"
    directory.mkdir()
    entry = store.listing_entry(directory, dirs_only=True)
    assert entry is not None
    assert entry[:2] == ("d", directory.stat().st_ino)


def test_listing_fingerprint_drops_vanished_entries(tmp_path: Path) -> None:
    kept = tmp_path / "kept.txt"
    kept.write_text("x", encoding="utf-8")
    fingerprint = store.listing_fingerprint([kept, tmp_path / "gone.txt"])
    assert [name for name, _, _ in fingerprint] == ["kept.txt"]


def test_listing_fingerprint_changes_when_entry_replaced_with_different_inode(
    tmp_path: Path,
) -> None:
    path = tmp_path / "f.txt"
    path.write_text("same", encoding="utf-8")
    before = store.listing_fingerprint([path])
    staged = tmp_path / "f.txt.new"
    staged.write_text("same", encoding="utf-8")
    staged.replace(path)
    assert store.listing_fingerprint([path]) != before


def test_listing_fingerprint_changes_when_file_size_changes(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("short", encoding="utf-8")
    before = store.listing_fingerprint([path])
    path.write_text("a longer body", encoding="utf-8")
    assert store.listing_fingerprint([path]) != before


def test_listing_fingerprint_ignores_timestamps(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("body", encoding="utf-8")
    before = store.listing_fingerprint([path])
    os.utime(path, ns=(1, 1))
    assert store.listing_fingerprint([path]) == before
