from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from alan.yaml_normalize import (
    FreeTextList,
    _recover_dict,
    _recover_list,
    _scalarize,
    normalize_str_list,
    recover_item,
)


class _M(BaseModel):
    clauses: FreeTextList


def test_recover_item_none_returns_empty() -> None:
    assert recover_item(None) == ""


def test_recover_item_string_returns_unchanged() -> None:
    assert recover_item("hello") == "hello"


def test_recover_item_empty_string_returns_empty() -> None:
    assert recover_item("") == ""


def test_recover_item_single_pair_dict_reconstructs_colon_clause() -> None:
    assert recover_item({"Clause text": "with detail"}) == "Clause text: with detail"


def test_recover_item_multi_pair_dict_joins_with_comma() -> None:
    result = recover_item({"k1": "v1", "k2": "v2"})
    assert result == "k1: v1, k2: v2"


def test_recover_item_nested_list_joins_non_empty() -> None:
    assert recover_item(["a", "b"]) == "a b"


def test_recover_item_nested_list_drops_nones() -> None:
    assert recover_item([None, "x", None]) == "x"


def test_recover_item_int_converts_to_string() -> None:
    assert recover_item(42) == "42"


def test_recover_item_float_converts_to_string() -> None:
    assert recover_item(3.14) == "3.14"


def test_recover_item_bool_converts_to_string() -> None:
    assert recover_item(True) == "True"
    assert recover_item(False) == "False"


def test_recover_dict_empty_returns_empty_string() -> None:
    assert _recover_dict({}) == ""


def test_recover_dict_nested_dict_value() -> None:
    result = _recover_dict({"key": {"nested": "val"}})
    assert result == "key: nested: val"


def test_recover_dict_nested_list_value() -> None:
    result = _recover_dict({"key": ["a", "b"]})
    assert result == "key: a b"


def test_recover_dict_none_value() -> None:
    result = _recover_dict({"k": None})
    assert result == "k: "


def test_recover_list_empty_returns_empty_string() -> None:
    assert _recover_list([]) == ""


def test_recover_list_drops_empty_survivors() -> None:
    assert _recover_list(["a", None, "", "b"]) == "a b"


def test_scalarize_string_passthrough() -> None:
    assert _scalarize("abc") == "abc"


def test_scalarize_none_returns_empty() -> None:
    assert _scalarize(None) == ""


def test_scalarize_dict() -> None:
    assert _scalarize({"k": "v"}) == "k: v"


def test_scalarize_list() -> None:
    assert _scalarize(["x", "y"]) == "x y"


def test_scalarize_int() -> None:
    assert _scalarize(7) == "7"


def test_normalize_str_list_non_list_passthrough() -> None:
    assert normalize_str_list("not-a-list") == "not-a-list"
    assert normalize_str_list(42) == 42
    assert normalize_str_list(None) is None


def test_normalize_str_list_empty_list() -> None:
    assert normalize_str_list([]) == []


def test_normalize_str_list_all_strings_unchanged() -> None:
    assert normalize_str_list(["a", "b"]) == ["a", "b"]


def test_normalize_str_list_drops_none_items() -> None:
    assert normalize_str_list([None, "x", None]) == ["x"]


def test_normalize_str_list_drops_empty_strings() -> None:
    assert normalize_str_list(["", "x", ""]) == ["x"]


def test_normalize_str_list_dict_item_recovered() -> None:
    assert normalize_str_list([{"Clause": "detail"}]) == ["Clause: detail"]


def test_normalize_str_list_int_item_converted() -> None:
    assert normalize_str_list([1, "ok"]) == ["1", "ok"]


def test_free_text_list_clean_strings_unchanged() -> None:
    m = _M(clauses=["one", "two"])
    assert m.clauses == ["one", "two"]


def test_free_text_list_dict_item_recovered_via_model() -> None:
    m = _M(clauses=[{"Clause text": "with detail"}])  # type: ignore[list-item]
    assert m.clauses == ["Clause text: with detail"]


def test_free_text_list_none_items_dropped() -> None:
    m = _M(clauses=[None, "ok", None])  # type: ignore[list-item]
    assert m.clauses == ["ok"]


def test_free_text_list_empty_strings_dropped() -> None:
    m = _M(clauses=["", "ok"])
    assert m.clauses == ["ok"]


def test_free_text_list_non_list_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        _M(clauses="not-a-list")  # type: ignore[arg-type]


def test_free_text_list_empty_list_valid() -> None:
    m = _M(clauses=[])
    assert m.clauses == []
