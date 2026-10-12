from __future__ import annotations

import json
import subprocess

import pytest

from alan import process_runner
from alan.engine_config import ForgeTarget, RepoIdentity
from alan.forge_gh import GhForge
from alan.teams._pr_forge import GH_TIMEOUT_S

_TARGET = ForgeTarget(
    repo=RepoIdentity(owner="acme", repo="widget"),
    credential_env_name="ACME_GH_TOKEN",
)
_OTHER_TARGET = ForgeTarget(
    repo=RepoIdentity(owner="other", repo="repo"),
    credential_env_name="OTHER_GH_TOKEN",
)

_FORGE = GhForge()


def _fake_run_subprocess(
    monkeypatch: pytest.MonkeyPatch, stdout: str = "", returncode: int = 0, stderr: str = "",
) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    return calls


def test_read_pr_builds_exact_argv_and_never_emits_dash_q(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_run_subprocess(monkeypatch, stdout='{"headRefName": "teams/x"}')
    result = _FORGE.read_pr(42, ("headRefName", "baseRefName"), forge_target=_TARGET)
    assert calls == [[
        "gh", "pr", "view", "42", "--repo", "acme/widget",
        "--json", "headRefName,baseRefName",
    ]]
    assert "-q" not in calls[0]
    assert result.ok is True
    assert result.fields == {"headRefName": "teams/x"}
    assert result.returncode == 0


@pytest.mark.parametrize("stdout", ["not json", "[1, 2]", '"text"', "7"])
def test_read_pr_falls_back_to_empty_fields_on_non_dict_payload(
    monkeypatch: pytest.MonkeyPatch, stdout: str,
) -> None:
    _fake_run_subprocess(monkeypatch, stdout=stdout)
    result = _FORGE.read_pr(1, ("state",), forge_target=_TARGET)
    assert result.fields == {}


def test_read_pr_carries_returncode_and_stderr_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_run_subprocess(monkeypatch, returncode=1, stderr="not found")
    result = _FORGE.read_pr(1, ("state",), forge_target=_TARGET)
    assert result.ok is False
    assert result.returncode == 1
    assert result.stderr == "not found"


def test_read_checks_builds_exact_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_run_subprocess(monkeypatch, stdout='[{"name": "ci", "state": "SUCCESS"}]')
    result = _FORGE.read_checks(7, forge_target=_TARGET)
    assert calls == [[
        "gh", "pr", "checks", "7", "--repo", "acme/widget", "--json", "name,state",
    ]]
    assert result.ok is True
    assert result.checks == ({"name": "ci", "state": "SUCCESS"},)


def test_read_checks_filters_non_dict_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_run_subprocess(monkeypatch, stdout='[{"name": "ci"}, "garbage", 1]')
    result = _FORGE.read_checks(7, forge_target=_TARGET)
    assert result.checks == ({"name": "ci"},)


def test_read_checks_empty_on_non_list_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_run_subprocess(monkeypatch, stdout='{"not": "a list"}')
    result = _FORGE.read_checks(7, forge_target=_TARGET)
    assert result.checks == ()


def test_merge_pr_builds_exact_argv_with_match_head_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_run_subprocess(monkeypatch, stdout="")
    result = _FORGE.merge_pr(9, "a" * 40, forge_target=_TARGET)
    assert calls == [[
        "gh", "pr", "merge", "9", "--repo", "acme/widget",
        "--squash", "--delete-branch", "--match-head-commit", "a" * 40,
    ]]
    assert result.ok is True
    assert result.returncode == 0


def test_merge_pr_reports_failure_without_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_run_subprocess(
        monkeypatch, returncode=1, stderr="base branch policy prohibits the merge",
    )
    result = _FORGE.merge_pr(9, "a" * 40, forge_target=_TARGET)
    assert result.ok is False
    assert result.returncode == 1
    assert "policy" in result.stderr


_SHA = "a" * 40
_COMPARE_SLUG = f"repos/acme/widget/compare/{_SHA}"
_CAP = 300


def _patch_transport(
    monkeypatch: pytest.MonkeyPatch, *, stdout: str | None = "", stderr: str | None = "",
    returncode: int = 0, raises: BaseException | None = None,
) -> list[tuple[list[str], dict[str, object]]]:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((cmd, kwargs))
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    return calls


def _compare_body(**fields: object) -> str:
    return json.dumps(fields)


def _files_payload(names: list[str]) -> str:
    return _compare_body(ahead_by=1, files=[{"filename": name} for name in names])


def test_compare_base_builds_exact_argv_and_forwards_the_default_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_transport(monkeypatch, stdout=_compare_body(ahead_by=0))
    _FORGE.compare_base(_SHA, "main", forge_target=_TARGET)
    assert calls == [
        (["gh", "api", f"{_COMPARE_SLUG}...main"], {"cwd": None, "timeout": GH_TIMEOUT_S}),
    ]


def test_compare_base_forwards_an_explicit_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_transport(monkeypatch, stdout=_compare_body(ahead_by=0))
    _FORGE.compare_base(_SHA, "main", forge_target=_TARGET, timeout=12.5)
    assert calls[0][1] == {"cwd": None, "timeout": 12.5}


def test_compare_base_targets_the_forge_target_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_transport(monkeypatch, stdout=_compare_body(ahead_by=0))
    _FORGE.compare_base(_SHA, "main", forge_target=_OTHER_TARGET)
    assert calls[0][0][-1] == f"repos/other/repo/compare/{_SHA}...main"


@pytest.mark.parametrize(
    ("base_ref", "encoded"),
    [("release/1.2", "release%2F1.2"), ("feat/a b#c", "feat%2Fa%20b%23c"), ("main", "main")],
)
def test_compare_base_percent_encodes_every_reserved_character_of_the_base_ref(
    monkeypatch: pytest.MonkeyPatch, base_ref: str, encoded: str,
) -> None:
    calls = _patch_transport(monkeypatch, stdout=_compare_body(ahead_by=0))
    _FORGE.compare_base(_SHA, base_ref, forge_target=_TARGET)
    assert calls[0][0][-1] == f"{_COMPARE_SLUG}...{encoded}"


def test_compare_base_returns_the_behind_count_and_filenames_of_a_clean_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _compare_body(ahead_by=3, files=[{"filename": "a.py"}, {"filename": "b.py"}])
    _patch_transport(monkeypatch, stdout=body)
    result = _FORGE.compare_base(_SHA, "main", forge_target=_TARGET)
    assert result.ok is True
    assert result.behind_by == 3
    assert result.files == frozenset({"a.py", "b.py"})
    assert result.truncated is False
    assert result.returncode == 0
    assert result.stderr == ""


def test_compare_base_carries_returncode_and_stderr_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_transport(monkeypatch, returncode=1, stderr="not found")
    result = _FORGE.compare_base(_SHA, "main", forge_target=_TARGET)
    assert result.ok is False
    assert result.returncode == 1
    assert result.stderr == "not found"
    assert result.behind_by is None
    assert result.files == frozenset()


def test_compare_base_treats_none_stdout_and_stderr_as_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_transport(monkeypatch, stdout=None, stderr=None)
    result = _FORGE.compare_base(_SHA, "main", forge_target=_TARGET)
    assert result.behind_by is None
    assert result.files == frozenset()
    assert result.stderr == ""


@pytest.mark.parametrize("stdout", ["not json", "[1, 2]", '"text"', "7"])
def test_compare_base_reads_an_undecodable_or_non_object_payload_as_empty(
    monkeypatch: pytest.MonkeyPatch, stdout: str,
) -> None:
    _patch_transport(monkeypatch, stdout=stdout)
    result = _FORGE.compare_base(_SHA, "main", forge_target=_TARGET)
    assert result.ok is True
    assert result.behind_by is None
    assert result.files == frozenset()
    assert result.truncated is False


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({}, None),
        ({"ahead_by": "3"}, None),
        ({"ahead_by": 1.5}, None),
        ({"ahead_by": True}, None),
        ({"ahead_by": None}, None),
        ({"ahead_by": 0}, 0),
        ({"ahead_by": 5}, 5),
    ],
    ids=["absent", "str", "float", "bool", "null", "zero", "int"],
)
def test_compare_base_reports_a_behind_count_only_for_an_integer_ahead_by(
    monkeypatch: pytest.MonkeyPatch, body: dict[str, object], expected: int | None,
) -> None:
    _patch_transport(monkeypatch, stdout=json.dumps(body))
    assert _FORGE.compare_base(_SHA, "main", forge_target=_TARGET).behind_by == expected


@pytest.mark.parametrize("files", [None, "a.py", {"filename": "a.py"}, 3])
def test_compare_base_reads_a_files_value_that_is_not_a_list_as_empty(
    monkeypatch: pytest.MonkeyPatch, files: object,
) -> None:
    _patch_transport(monkeypatch, stdout=_compare_body(ahead_by=1, files=files))
    result = _FORGE.compare_base(_SHA, "main", forge_target=_TARGET)
    assert result.files == frozenset()
    assert result.truncated is False


def test_compare_base_reads_a_missing_files_key_as_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_transport(monkeypatch, stdout=_compare_body(ahead_by=1))
    assert _FORGE.compare_base(_SHA, "main", forge_target=_TARGET).files == frozenset()


def test_compare_base_keeps_only_string_filenames_of_object_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = ["not-a-dict", {"no_filename_key": True}, {"filename": 123}, {"filename": "a.py"}]
    _patch_transport(monkeypatch, stdout=_compare_body(ahead_by=1, files=entries))
    result = _FORGE.compare_base(_SHA, "main", forge_target=_TARGET)
    assert result.files == frozenset({"a.py"})
    assert result.truncated is False


@pytest.mark.parametrize(
    ("count", "truncated"),
    [(_CAP - 1, False), (_CAP, True), (_CAP + 1, True)],
)
def test_compare_base_flags_truncation_at_the_page_cap_boundary(
    monkeypatch: pytest.MonkeyPatch, count: int, truncated: bool,
) -> None:
    _patch_transport(monkeypatch, stdout=_files_payload([f"f{i}.py" for i in range(count)]))
    result = _FORGE.compare_base(_SHA, "main", forge_target=_TARGET)
    assert result.truncated is truncated
    assert len(result.files) == count


def test_compare_base_flags_truncation_before_deduplicating_repeated_filenames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_transport(monkeypatch, stdout=_files_payload(["same.py"] * _CAP))
    result = _FORGE.compare_base(_SHA, "main", forge_target=_TARGET)
    assert result.files == frozenset({"same.py"})
    assert result.truncated is True


@pytest.mark.parametrize(
    "error",
    [FileNotFoundError("gh not on PATH"), subprocess.TimeoutExpired("gh", 1.0)],
    ids=["missing-binary", "timeout"],
)
def test_compare_base_lets_transport_exceptions_propagate(
    monkeypatch: pytest.MonkeyPatch, error: BaseException,
) -> None:
    _patch_transport(monkeypatch, raises=error)
    with pytest.raises(type(error)):
        _FORGE.compare_base(_SHA, "main", forge_target=_TARGET)
