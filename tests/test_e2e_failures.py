from __future__ import annotations

from alan.e2e_failures import FailingE2ETest, parse_failing_e2e_annotations

_SPEC = "alan/web/e2e/home-refetch-stability.spec.ts"
_TITLE = (
    "[chromium] › e2e/home-refetch-stability.spec.ts:52:3 › "
    "engines-bucket refetch stability › no loading collapse and no layout jump"
)
_MESSAGE = "  1) [chromium] › … Error: expect(received).toBeLessThan(expected)"
_RATCHET_TITLE = "Flake ratchet: new flaky spec file"


def _failure(
    path: str = _SPEC, title: str = _TITLE, message: str = _MESSAGE,
) -> dict[str, object]:
    return {
        "annotation_level": "failure",
        "path": path,
        "start_line": 93,
        "title": title,
        "message": message,
    }


def test_parse_failing_e2e_annotations_extracts_spec_title_and_message_from_failure_row() -> None:
    assert parse_failing_e2e_annotations([_failure()]) == (
        FailingE2ETest(spec=_SPEC, title=_TITLE, message=_MESSAGE),
    )


def test_parse_failing_e2e_annotations_skips_notice_level_run_summary() -> None:
    notice = {
        "annotation_level": "notice",
        "path": ".github",
        "title": "🎭 Playwright Run Summary",
        "message": f"  1 flaky\n    {_TITLE}",
    }
    assert parse_failing_e2e_annotations([notice]) == ()


def test_parse_failing_e2e_annotations_skips_github_path_step_failure_row() -> None:
    step = {
        "annotation_level": "failure",
        "path": ".github",
        "title": "",
        "message": "Process completed with exit code 1.",
    }
    assert parse_failing_e2e_annotations([step]) == ()


def test_parse_failing_e2e_annotations_skips_failure_rows_outside_spec_files() -> None:
    pytest_row = _failure(path="tests/test_server.py", title="test_boot")
    assert parse_failing_e2e_annotations([pytest_row]) == ()


def test_parse_failing_e2e_annotations_skips_failure_row_without_title() -> None:
    assert parse_failing_e2e_annotations([_failure(title="")]) == ()


def test_parse_failing_e2e_annotations_skips_failure_row_with_non_string_path() -> None:
    row = _failure()
    row["path"] = 42
    assert parse_failing_e2e_annotations([row]) == ()


def test_parse_failing_e2e_annotations_skips_failure_row_with_non_string_title() -> None:
    row = _failure()
    row["title"] = None
    assert parse_failing_e2e_annotations([row]) == ()


def test_parse_failing_e2e_annotations_message_defaults_empty_when_missing_or_non_string() -> None:
    missing = {"annotation_level": "failure", "path": _SPEC, "title": _TITLE}
    non_string = _failure()
    non_string["message"] = ["not", "text"]
    parsed = parse_failing_e2e_annotations([missing, non_string])
    assert [t.message for t in parsed] == ["", ""]


def test_parse_failing_e2e_annotations_keeps_ratchet_verdict_row_as_separate_test() -> None:
    ratchet = _failure(
        title=_RATCHET_TITLE,
        message="home-refetch-stability.spec.ts flaked (passed on retry)",
    )
    parsed = parse_failing_e2e_annotations([_failure(), ratchet])
    assert [t.title for t in parsed] == [_TITLE, _RATCHET_TITLE]
    assert {t.spec for t in parsed} == {_SPEC}


def test_parse_failing_e2e_annotations_does_not_dedup() -> None:
    assert len(parse_failing_e2e_annotations([_failure(), _failure()])) == 2


def test_parse_failing_e2e_annotations_ignores_non_mapping_items() -> None:
    assert parse_failing_e2e_annotations(["failure", 7, None, _failure()]) == (
        FailingE2ETest(spec=_SPEC, title=_TITLE, message=_MESSAGE),
    )


def test_parse_failing_e2e_annotations_empty_payload_returns_empty_tuple() -> None:
    assert parse_failing_e2e_annotations([]) == ()


def test_parse_failing_e2e_annotations_skips_notice_level_machine_load_signal() -> None:
    load_notice = {
        "annotation_level": "notice",
        "path": _SPEC,
        "title": "E2E machine load at failure",
        "message": f"{_SPEC} flaked at a measured machine load of 2.00/cpu.",
    }
    assert parse_failing_e2e_annotations([load_notice]) == ()
