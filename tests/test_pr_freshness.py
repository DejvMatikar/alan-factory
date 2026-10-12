from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

import pytest

from alan import forge as forge_mod
from alan.engine_config import DEFAULT_FORGE_TARGET, ForgeTarget, RepoIdentity
from alan.forge import CompareRead, PrRead
from alan.teams import _pr_freshness
from alan.teams._pr_forge import GH_TIMEOUT_S
from tests._pr_branch_support import _audit_entries
from tests.fakes.forge import FakeForge

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WIDGET = ForgeTarget(
    repo=RepoIdentity(owner="acme", repo="widget"),
    credential_env_name="ACME_GH_TOKEN",  # noqa: S106  # nosec B106
)


@pytest.fixture(autouse=True)
def _logs_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))


@pytest.fixture
def forge() -> FakeForge:
    fake = FakeForge()
    forge_mod.set_active_forge(fake)
    return fake


def _audit(tmp_path: Path) -> list[tuple[object, object]]:
    return [(e["action"], e["reason"]) for e in _audit_entries(tmp_path)]


def _returning(value: object) -> Callable[..., object]:
    return lambda *_a, **_kw: value


def _raising(error: Exception) -> Callable[..., NoReturn]:
    def _raise(*_a: object, **_kw: object) -> NoReturn:
        raise error

    return _raise


def _called(forge: FakeForge) -> list[str]:
    return [name for name, _args, _kwargs in forge.calls]


def _refs_read(head_sha: str = "a" * 40, base_ref: str = "main") -> PrRead:
    return PrRead(
        ok=True, fields={"headRefOid": head_sha, "baseRefName": base_ref},
        returncode=0, stderr="",
    )


def _compare(
    behind_by: int | None, files: tuple[str, ...] = (), *, truncated: bool = False,
) -> CompareRead:
    return CompareRead(
        ok=True, behind_by=behind_by, files=frozenset(files), truncated=truncated,
        returncode=0, stderr="",
    )


def _stub_probe(forge: FakeForge, compare: CompareRead, refs: PrRead | None = None) -> None:
    forge.stub("read_pr", _returning(refs or _refs_read()))
    forge.stub("compare_base", _returning(compare))


def test_not_stale_when_pr_is_up_to_date_and_skips_the_diff_fetch(forge: FakeForge) -> None:
    _stub_probe(forge, _compare(0))
    assert _pr_freshness.is_measured_baseline_stale(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) is False
    assert _called(forge) == ["read_pr", "compare_base"]


def test_stale_when_the_pr_diff_touches_a_registered_file(forge: FakeForge) -> None:
    _stub_probe(forge, _compare(1, ("alan/foo.py",)))
    forge.stub("changed_files", _returning(frozenset({"tests/size_baseline.py"})))
    assert _pr_freshness.is_measured_baseline_stale(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) is True


def test_stale_when_main_moved_a_registered_file_in_the_unabsorbed_commits(
    forge: FakeForge,
) -> None:
    _stub_probe(forge, _compare(1, ("tests/size_baseline.py",)))
    assert _pr_freshness.is_measured_baseline_stale(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) is True


def test_not_stale_when_neither_side_touches_a_registered_file(forge: FakeForge) -> None:
    _stub_probe(forge, _compare(1, ("alan/foo.py",)))
    forge.stub("changed_files", _returning(frozenset({"alan/bar.py"})))
    assert _pr_freshness.is_measured_baseline_stale(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) is False


def test_not_stale_when_the_gap_and_the_diff_are_both_empty(forge: FakeForge) -> None:
    _stub_probe(forge, _compare(1))
    forge.stub("changed_files", _returning(frozenset()))
    assert _pr_freshness.is_measured_baseline_stale(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) is False


def test_gap_side_hit_short_circuits_before_fetching_the_pr_diff(forge: FakeForge) -> None:
    _stub_probe(forge, _compare(1, ("alan/web/.jscpd.json",)))
    assert _pr_freshness.is_measured_baseline_stale(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) is True
    assert "changed_files" not in _called(forge)


def test_truncated_compare_is_treated_as_implicated_without_the_diff_fetch(
    forge: FakeForge,
) -> None:
    _stub_probe(forge, _compare(1, ("alan/foo.py",), truncated=True))
    assert _pr_freshness.is_measured_baseline_stale(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) is True
    assert "changed_files" not in _called(forge)


def test_compare_is_not_treated_as_truncated_from_the_file_count_alone(
    forge: FakeForge,
) -> None:
    files = tuple(f"alan/f{i}.py" for i in range(400))
    _stub_probe(forge, _compare(1, files))
    forge.stub("changed_files", _returning(frozenset({"alan/bar.py"})))
    assert _pr_freshness.is_measured_baseline_stale(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) is False


def test_refs_are_read_with_exactly_the_head_and_base_fields(forge: FakeForge) -> None:
    _stub_probe(forge, _compare(0))
    _pr_freshness.is_measured_baseline_stale(42, "task-x", forge_target=_WIDGET)
    assert forge.calls[0] == (
        "read_pr", (42, ("headRefOid", "baseRefName")),
        {"forge_target": _WIDGET, "timeout": GH_TIMEOUT_S},
    )


def test_compare_receives_the_raw_head_sha_and_base_ref_from_the_refs_read(
    forge: FakeForge,
) -> None:
    _stub_probe(forge, _compare(0), _refs_read(head_sha="b" * 40, base_ref="release/1.2"))
    _pr_freshness.is_measured_baseline_stale(42, "task-x", forge_target=_WIDGET)
    assert forge.calls[1] == (
        "compare_base", ("b" * 40, "release/1.2"),
        {"forge_target": _WIDGET, "timeout": GH_TIMEOUT_S},
    )


def test_diff_fetch_receives_the_pr_number_and_forge_target(forge: FakeForge) -> None:
    _stub_probe(forge, _compare(1, ("alan/foo.py",)))
    forge.stub("changed_files", _returning(frozenset()))
    _pr_freshness.is_measured_baseline_stale(42, "task-x", forge_target=_WIDGET)
    assert forge.calls[2] == ("changed_files", (42,), {"forge_target": _WIDGET})


@pytest.mark.parametrize(
    "error",
    [FileNotFoundError("gh not on PATH"), subprocess.TimeoutExpired("gh", 1.0)],
    ids=["missing-binary", "timeout"],
)
def test_refs_read_raising_returns_not_stale_and_audits(
    forge: FakeForge, tmp_path: Path, error: Exception,
) -> None:
    forge.stub("read_pr", _raising(error))
    assert _pr_freshness.is_measured_baseline_stale(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) is False
    assert _audit(tmp_path) == [
        ("baseline_freshness_probe_failed", f"gh subprocess error: {error}"),
    ]
    assert _called(forge) == ["read_pr"]


@pytest.mark.parametrize(
    "error",
    [FileNotFoundError("gh not on PATH"), subprocess.TimeoutExpired("gh", 1.0)],
    ids=["missing-binary", "timeout"],
)
def test_compare_raising_returns_not_stale_and_audits_a_distinct_action(
    forge: FakeForge, tmp_path: Path, error: Exception,
) -> None:
    forge.stub("read_pr", _returning(_refs_read()))
    forge.stub("compare_base", _raising(error))
    assert _pr_freshness.is_measured_baseline_stale(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) is False
    assert _audit(tmp_path) == [
        ("baseline_freshness_compare_failed", f"gh subprocess error: {error}"),
    ]


def test_compare_without_a_behind_count_returns_not_stale_and_audits(
    forge: FakeForge, tmp_path: Path,
) -> None:
    _stub_probe(forge, _compare(None))
    assert _pr_freshness.is_measured_baseline_stale(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) is False
    assert _audit(tmp_path) == [("baseline_freshness_compare_failed", "ahead_by_missing")]


def test_diff_fetch_failure_after_clean_gap_returns_not_stale(forge: FakeForge) -> None:
    _stub_probe(forge, _compare(1, ("alan/foo.py",)))
    forge.stub("changed_files", _returning(None))
    assert _pr_freshness.is_measured_baseline_stale(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) is False


def test_refs_read_failure_returns_not_stale_and_audits(
    forge: FakeForge, tmp_path: Path,
) -> None:
    forge.stub(
        "read_pr",
        _returning(PrRead(ok=False, fields={}, returncode=1, stderr="boom")),
    )
    assert _pr_freshness.is_measured_baseline_stale(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) is False
    assert _audit(tmp_path) == [("baseline_freshness_probe_failed", "gh returncode=1: boom")]
    assert _called(forge) == ["read_pr"]


def test_compare_failure_returns_not_stale_and_audits_a_distinct_action(
    forge: FakeForge, tmp_path: Path,
) -> None:
    failed = CompareRead(
        ok=False, behind_by=None, files=frozenset(), truncated=False,
        returncode=1, stderr="boom",
    )
    _stub_probe(forge, failed)
    assert _pr_freshness.is_measured_baseline_stale(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) is False
    assert _audit(tmp_path) == [("baseline_freshness_compare_failed", "gh returncode=1: boom")]
    assert "changed_files" not in _called(forge)


def test_a_failed_read_reports_at_most_two_hundred_characters_of_stderr(
    forge: FakeForge, tmp_path: Path,
) -> None:
    forge.stub(
        "read_pr",
        _returning(PrRead(ok=False, fields={}, returncode=2, stderr="x" * 500)),
    )
    _pr_freshness.is_measured_baseline_stale(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert _audit(tmp_path) == [
        ("baseline_freshness_probe_failed", f"gh returncode=2: {'x' * 200}"),
    ]


def test_probe_never_raises_forge_errors_on_a_rate_limited_response(forge: FakeForge) -> None:
    forge.stub(
        "read_pr",
        _returning(PrRead(
            ok=False, fields={}, returncode=1,
            stderr="GraphQL: API rate limit already exceeded for user ID 1.",
        )),
    )
    assert _pr_freshness.is_measured_baseline_stale(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) is False


def test_a_refs_read_with_no_decoded_fields_returns_not_stale_and_audits(
    forge: FakeForge, tmp_path: Path,
) -> None:
    forge.stub("read_pr", _returning(PrRead(ok=True, fields={}, returncode=0, stderr="")))
    assert _pr_freshness.is_measured_baseline_stale(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) is False
    assert _audit(tmp_path) == [("baseline_freshness_probe_failed", "json_malformed")]
    assert _called(forge) == ["read_pr"]


@pytest.mark.parametrize(
    "fields",
    [
        {"headRefOid": "abc"},
        {"baseRefName": "main"},
        {"headRefOid": "", "baseRefName": "main"},
        {"headRefOid": "abc", "baseRefName": 7},
    ],
    ids=["no-base", "no-head", "empty-head", "non-string-base"],
)
def test_missing_head_or_base_ref_skips_the_compare_call_and_audits(
    forge: FakeForge, tmp_path: Path, fields: dict[str, object],
) -> None:
    forge.stub("read_pr", _returning(PrRead(ok=True, fields=fields, returncode=0, stderr="")))
    assert _pr_freshness.is_measured_baseline_stale(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) is False
    assert _called(forge) == ["read_pr"]
    assert _audit(tmp_path) == [("baseline_freshness_probe_failed", "head_or_base_ref_missing")]


@pytest.mark.parametrize("path", sorted(_pr_freshness.MEASURED_BASELINE_PATHS))
def test_registered_measured_baseline_path_exists(path: str) -> None:
    assert (_REPO_ROOT / path).is_file(), path


def test_registered_paths_are_repo_relative() -> None:
    for path in _pr_freshness.MEASURED_BASELINE_PATHS:
        assert not path.startswith("/")
        assert ".." not in path.split("/")


def test_measured_baseline_paths_includes_the_size_ratchet_test_module() -> None:
    assert "tests/test_size_ratchets.py" in _pr_freshness.MEASURED_BASELINE_PATHS
    assert len(_pr_freshness.MEASURED_BASELINE_PATHS) == 11


def test_measured_baseline_paths_includes_the_ci_duration_budgets() -> None:
    assert ".github/ci-duration-budgets.json" in _pr_freshness.MEASURED_BASELINE_PATHS
