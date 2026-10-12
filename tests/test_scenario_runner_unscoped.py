from __future__ import annotations

from alan.engine_presets import ios_preset
from alan.teams import scenario_authoring
from alan.teams._scenario_block import _npm_unscoped, _pytest_unscoped, unscoped_suite_when


def test_unscoped_suite_when_flags_bare_pytest() -> None:
    assert unscoped_suite_when("uv run pytest") is True


def test_unscoped_suite_when_flags_bare_pytest_with_no_target() -> None:
    assert unscoped_suite_when("uv run --no-active pytest") is True


def test_unscoped_suite_when_flags_pytest_with_only_flags() -> None:
    assert unscoped_suite_when("uv run --no-active pytest -v") is True


def test_unscoped_suite_when_accepts_pytest_with_a_filter_expression() -> None:
    assert unscoped_suite_when("uv run --no-active pytest -k foo") is False


def test_unscoped_suite_when_accepts_scoped_pytest_file() -> None:
    assert unscoped_suite_when("uv run --no-active pytest tests/test_x.py") is False


def test_unscoped_suite_when_accepts_scoped_pytest_node() -> None:
    assert (
        unscoped_suite_when("uv run --no-active pytest tests/test_x.py::test_y") is False
    )


def test_unscoped_suite_when_flags_bare_npm_test() -> None:
    assert unscoped_suite_when("npm --prefix alan/web run test") is True


def test_unscoped_suite_when_flags_bare_npm_test_coverage() -> None:
    assert unscoped_suite_when("npm --prefix alan/web run test:coverage") is True


def test_unscoped_suite_when_flags_bare_npm_e2e() -> None:
    assert unscoped_suite_when("npm --prefix alan/web run e2e") is True


def test_unscoped_suite_when_flags_bare_npm_run_test() -> None:
    assert unscoped_suite_when("npm run test") is True


def test_unscoped_suite_when_accepts_npm_e2e_with_a_spec_arg() -> None:
    assert (
        unscoped_suite_when("npm --prefix alan/web run e2e -- e2e/foo.spec.ts") is False
    )


def test_unscoped_suite_when_accepts_npm_scripts_outside_the_whole_suite_set() -> None:
    assert unscoped_suite_when("npm --prefix alan/web run lint") is False
    assert unscoped_suite_when("npm --prefix alan/web run typecheck") is False


def test_unscoped_suite_when_fails_open_on_undocumented_prefixes() -> None:
    assert unscoped_suite_when("dotnet test MyProj.Tests") is False
    assert unscoped_suite_when("The user clicks the submit button") is False


def test_unscoped_suite_when_fails_open_on_unbalanced_quotes() -> None:
    assert unscoped_suite_when("uv run --no-active pytest 'unclosed") is False


def test_unscoped_suite_when_fails_open_on_empty_string() -> None:
    assert unscoped_suite_when("") is False


def test_unscoped_suite_when_flags_pytest_with_a_value_taking_option() -> None:
    assert unscoped_suite_when("uv run pytest --maxfail 1") is True


def test_unscoped_suite_when_flags_pytest_with_a_short_value_taking_option() -> None:
    assert unscoped_suite_when("uv run pytest -n 2") is True


def test_unscoped_suite_when_accepts_pytest_with_a_filter_option_and_noise() -> None:
    assert unscoped_suite_when("uv run pytest --maxfail 1 -k foo") is False


def test_unscoped_suite_when_accepts_scoped_pytest_node_with_option_noise() -> None:
    assert (
        unscoped_suite_when("uv run pytest --maxfail 1 tests/test_x.py::test_y") is False
    )


def test_unscoped_suite_when_flags_npm_test_with_a_bare_flag() -> None:
    assert unscoped_suite_when("npm --prefix alan/web run test --silent") is True


def test_pytest_unscoped_false_without_pytest_token() -> None:
    assert _pytest_unscoped(["uv", "run", "--no-active"]) is False


def test_npm_unscoped_false_without_run_token() -> None:
    assert _npm_unscoped(["npm", "--prefix", "alan/web"]) is False


def test_npm_unscoped_false_when_run_is_last_token() -> None:
    assert _npm_unscoped(["npm", "run"]) is False


def test_whole_gate_when_flags_bare_default_pytest_gate() -> None:
    assert scenario_authoring.whole_gate_when("uv run --no-active pytest", None) is True


def test_whole_gate_when_flags_bare_default_npm_gate() -> None:
    when = "npm --prefix alan/web run typecheck"
    assert scenario_authoring.whole_gate_when(when, None) is True


def test_whole_gate_when_accepts_scoped_default_pytest_command() -> None:
    when = "uv run --no-active pytest tests/test_x.py"
    assert scenario_authoring.whole_gate_when(when, None) is False


def test_whole_gate_when_flags_bare_ios_gate_script() -> None:
    cfg = ios_preset()
    assert scenario_authoring.whole_gate_when("bash scripts/gate.sh", cfg) is True


def test_whole_gate_when_accepts_ios_xcodebuild_command() -> None:
    cfg = ios_preset()
    when = "xcodebuild test -scheme Foo"
    assert scenario_authoring.whole_gate_when(when, cfg) is False


def test_whole_gate_when_does_not_reject_a_prefix_merely_outside_the_allowlist() -> None:
    assert scenario_authoring.whole_gate_when("curl http://example.com", None) is False


def test_whole_gate_when_false_for_empty_when() -> None:
    assert scenario_authoring.whole_gate_when("", None) is False


def test_whole_gate_when_false_for_unbalanced_quotes() -> None:
    when = 'uv run --no-active pytest "unclosed'
    assert scenario_authoring.whole_gate_when(when, None) is False
