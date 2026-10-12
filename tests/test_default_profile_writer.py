from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from alan import default_profile, default_profile_writer
from alan.default_profile import DefaultProfile, ProfileRequirement, Source
from alan.default_profile_writer import (
    GATE_APP_ID,
    ProfileWriterError,
    WriteOutcome,
    build_profile,
    main,
    render_profile,
    write_profile,
)
from alan.engine_config_validation import Posture

OWNER = "example-owner"
REPO = "example-repo"


def _engines(tmp_path: Path) -> Path:
    return tmp_path / "engines"


def _installed(tmp_path: Path) -> Path:
    return _engines(tmp_path) / default_profile.PROFILE_FILE_NAME


def _resolved(tmp_path: Path) -> default_profile.Resolved:
    return default_profile.resolve(_engines(tmp_path), tmp_path / "absent_owner.yaml")


def _write(tmp_path: Path, owner: str = OWNER, repo: str = REPO) -> WriteOutcome:
    return write_profile(_engines(tmp_path), owner, repo)


def _argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owner: str = OWNER, repo: str = REPO
) -> list[str]:
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(_engines(tmp_path)))
    return ["--owner", owner, "--repo", repo]


def _snapshot(path: Path) -> tuple[bytes, int]:
    return path.read_bytes(), path.stat().st_mtime_ns


def _age(path: Path) -> None:
    os.utime(path, ns=(1_000_000_000, 1_000_000_000))


def test_a_written_profile_resolves_to_the_users_copy_as_the_installed_profile(
    tmp_path: Path,
) -> None:
    assert _write(tmp_path) is WriteOutcome.written
    resolved = _resolved(tmp_path)
    assert resolved.source is Source.profile
    assert resolved.path == _installed(tmp_path)
    assert resolved.identity_fields() == {"owner": OWNER, "repo": REPO}


_EXPECTED_FIELDS: list[tuple[str, Any]] = [
    ("posture", Posture.propose),
    ("authoritative_checks", frozenset({"gate"})),
    ("authoritative_check_apps", {"gate": 15368}),
    ("enforce_admins", True),
    ("requirements", [ProfileRequirement.reviewer_credential]),
    ("dependency_sweep", False),
    ("triage_sweep", False),
    ("main_ci_workflow", "tests.yml"),
    ("deploy_workflow", None),
    ("code_quality", None),
]


@pytest.mark.parametrize(
    ("field", "expected"), _EXPECTED_FIELDS, ids=[name for name, _ in _EXPECTED_FIELDS]
)
def test_a_written_profile_carries_the_default_engine_field(
    tmp_path: Path, field: str, expected: Any
) -> None:
    _write(tmp_path)
    assert getattr(_resolved(tmp_path).profile, field) == expected


def test_the_gate_check_is_pinned_to_an_integer_app_id(tmp_path: Path) -> None:
    _write(tmp_path)
    apps = _resolved(tmp_path).profile.authoritative_check_apps
    assert apps == {"gate": GATE_APP_ID}
    assert type(apps["gate"]) is int


def test_the_resolved_forge_fields_enforce_admins_and_pin_the_gate_app(tmp_path: Path) -> None:
    _write(tmp_path)
    forge = _resolved(tmp_path).forge_fields()
    assert forge["authoritative_check_apps"] == {"gate": 15368}
    assert forge["enforce_admins"] is True
    assert forge["authoritative_checks"] == frozenset({"gate"})


def test_the_resolved_engine_fields_name_the_main_ci_workflow_and_no_code_quality_block(
    tmp_path: Path,
) -> None:
    _write(tmp_path)
    fields = _resolved(tmp_path).engine_fields()
    assert fields["ci"] == {"main_ci_workflow": "tests.yml", "deploy_workflow": None}
    assert fields["code_quality"] is None
    assert fields["posture"] is Posture.propose
    assert fields["dependency_sweep"] == {"enabled": False, "enable_python_vuln_scan": False}


def test_the_rendered_text_names_no_deploy_workflow_and_no_code_quality_block(
    tmp_path: Path,
) -> None:
    _write(tmp_path)
    keys = set(yaml.safe_load(_installed(tmp_path).read_text(encoding="utf-8")))
    assert "deploy_workflow" not in keys
    assert "code_quality" not in keys


_VALID_PAIRS = [
    ("a", "b"),
    ("A1-b2", ".github"),
    ("x" * 39, "y" * 100),
    ("o", "repo.name_x-1"),
    ("Example-Owner", "Example-Repo"),
]


@pytest.mark.parametrize(("owner", "repo"), _VALID_PAIRS)
def test_the_rendered_output_always_passes_validation(
    tmp_path: Path, owner: str, repo: str
) -> None:
    text = render_profile(build_profile(owner, repo))
    assert DefaultProfile.model_validate(yaml.safe_load(text)) == build_profile(owner, repo)
    _write(tmp_path, owner, repo)
    assert _resolved(tmp_path).identity_fields() == {"owner": owner, "repo": repo}


def test_rendering_is_deterministic_and_sorts_set_fields() -> None:
    profile = DefaultProfile(
        owner=OWNER,
        repo=REPO,
        posture=Posture.propose,
        authoritative_checks=frozenset({"zeta", "alpha", "mid"}),
        codeowners_repos=frozenset({"b-owner/repo", "a-owner/repo"}),
    )
    first = render_profile(profile)
    assert first == render_profile(profile)
    loaded = yaml.safe_load(first)
    assert loaded["authoritative_checks"] == ["alpha", "mid", "zeta"]
    assert loaded["codeowners_repos"] == ["a-owner/repo", "b-owner/repo"]
    assert list(loaded) == sorted(loaded)


def test_the_profile_is_created_with_its_parent_directory(tmp_path: Path) -> None:
    assert not _engines(tmp_path).exists()
    _write(tmp_path)
    assert sorted(path.name for path in _engines(tmp_path).iterdir()) == [
        default_profile.PROFILE_FILE_NAME
    ]


def test_a_failed_replace_leaves_neither_a_profile_nor_a_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse_replace(self: Path, target: Path) -> Path:
        raise OSError("replace refused")

    monkeypatch.setattr(Path, "replace", refuse_replace)
    with pytest.raises(OSError, match="replace refused"):
        _write(tmp_path)
    assert list(_engines(tmp_path).iterdir()) == []


def test_a_failed_write_reports_the_error_and_creates_no_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse_write(path: Path, text: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(default_profile_writer, "write_text_atomic", refuse_write)
    code = main(_argv(tmp_path, monkeypatch))
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert captured.err == "default_profile_writer: disk full\n"
    assert not _installed(tmp_path).exists()


def test_a_profile_naming_a_different_repo_is_refused_and_left_byte_identical(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "other-owner", "other-repo")
    _age(_installed(tmp_path))
    before = _snapshot(_installed(tmp_path))
    with pytest.raises(ProfileWriterError, match="different repository"):
        _write(tmp_path)
    assert _snapshot(_installed(tmp_path)) == before


def test_a_profile_naming_the_same_owner_but_another_repo_is_refused(tmp_path: Path) -> None:
    _write(tmp_path, OWNER, "another-repo")
    with pytest.raises(ProfileWriterError, match="different repository"):
        _write(tmp_path)
    assert _resolved(tmp_path).profile.repo == "another-repo"


def test_the_refusal_exits_non_zero_and_prints_no_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, "other-owner", "other-repo")
    code = main(_argv(tmp_path, monkeypatch))
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert "different repository" in captured.err


def test_an_identical_profile_is_reported_already_and_not_rewritten(tmp_path: Path) -> None:
    _write(tmp_path)
    _age(_installed(tmp_path))
    before = _snapshot(_installed(tmp_path))
    assert _write(tmp_path) is WriteOutcome.already
    assert _snapshot(_installed(tmp_path)) == before


def test_a_profile_differing_only_in_owner_and_repo_case_is_reported_already(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "Example-Owner", "Example-Repo")
    _age(_installed(tmp_path))
    before = _snapshot(_installed(tmp_path))
    assert _write(tmp_path) is WriteOutcome.already
    assert _snapshot(_installed(tmp_path)) == before


def test_a_profile_that_gained_a_code_quality_block_is_reported_already(tmp_path: Path) -> None:
    _write(tmp_path)
    loaded = yaml.safe_load(_installed(tmp_path).read_text(encoding="utf-8"))
    loaded["code_quality"] = {"organization": "example-org", "project_key": "example-key"}
    _installed(tmp_path).write_text(yaml.safe_dump(loaded), encoding="utf-8")
    _age(_installed(tmp_path))
    before = _snapshot(_installed(tmp_path))
    assert _write(tmp_path) is WriteOutcome.already
    assert _snapshot(_installed(tmp_path)) == before


def _install_with(tmp_path: Path, **changes: Any) -> None:
    _write(tmp_path)
    loaded = yaml.safe_load(_installed(tmp_path).read_text(encoding="utf-8"))
    loaded.update(changes)
    _installed(tmp_path).write_text(yaml.safe_dump(loaded), encoding="utf-8")
    _age(_installed(tmp_path))


def test_a_written_profile_lists_only_the_reviewer_credential_requirement(tmp_path: Path) -> None:
    _write(tmp_path)
    loaded = yaml.safe_load(_installed(tmp_path).read_text(encoding="utf-8"))
    assert loaded["requirements"] == ["reviewer_credential"]


@pytest.mark.parametrize(
    "requirements",
    [["reviewer_credential", "code_quality"], ["code_quality", "reviewer_credential"]],
    ids=["appended", "prepended"],
)
def test_a_profile_that_also_lists_the_code_quality_requirement_is_reported_already(
    tmp_path: Path, requirements: list[str]
) -> None:
    _install_with(tmp_path, requirements=requirements)
    before = _snapshot(_installed(tmp_path))
    assert _write(tmp_path) is WriteOutcome.already
    assert _snapshot(_installed(tmp_path)) == before


def test_an_extra_code_quality_requirement_does_not_hide_another_drifted_field(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_with(
        tmp_path, requirements=["reviewer_credential", "code_quality"], posture="autonomous"
    )
    before = _snapshot(_installed(tmp_path))
    assert _write(tmp_path) is WriteOutcome.kept
    assert _snapshot(_installed(tmp_path)) == before
    assert "differs in: posture\n" in capsys.readouterr().err


@pytest.mark.parametrize(
    "requirements",
    [["code_quality"], []],
    ids=["only-code-quality", "none"],
)
def test_a_profile_missing_the_reviewer_credential_requirement_is_kept_and_names_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], requirements: list[str]
) -> None:
    _install_with(tmp_path, requirements=requirements)
    assert _write(tmp_path) is WriteOutcome.kept
    assert "differs in: requirements\n" in capsys.readouterr().err


def test_a_drifted_profile_for_the_same_repo_is_kept_and_its_fields_are_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path)
    loaded = yaml.safe_load(_installed(tmp_path).read_text(encoding="utf-8"))
    loaded["posture"] = "autonomous"
    loaded["enforce_admins"] = False
    _installed(tmp_path).write_text(yaml.safe_dump(loaded), encoding="utf-8")
    _age(_installed(tmp_path))
    before = _snapshot(_installed(tmp_path))
    assert _write(tmp_path) is WriteOutcome.kept
    assert _snapshot(_installed(tmp_path)) == before
    assert "differs in: enforce_admins, posture" in capsys.readouterr().err


_UNUSABLE_EXISTING: list[tuple[str, bytes]] = [
    ("invalid-yaml", b"owner: [unclosed\n"),
    ("a-list", b"- one\n- two\n"),
    ("an-empty-file", b""),
    ("missing-fields", b"owner: example-owner\n"),
    ("an-unknown-key", b"owner: a\nrepo: b\nposture: propose\nsurprise: 1\n"),
    ("undecodable-bytes", b"\xff\xfe\x00"),
]


@pytest.mark.parametrize(
    "content",
    [content for _, content in _UNUSABLE_EXISTING],
    ids=[name for name, _ in _UNUSABLE_EXISTING],
)
def test_an_unusable_installed_profile_is_refused_and_left_byte_identical(
    tmp_path: Path, content: bytes
) -> None:
    _engines(tmp_path).mkdir()
    _installed(tmp_path).write_bytes(content)
    _age(_installed(tmp_path))
    before = _snapshot(_installed(tmp_path))
    with pytest.raises(ValueError):
        _write(tmp_path)
    assert _snapshot(_installed(tmp_path)) == before


def test_an_installed_profile_that_is_a_directory_fails_with_an_os_error(tmp_path: Path) -> None:
    _installed(tmp_path).mkdir(parents=True)
    with pytest.raises(OSError):
        _write(tmp_path)


_INVALID_INPUT: list[tuple[str, str, str]] = [
    ("empty-owner", "", REPO),
    ("leading-dash-owner", "-bad", REPO),
    ("underscore-owner", "a_b", REPO),
    ("overlong-owner", "a" * 40, REPO),
    ("traversal-owner", "../x", REPO),
    ("space-owner", "x y", REPO),
    ("empty-repo", OWNER, ""),
    ("dot-dot-repo", OWNER, ".."),
    ("space-repo", OWNER, "a b"),
    ("overlong-repo", OWNER, "x" * 101),
    ("slash-repo", OWNER, "a/b"),
]


@pytest.mark.parametrize(
    ("owner", "repo"),
    [(owner, repo) for _, owner, repo in _INVALID_INPUT],
    ids=[name for name, _, _ in _INVALID_INPUT],
)
def test_invalid_owner_or_repo_is_rejected_without_creating_a_file(
    tmp_path: Path, owner: str, repo: str
) -> None:
    with pytest.raises(ProfileWriterError) as caught:
        _write(tmp_path, owner, repo)
    assert str(caught.value) == "invalid owner or repo"
    assert not _engines(tmp_path).exists()


def test_invalid_input_exits_non_zero_without_echoing_the_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(_argv(tmp_path, monkeypatch, owner="not a valid owner value"))
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert captured.err == "default_profile_writer: invalid owner or repo\n"
    assert not _engines(tmp_path).exists()


@pytest.mark.parametrize("outcome", ["written", "already"])
def test_main_prints_exactly_the_outcome_word_and_exits_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    outcome: str,
) -> None:
    argv = _argv(tmp_path, monkeypatch)
    if outcome == "already":
        assert main(argv) == 0
        capsys.readouterr()
    assert main(argv) == 0
    captured = capsys.readouterr()
    assert captured.out == f"{outcome}\n"
    assert captured.err == ""


def test_main_reports_kept_on_stdout_with_the_drift_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path)
    loaded = yaml.safe_load(_installed(tmp_path).read_text(encoding="utf-8"))
    loaded["dependency_sweep"] = True
    _installed(tmp_path).write_text(yaml.safe_dump(loaded), encoding="utf-8")
    capsys.readouterr()
    code = main(_argv(tmp_path, monkeypatch))
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == "kept\n"
    assert "dependency_sweep" in captured.err


def test_an_engines_root_with_a_parent_component_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "a" / ".." / "engines"))
    code = main(["--owner", OWNER, "--repo", REPO])
    assert code == 1
    assert "default_profile_writer:" in capsys.readouterr().err
    assert not (tmp_path / "engines").exists()


@pytest.mark.parametrize("missing", ["--owner", "--repo"])
def test_main_requires_every_argument(missing: str) -> None:
    argv = {"--owner": OWNER, "--repo": REPO}
    del argv[missing]
    flat = [item for pair in argv.items() for item in pair]
    with pytest.raises(SystemExit) as caught:
        main(flat)
    assert caught.value.code == 2


def test_main_no_longer_accepts_an_engines_directory_argument(tmp_path: Path) -> None:
    argv = ["--engines-root", str(_engines(tmp_path)), "--owner", OWNER, "--repo", REPO]
    with pytest.raises(SystemExit) as caught:
        main(argv)
    assert caught.value.code == 2
    assert not _engines(tmp_path).exists()


def test_main_without_an_override_writes_under_the_home_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ALAN_ENGINES_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    code = main(["--owner", OWNER, "--repo", REPO])
    assert code == 0
    assert capsys.readouterr().out == "written\n"
    installed = tmp_path / ".alan" / "engines" / default_profile.PROFILE_FILE_NAME
    assert installed.is_file()


def test_the_writer_never_reads_or_writes_the_shared_default_profile_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "unrelated"))
    _write(tmp_path)
    assert not (tmp_path / "unrelated").exists()
