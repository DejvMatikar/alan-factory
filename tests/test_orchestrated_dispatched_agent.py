from __future__ import annotations

import json
import re
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from alan.teams._orchestrated import DispatchedAgent, InvocationError, parse_dispatch

_VALID_PROMPT = "SCOPE: s\nCOVERAGE: c\nSUCCESS: d\nCONTEXT: x"
_MISSING_MARKERS_PROMPT = "SCOPE: only this"
_ROLE_PATTERN_MESSAGE = "must match [a-z][a-z0-9_]{0,63}"


def _payload(*agents: object) -> str:
    return json.dumps({"agents": list(agents)})


def _agent(role: object = "alpha", prompt: object = _VALID_PROMPT) -> dict[str, object]:
    return {"role": role, "prompt": prompt}


_INVALID_JSON_TEXT = "x" * 300

_MALFORMED_CASES = [
    pytest.param(
        _INVALID_JSON_TEXT,
        f"orchestrator output is not valid JSON: {'x' * 200}",
        id="invalid_json_truncated_to_200_chars",
    ),
    pytest.param(
        "[1, 2]",
        "orchestrator output must be a JSON object",
        id="root_not_object",
    ),
    pytest.param(
        '{"other": 1}',
        "orchestrator output missing non-empty 'agents'",
        id="agents_absent",
    ),
    pytest.param(
        '{"agents": []}',
        "orchestrator output missing non-empty 'agents'",
        id="agents_empty",
    ),
    pytest.param(
        '{"agents": "alpha"}',
        "orchestrator output missing non-empty 'agents'",
        id="agents_not_list",
    ),
    pytest.param(
        _payload(5),
        "agent entry must be object, got <class 'int'>",
        id="entry_not_object_int",
    ),
    pytest.param(
        _payload(["alpha"]),
        "agent entry must be object, got <class 'list'>",
        id="entry_not_object_list",
    ),
    pytest.param(
        _payload({"prompt": _VALID_PROMPT}),
        "agent entry missing string 'role'",
        id="role_absent",
    ),
    pytest.param(
        _payload(_agent(role="   ")),
        "agent entry missing string 'role'",
        id="role_blank",
    ),
    pytest.param(
        _payload(_agent(role=7)),
        "agent entry missing string 'role'",
        id="role_not_string",
    ),
    pytest.param(
        _payload(_agent(role="Bad-Role")),
        f"agent role 'Bad-Role' {_ROLE_PATTERN_MESSAGE}",
        id="role_pattern_mismatch",
    ),
    pytest.param(
        _payload(_agent(role="synthesizer")),
        "agent role 'synthesizer' is reserved",
        id="role_reserved",
    ),
    pytest.param(
        _payload({"role": "alpha"}),
        "agent entry 'alpha' missing string 'prompt'",
        id="prompt_absent",
    ),
    pytest.param(
        _payload(_agent(prompt="  \n ")),
        "agent entry 'alpha' missing string 'prompt'",
        id="prompt_blank",
    ),
    pytest.param(
        _payload(_agent(prompt=["SCOPE"])),
        "agent entry 'alpha' missing string 'prompt'",
        id="prompt_not_string",
    ),
    pytest.param(
        _payload(_agent(prompt=_MISSING_MARKERS_PROMPT)),
        "agent entry 'alpha' missing required field(s): COVERAGE:, SUCCESS:, CONTEXT:",
        id="prompt_missing_markers",
    ),
    pytest.param(
        _payload(_agent("alpha"), _agent("beta"), _agent("alpha")),
        "duplicate agent role: 'alpha'",
        id="duplicate_role",
    ),
]


@pytest.mark.parametrize(("text", "message"), _MALFORMED_CASES)
def test_malformed_dispatch_raises_invocation_error_with_exact_message(
    text: str, message: str
) -> None:
    with pytest.raises(InvocationError) as excinfo:
        parse_dispatch(text)
    assert type(excinfo.value) is InvocationError
    assert re.fullmatch(re.escape(message), str(excinfo.value))


@pytest.mark.parametrize(
    ("text", "message"),
    [
        pytest.param(
            _payload(_agent(prompt=_MISSING_MARKERS_PROMPT), 5),
            "agent entry 'alpha' missing required field(s): COVERAGE:, SUCCESS:, CONTEXT:",
            id="earlier_bad_prompt_wins_over_later_non_object_entry",
        ),
        pytest.param(
            _payload(_agent("alpha"), _agent("alpha"), 5),
            "agent entry must be object, got <class 'int'>",
            id="non_object_entry_wins_over_earlier_duplicate_role",
        ),
        pytest.param(
            _payload(_agent("alpha"), _agent("Bad-Role"), _agent("alpha")),
            f"agent role 'Bad-Role' {_ROLE_PATTERN_MESSAGE}",
            id="bad_role_wins_over_duplicate_role",
        ),
        pytest.param(
            _payload({"role": "Bad-Role", "prompt": None}),
            f"agent role 'Bad-Role' {_ROLE_PATTERN_MESSAGE}",
            id="role_pattern_wins_over_missing_prompt",
        ),
        pytest.param(
            _payload({"role": "verifier"}),
            "agent role 'verifier' is reserved",
            id="reserved_role_wins_over_missing_prompt",
        ),
        pytest.param(
            _payload({"role": "alpha", "prompt": None}, {"prompt": _VALID_PROMPT}),
            "agent entry 'alpha' missing string 'prompt'",
            id="first_entry_prompt_wins_over_second_entry_role",
        ),
    ],
)
def test_first_failing_check_decides_the_error(text: str, message: str) -> None:
    with pytest.raises(InvocationError) as excinfo:
        parse_dispatch(text)
    assert str(excinfo.value) == message


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("[1, 2]", id="root_not_object"),
        pytest.param(_payload(5), id="entry_not_object"),
    ],
)
def test_not_object_errors_carry_no_explicit_cause(text: str) -> None:
    with pytest.raises(InvocationError) as excinfo:
        parse_dispatch(text)
    assert excinfo.value.__cause__ is None


def test_invalid_json_error_chains_the_decode_error() -> None:
    with pytest.raises(InvocationError) as excinfo:
        parse_dispatch("nope")
    assert isinstance(excinfo.value.__cause__, json.JSONDecodeError)


def test_dispatched_agents_compare_by_value_and_hash_equal() -> None:
    first = DispatchedAgent(role="alpha", prompt="p")
    second = DispatchedAgent(role="alpha", prompt="p")
    assert first == second
    assert first != DispatchedAgent(role="beta", prompt="p")
    assert first != DispatchedAgent(role="alpha", prompt="q")
    assert hash(first) == hash(second)
    assert len({first, second}) == 1
    assert first in {second}


def test_dispatched_agent_rejects_field_assignment() -> None:
    agent = DispatchedAgent(role="alpha", prompt="p")
    with pytest.raises(ValidationError):
        agent.role = "beta"
    assert agent.role == "alpha"


@pytest.mark.parametrize(
    ("role", "prompt"),
    [
        pytest.param(123, "p", id="role_not_string"),
        pytest.param("a", None, id="prompt_not_string"),
    ],
)
def test_dispatched_agent_constructor_validates_field_types(role: Any, prompt: Any) -> None:
    with pytest.raises(ValidationError):
        DispatchedAgent(role=role, prompt=prompt)


def test_parse_dispatch_returns_model_instances() -> None:
    result = parse_dispatch(_payload(_agent("alpha"), _agent("beta")))
    assert all(isinstance(agent, BaseModel) for agent in result)
    assert [type(agent) for agent in result] == [DispatchedAgent, DispatchedAgent]


def test_parse_dispatch_returns_trimmed_agents_in_list_order() -> None:
    result = parse_dispatch(
        _payload(_agent("  alpha  ", f"  {_VALID_PROMPT}  "), _agent("beta"))
    )
    assert result == [
        DispatchedAgent(role="alpha", prompt=_VALID_PROMPT),
        DispatchedAgent(role="beta", prompt=_VALID_PROMPT),
    ]
