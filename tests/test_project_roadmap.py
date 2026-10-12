from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan import store
from alan.project_roadmap import (
    ParsedRoadmapItem,
    RoadmapAlreadyImportedError,
    RoadmapError,
    RoadmapItemNotFoundError,
    RoadmapReorderMismatchError,
    edit_item,
    import_roadmap,
    parse_roadmap_markdown,
    read_items,
    reorder_items,
    seed_items,
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "projects"


def test_parse_two_items_with_verbatim_bodies() -> None:
    markdown = "## First\nBody one.\n\nMore text.\n\n## Second\nBody two.\n"
    items = parse_roadmap_markdown(markdown)
    assert items == [
        ParsedRoadmapItem(title="First", text="Body one.\n\nMore text."),
        ParsedRoadmapItem(title="Second", text="Body two."),
    ]


def test_parse_drops_leading_prose_before_first_heading() -> None:
    markdown = "Some intro prose nobody reads.\n\n## Real Item\nBody.\n"
    items = parse_roadmap_markdown(markdown)
    assert items == [ParsedRoadmapItem(title="Real Item", text="Body.")]


def test_parse_skips_whitespace_only_title_heading() -> None:
    markdown = "## \nSkipped body.\n\n## Kept\nKept body.\n"
    items = parse_roadmap_markdown(markdown)
    assert items == [ParsedRoadmapItem(title="Kept", text="Kept body.")]


def test_parse_skips_bare_hash_heading_with_no_title() -> None:
    markdown = "##\nSkipped body.\n\n## Kept\nKept body.\n"
    items = parse_roadmap_markdown(markdown)
    assert items == [ParsedRoadmapItem(title="Kept", text="Kept body.")]


def test_parse_ignores_h3_and_deeper_headings() -> None:
    markdown = "## Item\nIntro.\n\n### Sub-heading\nSub body.\n\nMore.\n"
    items = parse_roadmap_markdown(markdown)
    assert len(items) == 1
    assert items[0].title == "Item"
    assert "### Sub-heading" in items[0].text
    assert "Sub body." in items[0].text


def test_parse_ignores_h1_heading() -> None:
    markdown = "# Not an item\n\n## Real Item\nBody.\n"
    items = parse_roadmap_markdown(markdown)
    assert items == [ParsedRoadmapItem(title="Real Item", text="Body.")]


def test_parse_last_item_body_runs_to_eof() -> None:
    markdown = "## Only\nline one\nline two"
    items = parse_roadmap_markdown(markdown)
    assert items == [ParsedRoadmapItem(title="Only", text="line one\nline two")]


def test_parse_empty_markdown_returns_no_items() -> None:
    assert parse_roadmap_markdown("") == []


def test_parse_heading_with_no_body_yields_empty_text() -> None:
    markdown = "## Empty Body\n## Next\nSomething.\n"
    items = parse_roadmap_markdown(markdown)
    assert items[0] == ParsedRoadmapItem(title="Empty Body", text="")
    assert items[1] == ParsedRoadmapItem(title="Next", text="Something.")


def test_parse_strips_surrounding_whitespace_from_title() -> None:
    markdown = "##   Padded Title   \nBody.\n"
    items = parse_roadmap_markdown(markdown)
    assert items[0].title == "Padded Title"


def test_import_roadmap_seeds_ordered_items_with_opaque_ids(root: Path) -> None:
    markdown = "## Alpha\nA body.\n\n## Beta\nB body.\n"
    items = import_roadmap("proj-a", markdown, root=root)
    assert [i.title for i in items] == ["Alpha", "Beta"]
    assert [i.text for i in items] == ["A body.", "B body."]
    for item in items:
        assert item.id.startswith("itm_")
    assert items[0].id != items[1].id


def test_import_roadmap_persists_durably(root: Path) -> None:
    markdown = "## Only\nBody.\n"
    import_roadmap("proj-a", markdown, root=root)
    reloaded = read_items("proj-a", root=root)
    assert len(reloaded) == 1
    assert reloaded[0].title == "Only"
    assert reloaded[0].text == "Body."


def test_import_roadmap_lives_under_project_dir(root: Path) -> None:
    import_roadmap("proj-a", "## Only\nBody.\n", root=root)
    assert (root / "proj-a" / "roadmap.json").is_file()


def test_import_roadmap_second_call_raises_already_imported(root: Path) -> None:
    import_roadmap("proj-a", "## Only\nBody.\n", root=root)
    with pytest.raises(RoadmapAlreadyImportedError):
        import_roadmap("proj-a", "## Different\nOther.\n", root=root)
    reloaded = read_items("proj-a", root=root)
    assert len(reloaded) == 1
    assert reloaded[0].title == "Only"


def test_import_roadmap_with_zero_parsed_items_still_blocks_repeat(root: Path) -> None:
    import_roadmap("proj-a", "no headings here at all", root=root)
    assert read_items("proj-a", root=root) == []
    with pytest.raises(RoadmapAlreadyImportedError):
        import_roadmap("proj-a", "## Real\nBody.\n", root=root)


def test_seed_items_two_projects_do_not_collide(root: Path) -> None:
    seed_items("proj-a", [ParsedRoadmapItem(title="A", text="")], root=root)
    seed_items("proj-b", [ParsedRoadmapItem(title="B", text="")], root=root)
    assert [i.title for i in read_items("proj-a", root=root)] == ["A"]
    assert [i.title for i in read_items("proj-b", root=root)] == ["B"]


def test_read_items_returns_empty_when_no_roadmap_yet(root: Path) -> None:
    assert read_items("no-such-project", root=root) == []


def test_read_items_raises_on_malformed_json(root: Path) -> None:
    project_dir = root / "proj-a"
    project_dir.mkdir(parents=True)
    (project_dir / "roadmap.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(RoadmapError, match="malformed"):
        read_items("proj-a", root=root)


def test_reorder_items_changes_only_position(root: Path) -> None:
    seeded = seed_items(
        "proj-a",
        [
            ParsedRoadmapItem(title="First", text="f"),
            ParsedRoadmapItem(title="Second", text="s"),
            ParsedRoadmapItem(title="Third", text="t"),
        ],
        root=root,
    )
    ids = [i.id for i in seeded]
    reordered = reorder_items("proj-a", [ids[2], ids[0], ids[1]], root=root)
    assert [i.id for i in reordered] == [ids[2], ids[0], ids[1]]
    assert [i.title for i in reordered] == ["Third", "First", "Second"]
    by_id = {i.id: i for i in reordered}
    assert by_id[ids[0]].title == "First"
    assert by_id[ids[0]].text == "f"

    persisted = read_items("proj-a", root=root)
    assert [i.id for i in persisted] == [ids[2], ids[0], ids[1]]


def test_reorder_items_rejects_missing_id(root: Path) -> None:
    seeded = seed_items(
        "proj-a",
        [ParsedRoadmapItem(title="A", text=""), ParsedRoadmapItem(title="B", text="")],
        root=root,
    )
    with pytest.raises(RoadmapReorderMismatchError):
        reorder_items("proj-a", [seeded[0].id], root=root)


def test_reorder_items_rejects_unknown_id(root: Path) -> None:
    seeded = seed_items("proj-a", [ParsedRoadmapItem(title="A", text="")], root=root)
    with pytest.raises(RoadmapReorderMismatchError):
        reorder_items("proj-a", [seeded[0].id, "itm_unknown"], root=root)


def test_reorder_items_rejects_duplicate_id(root: Path) -> None:
    seeded = seed_items(
        "proj-a",
        [ParsedRoadmapItem(title="A", text=""), ParsedRoadmapItem(title="B", text="")],
        root=root,
    )
    with pytest.raises(RoadmapReorderMismatchError):
        reorder_items("proj-a", [seeded[0].id, seeded[0].id], root=root)


def test_edit_item_updates_title_only(root: Path) -> None:
    seeded = seed_items("proj-a", [ParsedRoadmapItem(title="Old", text="body")], root=root)
    updated = edit_item("proj-a", seeded[0].id, title="New", root=root)
    assert updated[0].id == seeded[0].id
    assert updated[0].title == "New"
    assert updated[0].text == "body"


def test_edit_item_updates_text_only(root: Path) -> None:
    seeded = seed_items("proj-a", [ParsedRoadmapItem(title="Title", text="old")], root=root)
    updated = edit_item("proj-a", seeded[0].id, text="new", root=root)
    assert updated[0].title == "Title"
    assert updated[0].text == "new"


def test_edit_item_updates_title_and_text(root: Path) -> None:
    seeded = seed_items("proj-a", [ParsedRoadmapItem(title="Old", text="old")], root=root)
    updated = edit_item("proj-a", seeded[0].id, title="New", text="new", root=root)
    assert updated[0].title == "New"
    assert updated[0].text == "new"


def test_edit_item_leaves_other_items_untouched(root: Path) -> None:
    seeded = seed_items(
        "proj-a",
        [ParsedRoadmapItem(title="A", text="a"), ParsedRoadmapItem(title="B", text="b")],
        root=root,
    )
    updated = edit_item("proj-a", seeded[0].id, title="A2", root=root)
    other = next(i for i in updated if i.id == seeded[1].id)
    assert other.title == "B"
    assert other.text == "b"


def test_edit_item_unknown_id_raises(root: Path) -> None:
    seed_items("proj-a", [ParsedRoadmapItem(title="A", text="")], root=root)
    with pytest.raises(RoadmapItemNotFoundError):
        edit_item("proj-a", "itm_unknown", title="X", root=root)


def test_edit_item_persists(root: Path) -> None:
    seeded = seed_items("proj-a", [ParsedRoadmapItem(title="Old", text="")], root=root)
    edit_item("proj-a", seeded[0].id, title="New", root=root)
    reloaded = read_items("proj-a", root=root)
    assert reloaded[0].title == "New"


def test_write_items_delegates_to_store_write_text_atomic(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, str]] = []

    def spy(path: Path, text: str, **_kw: object) -> None:
        calls.append((path, text))

    monkeypatch.setattr(store, "write_text_atomic", spy)
    seed_items("proj-a", [ParsedRoadmapItem(title="A", text="body")], root=root)
    assert len(calls) == 1
    path, text = calls[0]
    assert path == root / "proj-a" / "roadmap.json"
    parsed = json.loads(text)
    assert len(parsed) == 1
    assert parsed[0]["title"] == "A"
    assert parsed[0]["text"] == "body"
