from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from typing import Any, get_args

import pytest

from alan import operator_prompt, operator_session
from alan.operator_prompt import Scope
from alan.routes import operator_terminal

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PROTOCOL_TS = _REPO_ROOT / "alan/web/src/features/operator/operatorProtocol.ts"

_NUMBER_CONST = re.compile(r"^export const (?P<name>[A-Z_]+) = (?P<value>\d+);$", re.MULTILINE)
_STRING_CONST = re.compile(r'^export const (?P<name>[A-Z_]+) = "(?P<value>[^"]*)";$', re.MULTILINE)
_TUPLE_CONST = re.compile(
    r"^export const (?P<name>[A-Z_]+) = \[(?P<body>[^\]]*)\] as const;$", re.MULTILINE
)
_REGEX_CONST = re.compile(
    r"^export const (?P<name>[A-Z_]+_PATTERN) = /\^(?P<pattern>.+)\$/;$", re.MULTILINE
)
_QUOTED = re.compile(r'"([^"]+)"')

_CLOSE_CODES_THE_FRONTEND_LEAVES_TO_ITS_DEFAULT = frozenset({"CLOSE_ATTACH_FAILED"})

_PROTOCOL_FIX = (
    "keep alan/web/src/features/operator/operatorProtocol.ts and "
    "alan/routes/operator_terminal.py agreeing on the wire contract"
)


@cache
def _source() -> str:
    return _PROTOCOL_TS.read_text(encoding="utf-8")


def _numbers() -> dict[str, int]:
    return {m["name"]: int(m["value"]) for m in _NUMBER_CONST.finditer(_source())}


def _strings() -> dict[str, str]:
    return {m["name"]: m["value"] for m in _STRING_CONST.finditer(_source())}


def _tuples() -> dict[str, list[str]]:
    return {m["name"]: _QUOTED.findall(m["body"]) for m in _TUPLE_CONST.finditer(_source())}


def _patterns() -> dict[str, str]:
    return {m["name"]: m["pattern"].replace("\\/", "/") for m in _REGEX_CONST.finditer(_source())}


def _backend_close_codes() -> dict[str, int]:
    return {
        name: value
        for name, value in vars(operator_terminal).items()
        if name.startswith("CLOSE_") and isinstance(value, int)
    }


def _resize_model() -> Any:
    return vars(operator_terminal)["_ResizeMessage"]


def test_the_protocol_module_declares_every_constant_the_parity_checks_read() -> None:
    assert {"RESIZE_MESSAGE_TYPE"} <= set(_strings()), _PROTOCOL_FIX
    assert {"OPERATOR_SCOPES", "UNAVAILABLE_REASONS"} <= set(_tuples()), _PROTOCOL_FIX
    assert {"WORKSPACE_TARGET_PATTERN"} <= set(_patterns()), _PROTOCOL_FIX


@pytest.mark.parametrize(
    ("name", "backend_value"),
    [
        ("CLOSE_SUPERSEDED", operator_terminal.CLOSE_SUPERSEDED),
        ("CLOSE_ENDED", operator_terminal.CLOSE_ENDED),
        ("CLOSE_BAD_TICKET", operator_terminal.CLOSE_BAD_TICKET),
        ("CLOSE_NO_SESSION", operator_terminal.CLOSE_NO_SESSION),
    ],
)
def test_close_code_equals_the_backend_constant(name: str, backend_value: int) -> None:
    assert _numbers().get(name) == backend_value, _PROTOCOL_FIX


def test_every_backend_close_code_is_named_by_the_frontend_or_left_to_its_default() -> None:
    frontend = {name for name in _numbers() if name.startswith("CLOSE_")}
    backend = set(_backend_close_codes())
    assert frontend | _CLOSE_CODES_THE_FRONTEND_LEAVES_TO_ITS_DEFAULT == backend, _PROTOCOL_FIX
    assert not frontend & _CLOSE_CODES_THE_FRONTEND_LEAVES_TO_ITS_DEFAULT


def test_the_frontend_names_no_close_code_the_backend_does_not_send() -> None:
    assert {
        name for name in _numbers() if name.startswith("CLOSE_")
    } <= set(_backend_close_codes()), _PROTOCOL_FIX


def test_close_codes_stay_in_the_application_range_and_are_distinct() -> None:
    codes = [value for name, value in _numbers().items() if name.startswith("CLOSE_")]
    assert all(4000 <= code <= 4999 for code in codes)
    assert len(set(codes)) == len(codes)


def test_the_resize_message_type_is_the_only_type_the_backend_accepts() -> None:
    annotation = _resize_model().model_fields["type"].annotation
    assert get_args(annotation) == (_strings()["RESIZE_MESSAGE_TYPE"],), _PROTOCOL_FIX


def test_the_resize_message_carries_exactly_the_fields_the_backend_reads() -> None:
    assert set(_resize_model().model_fields) == {"type", "cols", "rows"}, _PROTOCOL_FIX
    interface = re.search(r"export interface ResizeMessage \{(?P<body>[^}]*)\}", _source())
    assert interface is not None, _PROTOCOL_FIX
    fields = set(re.findall(r"readonly (\w+):", interface["body"]))
    assert fields == {"type", "cols", "rows"}, _PROTOCOL_FIX


def test_the_scopes_equal_the_backend_scopes_in_order() -> None:
    assert _tuples()["OPERATOR_SCOPES"] == [scope.value for scope in Scope], _PROTOCOL_FIX


def test_the_unavailable_reasons_equal_the_backend_reasons() -> None:
    backend = vars(operator_session)["_UNAVAILABLE_DETAILS"]
    assert set(_tuples()["UNAVAILABLE_REASONS"]) == set(backend), _PROTOCOL_FIX


def test_the_workspace_target_pattern_equals_the_backend_pattern() -> None:
    backend = vars(operator_prompt)["_TARGET_PATTERNS"][Scope.WORKSPACE].pattern
    assert _patterns()["WORKSPACE_TARGET_PATTERN"] == backend, _PROTOCOL_FIX
