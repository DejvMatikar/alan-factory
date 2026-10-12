from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan import _ci_test_timings as tt

_PYTEST_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites name="pytest tests"><testsuite name="pytest" tests="5">
<testcase classname="tests.test_alpha" name="test_one" time="1.500"/>
<testcase classname="tests.test_alpha" name="test_two" time="0.500"></testcase>
<testcase classname="tests.test_alpha.TestGroup" name="test_three" time="2.000"/>
<testcase classname="tests.owner.test_beta" name="test_four[a &amp; b]" time="4.250">
<failure message="x">boom</failure></testcase>
<testcase classname="tests.test_gamma" name="test_five" time="oops"/>
</testsuite></testsuites>
"""

_VITEST_XML = """<testsuites><testsuite name="src/a.test.ts">
<testcase classname="src/a.test.ts" name="renders" time="0.25"/>
<testcase classname="alan/web/src/b.test.tsx" name="renders" time="0.75"/>
<testcase name="no classname" time="9"/>
<testcase classname="c.test.ts" name="x" file="src/c.test.ts" time="1"/>
<testcase classname="d.test.ts" name="x"/>
</testsuite></testsuites>"""


@pytest.mark.parametrize(
    ("classname", "expected"),
    [
        ("tests.test_alpha", "tests/test_alpha.py"),
        ("tests.owner.test_beta", "tests/owner/test_beta.py"),
        ("tests.test_alpha.TestGroup", "tests/test_alpha.py"),
        ("tests/test_alpha.py", "tests/test_alpha.py"),
        ("src/a.test.ts", "src/a.test.ts"),
        ("a.test.tsx", "a.test.tsx"),
        ("", ""),
    ],
)
def test_testcase_file_maps_classnames_to_paths(classname: str, expected: str) -> None:
    assert tt.testcase_file(classname) == expected


def test_pytest_junit_durations_are_summed_per_file() -> None:
    assert tt.junit_file_durations(_PYTEST_XML) == {
        "tests/test_alpha.py": 4.0,
        "tests/owner/test_beta.py": 4.25,
        "tests/test_gamma.py": 0.0,
    }


def test_vitest_junit_durations_use_the_classname_or_the_file_attribute() -> None:
    assert tt.junit_file_durations(_VITEST_XML) == {
        "src/a.test.ts": 0.25,
        "alan/web/src/b.test.tsx": 0.75,
        "src/c.test.ts": 1.0,
        "d.test.ts": 0.0,
    }


def test_malformed_junit_yields_no_durations() -> None:
    assert tt.junit_file_durations("this is not xml") == {}


_PLAYWRIGHT = {
    "suites": [
        {
            "file": "specs/a.spec.ts",
            "specs": [
                {
                    "tests": [
                        {"results": [{"duration": 1500}, {"duration": 500}, {"duration": True}]},
                        {"results": [{"duration": "slow"}]},
                    ]
                }
            ],
            "suites": [
                {
                    "specs": [
                        {"file": "specs/b.spec.ts", "tests": [{"results": [{"duration": 250}]}]}
                    ],
                    "suites": [{"specs": [{"tests": [{"results": [{"duration": 1000}]}]}]}],
                },
                "not-a-suite",
            ],
        },
        {"specs": [{"tests": [{"results": [{"duration": 99}]}]}]},
    ]
}


def test_playwright_durations_are_summed_per_file_in_seconds() -> None:
    assert tt.playwright_file_durations(json.dumps(_PLAYWRIGHT)) == {
        "specs/a.spec.ts": 3.0,
        "specs/b.spec.ts": 0.25,
    }


@pytest.mark.parametrize("text", ["not json", "[]", '"x"', '{"suites": "no"}', "{}"])
def test_malformed_or_empty_playwright_reports_yield_no_durations(text: str) -> None:
    assert tt.playwright_file_durations(text) == {}


def test_loaders_read_files_and_treat_a_missing_file_as_empty(tmp_path: Path) -> None:
    junit = tmp_path / "junit.xml"
    junit.write_text(_PYTEST_XML, encoding="utf-8")
    report = tmp_path / "results.json"
    report.write_text(json.dumps(_PLAYWRIGHT), encoding="utf-8")
    assert tt.load_junit(junit)["tests/test_alpha.py"] == 4.0
    assert tt.load_playwright(report)["specs/b.spec.ts"] == 0.25
    assert tt.load_junit(tmp_path / "absent.xml") == {}
    assert tt.load_playwright(tmp_path / "absent.json") == {}


def test_slowest_changed_ranks_only_files_the_pr_touched() -> None:
    durations = {
        "tests/test_alpha.py": 4.0,
        "tests/test_beta.py": 9.0,
        "tests/test_untouched.py": 50.0,
    }
    changed = ["tests/test_alpha.py", "tests/test_beta.py", "alan/foo.py"]
    assert tt.slowest_changed(durations, changed) == [
        ("tests/test_beta.py", 9.0),
        ("tests/test_alpha.py", 4.0),
    ]


def test_slowest_changed_matches_across_the_web_prefix_and_by_suffix() -> None:
    durations = {"specs/a.spec.ts": 3.0, "src/b.test.ts": 2.0, "alan/web/src/c.test.ts": 1.0}
    changed = ["alan/web/e2e/specs/a.spec.ts", "alan/web/src/b.test.ts", "src/c.test.ts"]
    assert tt.slowest_changed(durations, changed) == [
        ("alan/web/e2e/specs/a.spec.ts", 3.0),
        ("alan/web/src/b.test.ts", 2.0),
        ("src/c.test.ts", 1.0),
    ]


def test_slowest_changed_breaks_ties_by_path_and_respects_the_limit() -> None:
    durations = {"b.py": 1.0, "a.py": 1.0, "c.py": 1.0}
    assert tt.slowest_changed(durations, ["a.py", "b.py", "c.py"], limit=2) == [
        ("a.py", 1.0),
        ("b.py", 1.0),
    ]


def test_slowest_changed_sums_durations_reported_under_several_names() -> None:
    durations = {"tests/a.py": 1.0, "a.py": 2.0}
    assert tt.slowest_changed(durations, ["tests/a.py"]) == [("tests/a.py", 3.0)]


def test_slowest_changed_of_nothing_is_empty() -> None:
    assert tt.slowest_changed({}, ["a.py"]) == []
    assert tt.slowest_changed({"a.py": 1.0}, []) == []
