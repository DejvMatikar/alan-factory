from __future__ import annotations

from alan.teams import _pr_json


def test_str_field_first_truthy_key_wins() -> None:
    item = {"conclusion": "", "state": "failure"}
    assert _pr_json._str_field(item, "conclusion", "state") == "FAILURE"


def test_str_field_returns_empty_when_all_keys_missing() -> None:
    assert _pr_json._str_field({}, "conclusion", "state") == ""


def test_str_field_coerces_non_str_value() -> None:
    assert _pr_json._str_field({"count": 5}, "count") == "5"


def test_safe_json_valid_object() -> None:
    assert _pr_json.safe_json('{"a": 1}') == {"a": 1}


def test_safe_json_valid_list() -> None:
    assert _pr_json.safe_json("[1, 2]") == [1, 2]


def test_safe_json_malformed_returns_none() -> None:
    assert _pr_json.safe_json("{not json") is None


def test_safe_json_empty_string_returns_none() -> None:
    assert _pr_json.safe_json("") is None
    assert _pr_json.safe_json("   ") is None


def test_safe_int_with_int_returns_value() -> None:
    assert _pr_json._safe_int(7) == 7


def test_safe_int_with_numeric_string_returns_zero() -> None:
    assert _pr_json._safe_int("7") == 0


def test_safe_int_with_non_numeric_returns_zero() -> None:
    assert _pr_json._safe_int(None) == 0


def test_safe_list_with_list_returns_copy() -> None:
    original = [1, 2, 3]
    result = _pr_json._safe_list(original)
    assert result == original
    assert result is not original


def test_safe_list_with_non_list_returns_empty() -> None:
    assert _pr_json._safe_list("not-a-list") == []


def test_nav_json_path_valid_path_returns_dict() -> None:
    stdout = '{"a": {"b": {"c": 1}}}'
    assert _pr_json._nav_json_path(stdout, "a", "b") == {"c": 1}


def test_nav_json_path_no_keys_returns_root_when_dict() -> None:
    stdout = '{"a": 1}'
    assert _pr_json._nav_json_path(stdout) == {"a": 1}


def test_nav_json_path_missing_key_returns_none() -> None:
    stdout = '{"a": {}}'
    assert _pr_json._nav_json_path(stdout, "a", "b") is None


def test_nav_json_path_non_dict_intermediate_layer_returns_none() -> None:
    stdout = '{"a": "not-a-dict"}'
    assert _pr_json._nav_json_path(stdout, "a", "b") is None
