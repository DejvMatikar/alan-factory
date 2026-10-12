from __future__ import annotations

from pathlib import Path

from alan import engine_stack_detect as esd


def _touch(root: Path, *names: str) -> None:
    for name in names:
        (root / name).write_text("x")


def test_detect_stack_recommends_dotnet_from_csproj(tmp_path: Path) -> None:
    _touch(tmp_path, "Widget.csproj")
    result = esd.detect_stack(tmp_path)
    assert result.recommended_preset == "dotnet"
    assert result.detected_stacks == ("dotnet",)
    assert result.additional_stacks == ()


def test_detect_stack_recommends_node_from_package_json(tmp_path: Path) -> None:
    _touch(tmp_path, "package.json")
    result = esd.detect_stack(tmp_path)
    assert result.recommended_preset == "node"
    assert result.detected_stacks == ("node",)


def test_detect_stack_recommends_python_from_pyproject(tmp_path: Path) -> None:
    _touch(tmp_path, "pyproject.toml")
    result = esd.detect_stack(tmp_path)
    assert result.recommended_preset == "python"
    assert result.detected_stacks == ("python",)


def test_detect_stack_flags_additional_stacks_when_mixed(tmp_path: Path) -> None:
    _touch(tmp_path, "package.json", "pyproject.toml")
    result = esd.detect_stack(tmp_path)
    assert result.recommended_preset == "node"
    assert set(result.detected_stacks) == {"node", "python"}
    assert result.additional_stacks == ("python",)


def test_detect_stack_tie_break_uses_priority_order(tmp_path: Path) -> None:
    _touch(tmp_path, "Widget.csproj", "package.json", "pyproject.toml")
    result = esd.detect_stack(tmp_path)
    assert result.recommended_preset == "dotnet"
    assert set(result.detected_stacks) == {"dotnet", "node", "python"}
    assert set(result.additional_stacks) == {"node", "python"}


def test_detect_stack_more_marker_hits_wins_over_priority_order(tmp_path: Path) -> None:
    _touch(tmp_path, "package.json", "pyproject.toml", "setup.py", "requirements.txt")
    result = esd.detect_stack(tmp_path)
    assert result.recommended_preset == "python"
    assert result.additional_stacks == ("node",)


def test_detect_stack_falls_back_to_first_priority_when_nothing_detected(
    tmp_path: Path,
) -> None:
    _touch(tmp_path, "README.md")
    result = esd.detect_stack(tmp_path)
    assert result.recommended_preset == "dotnet"
    assert result.detected_stacks == ()
    assert result.additional_stacks == ()


def test_detect_stack_handles_missing_directory(tmp_path: Path) -> None:
    result = esd.detect_stack(tmp_path / "does-not-exist")
    assert result.recommended_preset == "dotnet"
    assert result.detected_stacks == ()


def test_detect_stack_is_depth_one_only_ignores_nested_files(tmp_path: Path) -> None:
    nested = tmp_path / "vendor"
    nested.mkdir()
    _touch(nested, "package.json")
    result = esd.detect_stack(tmp_path)
    assert result.detected_stacks == ()
    assert result.recommended_preset == "dotnet"


def test_detect_stack_ignores_directories_matching_marker_names(tmp_path: Path) -> None:
    (tmp_path / "package.json").mkdir()
    result = esd.detect_stack(tmp_path)
    assert result.detected_stacks == ()


def test_detect_stack_recommends_ios_from_swift_manifests(tmp_path: Path) -> None:
    _touch(tmp_path, "Project.swift", "Package.swift")
    result = esd.detect_stack(tmp_path)
    assert result.recommended_preset == "ios"
    assert result.detected_stacks == ("ios",)
    assert result.additional_stacks == ()


def test_detect_stack_recommends_ios_from_package_swift_alone(tmp_path: Path) -> None:
    _touch(tmp_path, "Package.swift")
    result = esd.detect_stack(tmp_path)
    assert result.recommended_preset == "ios"
    assert result.detected_stacks == ("ios",)


def test_detect_stack_ios_marker_hits_beat_priority_order(tmp_path: Path) -> None:
    _touch(tmp_path, "Project.swift", "Package.swift", "Widget.csproj")
    result = esd.detect_stack(tmp_path)
    assert result.recommended_preset == "ios"
    assert set(result.detected_stacks) == {"ios", "dotnet"}
    assert result.additional_stacks == ("dotnet",)


def test_detect_stack_four_way_tie_still_recommends_dotnet(tmp_path: Path) -> None:
    _touch(tmp_path, "Widget.csproj", "package.json", "pyproject.toml", "Project.swift")
    result = esd.detect_stack(tmp_path)
    assert result.recommended_preset == "dotnet"
    assert set(result.detected_stacks) == {"dotnet", "node", "python", "ios"}


def test_detect_stack_xcodeproj_directory_is_not_a_marker(tmp_path: Path) -> None:
    (tmp_path / "Foo.xcodeproj").mkdir()
    result = esd.detect_stack(tmp_path)
    assert result.detected_stacks == ()
    assert result.recommended_preset == "dotnet"


def test_preset_priority_tuple_is_dotnet_node_python_ios() -> None:
    assert esd._PRESET_PRIORITY == ("dotnet", "node", "python", "ios")  # noqa: SLF001
