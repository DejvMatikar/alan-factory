from __future__ import annotations

from pathlib import Path

import pytest

from alan.teams import TEAM_ROLE_SUMMARIES, TEAMS
from scripts import gen_teams_table


def _extract_block(text: str) -> str:
    start = text.index(gen_teams_table.BEGIN_MARKER)
    end = text.index(gen_teams_table.END_MARKER) + len(gen_teams_table.END_MARKER)
    return text[start:end]


def test_doc_block_is_byte_identical_to_generated() -> None:
    doc_text = gen_teams_table.DOC_PATH.read_text()
    assert _extract_block(doc_text) == gen_teams_table.render_block()


def test_main_check_passes_against_real_doc() -> None:
    assert gen_teams_table._main(["--check"]) == 0


def test_check_membership_is_empty_for_current_registry() -> None:
    assert gen_teams_table.check_membership() == []


def test_ui_review_is_registered_and_rendered() -> None:
    assert "ui_review" in TEAMS
    assert "ui_review" in TEAM_ROLE_SUMMARIES
    assert "`ui_review`" in gen_teams_table.render_table()


def test_check_membership_reports_teams_missing_a_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_summaries = dict(TEAM_ROLE_SUMMARIES)
    del stale_summaries["architecture"]
    monkeypatch.setattr(gen_teams_table, "TEAM_ROLE_SUMMARIES", stale_summaries)
    assert gen_teams_table.check_membership() == ["architecture"]


def test_primitive_for_returns_the_instance_class_name() -> None:
    assert gen_teams_table.primitive_for("architecture") == "ArchitectureTeam"
    assert gen_teams_table.primitive_for("research") == "ResearchTeam"
    assert gen_teams_table.primitive_for("maintenance") == "MaintenanceTeam"


def test_primitive_for_unregistered_team_returns_sentinel() -> None:
    assert "triage" not in TEAMS
    assert gen_teams_table.primitive_for("triage") == gen_teams_table.NO_INSTANCE_SENTINEL


def test_dispatchability_initial_dispatchable() -> None:
    assert gen_teams_table.dispatchability("architecture") == "dispatchable"


def test_dispatchability_post_initial() -> None:
    assert gen_teams_table.dispatchability("pr_merge") == "dispatchable (post-initial)"


def test_dispatchability_internal_only() -> None:
    assert gen_teams_table.dispatchability("decomposer") == "internal-only"


def test_dispatchability_unregistered_team() -> None:
    assert (
        gen_teams_table.dispatchability("triage")
        == "not user-dispatchable (no `TEAMS` entry)"
    )


def test_render_table_raises_when_a_team_lacks_a_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_summaries = dict(TEAM_ROLE_SUMMARIES)
    del stale_summaries["architecture"]
    monkeypatch.setattr(gen_teams_table, "TEAM_ROLE_SUMMARIES", stale_summaries)
    with pytest.raises(ValueError, match="architecture"):
        gen_teams_table.render_table()


def test_render_table_has_a_row_per_summary_sorted_alphabetically() -> None:
    table = gen_teams_table.render_table()
    rows = table.splitlines()[2:]
    assert len(rows) == len(TEAM_ROLE_SUMMARIES)
    assert [row.split(" | ")[0].strip("| `") for row in rows] == sorted(TEAM_ROLE_SUMMARIES)


def test_render_block_is_wrapped_in_markers() -> None:
    block = gen_teams_table.render_block()
    assert block.startswith(gen_teams_table.BEGIN_MARKER)
    assert block.endswith(gen_teams_table.END_MARKER)


def test_splice_replaces_only_the_marked_region() -> None:
    doc_text = (
        "before\n"
        f"{gen_teams_table.BEGIN_MARKER}\nold content\n{gen_teams_table.END_MARKER}\n"
        "after"
    )
    result = gen_teams_table.splice(doc_text, "NEW_BLOCK")
    assert result == "before\nNEW_BLOCK\nafter"


def test_main_check_fails_on_stale_doc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stale_doc = tmp_path / "teams_taxonomy.md"
    stale_doc.write_text(
        f"{gen_teams_table.BEGIN_MARKER}\nstale\n{gen_teams_table.END_MARKER}\n"
    )
    monkeypatch.setattr(gen_teams_table, "DOC_PATH", stale_doc)
    assert gen_teams_table._main(["--check"]) == 1
    assert "DRIFT" in capsys.readouterr().err


def test_main_write_regenerates_the_doc_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale_doc = tmp_path / "teams_taxonomy.md"
    stale_doc.write_text(
        f"before\n{gen_teams_table.BEGIN_MARKER}\nstale\n{gen_teams_table.END_MARKER}\nafter\n"
    )
    monkeypatch.setattr(gen_teams_table, "DOC_PATH", stale_doc)
    assert gen_teams_table._main(["--write"]) == 0
    assert _extract_block(stale_doc.read_text()) == gen_teams_table.render_block()
    assert stale_doc.read_text().startswith("before\n")
    assert stale_doc.read_text().endswith("after\n")


def test_main_requires_check_or_write() -> None:
    with pytest.raises(SystemExit):
        gen_teams_table._main([])
