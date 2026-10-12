from __future__ import annotations

import logging
import subprocess

import pytest

from alan.forge import JsonObject, RowsOutcome
from alan.teams import unblock_issue as issue_mod
from tests._pr_branch_support import _WIDGET_FORGE
from tests.fakes.forge import FakeForge
from tests.forge_stubs import always, api_result, rows_read

_TITLE = "Unblocker: a stale verifier review blocked the merge (acme-corp/widget-app)"
_URL = "https://github.com/acme-corp/widget-app/issues/41\n"
_FAULTS = [FileNotFoundError("gh"), subprocess.TimeoutExpired(cmd="gh", timeout=1)]
_LABEL_DESCRIPTION = "Filed by alan's recipe unblocker: one issue per park shape per engine"


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> FakeForge:
    return _active_fake_forge


def _names(fake: FakeForge) -> list[str]:
    return [name for name, _args, _kwargs in fake.calls]


def _lookup_rows(fake: FakeForge, rows: list[JsonObject]) -> None:
    always(fake, "open_issues_by_title", rows_read(rows))


def test_issue_url_and_title_shapes() -> None:
    assert issue_mod.issue_url("o/r", 12) == "https://github.com/o/r/issues/12"
    assert issue_mod.shape_issue_title("x blocked", "o/r") == "Unblocker: x blocked (o/r)"


def test_find_open_issue_returns_the_matching_number(forge: FakeForge) -> None:
    _lookup_rows(forge, [{"number": 3, "title": "other"}, {"number": 9, "title": _TITLE}])
    assert issue_mod.find_open_issue(_TITLE, forge_target=_WIDGET_FORGE) == issue_mod.IssueLookup(
        ok=True, number=9
    )


def test_find_open_issue_asks_the_forge_for_the_unblocker_labelled_title_search(
    forge: FakeForge,
) -> None:
    _lookup_rows(forge, [])
    issue_mod.find_open_issue(_TITLE, forge_target=_WIDGET_FORGE)
    assert forge.calls == [
        (
            "open_issues_by_title", (_TITLE,),
            {"forge_target": _WIDGET_FORGE, "label": "unblocker"},
        ),
    ]


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"number": "9", "title": _TITLE}],
        [{"number": 9, "title": "different"}],
        [{"title": _TITLE}],
        [{"number": None, "title": _TITLE}],
    ],
)
def test_find_open_issue_reports_no_open_issue_for_a_listing_without_an_exact_numbered_match(
    rows: list[JsonObject], forge: FakeForge,
) -> None:
    _lookup_rows(forge, rows)
    assert issue_mod.find_open_issue(_TITLE, forge_target=_WIDGET_FORGE) == issue_mod.IssueLookup(
        ok=True, number=None
    )


def test_find_open_issue_reports_a_failed_lookup_on_a_failed_listing(forge: FakeForge) -> None:
    always(forge, "open_issues_by_title", rows_read(None, outcome="failed", returncode=1))
    assert issue_mod.find_open_issue(_TITLE, forge_target=_WIDGET_FORGE) == issue_mod.IssueLookup(
        ok=False, number=None
    )


def test_find_open_issue_reports_a_failed_lookup_and_warns_when_the_listing_raised(
    forge: FakeForge, caplog: pytest.LogCaptureFixture,
) -> None:
    always(
        forge, "open_issues_by_title",
        rows_read(None, outcome="raised", returncode=-1, stderr="gh vanished"),
    )
    with caplog.at_level(logging.WARNING, logger=issue_mod.logger.name):
        lookup = issue_mod.find_open_issue(_TITLE, forge_target=_WIDGET_FORGE)
    assert lookup == issue_mod.IssueLookup(ok=False, number=None)
    assert "gh vanished" in caplog.text


def test_find_open_issue_keeps_an_unparseable_successful_listing_as_no_open_issue(
    forge: FakeForge,
) -> None:
    always(forge, "open_issues_by_title", rows_read(None))
    assert issue_mod.find_open_issue(_TITLE, forge_target=_WIDGET_FORGE) == issue_mod.IssueLookup(
        ok=True, number=None
    )


def test_ensure_label_creates_the_label_idempotently_through_the_forge(forge: FakeForge) -> None:
    always(forge, "create_label", True)
    issue_mod.ensure_label(forge_target=_WIDGET_FORGE)
    assert forge.calls == [
        (
            "create_label", ("unblocker",),
            {
                "forge_target": _WIDGET_FORGE, "color": "0e8a16",
                "description": _LABEL_DESCRIPTION,
            },
        ),
    ]


def test_ensure_label_ignores_a_false_result(forge: FakeForge) -> None:
    always(forge, "create_label", False)
    issue_mod.ensure_label(forge_target=_WIDGET_FORGE)
    assert _names(forge) == ["create_label"]


def test_create_issue_parses_the_number_from_the_url(forge: FakeForge) -> None:
    always(forge, "create_numbered_issue", api_result(_URL))
    assert issue_mod.create_issue(_TITLE, "body", forge_target=_WIDGET_FORGE) == 41
    assert forge.calls == [
        (
            "create_numbered_issue", (_TITLE, "body"),
            {"forge_target": _WIDGET_FORGE, "label": "unblocker"},
        ),
    ]


def test_create_issue_returns_none_without_a_url(forge: FakeForge) -> None:
    always(forge, "create_numbered_issue", api_result("created"))
    assert issue_mod.create_issue(_TITLE, "body", forge_target=_WIDGET_FORGE) is None


def test_create_issue_returns_none_on_a_failed_create(forge: FakeForge) -> None:
    always(forge, "create_numbered_issue", api_result(_URL, returncode=2))
    assert issue_mod.create_issue(_TITLE, "body", forge_target=_WIDGET_FORGE) is None


@pytest.mark.parametrize("error", _FAULTS)
def test_create_issue_returns_none_and_warns_on_a_missing_binary_or_timeout(
    error: BaseException, forge: FakeForge, caplog: pytest.LogCaptureFixture,
) -> None:
    always(forge, "create_numbered_issue", error)
    with caplog.at_level(logging.WARNING, logger=issue_mod.logger.name):
        assert issue_mod.create_issue(_TITLE, "body", forge_target=_WIDGET_FORGE) is None
    assert "issue create raised" in caplog.text


def test_create_issue_lets_any_other_os_error_propagate(forge: FakeForge) -> None:
    always(forge, "create_numbered_issue", PermissionError("denied"))
    with pytest.raises(PermissionError):
        issue_mod.create_issue(_TITLE, "body", forge_target=_WIDGET_FORGE)


def test_ensure_shape_issue_reuses_an_open_issue(forge: FakeForge) -> None:
    _lookup_rows(forge, [{"number": 5, "title": _TITLE}])
    assert issue_mod.ensure_shape_issue(_TITLE, "body", forge_target=_WIDGET_FORGE) == 5
    assert _names(forge) == ["open_issues_by_title"]


def test_ensure_shape_issue_labels_then_creates(forge: FakeForge) -> None:
    _lookup_rows(forge, [])
    always(forge, "create_label", True)
    always(forge, "create_numbered_issue", api_result("https://github.com/o/r/issues/42\n"))
    assert issue_mod.ensure_shape_issue(_TITLE, "body", forge_target=_WIDGET_FORGE) == 42
    assert _names(forge) == ["open_issues_by_title", "create_label", "create_numbered_issue"]


def test_ensure_shape_issue_files_after_an_unparseable_successful_listing(
    forge: FakeForge,
) -> None:
    always(forge, "open_issues_by_title", rows_read(None))
    always(forge, "create_label", True)
    always(forge, "create_numbered_issue", api_result(_URL))
    assert issue_mod.ensure_shape_issue(_TITLE, "body", forge_target=_WIDGET_FORGE) == 41
    assert _names(forge) == ["open_issues_by_title", "create_label", "create_numbered_issue"]


@pytest.mark.parametrize("outcome", ["failed", "raised"])
def test_ensure_shape_issue_never_labels_or_creates_after_a_failed_lookup(
    outcome: RowsOutcome, forge: FakeForge,
) -> None:
    always(forge, "open_issues_by_title", rows_read(None, outcome=outcome, returncode=1))
    assert issue_mod.ensure_shape_issue(_TITLE, "body", forge_target=_WIDGET_FORGE) is None
    assert _names(forge) == ["open_issues_by_title"]


def test_comment_posts_through_the_forge(forge: FakeForge) -> None:
    always(forge, "comment_on_issue", api_result("{}"))
    assert issue_mod.comment(42, "hello", forge_target=_WIDGET_FORGE) is True
    assert forge.calls == [
        ("comment_on_issue", (42, "hello"), {"forge_target": _WIDGET_FORGE}),
    ]


def test_comment_reports_a_failed_post(forge: FakeForge) -> None:
    always(forge, "comment_on_issue", api_result(returncode=1, stderr="x"))
    assert issue_mod.comment(42, "hello", forge_target=_WIDGET_FORGE) is False


@pytest.mark.parametrize("error", _FAULTS)
def test_comment_reports_false_and_warns_on_a_missing_binary_or_timeout(
    error: BaseException, forge: FakeForge, caplog: pytest.LogCaptureFixture,
) -> None:
    always(forge, "comment_on_issue", error)
    with caplog.at_level(logging.WARNING, logger=issue_mod.logger.name):
        assert issue_mod.comment(42, "hello", forge_target=_WIDGET_FORGE) is False
    assert "comment on #42 raised" in caplog.text


def test_comment_lets_any_other_os_error_propagate(forge: FakeForge) -> None:
    always(forge, "comment_on_issue", PermissionError("denied"))
    with pytest.raises(PermissionError):
        issue_mod.comment(42, "hello", forge_target=_WIDGET_FORGE)
