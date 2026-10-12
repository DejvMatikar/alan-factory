from __future__ import annotations

import logging
from pathlib import Path

import pytest

from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _pr_flake_ledger
from tests import _pr_branch_support as support
from tests._pr_branch_support import _E2E_GATE, _WIDGET_FORGE, _audit_entries
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript

_MAX_BYTES = _pr_flake_ledger._DIFF_PATCH_MAX_BYTES  # noqa: SLF001
_TITLE = f"Flake quarantine proposal: {_E2E_GATE}"
_LABEL = "flake-quarantine"


@pytest.fixture(autouse=True)
def _default_transport_token(monkeypatch: pytest.MonkeyPatch) -> None:
    support.set_verifier_transport_token(monkeypatch)


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


def _open_proposal_for_three_prs() -> None:
    for pr in (1, 2, 3):
        _pr_flake_ledger.record_flake_occurrence(
            pr, (_E2E_GATE,), "task-x", forge_target=DEFAULT_FORGE_TARGET,
        )


def test_fetch_pr_diff_patch_asks_the_port_for_the_pr_on_the_given_forge_target(
    forge: ForgeScript,
) -> None:
    forge.queue("pr_patch", "-x\n")
    _pr_flake_ledger.fetch_pr_diff_patch(42, forge_target=_WIDGET_FORGE)
    assert forge.calls("pr_patch") == [((42,), {"forge_target": _WIDGET_FORGE})]


def test_fetch_pr_diff_patch_returns_a_patch_exactly_at_the_ceiling(forge: ForgeScript) -> None:
    patch = "x" * _MAX_BYTES
    forge.queue("pr_patch", patch)
    assert _pr_flake_ledger.fetch_pr_diff_patch(42, forge_target=DEFAULT_FORGE_TARGET) == patch


def test_fetch_pr_diff_patch_skips_a_patch_one_byte_over_the_ceiling(
    forge: ForgeScript, caplog: pytest.LogCaptureFixture,
) -> None:
    forge.queue("pr_patch", "x" * (_MAX_BYTES + 1))
    with caplog.at_level(logging.WARNING):
        assert _pr_flake_ledger.fetch_pr_diff_patch(42, forge_target=DEFAULT_FORGE_TARGET) is None
    assert [r.getMessage() for r in caplog.records] == [
        f"fetch_pr_diff_patch over {_MAX_BYTES} bytes for PR #42 — skipping diff-join",
    ]


def test_fetch_pr_diff_patch_measures_the_ceiling_in_encoded_bytes_not_characters(
    forge: ForgeScript,
) -> None:
    patch = "é" * (_MAX_BYTES // 2 + 1)
    assert len(patch) < _MAX_BYTES
    forge.queue("pr_patch", patch)
    assert _pr_flake_ledger.fetch_pr_diff_patch(42, forge_target=DEFAULT_FORGE_TARGET) is None


def test_fetch_pr_diff_patch_accepts_multibyte_text_exactly_at_the_ceiling(
    forge: ForgeScript,
) -> None:
    patch = "é" * (_MAX_BYTES // 2)
    forge.queue("pr_patch", patch)
    assert _pr_flake_ledger.fetch_pr_diff_patch(42, forge_target=DEFAULT_FORGE_TARGET) == patch


def test_fetch_pr_diff_patch_returns_an_empty_diff_as_an_empty_string(
    forge: ForgeScript, caplog: pytest.LogCaptureFixture,
) -> None:
    forge.queue("pr_patch", "")
    with caplog.at_level(logging.WARNING):
        assert _pr_flake_ledger.fetch_pr_diff_patch(42, forge_target=DEFAULT_FORGE_TARGET) == ""
    assert caplog.records == []


def test_fetch_pr_diff_patch_logs_one_warning_and_returns_none_on_a_port_fault(
    forge: ForgeScript, caplog: pytest.LogCaptureFixture,
) -> None:
    forge.queue("pr_patch", None)
    with caplog.at_level(logging.WARNING):
        assert _pr_flake_ledger.fetch_pr_diff_patch(42, forge_target=DEFAULT_FORGE_TARGET) is None
    assert [r.getMessage() for r in caplog.records] == ["fetch_pr_diff_patch failed for PR #42"]


def test_has_open_flake_proposal_searches_the_flake_label_for_the_proposal_title(
    forge: ForgeScript,
) -> None:
    forge.queue("search_open_issues", ())
    _pr_flake_ledger._has_open_flake_proposal(  # noqa: SLF001
        _E2E_GATE, forge_target=_WIDGET_FORGE,
    )
    assert forge.calls("search_open_issues") == [
        ((_TITLE,), {"forge_target": _WIDGET_FORGE, "label": _LABEL}),
    ]


def test_has_open_flake_proposal_filters_a_fuzzy_candidate_list_to_an_exact_title(
    forge: ForgeScript,
) -> None:
    forge.queue(
        "search_open_issues",
        ({"title": _TITLE + " (duplicate)"}, {"title": "unrelated"}, {"title": _TITLE}),
    )
    assert _pr_flake_ledger._has_open_flake_proposal(
        _E2E_GATE, forge_target=DEFAULT_FORGE_TARGET,
    ) is True  # noqa: SLF001


def test_has_open_flake_proposal_rejects_a_near_miss_title(forge: ForgeScript) -> None:
    forge.queue("search_open_issues", ({"title": _TITLE + " (duplicate)"},))
    assert _pr_flake_ledger._has_open_flake_proposal(
        _E2E_GATE, forge_target=DEFAULT_FORGE_TARGET,
    ) is False  # noqa: SLF001


def test_has_open_flake_proposal_ignores_a_candidate_without_a_title(
    forge: ForgeScript,
) -> None:
    forge.queue("search_open_issues", ({"number": 7},))
    assert _pr_flake_ledger._has_open_flake_proposal(
        _E2E_GATE, forge_target=DEFAULT_FORGE_TARGET,
    ) is False  # noqa: SLF001


def test_has_open_flake_proposal_matches_the_spec_scoped_title(forge: ForgeScript) -> None:
    forge.queue(
        "search_open_issues", ({"title": "Flake quarantine proposal: spec a.spec.ts"},),
    )
    assert _pr_flake_ledger._has_open_flake_proposal(
        "spec:a.spec.ts", forge_target=DEFAULT_FORGE_TARGET,
    ) is True  # noqa: SLF001


def test_ensure_flake_label_creates_the_label_on_the_given_forge_target(
    forge: ForgeScript,
) -> None:
    forge.queue("create_label", True)
    _pr_flake_ledger._ensure_flake_label(forge_target=_WIDGET_FORGE)  # noqa: SLF001
    assert forge.calls("create_label") == [
        (
            (_LABEL,),
            {
                "forge_target": _WIDGET_FORGE,
                "color": "d4c5f9",
                "description": "Proposal to quarantine a flaky external check",
            },
        ),
    ]


def test_open_flake_issue_creates_the_labelled_issue_on_the_given_forge_target(
    forge: ForgeScript,
) -> None:
    forge.queue("create_issue", True)
    _pr_flake_ledger._open_flake_issue(  # noqa: SLF001
        "title", "body", forge_target=_WIDGET_FORGE,
    )
    assert forge.calls("create_issue") == [
        (("title", "body"), {"forge_target": _WIDGET_FORGE, "label": _LABEL}),
    ]


def test_proposal_flow_searches_then_creates_the_label_then_searches_again_then_opens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    forge.queue("search_open_issues", (), ())
    forge.queue("create_label", True)
    forge.queue("create_issue", True)
    _open_proposal_for_three_prs()
    assert [name for name, _args, _kwargs in forge.fake.calls] == [
        "search_open_issues", "create_label", "search_open_issues", "create_issue",
    ]


def test_proposal_flow_stops_when_the_recheck_after_label_creation_finds_a_proposal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    forge.queue("search_open_issues", (), ({"title": _TITLE},))
    forge.queue("create_label", True)
    _open_proposal_for_three_prs()
    assert [name for name, _args, _kwargs in forge.fake.calls] == [
        "search_open_issues", "create_label", "search_open_issues",
    ]


def test_proposal_flow_records_a_failed_issue_creation_in_the_audit_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    forge.queue("search_open_issues", (), ())
    forge.queue("create_label", False)
    forge.queue("create_issue", False)
    _open_proposal_for_three_prs()
    entry = next(
        e for e in _audit_entries(tmp_path) if e.get("action") == "flake_proposal_opened"
    )
    assert entry["ok"] is False
