from __future__ import annotations

from pathlib import Path

import pytest

from alan.driver_loop_registry import (
    ISSUE_DRIVER_NAMES,
    DriverLoopEntry,
    DriverLoopValidationError,
    config_dir,
    load_registry,
    validate_entry,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_yaml(directory: Path, name: str, text: str) -> Path:
    path = directory / f"{name}.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _base_entry(**overrides: object) -> DriverLoopEntry:
    fields: dict[str, object] = {
        "name": "babysit",
        "command": "/babysit",
        "cwd": "~/projects/alan",
        "self_paced": True,
        "timeout_seconds": 3600,
    }
    fields.update(overrides)
    return DriverLoopEntry.model_validate(fields)


def test_config_dir_is_checkout_relative() -> None:
    assert config_dir() == _REPO_ROOT / "config" / "driver_loops.d"


def test_shipped_registry_loads_with_no_quarantine() -> None:
    entries, quarantined = load_registry()
    assert entries == []
    assert quarantined == []


def test_config_dir_with_only_a_placeholder_file_yields_an_empty_registry(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("placeholder\n", encoding="utf-8")
    assert load_registry(tmp_path) == ([], [])


def test_synthetic_registry_grants_only_issue_drivers(tmp_path: Path) -> None:
    _write_yaml(
        tmp_path,
        "alan-issue-driver",
        "name: alan-issue-driver\ncommand: /alan-issue-driver\nargs: headless\n"
        "cwd: ~/projects/alan-issue-driver-clone\ndedicated_clone: true\nself_paced: true\n"
        "timeout_seconds: 3600\nown_state_write: true\nallowed_tools:\n  - Bash(git log:*)\n",
    )
    _write_yaml(
        tmp_path,
        "babysit",
        "name: babysit\ncommand: /babysit\ncwd: ~/projects/alan\nself_paced: true\n"
        "timeout_seconds: 3600\n",
    )
    entries, quarantined = load_registry(tmp_path)
    assert quarantined == []
    for entry in entries:
        has_grants = bool(entry.allowed_tools) or bool(entry.add_dirs) or entry.own_state_write
        assert has_grants == (entry.name in ISSUE_DRIVER_NAMES)


def test_synthetic_issue_drivers_grant_read_commands_with_headless_args(tmp_path: Path) -> None:
    expected_tools = {
        "Bash(gh pr list:*)",
        "Bash(gh pr view:*)",
        "Bash(gh pr checks:*)",
        "Bash(gh run list:*)",
        "Bash(gh run view:*)",
        "Bash(gh label list:*)",
        "Bash(git merge-base:*)",
        "Bash(git rev-parse:*)",
    }
    tools_yaml = "\n".join(f"  - {tool}" for tool in sorted(expected_tools))
    for name in ISSUE_DRIVER_NAMES:
        _write_yaml(
            tmp_path,
            name,
            f"name: {name}\ncommand: /{name}\nargs: headless\n"
            f"cwd: ~/projects/{name}-clone\ndedicated_clone: true\nself_paced: true\n"
            f"timeout_seconds: 3600\nown_state_write: true\nallowed_tools:\n{tools_yaml}\n",
        )
    entries, quarantined = load_registry(tmp_path)
    assert quarantined == []
    for name in ISSUE_DRIVER_NAMES:
        entry = next(e for e in entries if e.name == name)
        assert entry.args == "headless"
        assert expected_tools.issubset(set(entry.allowed_tools))


def test_issue_driver_allowlist_holds_only_the_alan_issue_driver() -> None:
    assert ISSUE_DRIVER_NAMES == frozenset({"alan-issue-driver"})


def test_synthetic_second_driver_carrying_grants_is_quarantined(tmp_path: Path) -> None:
    _write_yaml(
        tmp_path,
        "sample-issue-driver",
        "name: sample-issue-driver\ncommand: /sample-issue-driver\nargs: headless\n"
        "cwd: /opt/checkouts/sample-issue-driver-clone\ndedicated_clone: true\nself_paced: true\n"
        "timeout_seconds: 3600\nown_state_write: true\nadd_dirs:\n  - /opt/checkouts/docs\n",
    )
    _write_yaml(
        tmp_path,
        "alan-issue-driver",
        "name: alan-issue-driver\ncommand: /alan-issue-driver\nargs: headless\n"
        "cwd: /opt/checkouts/alan-issue-driver-clone\ndedicated_clone: true\nself_paced: true\n"
        "timeout_seconds: 3600\nown_state_write: true\n",
    )
    entries, quarantined = load_registry(tmp_path)
    assert [entry.name for entry in entries] == ["alan-issue-driver"]
    assert [item.path.name for item in quarantined] == ["sample-issue-driver.yaml"]
    assert "grants_on_non_issue_driver" in quarantined[0].error


def test_load_registry_quarantines_invalid_yaml_syntax(tmp_path: Path) -> None:
    _write_yaml(tmp_path, "bad", "name: [unterminated")
    entries, quarantined = load_registry(tmp_path)
    assert entries == []
    assert len(quarantined) == 1
    assert quarantined[0].path.name == "bad.yaml"


def test_load_registry_quarantines_schema_violation(tmp_path: Path) -> None:
    _write_yaml(tmp_path, "bad", "name: babysit\n")
    entries, quarantined = load_registry(tmp_path)
    assert entries == []
    assert len(quarantined) == 1


def test_load_registry_quarantines_unreadable_file(tmp_path: Path) -> None:
    directory = tmp_path / "cfg"
    directory.mkdir()
    unreadable = directory / "broken.yaml"
    unreadable.write_text("name: babysit\n", encoding="utf-8")
    unreadable.chmod(0)
    try:
        entries, quarantined = load_registry(directory)
    finally:
        unreadable.chmod(0o644)
    assert entries == []
    assert len(quarantined) == 1
    assert "cannot read file" in quarantined[0].error


def test_load_registry_quarantines_non_mapping_yaml_root(tmp_path: Path) -> None:
    _write_yaml(tmp_path, "list-root", "- a\n- b\n")
    entries, quarantined = load_registry(tmp_path)
    assert entries == []
    assert len(quarantined) == 1
    assert "expected mapping" in quarantined[0].error


def test_load_registry_quarantines_duplicate_name(tmp_path: Path) -> None:
    text = (
        "name: babysit\ncommand: /babysit\ncwd: ~/projects/alan\n"
        "self_paced: true\ntimeout_seconds: 60\n"
    )
    _write_yaml(tmp_path, "a", text)
    _write_yaml(tmp_path, "b", text)
    entries, quarantined = load_registry(tmp_path)
    assert len(entries) == 1
    assert len(quarantined) == 1
    assert "duplicate" in quarantined[0].error


def test_load_registry_skips_non_yaml_files(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("not yaml", encoding="utf-8")
    entries, quarantined = load_registry(tmp_path)
    assert entries == []
    assert quarantined == []


def test_load_registry_empty_directory_is_clean(tmp_path: Path) -> None:
    entries, quarantined = load_registry(tmp_path / "does-not-exist")
    assert entries == []
    assert quarantined == []


def test_validate_entry_rejects_invalid_name() -> None:
    entry = _base_entry(name="Bad_Name")
    with pytest.raises(DriverLoopValidationError) as exc:
        validate_entry(entry)
    assert exc.value.reason == "invalid_name"


def test_validate_entry_rejects_reserved_name() -> None:
    entry = _base_entry(name="state")
    with pytest.raises(DriverLoopValidationError) as exc:
        validate_entry(entry)
    assert exc.value.reason == "name_collides_with_reserved_subdir"


def test_validate_entry_rejects_both_interval_and_self_paced() -> None:
    entry = _base_entry(interval_seconds=60, self_paced=True)
    with pytest.raises(DriverLoopValidationError) as exc:
        validate_entry(entry)
    assert exc.value.reason == "schedule_ambiguous"


def test_validate_entry_rejects_neither_interval_nor_self_paced() -> None:
    entry = _base_entry(self_paced=False)
    with pytest.raises(DriverLoopValidationError) as exc:
        validate_entry(entry)
    assert exc.value.reason == "schedule_ambiguous"


def test_validate_entry_rejects_non_positive_interval() -> None:
    entry = _base_entry(self_paced=False, interval_seconds=0)
    with pytest.raises(DriverLoopValidationError) as exc:
        validate_entry(entry)
    assert exc.value.reason == "schedule_interval_not_positive"


def test_validate_entry_accepts_positive_interval() -> None:
    entry = _base_entry(self_paced=False, interval_seconds=1800)
    validate_entry(entry)


def test_validate_entry_rejects_grants_on_non_issue_driver() -> None:
    entry = _base_entry(allowed_tools=("Read",))
    with pytest.raises(DriverLoopValidationError) as exc:
        validate_entry(entry)
    assert exc.value.reason == "grants_on_non_issue_driver"


def test_validate_entry_rejects_add_dirs_on_non_issue_driver() -> None:
    entry = _base_entry(add_dirs=("/opt/checkouts/foo",))
    with pytest.raises(DriverLoopValidationError) as exc:
        validate_entry(entry)
    assert exc.value.reason == "grants_on_non_issue_driver"


def test_validate_entry_rejects_own_state_write_on_non_issue_driver() -> None:
    entry = _base_entry(own_state_write=True)
    with pytest.raises(DriverLoopValidationError) as exc:
        validate_entry(entry)
    assert exc.value.reason == "grants_on_non_issue_driver"


def test_validate_entry_accepts_base_read_tool_for_issue_driver() -> None:
    entry = _base_entry(
        name="alan-issue-driver", self_paced=False, interval_seconds=1800,
        allowed_tools=("Read", "Grep", "Glob"),
    )
    validate_entry(entry)


def test_validate_entry_accepts_multiple_web_fetch_domains() -> None:
    entry = _base_entry(
        name="alan-issue-driver", self_paced=False, interval_seconds=1800,
        web_fetch_domains=("github.com", "raw.githubusercontent.com"),
    )
    validate_entry(entry)


def test_validate_entry_accepts_git_read_only_tool_for_issue_driver() -> None:
    entry = _base_entry(
        name="alan-issue-driver", self_paced=False, interval_seconds=1800,
        allowed_tools=("Bash(git log:*)",),
    )
    validate_entry(entry)


def test_validate_entry_rejects_raw_gh_api() -> None:
    entry = _base_entry(
        name="alan-issue-driver", self_paced=False, interval_seconds=1800,
        allowed_tools=("Bash(gh api:*)",),
    )
    with pytest.raises(DriverLoopValidationError) as exc:
        validate_entry(entry)
    assert exc.value.reason == "raw_gh_api_forbidden"


def test_validate_entry_rejects_grants_on_a_second_issue_driver_name() -> None:
    entry = _base_entry(
        name="sample-issue-driver", self_paced=False, interval_seconds=1800,
        allowed_tools=("Read",),
    )
    with pytest.raises(DriverLoopValidationError) as exc:
        validate_entry(entry)
    assert exc.value.reason == "grants_on_non_issue_driver"


def test_validate_entry_rejects_gh_pr_merge() -> None:
    entry = _base_entry(
        name="alan-issue-driver", self_paced=False, interval_seconds=1800,
        allowed_tools=("Bash(gh pr merge:*)",),
    )
    with pytest.raises(DriverLoopValidationError) as exc:
        validate_entry(entry)
    assert exc.value.reason == "raw_gh_api_forbidden"


def test_validate_entry_rejects_unknown_tool_family() -> None:
    entry = _base_entry(
        name="alan-issue-driver", self_paced=False, interval_seconds=1800,
        allowed_tools=("Bash(git push:*)",),
    )
    with pytest.raises(DriverLoopValidationError) as exc:
        validate_entry(entry)
    assert exc.value.reason == "tool_not_in_allow_list"


@pytest.mark.parametrize(
    "tool",
    [
        "Bash(gh pr list:*)",
        "Bash(gh pr view:*)",
        "Bash(gh pr checks:*)",
        "Bash(gh run list:*)",
        "Bash(gh run view:*)",
        "Bash(gh label list:*)",
        "Bash(git merge-base:*)",
        "Bash(git rev-parse:*)",
    ],
    ids=[
        "gh-pr-list",
        "gh-pr-view",
        "gh-pr-checks",
        "gh-run-list",
        "gh-run-view",
        "gh-label-list",
        "git-merge-base",
        "git-rev-parse",
    ],
)
def test_validate_entry_accepts_issue_driver_read_only_command(tool: str) -> None:
    entry = _base_entry(
        name="alan-issue-driver", self_paced=False, interval_seconds=1800,
        allowed_tools=(tool,),
    )
    validate_entry(entry)


@pytest.mark.parametrize(
    "tool",
    ["Bash(gh pr:*)", "Bash(gh run:*)", "Bash(gh label:*)"],
    ids=["gh-pr-prefix", "gh-run-prefix", "gh-label-prefix"],
)
def test_validate_entry_rejects_broad_gh_prefix(tool: str) -> None:
    entry = _base_entry(
        name="alan-issue-driver", self_paced=False, interval_seconds=1800,
        allowed_tools=(tool,),
    )
    with pytest.raises(DriverLoopValidationError) as exc:
        validate_entry(entry)
    assert exc.value.reason == "tool_not_in_allow_list"


@pytest.mark.parametrize(
    "tool",
    [
        "Bash(gh pr edit:*)",
        "Bash(gh pr comment:*)",
        "Bash(gh pr review:*)",
        "Bash(gh pr close:*)",
        "Bash(gh run rerun:*)",
        "Bash(gh run cancel:*)",
        "Bash(gh label edit:*)",
        "Bash(gh label delete:*)",
        "Bash(git merge:*)",
    ],
    ids=[
        "gh-pr-edit",
        "gh-pr-comment",
        "gh-pr-review",
        "gh-pr-close",
        "gh-run-rerun",
        "gh-run-cancel",
        "gh-label-edit",
        "gh-label-delete",
        "git-merge",
    ],
)
def test_validate_entry_rejects_write_subcommand(tool: str) -> None:
    entry = _base_entry(
        name="alan-issue-driver", self_paced=False, interval_seconds=1800,
        allowed_tools=(tool,),
    )
    with pytest.raises(DriverLoopValidationError) as exc:
        validate_entry(entry)
    assert exc.value.reason == "tool_not_in_allow_list"


@pytest.mark.parametrize(
    "tool",
    ["Bash(gh repo delete:*)", "Bash(gh auth:*)"],
    ids=["gh-repo-delete", "gh-auth"],
)
def test_validate_entry_rejects_gh_repo_delete_and_auth(tool: str) -> None:
    entry = _base_entry(
        name="alan-issue-driver", self_paced=False, interval_seconds=1800,
        allowed_tools=(tool,),
    )
    with pytest.raises(DriverLoopValidationError) as exc:
        validate_entry(entry)
    assert exc.value.reason == "raw_gh_api_forbidden"


def test_validate_entry_accepts_webfetch_with_domain() -> None:
    entry = _base_entry(
        name="alan-issue-driver", self_paced=False, interval_seconds=1800,
        allowed_tools=("WebFetch(domain:github.com)",),
    )
    validate_entry(entry)


def test_validate_entry_rejects_webfetch_without_domain() -> None:
    entry = _base_entry(
        name="alan-issue-driver", self_paced=False, interval_seconds=1800,
        allowed_tools=("WebFetch",),
    )
    with pytest.raises(DriverLoopValidationError) as exc:
        validate_entry(entry)
    assert exc.value.reason == "tool_not_in_allow_list"


def test_validate_entry_rejects_invalid_web_fetch_domain() -> None:
    entry = _base_entry(
        name="alan-issue-driver", self_paced=False, interval_seconds=1800,
        web_fetch_domains=("not a domain",),
    )
    with pytest.raises(DriverLoopValidationError) as exc:
        validate_entry(entry)
    assert exc.value.reason == "web_fetch_domain_invalid"


def test_validate_entry_rejects_cwd_equal_to_alan_home() -> None:
    entry = _base_entry(cwd="~/.alan")
    with pytest.raises(DriverLoopValidationError) as exc:
        validate_entry(entry)
    assert exc.value.reason == "cwd_inside_alan_home"


def test_validate_entry_rejects_cwd_inside_alan_state() -> None:
    entry = _base_entry(cwd="~/.alan/state/foo")
    with pytest.raises(DriverLoopValidationError) as exc:
        validate_entry(entry)
    assert exc.value.reason == "cwd_inside_alan_home"


def test_validate_entry_rejects_cwd_inside_alan_secrets() -> None:
    entry = _base_entry(cwd="~/.alan/secrets")
    with pytest.raises(DriverLoopValidationError) as exc:
        validate_entry(entry)
    assert exc.value.reason == "cwd_inside_alan_home"


def test_validate_entry_rejects_relative_cwd() -> None:
    entry = _base_entry(cwd="relative/path")
    with pytest.raises(DriverLoopValidationError) as exc:
        validate_entry(entry)
    assert exc.value.reason == "cwd_not_absolute"


def test_validate_entry_rejects_add_dir_inside_alan_home() -> None:
    entry = _base_entry(
        name="alan-issue-driver", self_paced=False, interval_seconds=1800,
        add_dirs=("~/.alan/somewhere",),
    )
    with pytest.raises(DriverLoopValidationError) as exc:
        validate_entry(entry)
    assert exc.value.reason == "add_dir_inside_alan_home"


def test_validate_entry_accepts_add_dir_outside_alan_home() -> None:
    entry = _base_entry(
        name="alan-issue-driver", self_paced=False, interval_seconds=1800,
        add_dirs=("/opt/checkouts/some-clone",),
    )
    validate_entry(entry)


def test_driver_loop_entry_state_dir_uses_driver_loop_state_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_DRIVER_LOOP_STATE_ROOT", str(tmp_path))
    entry = _base_entry(name="alan-issue-driver")
    assert entry.state_dir == tmp_path / "alan-issue-driver"


