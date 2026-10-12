from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

import alan.project as project_mod
from alan.project_doc import (
    ANCHORS,
    DOC_FILENAME,
    LedgerOutcome,
    ProjectDocError,
    _read_text,  # pyright: ignore[reportPrivateUsage]
    _write_text,  # pyright: ignore[reportPrivateUsage]
    append_completion_summary,
    append_decision,
    append_discovered_fact,
    append_to_section,
    apply_vision_scope_amendment,
    doc_path,
    ensure_doc,
    parse_anchors,
    read_scope_section,
    replace_section,
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path.resolve() / "projects"


def test_ensure_doc_scaffolds_four_anchors_in_order(root: Path) -> None:
    project = project_mod.create("My Project", root=root)

    updated, path = ensure_doc(project, root=root)

    assert path == root / project.id / DOC_FILENAME
    assert path.is_file()
    assert updated.doc_ref == DOC_FILENAME
    text = path.read_text(encoding="utf-8")
    positions = [text.index(f"## {anchor}") for anchor in ANCHORS]
    assert positions == sorted(positions)
    reloaded = project_mod.read(project.id, root=root)
    assert reloaded.doc_ref == DOC_FILENAME


def test_ensure_doc_backfills_preexisting_project_with_no_migration(root: Path) -> None:
    project = project_mod.create("Legacy Project", root=root)
    assert project.doc_ref is None
    original_title = project.title

    updated, path = ensure_doc(project, root=root)

    assert updated.title == original_title
    assert updated.doc_ref == DOC_FILENAME
    assert path.is_file()


def test_ensure_doc_idempotent_on_second_access(root: Path) -> None:
    project = project_mod.create("Idempotent", root=root)
    first, path = ensure_doc(project, root=root)
    first_text = path.read_text(encoding="utf-8")

    second, _ = ensure_doc(first, root=root)

    assert second.updated_at == first.updated_at
    assert path.read_text(encoding="utf-8") == first_text


def test_doc_path_resolves_using_bare_custom_doc_ref(root: Path) -> None:
    project = project_mod.create("Custom", root=root, doc_ref="notes.md")
    path = doc_path(project, root=root)
    assert path == root / project.id / "notes.md"


def test_doc_path_rejects_doc_ref_with_path_separator(root: Path) -> None:
    project = project_mod.create("Traversal", root=root, doc_ref="../../etc/passwd")
    with pytest.raises(ProjectDocError, match="bare filename"):
        doc_path(project, root=root)


def test_doc_path_rejects_doc_ref_that_looks_like_a_url(root: Path) -> None:
    project = project_mod.create("Url", root=root, doc_ref="https://example.com/spec")
    with pytest.raises(ProjectDocError, match="bare filename"):
        doc_path(project, root=root)


def test_doc_path_rejects_ref_that_symlinks_outside_project_dir(root: Path, tmp_path: Path) -> None:
    project = project_mod.create("Symlinked", root=root, doc_ref="escape.md")
    pdir = root / project.id
    outside = tmp_path.resolve() / "outside.md"
    outside.write_text("nope", encoding="utf-8")
    (pdir / "escape.md").symlink_to(outside)

    with pytest.raises(ProjectDocError, match="bare filename"):
        doc_path(project, root=root)


def test_doc_path_rejects_bare_dot_dot_doc_ref(root: Path) -> None:
    project = project_mod.create("DotDot", root=root, doc_ref="..")
    with pytest.raises(ProjectDocError, match="bare filename"):
        doc_path(project, root=root)


def test_parse_anchors_empty_initially(root: Path) -> None:
    project = project_mod.create("Parsed", root=root)
    _, path = ensure_doc(project, root=root)
    sections = parse_anchors(path.read_text(encoding="utf-8"))
    assert sections.vision == ""
    assert sections.scope == ""
    assert sections.decision_log == ""
    assert sections.discovered_facts == ""


def test_parse_anchors_handles_final_anchor_without_trailing_newline() -> None:
    text = "## Vision\n\n## Scope/Anti-scope\n\n## Decision Log\n\n## Discovered Facts"
    sections = parse_anchors(text)
    assert sections.discovered_facts == ""


def test_parse_anchors_rejects_duplicate_anchor_header() -> None:
    text = (
        "## Vision\n\n## Vision\n\n## Scope/Anti-scope\n\n"
        "## Decision Log\n\n## Discovered Facts\n\n"
    )
    with pytest.raises(ValueError, match="duplicate anchor"):
        parse_anchors(text)


def test_parse_anchors_rejects_missing_anchor_header() -> None:
    text = "## Vision\n\n## Scope/Anti-scope\n\n## Decision Log\n\n"
    with pytest.raises(ValueError, match="missing anchor"):
        parse_anchors(text)


def test_fact_append_touches_only_facts(root: Path) -> None:
    project = project_mod.create("Facts", root=root)
    project, path = ensure_doc(project, root=root)
    before = parse_anchors(path.read_text(encoding="utf-8"))

    append_discovered_fact(project, "the sky is blue", root=root)

    after = parse_anchors(path.read_text(encoding="utf-8"))
    assert after.vision == before.vision
    assert after.scope == before.scope
    assert after.decision_log == before.decision_log
    assert "the sky is blue" in after.discovered_facts

    append_discovered_fact(project, "grass is green", root=root)
    after2 = parse_anchors(path.read_text(encoding="utf-8"))
    assert "the sky is blue" in after2.discovered_facts
    assert "grass is green" in after2.discovered_facts
    assert after2.vision == before.vision
    assert after2.scope == before.scope
    assert after2.decision_log == before.decision_log


def test_amendment_touches_only_vision_scope(root: Path) -> None:
    project = project_mod.create("Amend", root=root)
    project, path = ensure_doc(project, root=root)
    append_discovered_fact(project, "pre-existing fact", root=root)
    before = parse_anchors(path.read_text(encoding="utf-8"))

    apply_vision_scope_amendment(
        project,
        vision="Ship the spine",
        scope="In: store. Out: UI.",
        decision_entry="Adopted the four-anchor doc.",
        root=root,
    )

    after = parse_anchors(path.read_text(encoding="utf-8"))
    assert after.vision == "Ship the spine"
    assert after.scope == "In: store. Out: UI."
    assert "Adopted the four-anchor doc." in after.decision_log
    assert after.discovered_facts == before.discovered_facts


def test_amendment_clearing_vision_scope_writes_blank_section(root: Path) -> None:
    project = project_mod.create("Clear", root=root)
    project, path = ensure_doc(project, root=root)

    apply_vision_scope_amendment(
        project,
        vision="",
        scope="   ",
        decision_entry="Cleared vision and scope.",
        root=root,
    )

    after = parse_anchors(path.read_text(encoding="utf-8"))
    assert after.vision == ""
    assert after.scope == ""
    assert "Cleared vision and scope." in after.decision_log


def test_concurrent_fact_appends_all_survive(root: Path) -> None:
    project = project_mod.create("Concurrent", root=root)
    project, path = ensure_doc(project, root=root)
    facts = [f"fact-{i}" for i in range(8)]
    barrier = threading.Barrier(len(facts))

    def _write(fact: str) -> None:
        barrier.wait()
        append_discovered_fact(project, fact, root=root)

    threads = [threading.Thread(target=_write, args=(fact,)) for fact in facts]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    text = path.read_text(encoding="utf-8")
    sections = parse_anchors(text)
    for fact in facts:
        assert fact in sections.discovered_facts


def test_append_to_section_rejects_unknown_anchor() -> None:
    with pytest.raises(ValueError, match="unknown anchor"):
        append_to_section("## Vision\n\n", "Not An Anchor", "x")


def test_replace_section_rejects_unknown_anchor() -> None:
    with pytest.raises(ValueError, match="unknown anchor"):
        replace_section("## Vision\n\n", "Not An Anchor", "x")


def test_write_text_cleans_up_tmp_on_failure(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = project_mod.create("Dirty", root=root)
    project, path = ensure_doc(project, root=root)

    def boom(*_a: object, **_kw: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", boom)
    with pytest.raises(OSError, match="disk full"):
        append_discovered_fact(project, "x", root=root)

    leftover = list(path.parent.glob("*.tmp"))
    assert leftover == []


def test_concurrent_ensure_doc_backfills_exactly_once(root: Path) -> None:
    project = project_mod.create("RaceBackfill", root=root)
    assert project.doc_ref is None
    barrier = threading.Barrier(8)
    results: list[Path] = []

    def _ensure() -> None:
        barrier.wait()
        _, path = ensure_doc(project, root=root)
        results.append(path)

    threads = [threading.Thread(target=_ensure) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    reloaded = project_mod.read(project.id, root=root)
    assert reloaded.doc_ref == DOC_FILENAME
    assert len(set(results)) == 1


def test_project_project_dir_helper(root: Path) -> None:
    project = project_mod.create("Dirs", root=root)
    assert project_mod.project_dir(project.id, root=root) == root / project.id


def test_write_text_rejects_path_outside_root(root: Path, tmp_path: Path) -> None:
    outside = tmp_path.resolve() / "elsewhere"
    outside.mkdir()
    target = outside / "file.md"

    with pytest.raises(ProjectDocError, match="escapes project root"):
        _write_text(target, "text", root=root)

    assert not target.exists()


def test_read_text_rejects_path_outside_root(root: Path, tmp_path: Path) -> None:
    outside = tmp_path.resolve() / "elsewhere"
    outside.mkdir()
    target = outside / "file.md"
    target.write_text("nope", encoding="utf-8")

    with pytest.raises(ProjectDocError, match="escapes project root"):
        _read_text(target, root=root)


def test_read_scope_section_returns_none_for_unknown_project(root: Path) -> None:
    assert read_scope_section("no-such-project", root=root) is None


def test_read_scope_section_returns_none_when_doc_not_yet_created(root: Path) -> None:
    project = project_mod.create("No Doc", root=root)
    assert read_scope_section(project.id, root=root) is None
    assert not (root / project.id / DOC_FILENAME).exists()


def test_read_scope_section_returns_none_for_empty_scope(root: Path) -> None:
    project = project_mod.create("Empty Scope", root=root)
    project, _ = ensure_doc(project, root=root)
    assert read_scope_section(project.id, root=root) is None


def test_read_scope_section_returns_scope_text(root: Path) -> None:
    project = project_mod.create("Has Scope", root=root)
    project, _ = ensure_doc(project, root=root)
    apply_vision_scope_amendment(
        project,
        vision="Ship it",
        scope="Anti-scope: do not touch billing.",
        decision_entry="set scope",
        root=root,
    )
    assert read_scope_section(project.id, root=root) == "Anti-scope: do not touch billing."


def test_read_scope_section_returns_none_for_malformed_doc(root: Path) -> None:
    project = project_mod.create("Malformed", root=root)
    project, path = ensure_doc(project, root=root)
    path.write_text("not a valid project doc", encoding="utf-8")
    assert read_scope_section(project.id, root=root) is None


def test_append_completion_summary_appends_to_decision_log_only(root: Path) -> None:
    project = project_mod.create("Complete Me", root=root)
    project, path = ensure_doc(project, root=root)
    apply_vision_scope_amendment(
        project,
        vision="Ship it",
        scope="In: store.",
        decision_entry="initial decision",
        root=root,
    )
    append_discovered_fact(project, "a discovered fact", root=root)
    before = parse_anchors(path.read_text(encoding="utf-8"))

    append_completion_summary(
        project,
        ledger=[LedgerOutcome(task_id="t1", title="Task One", status="completed", pr_number=7)],
        force=False,
        reason="",
        completed_at=datetime(2026, 1, 1, tzinfo=UTC),
        root=root,
    )

    after = parse_anchors(path.read_text(encoding="utf-8"))
    assert after.vision == before.vision
    assert after.scope == before.scope
    assert after.discovered_facts == before.discovered_facts
    assert "initial decision" in after.decision_log
    assert 'Project "Complete Me" marked complete at' in after.decision_log
    assert "Task One [t1]: completed, PR #7" in after.decision_log


def test_append_completion_summary_no_workspaces(root: Path) -> None:
    project = project_mod.create("No Workspaces", root=root)
    project, path = ensure_doc(project, root=root)

    append_completion_summary(
        project,
        ledger=[],
        force=False,
        reason="",
        completed_at=datetime(2026, 1, 1, tzinfo=UTC),
        root=root,
    )

    after = parse_anchors(path.read_text(encoding="utf-8"))
    assert "No workspaces were associated with this project." in after.decision_log


def test_append_completion_summary_forced_includes_reason(root: Path) -> None:
    project = project_mod.create("Forced", root=root)
    project, path = ensure_doc(project, root=root)

    append_completion_summary(
        project,
        ledger=[LedgerOutcome(task_id="t1", title="Task One", status="active")],
        force=True,
        reason="shipping despite an open workspace",
        completed_at=datetime(2026, 1, 1, tzinfo=UTC),
        root=root,
    )

    after = parse_anchors(path.read_text(encoding="utf-8"))
    assert "Forced complete: shipping despite an open workspace" in after.decision_log
    assert "Task One [t1]: active" in after.decision_log


def test_append_decision_appends_to_decision_log_only(root: Path) -> None:
    project = project_mod.create("Answer Log", root=root)
    project, path = ensure_doc(project, root=root)
    append_discovered_fact(project, "pre-existing fact", root=root)
    before = parse_anchors(path.read_text(encoding="utf-8"))

    append_decision(project, "Answered question [q1]: chosen 'retry'", root=root)

    after = parse_anchors(path.read_text(encoding="utf-8"))
    assert after.vision == before.vision
    assert after.scope == before.scope
    assert after.discovered_facts == before.discovered_facts
    assert "Answered question [q1]: chosen 'retry'" in after.decision_log


def test_append_decision_multiple_calls_both_survive(root: Path) -> None:
    project = project_mod.create("Answer Log Multi", root=root)
    project, path = ensure_doc(project, root=root)

    append_decision(project, "Answered question [q1]: chosen 'retry'", root=root)
    append_decision(project, "Answered question [q2]: chosen 'abort'", root=root)

    after = parse_anchors(path.read_text(encoding="utf-8"))
    assert "Answered question [q1]: chosen 'retry'" in after.decision_log
    assert "Answered question [q2]: chosen 'abort'" in after.decision_log


def test_append_completion_summary_pr_number_none_omits_suffix(root: Path) -> None:
    project = project_mod.create("No PR", root=root)
    project, path = ensure_doc(project, root=root)

    append_completion_summary(
        project,
        ledger=[
            LedgerOutcome(task_id="t1", title="Task One", status="archived", pr_number=None)
        ],
        force=False,
        reason="",
        completed_at=datetime(2026, 1, 1, tzinfo=UTC),
        root=root,
    )

    after = parse_anchors(path.read_text(encoding="utf-8"))
    assert "Task One [t1]: archived" in after.decision_log
    assert "PR #" not in after.decision_log
