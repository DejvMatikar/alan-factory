from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from alan import default_profile, default_profile_writer
from alan.default_profile import CodeQualityProfile, ProfileRequirement
from alan.default_profile_writer import (
    ProfileWriterError,
    WriteOutcome,
    installed_code_quality,
    requires_code_quality,
    write_code_quality,
    write_profile,
)

OWNER = "example-owner"
REPO = "example-repo"
ORGANIZATION = "example-org"
PROJECT_KEY = "example-org_example-repo"


def _engines(tmp_path: Path) -> Path:
    return tmp_path / "engines"


def _installed(tmp_path: Path) -> Path:
    return _engines(tmp_path) / default_profile.PROFILE_FILE_NAME


def _seed(tmp_path: Path, owner: str = OWNER, repo: str = REPO) -> None:
    write_profile(_engines(tmp_path), owner, repo)
    os.utime(_installed(tmp_path), ns=(1_000_000_000, 1_000_000_000))


def _snapshot(path: Path) -> tuple[bytes, int]:
    return path.read_bytes(), path.stat().st_mtime_ns


def _add(
    tmp_path: Path,
    organization: str = ORGANIZATION,
    project_key: str = PROJECT_KEY,
    *,
    owner: str = OWNER,
    repo: str = REPO,
    calls: list[str] | None = None,
) -> WriteOutcome:
    seen = calls if calls is not None else []
    return write_code_quality(
        _engines(tmp_path),
        owner,
        repo,
        organization,
        project_key,
        before_write=lambda: seen.append("before"),
    )


def _resolved(tmp_path: Path) -> default_profile.Resolved:
    return default_profile.resolve(_engines(tmp_path), tmp_path / "absent_owner.yaml")


def test_the_block_is_added_to_the_installed_profile_for_the_same_repository(
    tmp_path: Path,
) -> None:
    _seed(tmp_path)
    assert _add(tmp_path) is WriteOutcome.written
    profile = _resolved(tmp_path).profile
    assert profile.code_quality == CodeQualityProfile(
        organization=ORGANIZATION, project_key=PROJECT_KEY
    )
    assert (profile.owner, profile.repo) == (OWNER, REPO)


def test_every_other_field_of_the_installed_profile_is_kept(tmp_path: Path) -> None:
    _seed(tmp_path)
    before = yaml.safe_load(_installed(tmp_path).read_text(encoding="utf-8"))
    _add(tmp_path)
    after = yaml.safe_load(_installed(tmp_path).read_text(encoding="utf-8"))
    assert after.pop("code_quality") == {"organization": ORGANIZATION, "project_key": PROJECT_KEY}
    assert after == before


def test_a_drifted_profile_keeps_its_drift_and_gains_the_block(tmp_path: Path) -> None:
    _seed(tmp_path)
    loaded = yaml.safe_load(_installed(tmp_path).read_text(encoding="utf-8"))
    loaded["posture"] = "autonomous"
    _installed(tmp_path).write_text(yaml.safe_dump(loaded), encoding="utf-8")
    assert _add(tmp_path) is WriteOutcome.written
    profile = _resolved(tmp_path).profile
    assert profile.posture.value == "autonomous"
    assert profile.code_quality is not None


def test_the_resolved_engine_carries_the_block_with_the_sonarcloud_host_and_token_name(
    tmp_path: Path,
) -> None:
    _seed(tmp_path)
    _add(tmp_path)
    assert _resolved(tmp_path).engine_fields()["code_quality"] == {
        "organization": ORGANIZATION,
        "project_key": PROJECT_KEY,
        "host_url": default_profile.SONAR_HOST_URL,
        "token_env_var": default_profile.SONAR_TOKEN_ENV,
    }


def test_a_different_block_replaces_the_one_in_place(tmp_path: Path) -> None:
    _seed(tmp_path)
    _add(tmp_path)
    assert _add(tmp_path, "other-org", "other-org_other-key") is WriteOutcome.written
    block = _resolved(tmp_path).profile.code_quality
    assert block == CodeQualityProfile(organization="other-org", project_key="other-org_other-key")


def test_changing_only_the_project_key_replaces_the_block(tmp_path: Path) -> None:
    _seed(tmp_path)
    _add(tmp_path)
    assert _add(tmp_path, ORGANIZATION, "renamed-key") is WriteOutcome.written
    block = _resolved(tmp_path).profile.code_quality
    assert block is not None
    assert (block.organization, block.project_key) == (ORGANIZATION, "renamed-key")


def test_the_same_block_is_reported_already_and_the_file_is_left_alone(tmp_path: Path) -> None:
    _seed(tmp_path)
    _add(tmp_path)
    os.utime(_installed(tmp_path), ns=(1_000_000_000, 1_000_000_000))
    before = _snapshot(_installed(tmp_path))
    calls: list[str] = []
    assert _add(tmp_path, calls=calls) is WriteOutcome.already
    assert _snapshot(_installed(tmp_path)) == before
    assert calls == []


def test_a_profile_differing_only_in_repository_case_is_the_same_repository(
    tmp_path: Path,
) -> None:
    _seed(tmp_path, "Example-Owner", "Example-Repo")
    assert _add(tmp_path) is WriteOutcome.written
    assert _resolved(tmp_path).profile.code_quality is not None


@pytest.mark.parametrize(
    ("owner", "repo"),
    [("other-owner", "other-repo"), (OWNER, "another-repo"), ("another-owner", REPO)],
    ids=["both", "repo", "owner"],
)
def test_a_profile_for_another_repository_is_refused_and_left_byte_identical(
    tmp_path: Path, owner: str, repo: str
) -> None:
    _seed(tmp_path, owner, repo)
    before = _snapshot(_installed(tmp_path))
    calls: list[str] = []
    with pytest.raises(ProfileWriterError, match="different repository"):
        _add(tmp_path, calls=calls)
    assert _snapshot(_installed(tmp_path)) == before
    assert calls == []


def test_a_missing_profile_is_refused_and_nothing_is_created(tmp_path: Path) -> None:
    calls: list[str] = []
    with pytest.raises(ProfileWriterError, match="no installed profile"):
        _add(tmp_path, calls=calls)
    assert not _engines(tmp_path).exists()
    assert calls == []


@pytest.mark.parametrize(
    "content",
    [b"owner: [unclosed\n", b"- one\n- two\n", b"", b"owner: example-owner\n", b"\xff\xfe\x00"],
    ids=["invalid-yaml", "a-list", "empty", "missing-fields", "undecodable"],
)
def test_an_unusable_installed_profile_is_refused_and_left_byte_identical(
    tmp_path: Path, content: bytes
) -> None:
    _engines(tmp_path).mkdir()
    _installed(tmp_path).write_bytes(content)
    os.utime(_installed(tmp_path), ns=(1_000_000_000, 1_000_000_000))
    before = _snapshot(_installed(tmp_path))
    with pytest.raises(ValueError):
        _add(tmp_path)
    assert _snapshot(_installed(tmp_path)) == before


@pytest.mark.parametrize(
    ("organization", "project_key"),
    [("", PROJECT_KEY), (ORGANIZATION, "")],
    ids=["empty-organization", "empty-project-key"],
)
def test_an_empty_organization_or_project_key_is_refused_and_the_profile_kept(
    tmp_path: Path, organization: str, project_key: str
) -> None:
    _seed(tmp_path)
    before = _snapshot(_installed(tmp_path))
    with pytest.raises(ProfileWriterError, match="invalid code quality organization"):
        _add(tmp_path, organization, project_key)
    assert _snapshot(_installed(tmp_path)) == before


def test_an_invalid_owner_or_repo_is_refused_before_the_profile_is_touched(
    tmp_path: Path,
) -> None:
    _seed(tmp_path)
    before = _snapshot(_installed(tmp_path))
    with pytest.raises(ProfileWriterError, match="invalid owner or repo"):
        _add(tmp_path, owner="not a valid owner")
    assert _snapshot(_installed(tmp_path)) == before


def test_an_engines_root_with_a_parent_component_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ProfileWriterError, match="must not contain"):
        write_code_quality(
            tmp_path / "a" / ".." / "engines",
            OWNER,
            REPO,
            ORGANIZATION,
            PROJECT_KEY,
            before_write=lambda: None,
        )
    assert not (tmp_path / "engines").exists()


def test_the_callback_runs_once_before_the_file_changes(tmp_path: Path) -> None:
    _seed(tmp_path)
    original = _installed(tmp_path).read_bytes()
    seen: list[bytes] = []

    def before_write() -> None:
        seen.append(_installed(tmp_path).read_bytes())

    outcome = write_code_quality(
        _engines(tmp_path), OWNER, REPO, ORGANIZATION, PROJECT_KEY, before_write=before_write
    )
    assert outcome is WriteOutcome.written
    assert seen == [original]
    assert _installed(tmp_path).read_bytes() != original


def test_a_failing_callback_leaves_the_profile_byte_identical(tmp_path: Path) -> None:
    _seed(tmp_path)
    before = _snapshot(_installed(tmp_path))

    def refuse() -> None:
        raise OSError("marker refused")

    with pytest.raises(OSError, match="marker refused"):
        write_code_quality(
            _engines(tmp_path), OWNER, REPO, ORGANIZATION, PROJECT_KEY, before_write=refuse
        )
    assert _snapshot(_installed(tmp_path)) == before


def test_a_failed_write_leaves_the_profile_byte_identical_and_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path)
    before = _snapshot(_installed(tmp_path))

    def refuse_write(path: Path, text: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(default_profile_writer, "write_text_atomic", refuse_write)
    with pytest.raises(OSError, match="disk full"):
        _add(tmp_path)
    assert _snapshot(_installed(tmp_path)) == before
    assert [path.name for path in _engines(tmp_path).iterdir()] == [
        default_profile.PROFILE_FILE_NAME
    ]


def test_the_outcome_table_still_reports_a_profile_that_gained_the_block_as_already(
    tmp_path: Path,
) -> None:
    _seed(tmp_path)
    _add(tmp_path)
    os.utime(_installed(tmp_path), ns=(1_000_000_000, 1_000_000_000))
    before = _snapshot(_installed(tmp_path))
    assert write_profile(_engines(tmp_path), OWNER, REPO) is WriteOutcome.already
    assert _snapshot(_installed(tmp_path)) == before


def test_the_installed_block_is_none_until_one_is_written(tmp_path: Path) -> None:
    assert installed_code_quality(_engines(tmp_path), OWNER, REPO) is None
    _seed(tmp_path)
    assert installed_code_quality(_engines(tmp_path), OWNER, REPO) is None
    _add(tmp_path)
    assert installed_code_quality(_engines(tmp_path), OWNER, REPO) == CodeQualityProfile(
        organization=ORGANIZATION, project_key=PROJECT_KEY
    )


def test_the_installed_block_of_another_repository_is_not_reported(tmp_path: Path) -> None:
    _seed(tmp_path, "other-owner", "other-repo")
    _add(tmp_path, owner="other-owner", repo="other-repo")
    assert installed_code_quality(_engines(tmp_path), OWNER, REPO) is None


def test_reading_the_installed_block_never_writes(tmp_path: Path) -> None:
    _seed(tmp_path)
    before = _snapshot(_installed(tmp_path))
    installed_code_quality(_engines(tmp_path), OWNER, REPO)
    assert _snapshot(_installed(tmp_path)) == before


def test_an_unusable_installed_profile_is_an_error_when_reading_the_block(
    tmp_path: Path,
) -> None:
    _engines(tmp_path).mkdir()
    _installed(tmp_path).write_bytes(b"owner: [unclosed\n")
    with pytest.raises(ProfileWriterError):
        installed_code_quality(_engines(tmp_path), OWNER, REPO)


def _require_code_quality(tmp_path: Path) -> None:
    loaded = yaml.safe_load(_installed(tmp_path).read_text(encoding="utf-8"))
    loaded["requirements"] = [*loaded["requirements"], "code_quality"]
    _installed(tmp_path).write_text(yaml.safe_dump(loaded), encoding="utf-8")


def test_adding_the_block_never_adds_the_code_quality_requirement(tmp_path: Path) -> None:
    _seed(tmp_path)
    assert _add(tmp_path) is WriteOutcome.written
    assert _resolved(tmp_path).profile.requirements == [ProfileRequirement.reviewer_credential]
    assert requires_code_quality(_engines(tmp_path), OWNER, REPO) is False


def test_adding_the_block_keeps_a_code_quality_requirement_the_profile_already_lists(
    tmp_path: Path,
) -> None:
    _seed(tmp_path)
    _require_code_quality(tmp_path)
    assert _add(tmp_path) is WriteOutcome.written
    assert _resolved(tmp_path).profile.requirements == [
        ProfileRequirement.reviewer_credential,
        ProfileRequirement.code_quality,
    ]
    assert requires_code_quality(_engines(tmp_path), OWNER, REPO) is True


def test_a_setup_written_profile_does_not_require_code_quality(tmp_path: Path) -> None:
    _seed(tmp_path)
    assert requires_code_quality(_engines(tmp_path), OWNER, REPO) is False


def test_a_profile_listing_the_requirement_requires_code_quality(tmp_path: Path) -> None:
    _seed(tmp_path)
    _require_code_quality(tmp_path)
    assert requires_code_quality(_engines(tmp_path), OWNER, REPO) is True


def test_a_missing_profile_and_a_profile_for_another_repository_count_as_requiring_it(
    tmp_path: Path,
) -> None:
    assert requires_code_quality(_engines(tmp_path), OWNER, REPO) is True
    _seed(tmp_path, "other-owner", "other-repo")
    assert requires_code_quality(_engines(tmp_path), OWNER, REPO) is True


def test_reading_whether_code_quality_is_required_never_writes(tmp_path: Path) -> None:
    _seed(tmp_path)
    before = _snapshot(_installed(tmp_path))
    requires_code_quality(_engines(tmp_path), OWNER, REPO)
    assert _snapshot(_installed(tmp_path)) == before


def test_an_unusable_installed_profile_is_an_error_when_reading_the_requirement(
    tmp_path: Path,
) -> None:
    _engines(tmp_path).mkdir()
    _installed(tmp_path).write_bytes(b"owner: [unclosed\n")
    with pytest.raises(ProfileWriterError):
        requires_code_quality(_engines(tmp_path), OWNER, REPO)
