from __future__ import annotations

from alan.overlap import find_out_of_manifest, find_sibling_overlap


class TestFindOutOfManifest:
    def test_empty_manifest_is_a_no_op(self) -> None:
        assert find_out_of_manifest(["alan/x.py", "alan/y.py"], []) == []

    def test_blank_manifest_entries_are_a_no_op(self) -> None:
        assert find_out_of_manifest(["alan/x.py"], ["", "   "]) == []

    def test_dir_covers_file_beneath_it(self) -> None:
        manifest = ["alan/web/src/chooser/"]
        diff_files = ["alan/web/src/chooser/Foo.tsx"]
        assert find_out_of_manifest(diff_files, manifest) == []

    def test_dir_without_trailing_slash_still_covers_file(self) -> None:
        manifest = ["alan/web/src/chooser"]
        diff_files = ["alan/web/src/chooser/Foo.tsx"]
        assert find_out_of_manifest(diff_files, manifest) == []

    def test_exact_file_match_is_covered(self) -> None:
        manifest = ["alan/x.py"]
        diff_files = ["alan/x.py"]
        assert find_out_of_manifest(diff_files, manifest) == []

    def test_leading_dot_slash_and_slashes_are_normalized(self) -> None:
        manifest = ["./alan/x.py/"]
        diff_files = ["alan/x.py"]
        assert find_out_of_manifest(diff_files, manifest) == []

    def test_clean_no_overlap_flags_every_file(self) -> None:
        manifest = ["alan/other.py"]
        diff_files = ["alan/x.py", "tests/test_x.py"]
        assert find_out_of_manifest(diff_files, manifest) == diff_files

    def test_mixed_covered_and_out_of_manifest(self) -> None:
        manifest = ["alan/web/src/chooser/"]
        diff_files = ["alan/web/src/chooser/Foo.tsx", "alan/unrelated.py"]
        assert find_out_of_manifest(diff_files, manifest) == ["alan/unrelated.py"]


class TestFindSiblingOverlap:
    def test_no_siblings_returns_empty(self) -> None:
        assert find_sibling_overlap(["alan/x.py"], {}) == {}

    def test_sibling_with_empty_manifest_is_skipped(self) -> None:
        result = find_sibling_overlap(["alan/x.py"], {"sib-1": []})
        assert result == {}

    def test_sibling_with_no_overlap_is_omitted(self) -> None:
        result = find_sibling_overlap(
            ["alan/x.py"], {"sib-1": ["alan/other.py"]}
        )
        assert result == {}

    def test_sibling_overlap_reports_hits(self) -> None:
        result = find_sibling_overlap(
            ["alan/web/src/chooser/Foo.tsx", "alan/unrelated.py"],
            {"sib-1": ["alan/web/src/chooser/"]},
        )
        assert result == {"sib-1": ["alan/web/src/chooser/Foo.tsx"]}

    def test_multiple_siblings_mixed_results(self) -> None:
        result = find_sibling_overlap(
            ["alan/web/src/chooser/Foo.tsx", "alan/other.py"],
            {
                "sib-1": ["alan/web/src/chooser/"],
                "sib-2": [],
                "sib-3": ["alan/nowhere.py"],
            },
        )
        assert result == {"sib-1": ["alan/web/src/chooser/Foo.tsx"]}
