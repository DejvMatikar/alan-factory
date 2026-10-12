from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from alan import handover as handover_mod

_INVALID_YAML = "{{invalid: :"


@pytest.fixture(autouse=True)
def _clear_chain_index_cache() -> None:
    handover_mod._CHAIN_INDEX_CACHE.clear()


def _write_yaml(path: Path, doc: object) -> Path:
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


_SKIPPED_FILE_CONTENTS: dict[str, str] = {
    "invalid_yaml": _INVALID_YAML,
    "scalar_root": yaml.safe_dump("plain scalar"),
    "list_root": yaml.safe_dump(["a", "b"]),
    "empty_file": "",
    "handover_id_missing": yaml.safe_dump({"revisit_of": "p"}),
    "handover_id_empty": yaml.safe_dump({"handover_id": ""}),
    "handover_id_list": yaml.safe_dump({"handover_id": ["h"]}),
    "handover_id_int": yaml.safe_dump({"handover_id": 123}),
    "handover_id_null": yaml.safe_dump({"handover_id": None}),
}


@pytest.mark.parametrize(
    "content",
    [pytest.param(content, id=name) for name, content in _SKIPPED_FILE_CONTENTS.items()],
)
def test_load_chain_entry_skips_file(tmp_path: Path, content: str) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(content, encoding="utf-8")
    assert handover_mod._load_chain_entry(path) is None


def test_load_chain_entry_skips_unreadable_entry(tmp_path: Path) -> None:
    directory_named_like_a_file = tmp_path / "x.yaml"
    directory_named_like_a_file.mkdir()
    assert handover_mod._load_chain_entry(directory_named_like_a_file) is None


def test_scan_keeps_only_the_files_that_carry_a_handover_id(tmp_path: Path) -> None:
    (tmp_path / "x.yaml").mkdir()
    for name, content in _SKIPPED_FILE_CONTENTS.items():
        (tmp_path / f"bad_{name}.yaml").write_text(content, encoding="utf-8")
    _write_yaml(tmp_path / "good.yaml", {"handover_id": "good", "revisit_of": "parent"})
    assert handover_mod._build_chain_index(tmp_path) == {"good": "parent"}


@pytest.mark.parametrize(
    ("revisit_of", "expected"),
    [
        pytest.param("parent", "parent", id="string_passes_through"),
        pytest.param(5, 5, id="int_passes_through_untyped"),
        pytest.param(["a"], ["a"], id="list_passes_through_untyped"),
        pytest.param("", None, id="empty_string_becomes_none"),
        pytest.param(0, None, id="zero_becomes_none"),
        pytest.param(None, None, id="null_becomes_none"),
    ],
)
def test_load_chain_entry_passes_revisit_of_through_untyped(
    tmp_path: Path, revisit_of: object, expected: object
) -> None:
    path = _write_yaml(tmp_path / "h.yaml", {"handover_id": "h", "revisit_of": revisit_of})
    assert handover_mod._load_chain_entry(path) == ("h", expected)


def test_load_chain_entry_treats_absent_revisit_of_as_none(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path / "h.yaml", {"handover_id": "h"})
    assert handover_mod._load_chain_entry(path) == ("h", None)


def test_load_chain_entry_accepts_root_with_non_string_keys(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path / "h.yaml", {1: "one", "handover_id": "h", 2.5: "x", "revisit_of": "p"}
    )
    assert handover_mod._load_chain_entry(path) == ("h", "p")


def test_load_chain_entry_does_not_validate_against_the_handover_model(
    tmp_path: Path,
) -> None:
    path = _write_yaml(tmp_path / "h.yaml", {"handover_id": "h", "iteration": "not-an-int"})
    assert handover_mod._load_chain_entry(path) == ("h", None)
