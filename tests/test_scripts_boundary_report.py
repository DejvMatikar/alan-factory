from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import boundary_report  # type: ignore[import-not-found]  # noqa: E402

_REAL_FAILURE_XML = (
    '<?xml version="1.0" encoding="utf-8"?>'
    "<testsuites><testsuite>"
    '<testcase classname="tests.test_x" name="test_boom" time="0.000">'
    '<failure message="AssertionError: some &lt;weird&gt; failure&#10;assert 1 == 2">'
    "tests/test_x.py:2: in test_boom\n"
    '    assert 1 == 2, "some &lt;weird&gt; failure"\n'
    "E   AssertionError: some &lt;weird&gt; failure\n"
    "E   assert 1 == 2"
    "</failure></testcase></testsuite></testsuites>"
)


def test_extract_prefers_full_detail_over_message_attribute() -> None:
    xml_text = (
        "<testsuite><testcase>"
        '<failure message="short abbreviated message">'
        "full traceback detail line one\nfull traceback detail line two"
        "</failure></testcase></testsuite>"
    )
    extracted = boundary_report._extract_failure_text(xml_text)
    assert extracted == "full traceback detail line one\nfull traceback detail line two"
    assert extracted is not None
    assert "short abbreviated message" not in extracted


def test_extract_falls_back_to_message_attribute_when_no_detail_text() -> None:
    xml_text = '<testsuite><testcase><failure message="only a message" /></testcase></testsuite>'
    assert boundary_report._extract_failure_text(xml_text) == "only a message"


def test_extract_unescapes_xml_entities() -> None:
    xml_text = (
        "<testsuite><testcase>"
        '<failure message="x">'
        "AssertionError: some &lt;weird&gt; &amp; &quot;quoted&quot; text"
        "</failure></testcase></testsuite>"
    )
    extracted = boundary_report._extract_failure_text(xml_text)
    assert extracted == 'AssertionError: some <weird> & "quoted" text'


def test_extract_joins_multiple_failures_in_document_order() -> None:
    xml_text = (
        "<testsuite>"
        '<testcase><failure message="m1">first failure detail</failure></testcase>'
        '<testcase><error message="m2">second failure detail</error></testcase>'
        "</testsuite>"
    )
    extracted = boundary_report._extract_failure_text(xml_text)
    assert extracted == "first failure detail\n\n---\n\nsecond failure detail"


def test_extract_returns_none_when_no_failure_or_error_elements() -> None:
    xml_text = '<testsuite><testcase name="test_ok" /></testsuite>'
    assert boundary_report._extract_failure_text(xml_text) is None


def test_extract_handles_real_pytest_junit_xml() -> None:
    extracted = boundary_report._extract_failure_text(_REAL_FAILURE_XML)
    assert extracted is not None
    assert "AssertionError: some <weird> failure" in extracted
    assert "assert 1 == 2" in extracted
    assert "&lt;" not in extracted


def test_truncate_leaves_short_body_untouched() -> None:
    assert boundary_report._truncate("short body") == "short body"


def test_truncate_caps_body_and_appends_marker() -> None:
    body = "x" * 100_000
    truncated = boundary_report._truncate(body)
    assert len(truncated) == boundary_report._MAX_BODY_CHARS
    assert truncated.endswith(boundary_report._TRUNCATION_MARKER)


def test_fence_for_returns_triple_backtick_when_content_has_none() -> None:
    assert boundary_report._fence_for("plain traceback text") == "```"


def test_fence_for_widens_past_longest_backtick_run_in_content() -> None:
    assert boundary_report._fence_for("some output with ``` inside") == "````"
    assert boundary_report._fence_for("nested ```` fence") == "`````"


def test_wrap_in_fence_wraps_content_between_matching_fences() -> None:
    wrapped = boundary_report._wrap_in_fence("hello")
    assert wrapped == "```\nhello\n```"


def test_wrap_in_fence_uses_wider_fence_when_content_has_backticks() -> None:
    wrapped = boundary_report._wrap_in_fence("body with ``` triple backticks")
    assert wrapped == "````\nbody with ``` triple backticks\n````"


def test_render_body_returns_fallback_when_file_missing(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.xml"
    assert boundary_report.render_body(missing) == boundary_report._FALLBACK_BODY


def test_render_body_returns_fallback_on_malformed_xml(tmp_path: Path) -> None:
    path = tmp_path / "malformed.xml"
    path.write_text('<testsuite><testcase><failure message="oops">unterminated')
    assert boundary_report.render_body(path) == boundary_report._FALLBACK_BODY


def test_render_body_returns_fallback_when_no_failures_present(tmp_path: Path) -> None:
    path = tmp_path / "clean.xml"
    path.write_text('<testsuite><testcase name="test_ok" /></testsuite>')
    assert boundary_report.render_body(path) == boundary_report._FALLBACK_BODY


def test_render_body_returns_fallback_on_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "bad-encoding.xml"
    path.write_bytes(
        b'\xff\xfe<testsuite><testcase><failure message="x">bad</failure></testcase></testsuite>'
    )
    assert boundary_report.render_body(path) == boundary_report._FALLBACK_BODY


def test_render_body_extracts_real_pytest_failure_xml(tmp_path: Path) -> None:
    path = tmp_path / "results.xml"
    path.write_text(_REAL_FAILURE_XML)
    body = boundary_report.render_body(path)
    assert "AssertionError: some <weird> failure" in body
    assert "&lt;" not in body


def test_render_body_wraps_extracted_failure_in_fenced_code_block(tmp_path: Path) -> None:
    path = tmp_path / "results.xml"
    path.write_text(_REAL_FAILURE_XML)
    body = boundary_report.render_body(path)
    assert body.startswith("```\n")
    assert body.endswith("\n```")


def test_render_body_fallback_is_not_fenced(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.xml"
    body = boundary_report.render_body(missing)
    assert "`" not in body


def test_render_body_truncates_oversized_extraction(tmp_path: Path) -> None:
    path = tmp_path / "huge.xml"
    huge_detail = "x" * 100_000
    path.write_text(f'<testsuite><testcase><failure message="m">{huge_detail}</failure>'
                     "</testcase></testsuite>")
    body = boundary_report.render_body(path)
    fence = "```"
    assert body.startswith(fence + "\n")
    assert body.endswith("\n" + fence)
    inner = body[len(fence) + 1 : -(len(fence) + 1)]
    assert len(inner) == boundary_report._MAX_BODY_CHARS
    assert inner.endswith(boundary_report._TRUNCATION_MARKER)


def test_main_prints_rendered_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "results.xml"
    path.write_text(_REAL_FAILURE_XML)
    monkeypatch.setattr(sys, "argv", ["boundary_report.py", "--results", str(path)])
    assert boundary_report.main() == 0
    assert "AssertionError: some <weird> failure" in capsys.readouterr().out


def test_main_uses_default_results_path_when_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["boundary_report.py"])
    assert boundary_report.main() == 0
    assert boundary_report._FALLBACK_BODY in capsys.readouterr().out


def test_main_exits_one_on_bad_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["boundary_report.py", "--unknown-flag"])
    with pytest.raises(SystemExit) as exc_info:
        boundary_report.main()
    assert exc_info.value.code == 1


def test_parse_args_reraises_help_exit_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["boundary_report.py", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        boundary_report._parse_args()
    assert exc_info.value.code == 0
