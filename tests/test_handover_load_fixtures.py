from __future__ import annotations

from pathlib import Path
from typing import Any, get_args

import pytest
import yaml

from alan import handover as handover_mod
from alan.handover import SCHEMA_VERSION, HandoverError, load_handover

_CURRENT_MINOR = int(SCHEMA_VERSION.split(".")[1])
_EXPECTED_VERSIONS = [f"1.{n}" for n in range(_CURRENT_MINOR + 1)]
_TIMESTAMP = "2026-01-01T00:00:00+00:00"


@pytest.fixture(autouse=True)
def _clear_chain_index_cache() -> None:
    handover_mod._CHAIN_INDEX_CACHE.clear()


def _doc(handover_id: str = "handover_t_001", **overrides: Any) -> dict[Any, Any]:
    base: dict[Any, Any] = {
        "handover_id": handover_id,
        "handover_type": "forward",
        "task_id": "t",
        "from_team": "research",
        "from_invocation_id": "inv_001",
        "iteration": 1,
        "timestamp_utc": _TIMESTAMP,
        "full_synthesis": "synthesis text",
    }
    base.update(overrides)
    return base


def _write(path: Path, doc: object) -> Path:
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


def test_version_one_zero_document_loads(tmp_path: Path) -> None:
    path = _write(tmp_path / "h.yaml", _doc(schema_version="1.0"))
    loaded = load_handover(path)
    assert loaded.schema_version == "1.0"
    assert loaded.handover_id == "handover_t_001"
    assert loaded.full_synthesis == "synthesis text"


def test_document_without_version_loads_as_current(tmp_path: Path) -> None:
    path = _write(tmp_path / "h.yaml", _doc())
    loaded = load_handover(path)
    assert loaded.schema_version == SCHEMA_VERSION


def test_pre_chain_id_document_without_revisit_becomes_its_own_chain_root(
    tmp_path: Path,
) -> None:
    path = _write(tmp_path / "h.yaml", _doc(schema_version="1.0"))
    loaded = load_handover(path)
    assert loaded.handover_chain_id == "handover_t_001"


def test_pre_chain_id_document_derives_chain_id_from_sibling_root(tmp_path: Path) -> None:
    _write(tmp_path / "handover_t_001.yaml", _doc("handover_t_001", schema_version="1.0"))
    _write(
        tmp_path / "handover_t_002.yaml",
        _doc("handover_t_002", schema_version="1.0", revisit_of="handover_t_001"),
    )
    child = _write(
        tmp_path / "handover_t_003.yaml",
        _doc("handover_t_003", schema_version="1.0", revisit_of="handover_t_002"),
    )
    loaded = load_handover(child)
    assert loaded.handover_chain_id == "handover_t_001"
    assert loaded.revisit_of == "handover_t_002"


def test_pre_chain_id_document_with_missing_parent_uses_the_dangling_parent_id(
    tmp_path: Path,
) -> None:
    path = _write(
        tmp_path / "handover_t_002.yaml",
        _doc("handover_t_002", schema_version="1.0", revisit_of="handover_t_000"),
    )
    assert load_handover(path).handover_chain_id == "handover_t_000"


def test_explicit_chain_id_is_kept(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "h.yaml",
        _doc(handover_chain_id="handover_t_root", revisit_of="handover_t_000"),
    )
    assert load_handover(path).handover_chain_id == "handover_t_root"


def test_old_summary_field_is_migrated_to_full_synthesis(tmp_path: Path) -> None:
    doc = _doc(schema_version="1.5", summary_for_downstream="legacy summary")
    del doc["full_synthesis"]
    loaded = load_handover(_write(tmp_path / "h.yaml", doc))
    assert loaded.full_synthesis == "legacy summary"


def test_current_summary_field_wins_over_old_summary_field(tmp_path: Path) -> None:
    doc = _doc(summary_for_downstream="legacy summary")
    loaded = load_handover(_write(tmp_path / "h.yaml", doc))
    assert loaded.full_synthesis == "synthesis text"


@pytest.mark.parametrize(
    "root",
    [
        pytest.param(["a", "b"], id="list"),
        pytest.param("plain scalar", id="scalar"),
        pytest.param(42, id="int"),
        pytest.param(None, id="empty_document"),
    ],
)
def test_non_mapping_root_is_rejected(tmp_path: Path, root: object) -> None:
    path = _write(tmp_path / "h.yaml", root)
    with pytest.raises(HandoverError) as excinfo:
        load_handover(path)
    assert str(excinfo.value) == "h.yaml: expected mapping at root"


def test_empty_file_is_rejected_as_non_mapping_root(tmp_path: Path) -> None:
    path = tmp_path / "h.yaml"
    path.write_text("", encoding="utf-8")
    with pytest.raises(HandoverError) as excinfo:
        load_handover(path)
    assert str(excinfo.value) == "h.yaml: expected mapping at root"


@pytest.mark.parametrize(
    "version",
    [
        pytest.param("2.0", id="next_major"),
        pytest.param("abc", id="not_a_version"),
        pytest.param(1.5, id="float"),
        pytest.param(None, id="null"),
        pytest.param(f"1.{_CURRENT_MINOR + 1}", id="one_past_current"),
    ],
)
def test_unsupported_version_is_rejected_with_the_supported_list(
    tmp_path: Path, version: object
) -> None:
    path = _write(tmp_path / "h.yaml", _doc(schema_version=version))
    with pytest.raises(HandoverError) as excinfo:
        load_handover(path)
    assert str(excinfo.value) == (
        f"h.yaml: unsupported schema_version {version!r}; "
        f"supported: {sorted(get_args(handover_mod._SchemaVersion))}"
    )


def test_root_mapping_with_non_string_keys_still_loads(tmp_path: Path) -> None:
    doc = {1: "one", **_doc(schema_version="1.0"), 2.5: ["x"]}
    loaded = load_handover(_write(tmp_path / "h.yaml", doc))
    assert loaded.handover_id == "handover_t_001"
    assert loaded.schema_version == "1.0"
    assert loaded.handover_chain_id == "handover_t_001"


def test_supported_versions_are_one_zero_through_current() -> None:
    assert set(get_args(handover_mod._SchemaVersion)) == set(_EXPECTED_VERSIONS)
    assert handover_mod._SUPPORTED_SCHEMA_VERSIONS == frozenset(_EXPECTED_VERSIONS)


@pytest.mark.parametrize("version", _EXPECTED_VERSIONS)
def test_every_version_through_current_loads(tmp_path: Path, version: str) -> None:
    path = _write(tmp_path / "h.yaml", _doc(schema_version=version))
    assert load_handover(path).schema_version == version
