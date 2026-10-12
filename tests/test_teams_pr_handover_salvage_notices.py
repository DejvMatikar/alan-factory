from __future__ import annotations

from alan.teams import _pr_handover as ph

_FORBIDDEN_MERITS_PHRASE = "on their merits like any other contribution"


def test_ordinary_salvage_notice_never_tells_downstream_to_judge_on_merits() -> None:
    assert _FORBIDDEN_MERITS_PHRASE not in ph._SALVAGE_NOTICE  # noqa: SLF001
    assert "must be re-run" in ph._SALVAGE_NOTICE  # noqa: SLF001


def test_ordinary_salvage_failed_notice_never_tells_downstream_to_judge_on_merits() -> None:
    assert _FORBIDDEN_MERITS_PHRASE not in ph._SALVAGE_FAILED_NOTICE  # noqa: SLF001
    assert "must be re-run" in ph._SALVAGE_FAILED_NOTICE  # noqa: SLF001


def test_ceiling_host_clause_never_tells_downstream_to_judge_on_merits() -> None:
    assert _FORBIDDEN_MERITS_PHRASE not in ph._CEILING_SALVAGE_HOST_CLAUSE  # noqa: SLF001
    assert "must be re-run" in ph._CEILING_SALVAGE_HOST_CLAUSE  # noqa: SLF001


def test_ceiling_worker_clause_never_tells_downstream_to_judge_on_merits() -> None:
    assert _FORBIDDEN_MERITS_PHRASE not in ph._CEILING_SALVAGE_WORKER_CLAUSE  # noqa: SLF001
    assert "must be re-run" in ph._CEILING_SALVAGE_WORKER_CLAUSE  # noqa: SLF001


def test_ceiling_failed_clause_never_tells_downstream_to_judge_on_merits() -> None:
    assert _FORBIDDEN_MERITS_PHRASE not in ph._CEILING_SALVAGE_FAILED_CLAUSE  # noqa: SLF001
    assert "must be re-run" in ph._CEILING_SALVAGE_FAILED_CLAUSE  # noqa: SLF001


def test_all_five_salvage_texts_avoid_the_forbidden_judge_wording() -> None:
    texts = (
        ph._SALVAGE_NOTICE,  # noqa: SLF001
        ph._SALVAGE_FAILED_NOTICE,  # noqa: SLF001
        ph._CEILING_SALVAGE_HOST_CLAUSE,  # noqa: SLF001
        ph._CEILING_SALVAGE_WORKER_CLAUSE,  # noqa: SLF001
        ph._CEILING_SALVAGE_FAILED_CLAUSE,  # noqa: SLF001
    )
    for text in texts:
        assert "merits" not in text


def test_with_salvage_notice_interpolates_skipped_count_for_salvaged_outcome() -> None:
    result = ph.with_salvage_notice("## Summary\nDone.", "salvaged", skipped_count=2)
    assert "2 untracked path(s)" in result


def test_with_salvage_notice_no_interpolation_by_default() -> None:
    result = ph.with_salvage_notice("## Summary\nDone.", "salvaged")
    assert "untracked path(s)" not in result
