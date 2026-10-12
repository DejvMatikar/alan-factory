from __future__ import annotations

import json
from pathlib import Path

from alan.teams import _pr_freshness, _ratchet_direction
from tests.size_baseline import SOURCE_MODULE_CAP


def test_rule_table_keys_match_measured_baseline_paths() -> None:
    assert set(_ratchet_direction._RULES) == _pr_freshness.MEASURED_BASELINE_PATHS  # noqa: SLF001


def test_flaky_baseline_addition_is_loosening() -> None:
    finding = _ratchet_direction._check_flaky_baseline(  # noqa: SLF001
        json.dumps(["a.spec.ts"]), json.dumps([]),
    )
    assert finding == _ratchet_direction.Loosening(
        "alan/web/e2e/flaky-baseline.json",
        "spec(s) added to the flake baseline: a.spec.ts",
    )


def test_flaky_baseline_shrink_is_not_loosening() -> None:
    assert _ratchet_direction._check_flaky_baseline(  # noqa: SLF001
        json.dumps([]), json.dumps(["a.spec.ts"]),
    ) is None


def test_flaky_baseline_unchanged_is_not_loosening() -> None:
    assert _ratchet_direction._check_flaky_baseline(  # noqa: SLF001
        json.dumps(["a.spec.ts"]), json.dumps(["a.spec.ts"]),
    ) is None


def test_flaky_baseline_malformed_json_is_not_loosening() -> None:
    assert _ratchet_direction._check_flaky_baseline("not json", "[]") is None  # noqa: SLF001


def test_flaky_baseline_non_list_is_not_loosening() -> None:
    assert _ratchet_direction._check_flaky_baseline(  # noqa: SLF001
        json.dumps({"a": 1}), json.dumps([]),
    ) is None


def test_stress_floor_lowered_min_executed_tests_is_loosening() -> None:
    finding = _ratchet_direction._check_stress_floor(  # noqa: SLF001
        json.dumps({"minExecutedTests": 800, "minSpecFiles": 75}),
        json.dumps({"minExecutedTests": 840, "minSpecFiles": 75}),
    )
    assert finding is not None
    assert finding.path == "alan/web/e2e/stress-floor.json"
    assert "minExecutedTests lowered from 840 to 800" in finding.detail


def test_stress_floor_lowered_min_spec_files_is_loosening() -> None:
    finding = _ratchet_direction._check_stress_floor(  # noqa: SLF001
        json.dumps({"minExecutedTests": 840, "minSpecFiles": 70}),
        json.dumps({"minExecutedTests": 840, "minSpecFiles": 75}),
    )
    assert finding is not None
    assert "minSpecFiles lowered from 75 to 70" in finding.detail


def test_stress_floor_raise_is_not_loosening() -> None:
    assert _ratchet_direction._check_stress_floor(  # noqa: SLF001
        json.dumps({"minExecutedTests": 900, "minSpecFiles": 80}),
        json.dumps({"minExecutedTests": 840, "minSpecFiles": 75}),
    ) is None


def test_stress_floor_unchanged_is_not_loosening() -> None:
    payload = json.dumps({"minExecutedTests": 840, "minSpecFiles": 75})
    assert _ratchet_direction._check_stress_floor(payload, payload) is None  # noqa: SLF001


def test_jscpd_threshold_raised_is_loosening() -> None:
    finding = _ratchet_direction._check_jscpd(  # noqa: SLF001
        json.dumps({"threshold": 2}), json.dumps({"threshold": 1}),
    )
    assert finding is not None
    assert finding.path == "alan/web/.jscpd.json"
    assert "raised from 1 to 2" in finding.detail


def test_jscpd_threshold_lowered_is_not_loosening() -> None:
    assert _ratchet_direction._check_jscpd(  # noqa: SLF001
        json.dumps({"threshold": 1}), json.dumps({"threshold": 2}),
    ) is None


def test_jscpd_boolean_threshold_is_not_a_number() -> None:
    assert _ratchet_direction._check_jscpd(  # noqa: SLF001
        json.dumps({"threshold": True}), json.dumps({"threshold": False}),
    ) is None


def test_audit_baseline_dimension_raised_is_loosening() -> None:
    check = _ratchet_direction._audit_baseline_rule("some/baseline.json")  # noqa: SLF001
    finding = check(
        json.dumps({"home": {"overflow": 2}}),
        json.dumps({"home": {"overflow": 1}}),
    )
    assert finding == _ratchet_direction.Loosening(
        "some/baseline.json", "home.overflow raised from 1 to 2",
    )


def test_audit_baseline_new_screen_key_is_not_loosening() -> None:
    check = _ratchet_direction._audit_baseline_rule("some/baseline.json")  # noqa: SLF001
    finding = check(
        json.dumps({"home": {"overflow": 0}, "new-screen": {"overflow": 3}}),
        json.dumps({"home": {"overflow": 0}}),
    )
    assert finding is None


def test_audit_baseline_key_rename_resetting_count_is_not_loosening() -> None:
    check = _ratchet_direction._audit_baseline_rule("some/baseline.json")  # noqa: SLF001
    finding = check(
        json.dumps({"screen-b": {"overflow": 0}}),
        json.dumps({"screen-a": {"overflow": 5}}),
    )
    assert finding is None


def test_audit_baseline_dimension_lowered_is_not_loosening() -> None:
    check = _ratchet_direction._audit_baseline_rule("some/baseline.json")  # noqa: SLF001
    assert check(
        json.dumps({"home": {"overflow": 0}}),
        json.dumps({"home": {"overflow": 1}}),
    ) is None


def test_audit_baseline_malformed_json_is_not_loosening() -> None:
    check = _ratchet_direction._audit_baseline_rule("some/baseline.json")  # noqa: SLF001
    assert check("not json", "{}") is None


def test_size_baseline_test_file_cap_raised_is_loosening() -> None:
    finding = _ratchet_direction._check_size_baseline(  # noqa: SLF001
        "TEST_FILE_CAP = 1100\nSOURCE_MODULE_CAP = 800\n",
        "TEST_FILE_CAP = 1000\nSOURCE_MODULE_CAP = 800\n",
    )
    assert finding is not None
    assert finding.path == "tests/size_baseline.py"
    assert "TEST_FILE_CAP raised from 1000 to 1100" in finding.detail


def test_size_baseline_source_module_cap_raised_is_loosening() -> None:
    finding = _ratchet_direction._check_size_baseline(  # noqa: SLF001
        "TEST_FILE_CAP = 1000\nSOURCE_MODULE_CAP = 900\n",
        "TEST_FILE_CAP = 1000\nSOURCE_MODULE_CAP = 800\n",
    )
    assert finding is not None
    assert "SOURCE_MODULE_CAP raised from 800 to 900" in finding.detail


def test_size_baseline_claude_md_ceiling_raised_is_loosening() -> None:
    finding = _ratchet_direction._check_size_baseline(  # noqa: SLF001
        "CLAUDE_MD_BYTE_CEILING = 15000\n",
        "CLAUDE_MD_BYTE_CEILING = 14000\n",
    )
    assert finding is not None
    assert finding.path == "tests/size_baseline.py"
    assert "CLAUDE_MD_BYTE_CEILING raised from 14000 to 15000" in finding.detail


def test_size_baseline_pin_dict_change_alone_is_not_loosening() -> None:
    head = (
        "TEST_FILE_CAP = 1000\nSOURCE_MODULE_CAP = 800\n"
        "PINNED_TEST_FILES: dict[str, int] = {'tests/x.py': 1200}\n"
    )
    base = (
        "TEST_FILE_CAP = 1000\nSOURCE_MODULE_CAP = 800\n"
        "PINNED_TEST_FILES: dict[str, int] = {}\n"
    )
    assert _ratchet_direction._check_size_baseline(head, base) is None  # noqa: SLF001


def test_size_baseline_cap_lowered_is_not_loosening() -> None:
    assert _ratchet_direction._check_size_baseline(  # noqa: SLF001
        "TEST_FILE_CAP = 900\n", "TEST_FILE_CAP = 1000\n",
    ) is None


def test_size_baseline_syntax_error_is_not_loosening() -> None:
    assert _ratchet_direction._check_size_baseline("not (valid python", "x = 1\n") is None  # noqa: SLF001


def test_size_baseline_caps_covers_every_baseline_constant() -> None:
    assert set(_ratchet_direction._SIZE_BASELINE_CAPS) == {  # noqa: SLF001
        "TEST_FILE_CAP",
        "SOURCE_MODULE_CAP",
        "CLAUDE_MD_BYTE_CEILING",
    }


def test_pinned_frontend_bound_is_discovered_by_pinned_le_bounds() -> None:
    source = (
        "def test_pinned_frontend_baseline_does_not_grow() -> None:\n"
        "    assert sum(PINNED_FRONTEND_TEST_FILES.values()) <= 16867\n"
        "    assert PINNED_FRONTEND_TEST_TOTAL <= 16867\n"
        "    assert len(PINNED_FRONTEND_TEST_FILES) <= 8\n"
        "    assert PINNED_FRONTEND_TEST_COUNT <= 8\n"
    )
    bounds = _ratchet_direction._pinned_le_bounds(source)  # noqa: SLF001
    assert (
        "test_pinned_frontend_baseline_does_not_grow",
        "PINNED_FRONTEND_TEST_TOTAL",
    ) in bounds


_PINNED_FUNC = (
    "def test_pinned_test_baseline_does_not_grow() -> None:\n"
    "    assert sum(PINNED_TEST_FILES.values()) <= {total}\n"
    "    assert PINNED_TEST_TOTAL <= {total}\n"
    "    assert len(PINNED_TEST_FILES) <= {count}\n"
    "    assert PINNED_TEST_COUNT <= {count}\n"
)


def test_test_size_ratchets_bound_raised_is_loosening() -> None:
    head = _PINNED_FUNC.format(total=44100, count=26)
    base = _PINNED_FUNC.format(total=44075, count=26)
    finding = _ratchet_direction._check_test_size_ratchets(head, base)  # noqa: SLF001
    assert finding is not None
    assert finding.path == "tests/test_size_ratchets.py"
    assert "raised from 44075 to 44100" in finding.detail


def test_test_size_ratchets_bound_lowered_is_not_loosening() -> None:
    head = _PINNED_FUNC.format(total=44000, count=26)
    base = _PINNED_FUNC.format(total=44075, count=26)
    assert _ratchet_direction._check_test_size_ratchets(head, base) is None  # noqa: SLF001


def test_test_size_ratchets_new_function_only_in_head_is_not_loosening() -> None:
    head = _PINNED_FUNC.format(total=44075, count=26) + (
        "\n\ndef test_pinned_new_thing() -> None:\n"
        "    assert VALUE <= 5\n"
    )
    base = _PINNED_FUNC.format(total=44075, count=26)
    assert _ratchet_direction._check_test_size_ratchets(head, base) is None  # noqa: SLF001


def test_test_size_ratchets_non_pinned_function_ignored() -> None:
    func_name = "test_a_raised_test_pin_would_fail_the_aggregate_bound"
    head = f"def {func_name}() -> None:\n    assert 1 <= 5\n"
    base = f"def {func_name}() -> None:\n    assert 1 <= 1\n"
    assert _ratchet_direction._check_test_size_ratchets(head, base) is None  # noqa: SLF001


def test_test_size_ratchets_gt_comparison_ignored() -> None:
    head = "def test_pinned_x() -> None:\n    assert 900 > 800\n"
    base = "def test_pinned_x() -> None:\n    assert 800 > 800\n"
    assert _ratchet_direction._check_test_size_ratchets(head, base) is None  # noqa: SLF001


def test_test_size_ratchets_syntax_error_is_not_loosening() -> None:
    assert _ratchet_direction._check_test_size_ratchets(  # noqa: SLF001
        "not (valid python", "def test_pinned_x() -> None:\n    assert 1 <= 1\n",
    ) is None


_MOBILE_PIN_HEAD = (
    "export const MOBILE_AUDIT_BASELINE_HIGH_WATER: MobileAuditBaseline = {\n"
    "  home: { overflow: 2, tapTarget: 2, contrast: 0 },\n"
    "};\n"
)
_MOBILE_PIN_BASE = (
    "export const MOBILE_AUDIT_BASELINE_HIGH_WATER: MobileAuditBaseline = {\n"
    "  home: { overflow: 1, tapTarget: 2, contrast: 0 },\n"
    "};\n"
)


def test_pin_dimension_raised_is_loosening() -> None:
    check = _ratchet_direction._pin_rule("some/pin.ts")  # noqa: SLF001
    finding = check(_MOBILE_PIN_HEAD, _MOBILE_PIN_BASE)
    assert finding == _ratchet_direction.Loosening(
        "some/pin.ts", "home.overflow raised from 1 to 2",
    )


def test_pin_dimension_lowered_is_not_loosening() -> None:
    check = _ratchet_direction._pin_rule("some/pin.ts")  # noqa: SLF001
    assert check(_MOBILE_PIN_BASE, _MOBILE_PIN_HEAD) is None


def test_pin_new_screen_key_is_not_loosening() -> None:
    check = _ratchet_direction._pin_rule("some/pin.ts")  # noqa: SLF001
    head = (
        "export const X: T = {\n"
        "  home: { overflow: 0 },\n"
        '  "new-screen": { overflow: 3 },\n'
        "};\n"
    )
    base = "export const X: T = {\n  home: { overflow: 0 },\n};\n"
    assert check(head, base) is None


def test_pin_quoted_key_dimension_raised_is_loosening() -> None:
    check = _ratchet_direction._pin_rule("some/pin.ts")  # noqa: SLF001
    head = 'export const X: T = {\n  "home@standard": { actionGroupFit: 2 },\n};\n'
    base = 'export const X: T = {\n  "home@standard": { actionGroupFit: 1 },\n};\n'
    finding = check(head, base)
    assert finding is not None
    assert "actionGroupFit raised from 1 to 2" in finding.detail


def test_pin_malformed_ts_is_not_loosening() -> None:
    check = _ratchet_direction._pin_rule("some/pin.ts")  # noqa: SLF001
    assert check("not valid ts", "export const X: T = {};\n") is None


def test_pin_non_object_export_is_not_loosening() -> None:
    check = _ratchet_direction._pin_rule("some/pin.ts")  # noqa: SLF001
    assert check("export const X: T = [1, 2];\n", "export const X: T = {};\n") is None


def test_ts_object_literal_invalid_json_body_returns_none() -> None:
    assert _ratchet_direction._ts_object_literal(  # noqa: SLF001
        "export const X: T = { home: someFunc() };\n",
    ) is None


def test_ts_object_literal_parses_the_real_mobile_pin_file() -> None:
    root = Path(__file__).resolve().parents[1]
    content = (root / "alan/web/e2e/mobile-audit/baseline-high-water.ts").read_text(
        encoding="utf-8",
    )
    parsed = _ratchet_direction._ts_object_literal(content)  # noqa: SLF001
    assert parsed is not None
    assert parsed["home"] == {"overflow": 1, "tapTarget": 2, "contrast": 0}


def test_ts_object_literal_parses_the_real_desktop_pin_file() -> None:
    root = Path(__file__).resolve().parents[1]
    content = (root / "alan/web/e2e/desktop-audit/baseline-high-water.ts").read_text(
        encoding="utf-8",
    )
    parsed = _ratchet_direction._ts_object_literal(content)  # noqa: SLF001
    assert parsed is not None
    assert parsed["home@standard"] == {"actionGroupFit": 5, "crossAxisAlignment": 24}


def test_size_baseline_rule_on_the_real_baseline_flags_a_raised_source_cap() -> None:
    root = Path(__file__).resolve().parents[1]
    real = (root / "tests/size_baseline.py").read_text(encoding="utf-8")
    old = f"SOURCE_MODULE_CAP = {SOURCE_MODULE_CAP}\n"
    assert real.count(old) == 1
    raised = real.replace(old, f"SOURCE_MODULE_CAP = {SOURCE_MODULE_CAP + 1}\n")
    assert _ratchet_direction._check_size_baseline(real, real) is None  # noqa: SLF001
    assert _ratchet_direction._check_size_baseline(raised, real) == _ratchet_direction.Loosening(  # noqa: SLF001
        "tests/size_baseline.py",
        f"SOURCE_MODULE_CAP raised from {SOURCE_MODULE_CAP} to {SOURCE_MODULE_CAP + 1}",
    )


def test_render_ratchet_block_unresolved_base() -> None:
    block = _ratchet_direction.render_ratchet_block(None)
    assert block is not None
    assert block.startswith("## Ratchet direction observation (advisory)")
    assert "base version could not be resolved" in block
    assert "no direction is inferred from one side" in block


def test_render_ratchet_block_no_findings_is_none() -> None:
    assert _ratchet_direction.render_ratchet_block(()) is None


def test_as_number_returns_none_for_non_numeric_value() -> None:
    assert _ratchet_direction._as_number("5") is None  # noqa: SLF001
    assert _ratchet_direction._as_number(None) is None  # noqa: SLF001


def test_stress_floor_malformed_json_is_not_loosening() -> None:
    assert _ratchet_direction._check_stress_floor("not json", "{}") is None  # noqa: SLF001


def test_stress_floor_non_dict_is_not_loosening() -> None:
    assert _ratchet_direction._check_stress_floor(  # noqa: SLF001
        json.dumps([1, 2]), json.dumps({}),
    ) is None


def test_jscpd_malformed_json_is_not_loosening() -> None:
    assert _ratchet_direction._check_jscpd("not json", "{}") is None  # noqa: SLF001


def test_jscpd_non_dict_is_not_loosening() -> None:
    assert _ratchet_direction._check_jscpd(  # noqa: SLF001
        json.dumps([1]), json.dumps({}),
    ) is None


def test_audit_baseline_non_dict_top_level_is_not_loosening() -> None:
    check = _ratchet_direction._audit_baseline_rule("some/baseline.json")  # noqa: SLF001
    assert check(json.dumps([1, 2]), json.dumps({})) is None


def test_size_baseline_non_constant_assignment_is_ignored() -> None:
    finding = _ratchet_direction._check_size_baseline(  # noqa: SLF001
        "TEST_FILE_CAP = compute_cap()\n", "TEST_FILE_CAP = 1000\n",
    )
    assert finding is None


def test_test_size_ratchets_chained_comparison_is_ignored() -> None:
    head = "def test_pinned_x() -> None:\n    assert 1 <= VALUE <= 10\n"
    base = "def test_pinned_x() -> None:\n    assert 1 <= VALUE <= 10\n"
    assert _ratchet_direction._check_test_size_ratchets(head, base) is None  # noqa: SLF001


def test_test_size_ratchets_float_bound_is_ignored() -> None:
    head = "def test_pinned_y() -> None:\n    assert VALUE <= 5.5\n"
    base = "def test_pinned_y() -> None:\n    assert VALUE <= 5.5\n"
    assert _ratchet_direction._check_test_size_ratchets(head, base) is None  # noqa: SLF001


def test_render_ratchet_block_with_findings() -> None:
    findings = (
        _ratchet_direction.Loosening(
            "tests/size_baseline.py", "TEST_FILE_CAP raised from 1000 to 1100",
        ),
    )
    block = _ratchet_direction.render_ratchet_block(findings)
    assert block is not None
    assert block.startswith("## Ratchet direction observation (advisory)")
    assert "tests/size_baseline.py" in block
    assert "TEST_FILE_CAP raised from 1000 to 1100" in block
    assert "advisory only" in block
    assert "the host applies no mechanical block" in block
    assert "opened against the flake-quarantine issue" in block
    assert "ratchet being widened inside the pull request it is blocking" in block


def _gh_read_baseline_source(*keys: str) -> str:
    body = "".join(f'    "{key}",\n' for key in keys)
    return (
        "from __future__ import annotations\n\n"
        f"GH_READ_STUBBED_TESTS: frozenset[str] = frozenset({{\n{body}}})\n"
    )


def test_gh_read_baseline_addition_is_loosening() -> None:
    finding = _ratchet_direction._check_gh_read_baseline(  # noqa: SLF001
        _gh_read_baseline_source("tests/test_a.py::test_one", "tests/test_b.py::test_two"),
        _gh_read_baseline_source("tests/test_a.py::test_one"),
    )
    assert finding == _ratchet_direction.Loosening(
        "tests/gh_read_baseline.py",
        "test(s) added to the gh read baseline: tests/test_b.py::test_two",
    )


def test_gh_read_baseline_shrink_is_not_loosening() -> None:
    assert _ratchet_direction._check_gh_read_baseline(  # noqa: SLF001
        _gh_read_baseline_source(),
        _gh_read_baseline_source("tests/test_a.py::test_one"),
    ) is None


def test_gh_read_baseline_unchanged_is_not_loosening() -> None:
    same = _gh_read_baseline_source("tests/test_a.py::test_one")
    assert _ratchet_direction._check_gh_read_baseline(same, same) is None  # noqa: SLF001


def test_gh_read_baseline_syntax_error_is_not_loosening() -> None:
    assert _ratchet_direction._check_gh_read_baseline(  # noqa: SLF001
        "GH_READ_STUBBED_TESTS: frozenset[str] = frozenset({", _gh_read_baseline_source(),
    ) is None


def test_gh_read_baseline_missing_assignment_is_not_loosening() -> None:
    assert _ratchet_direction._check_gh_read_baseline(  # noqa: SLF001
        "OTHER: frozenset[str] = frozenset()\n", _gh_read_baseline_source(),
    ) is None
    assert _ratchet_direction._check_gh_read_baseline(  # noqa: SLF001
        _gh_read_baseline_source(), "x = 1\n",
    ) is None


def test_gh_read_baseline_non_frozenset_shapes_are_not_loosening() -> None:
    for head in (
        "GH_READ_STUBBED_TESTS: frozenset[str] = {'tests/test_a.py::test_one'}\n",
        "GH_READ_STUBBED_TESTS: frozenset[str] = set({'a'})\n",
        "GH_READ_STUBBED_TESTS: frozenset[str] = frozenset(['a'])\n",
        "GH_READ_STUBBED_TESTS: frozenset[str] = frozenset({1})\n",
        "GH_READ_STUBBED_TESTS: frozenset[str] = frozenset({'a'}, x=1)\n",
    ):
        assert _ratchet_direction._check_gh_read_baseline(  # noqa: SLF001
            head, _gh_read_baseline_source(),
        ) is None


_CI_BUDGETS_PATH = ".github/ci-duration-budgets.json"


def _ci_budgets_check(head: object, base: object) -> _ratchet_direction.Loosening | None:
    return _ratchet_direction._RULES[_CI_BUDGETS_PATH](  # noqa: SLF001
        json.dumps(head), json.dumps(base),
    )


def test_ci_duration_budget_raised_job_is_loosening() -> None:
    finding = _ci_budgets_check(
        {"tests.yml": {"Backend tests + coverage": 400, "Detect changed paths": 12}},
        {"tests.yml": {"Backend tests + coverage": 301, "Detect changed paths": 12}},
    )
    assert finding == _ratchet_direction.Loosening(
        _CI_BUDGETS_PATH, "tests.yml.Backend tests + coverage raised from 301 to 400",
    )


def test_ci_duration_budget_lowered_job_is_not_loosening() -> None:
    assert _ci_budgets_check(
        {"tests.yml": {"Backend tests + coverage": 250}},
        {"tests.yml": {"Backend tests + coverage": 301}},
    ) is None


def test_ci_duration_budget_unchanged_is_not_loosening() -> None:
    payload = {"tests.yml": {"Backend tests + coverage": 301}}
    assert _ci_budgets_check(payload, payload) is None


def test_ci_duration_budget_new_job_key_is_not_loosening() -> None:
    assert _ci_budgets_check(
        {"tests.yml": {"Backend tests + coverage": 301, "New job": 900}},
        {"tests.yml": {"Backend tests + coverage": 301}},
    ) is None


def test_ci_duration_budget_new_workflow_key_is_not_loosening() -> None:
    assert _ci_budgets_check(
        {"tests.yml": {"Backend tests + coverage": 301}, "new.yml": {"job": 900}},
        {"tests.yml": {"Backend tests + coverage": 301}},
    ) is None


def test_ci_duration_budget_removed_job_key_is_not_loosening() -> None:
    assert _ci_budgets_check(
        {"tests.yml": {"Backend tests + coverage": 301}},
        {"tests.yml": {"Backend tests + coverage": 301, "Old job": 10}},
    ) is None


def test_ci_duration_budget_malformed_json_is_not_loosening() -> None:
    assert _ratchet_direction._RULES[_CI_BUDGETS_PATH]("not json", "{}") is None  # noqa: SLF001


def test_ci_duration_budget_rule_on_the_real_file_flags_a_raised_job() -> None:
    root = Path(__file__).resolve().parents[1]
    real = json.loads((root / _CI_BUDGETS_PATH).read_text(encoding="utf-8"))
    workflow, jobs = next(iter(real.items()))
    job = next(iter(jobs))
    raised = {**real, workflow: {**jobs, job: jobs[job] + 1}}
    assert _ci_budgets_check(real, real) is None
    assert _ci_budgets_check(raised, real) == _ratchet_direction.Loosening(
        _CI_BUDGETS_PATH, f"{workflow}.{job} raised from {jobs[job]} to {jobs[job] + 1}",
    )


def test_gh_read_baseline_empty_frozenset_call_has_no_members() -> None:
    assert _ratchet_direction._check_gh_read_baseline(  # noqa: SLF001
        "GH_READ_STUBBED_TESTS: frozenset[str] = frozenset()\n",
        _gh_read_baseline_source("tests/test_a.py::test_one"),
    ) is None
