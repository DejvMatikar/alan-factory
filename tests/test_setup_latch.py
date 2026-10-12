from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from alan import default_profile, default_profile_writer, engine_preflight, setup_latch

OWNER = "example-owner"
REPO = "example-repo"
SLUG = f"{OWNER}/{REPO}"
ORG = "example-org"
KEY = "example-org_example-repo"


@pytest.fixture
def engines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "engines"
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(root))
    return root


def _profile(
    root: Path, *, block: tuple[str, str] | None = (ORG, KEY), required: bool = False
) -> None:
    default_profile_writer.write_profile(root, OWNER, REPO)
    if required:
        path = root / default_profile.PROFILE_FILE_NAME
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        loaded["requirements"] = [*loaded["requirements"], "code_quality"]
        path.write_text(yaml.safe_dump(loaded), encoding="utf-8")
    if block is not None:
        default_profile_writer.write_code_quality(
            root, OWNER, REPO, block[0], block[1], before_write=lambda: None
        )


def _latch_file(root: Path) -> Path:
    return root / engine_preflight.CODE_QUALITY_LATCH_FILE


def _recorded(root: Path) -> dict[str, object]:
    held: dict[str, object] = json.loads(_latch_file(root).read_text(encoding="utf-8"))
    return held


def test_a_profile_block_without_a_latch_is_not_latched(engines: Path) -> None:
    _profile(engines)
    assert setup_latch.latch_state(SLUG) == setup_latch.STATE_NOT_LATCHED


def test_no_profile_and_a_profile_without_a_block_name_no_project(engines: Path) -> None:
    assert setup_latch.latch_state(SLUG) == setup_latch.STATE_NO_PROJECT
    _profile(engines, block=None)
    assert setup_latch.latch_state(SLUG) == setup_latch.STATE_NO_PROJECT


def test_a_profile_for_another_repository_names_no_project(engines: Path) -> None:
    _profile(engines)
    assert setup_latch.latch_state("someone-else/other-repo") == setup_latch.STATE_NO_PROJECT


def test_an_unreadable_profile_names_no_project(engines: Path) -> None:
    engines.mkdir(parents=True)
    (engines / "default_profile.yaml").write_text("not: [valid", encoding="utf-8")
    assert setup_latch.latch_state(SLUG) == setup_latch.STATE_NO_PROJECT


def test_writing_the_latch_records_the_profiles_organization_and_project_key(
    engines: Path,
) -> None:
    _profile(engines, block=("changed-org", "changed-org_changed-key"))
    setup_latch.write_latch(SLUG)
    held = _recorded(engines)
    assert held["organization"] == "changed-org"
    assert held["project_key"] == "changed-org_changed-key"
    assert setup_latch.latch_state(SLUG) == setup_latch.STATE_LATCHED
    assert _latch_file(engines).stat().st_mode & 0o777 == 0o600


def test_a_latch_for_another_project_is_not_latched_until_rewritten(engines: Path) -> None:
    _profile(engines)
    engine_preflight.write_code_quality_latch("other-org", "other-org_other")
    assert setup_latch.latch_state(SLUG) == setup_latch.STATE_NOT_LATCHED
    setup_latch.write_latch(SLUG)
    assert setup_latch.latch_state(SLUG) == setup_latch.STATE_LATCHED


def test_a_corrupt_latch_file_is_not_latched(engines: Path) -> None:
    _profile(engines)
    _latch_file(engines).write_text("{not json", encoding="utf-8")
    assert setup_latch.latch_state(SLUG) == setup_latch.STATE_NOT_LATCHED


def test_a_latch_that_cannot_be_read_is_not_latched(
    engines: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _profile(engines)

    def _denied() -> engine_preflight.CodeQualityLatch:
        raise PermissionError("denied")

    monkeypatch.setattr(engine_preflight, "read_code_quality_latch", _denied)
    assert setup_latch.latch_state(SLUG) == setup_latch.STATE_NOT_LATCHED


def test_writing_without_a_project_is_refused_and_writes_nothing(engines: Path) -> None:
    _profile(engines, block=None)
    with pytest.raises(setup_latch.LatchRefused, match="run the sonar step first"):
        setup_latch.write_latch(SLUG)
    assert not _latch_file(engines).exists()


def test_a_latch_that_cannot_be_written_is_refused(
    engines: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _profile(engines)

    def _denied(organization: str, project_key: str) -> engine_preflight.CodeQualityLatch:
        raise PermissionError("denied")

    monkeypatch.setattr(engine_preflight, "write_code_quality_latch", _denied)
    with pytest.raises(setup_latch.LatchRefused, match="could not be written"):
        setup_latch.write_latch(SLUG)


def test_the_state_command_prints_the_state_and_exits_by_it(
    engines: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert setup_latch.main(["state", "--engine", SLUG]) == 1
    assert capsys.readouterr().out.strip() == "no-project"
    _profile(engines)
    assert setup_latch.main(["state", "--engine", SLUG]) == 2
    assert capsys.readouterr().out.strip() == "not-latched"
    assert setup_latch.main(["write", "--engine", SLUG]) == 0
    assert setup_latch.main(["state", "--engine", SLUG]) == 0
    assert capsys.readouterr().out.strip() == "latched"


def test_the_write_command_reports_a_refusal_on_standard_error(
    engines: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert setup_latch.main(["write", "--engine", SLUG]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "run the sonar step first" in captured.err


@pytest.mark.parametrize("engine", ["no-slash", "bad owner/repo", "owner/", "/repo", "../x/y"])
def test_an_engine_that_is_not_an_owner_repo_slug_names_no_project_and_writes_nothing(
    engines: Path, engine: str, capsys: pytest.CaptureFixture[str]
) -> None:
    _profile(engines)
    assert setup_latch.main(["state", "--engine", engine]) == 1
    assert capsys.readouterr().out.strip() == "no-project"
    assert setup_latch.main(["write", "--engine", engine]) == 1
    assert "run the sonar step first" in capsys.readouterr().err
    assert not _latch_file(engines).exists()


STANDINGS = {
    "optional-without-a-project": (False, None, False, "not-connected"),
    "optional-with-a-project-and-no-latch": (False, (ORG, KEY), False, "unreported"),
    "optional-with-a-project-and-a-latch": (False, (ORG, KEY), True, "latched"),
    "required-without-a-project": (True, None, False, "no-project"),
    "required-with-a-project-and-no-latch": (True, (ORG, KEY), False, "not-latched"),
    "required-with-a-project-and-a-latch": (True, (ORG, KEY), True, "latched"),
}


@pytest.mark.parametrize("case", STANDINGS)
def test_the_standing_tells_an_optional_profile_from_one_that_requires_sonarcloud(
    engines: Path, case: str
) -> None:
    required, block, latched, expected = STANDINGS[case]
    _profile(engines, block=block, required=required)
    if latched:
        engine_preflight.write_code_quality_latch(ORG, KEY)
    assert setup_latch.standing(SLUG) == expected


def test_a_latch_for_another_project_leaves_an_optional_profile_unreported(
    engines: Path,
) -> None:
    _profile(engines)
    engine_preflight.write_code_quality_latch("other-org", "other-org_other")
    assert setup_latch.standing(SLUG) == setup_latch.STATE_UNREPORTED


@pytest.mark.parametrize("content", [b"not: [valid", b"\xff\xfe\x00"], ids=["yaml", "bytes"])
def test_an_unreadable_profile_counts_as_requiring_sonarcloud(
    engines: Path, content: bytes
) -> None:
    engines.mkdir(parents=True)
    (engines / "default_profile.yaml").write_bytes(content)
    assert setup_latch.standing(SLUG) == setup_latch.STATE_NO_PROJECT


def test_no_profile_and_a_profile_for_another_repository_count_as_requiring_sonarcloud(
    engines: Path,
) -> None:
    assert setup_latch.standing(SLUG) == setup_latch.STATE_NO_PROJECT
    _profile(engines)
    assert setup_latch.standing("someone-else/other-repo") == setup_latch.STATE_NO_PROJECT


def test_the_standing_command_prints_the_standing_and_always_exits_zero(
    engines: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    printed = []
    assert setup_latch.main(["standing", "--engine", SLUG]) == 0
    printed.append(capsys.readouterr().out.strip())
    _profile(engines, block=None)
    assert setup_latch.main(["standing", "--engine", SLUG]) == 0
    printed.append(capsys.readouterr().out.strip())
    _profile(engines, block=(ORG, KEY))
    assert setup_latch.main(["standing", "--engine", SLUG]) == 0
    printed.append(capsys.readouterr().out.strip())
    assert setup_latch.main(["write", "--engine", SLUG]) == 0
    assert setup_latch.main(["standing", "--engine", SLUG]) == 0
    printed.append(capsys.readouterr().out.strip())
    assert printed == ["no-project", "not-connected", "unreported", "latched"]


def test_the_state_command_and_its_exit_codes_ignore_whether_the_profile_requires_sonarcloud(
    engines: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _profile(engines, block=None)
    assert setup_latch.main(["state", "--engine", SLUG]) == 1
    assert capsys.readouterr().out.strip() == "no-project"
    _profile(engines)
    assert setup_latch.main(["state", "--engine", SLUG]) == 2
    assert capsys.readouterr().out.strip() == "not-latched"
    assert setup_latch.EXIT_BY_STATE == {"latched": 0, "no-project": 1, "not-latched": 2}
