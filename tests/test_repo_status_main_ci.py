from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from typing import Any, cast

import pytest

from alan import process_runner, repo_status
from alan.engine_config import CiConfig, EngineConfig, ForgeConfig, ForgeTarget, RepoIdentity
from alan.forge import JsonObject, RowsOutcome
from alan.forge_gh import GhForge
from tests.fakes.forge import FakeForge
from tests.forge_stubs import always, rows_read

_FORGE_CREDENTIAL = "ACME_GH_TOKEN"  # noqa: S105
_WORKFLOW = "tests.yml"


def _forge(owner: str = "acme", repo: str = "widget") -> ForgeTarget:
    return ForgeTarget(
        repo=RepoIdentity(owner=owner, repo=repo),
        credential_env_name=_FORGE_CREDENTIAL,
        authoritative_checks=frozenset({"SonarCloud Code Analysis"}),
    )


@pytest.fixture
def fake(_active_fake_forge: FakeForge) -> FakeForge:
    return _active_fake_forge


def _check_run(
    name: str, conclusion: str, suite_id: int, sha: str = "abc1234def"
) -> JsonObject:
    return {
        "name": name,
        "conclusion": conclusion,
        "head_sha": sha,
        "check_suite": {"id": suite_id},
    }


def _stub_branch_and_checks(
    fake: FakeForge, runs: Sequence[JsonObject], *, branch: str = "main"
) -> None:
    always(fake, "default_branch", branch)
    always(fake, "commit_check_runs", rows_read(runs))


def _stub_workflow_run(fake: FakeForge, suite_id: int | None) -> None:
    rows: list[JsonObject] = [] if suite_id is None else [{"check_suite_id": suite_id}]
    always(fake, "latest_workflow_run", rows_read(rows))


def _calls(fake: FakeForge, name: str) -> list[tuple[tuple[object, ...], dict[str, object]]]:
    return [(args, kwargs) for call, args, kwargs in fake.calls if call == name]


def test_main_ci_counts_only_the_declared_workflows_check_suite(fake: FakeForge) -> None:
    runs = [
        _check_run("Backend tests + coverage", "success", 111),
        _check_run("Security audit (bandit + pip-audit)", "failure", 111),
        _check_run("SonarCloud Code Analysis", "success", 222),
    ]
    _stub_branch_and_checks(fake, runs)
    _stub_workflow_run(fake, 111)
    field = repo_status._fetch_main_ci(_forge(), main_ci_workflow=_WORKFLOW)  # noqa: SLF001
    assert (field.green_count, field.red_count) == (1, 1)
    assert field.counted_over == "main_ci_workflow"


def test_main_ci_ignores_a_failing_advisory_workflow_on_the_same_commit(fake: FakeForge) -> None:
    runs = [
        _check_run("Backend tests + coverage", "success", 111),
        _check_run("Playwright 3x repeat, no retries (advisory, files issues)", "failure", 999),
    ]
    _stub_branch_and_checks(fake, runs)
    _stub_workflow_run(fake, 111)
    field = repo_status._fetch_main_ci(_forge(), main_ci_workflow=_WORKFLOW)  # noqa: SLF001
    assert (field.green_count, field.red_count) == (1, 0)


def test_main_ci_reports_red_when_the_declared_workflow_has_a_failing_job(fake: FakeForge) -> None:
    runs = [
        _check_run("SonarCloud Code Analysis", "success", 222),
        _check_run("Security audit (bandit + pip-audit)", "failure", 111),
    ]
    _stub_branch_and_checks(fake, runs)
    _stub_workflow_run(fake, 111)
    field = repo_status._fetch_main_ci(_forge(), main_ci_workflow=_WORKFLOW)  # noqa: SLF001
    assert field.red_count == 1
    assert field.counted_over == "main_ci_workflow"


def test_main_ci_falls_back_to_all_checks_when_the_engine_declares_no_ci_workflow(
    fake: FakeForge,
) -> None:
    _stub_branch_and_checks(fake, [_check_run("anything", "failure", 111)])
    field = repo_status._fetch_main_ci(_forge(), main_ci_workflow=None)  # noqa: SLF001
    assert field.counted_over == "all_checks"
    assert field.red_count == 1
    assert "no ci.main_ci_workflow declared" in field.provenance.detail
    assert _calls(fake, "latest_workflow_run") == []


def test_main_ci_falls_back_to_all_checks_when_check_runs_carry_no_head_sha(
    fake: FakeForge,
) -> None:
    runs: list[JsonObject] = [{"name": "a", "conclusion": "success", "check_suite": {"id": 1}}]
    _stub_branch_and_checks(fake, runs)
    field = repo_status._fetch_main_ci(_forge(), main_ci_workflow=_WORKFLOW)  # noqa: SLF001
    assert field.counted_over == "all_checks"
    assert "no head SHA" in field.provenance.detail
    assert _calls(fake, "latest_workflow_run") == []


def test_main_ci_falls_back_to_all_checks_when_no_workflow_run_exists_for_the_head_commit(
    fake: FakeForge,
) -> None:
    _stub_branch_and_checks(fake, [_check_run("a", "success", 111)])
    _stub_workflow_run(fake, None)
    field = repo_status._fetch_main_ci(_forge(), main_ci_workflow=_WORKFLOW)  # noqa: SLF001
    assert field.counted_over == "all_checks"
    assert f"no {_WORKFLOW} run found" in field.provenance.detail


@pytest.mark.parametrize("outcome", ["failed", "raised"])
def test_main_ci_falls_back_to_all_checks_when_the_workflow_runs_call_fails(
    fake: FakeForge, outcome: RowsOutcome
) -> None:
    _stub_branch_and_checks(fake, [_check_run("a", "success", 111)])
    always(fake, "latest_workflow_run", rows_read(None, outcome=outcome, returncode=1))
    field = repo_status._fetch_main_ci(_forge(), main_ci_workflow=_WORKFLOW)  # noqa: SLF001
    assert field.counted_over == "all_checks"


def test_main_ci_reads_the_branch_then_its_check_runs_then_the_workflow_run_of_that_commit(
    fake: FakeForge,
) -> None:
    runs = [_check_run("a", "success", 111, sha="fed07dd5aaaaaaa")]
    _stub_branch_and_checks(fake, runs, branch="trunk")
    _stub_workflow_run(fake, 111)
    forge = _forge()
    repo_status._fetch_main_ci(forge, main_ci_workflow=_WORKFLOW)  # noqa: SLF001
    assert fake.calls == [
        ("default_branch", (), {"forge_target": forge, "timeout": 30.0}),
        ("commit_check_runs", ("trunk",), {"forge_target": forge, "timeout": 30.0}),
        (
            "latest_workflow_run", (_WORKFLOW,),
            {"forge_target": forge, "timeout": 30.0, "head_sha": "fed07dd5aaaaaaa"},
        ),
    ]


def test_workflow_suite_id_is_none_when_the_runs_key_is_not_a_list(fake: FakeForge) -> None:
    always(fake, "latest_workflow_run", rows_read(None))
    assert repo_status._workflow_suite_id(_forge(), _WORKFLOW, "abc1234") is None  # noqa: SLF001


def test_workflow_suite_id_is_none_when_the_adapter_reports_no_runs(fake: FakeForge) -> None:
    always(fake, "latest_workflow_run", rows_read([]))
    assert repo_status._workflow_suite_id(_forge(), _WORKFLOW, "abc1234") is None  # noqa: SLF001


def test_workflow_suite_id_is_none_when_the_run_payload_omits_an_integer_check_suite_id(
    fake: FakeForge,
) -> None:
    always(fake, "latest_workflow_run", rows_read([{"check_suite_id": "111"}]))
    assert repo_status._workflow_suite_id(_forge(), _WORKFLOW, "abc1234") is None  # noqa: SLF001


def test_workflow_suite_id_reads_the_integer_check_suite_id_of_the_first_run(
    fake: FakeForge,
) -> None:
    always(fake, "latest_workflow_run", rows_read([{"check_suite_id": 111}, {"check_suite_id": 5}]))
    assert repo_status._workflow_suite_id(_forge(), _WORKFLOW, "abc1234") == 111  # noqa: SLF001


def test_workflow_suite_id_passes_the_head_sha_the_workflow_and_the_bound_to_the_port(
    fake: FakeForge,
) -> None:
    _stub_workflow_run(fake, 111)
    forge = _forge()
    repo_status._workflow_suite_id(forge, "../../evil.yml", "abc1234def")  # noqa: SLF001
    assert fake.calls == [
        (
            "latest_workflow_run", ("../../evil.yml",),
            {"forge_target": forge, "timeout": 30.0, "head_sha": "abc1234def"},
        ),
    ]


def test_workflow_suite_id_ignores_a_lone_non_object_run_through_the_adapter(
    fake: FakeForge, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps({"workflow_runs": ["nope"]}), stderr=""
        )

    monkeypatch.setattr(process_runner, "run_subprocess", _run)
    fake.stub("latest_workflow_run", GhForge().latest_workflow_run)
    assert repo_status._workflow_suite_id(_forge(), _WORKFLOW, "abc1234") is None  # noqa: SLF001


def test_check_suite_id_is_none_when_the_check_suite_key_is_not_an_object() -> None:
    assert repo_status._check_suite_id({"check_suite": 5}) is None  # noqa: SLF001


def test_check_suite_id_is_none_when_the_suite_id_is_not_an_integer() -> None:
    assert repo_status._check_suite_id({"check_suite": {"id": "5"}}) is None  # noqa: SLF001


def test_main_ci_provenance_names_the_workflow_and_the_head_commit_it_counted(
    fake: FakeForge,
) -> None:
    _stub_branch_and_checks(fake, [_check_run("a", "success", 111, sha="fed07dd5aaaaaaa")])
    _stub_workflow_run(fake, 111)
    field = repo_status._fetch_main_ci(_forge(), main_ci_workflow=_WORKFLOW)  # noqa: SLF001
    assert _WORKFLOW in field.provenance.detail
    assert "fed07dd" in field.provenance.detail


def test_main_ci_provenance_names_the_checks_actually_counted_not_the_checks_declared(
    fake: FakeForge,
) -> None:
    runs = [
        _check_run("a", "success", 111),
        _check_run("b", "success", 111),
        _check_run("SonarCloud Code Analysis", "success", 222),
    ]
    _stub_branch_and_checks(fake, runs)
    _stub_workflow_run(fake, 111)
    field = repo_status._fetch_main_ci(_forge(), main_ci_workflow=_WORKFLOW)  # noqa: SLF001
    assert "counted over the 2 check(s)" in field.provenance.detail


def test_main_ci_counts_a_cancelled_check_run_as_red(fake: FakeForge) -> None:
    _stub_branch_and_checks(fake, [_check_run("a", "cancelled", 111)])
    _stub_workflow_run(fake, 111)
    field = repo_status._fetch_main_ci(_forge(), main_ci_workflow=_WORKFLOW)  # noqa: SLF001
    assert field.red_count == 1


def test_main_ci_ignores_skipped_and_queued_check_runs_in_both_counts(fake: FakeForge) -> None:
    runs: list[JsonObject] = [
        _check_run("a", "skipped", 111),
        _check_run("b", "success", 111),
        {"name": "c", "head_sha": "abc1234def", "check_suite": {"id": 111}},
    ]
    _stub_branch_and_checks(fake, runs)
    _stub_workflow_run(fake, 111)
    field = repo_status._fetch_main_ci(_forge(), main_ci_workflow=_WORKFLOW)  # noqa: SLF001
    assert (field.green_count, field.red_count) == (1, 0)


def test_main_ci_unknown_when_repo_lookup_fails(fake: FakeForge) -> None:
    always(fake, "default_branch", None)
    field = repo_status._fetch_main_ci(_forge(), main_ci_workflow=_WORKFLOW)  # noqa: SLF001
    assert field.provenance.status == "unknown"
    assert field.provenance.detail == "could not read the repo's default branch"
    assert _calls(fake, "commit_check_runs") == []


@pytest.mark.parametrize("outcome", ["failed", "raised"])
def test_main_ci_unknown_when_check_runs_fetch_fails(
    fake: FakeForge, outcome: RowsOutcome
) -> None:
    always(fake, "default_branch", "main")
    always(fake, "commit_check_runs", rows_read(None, outcome=outcome, returncode=1))
    field = repo_status._fetch_main_ci(_forge(), main_ci_workflow=_WORKFLOW)  # noqa: SLF001
    assert field.provenance.status == "unknown"
    assert field.provenance.detail == "could not read check-runs for the default branch"


def test_main_ci_unknown_when_check_runs_missing_array(fake: FakeForge) -> None:
    always(fake, "default_branch", "main")
    always(fake, "commit_check_runs", rows_read(None))
    field = repo_status._fetch_main_ci(_forge(), main_ci_workflow=_WORKFLOW)  # noqa: SLF001
    assert field.provenance.status == "unknown"
    assert field.provenance.detail == "check-runs response was missing a check_runs array"


def test_main_ci_reports_fresh_zero_counts_for_an_empty_check_run_list(fake: FakeForge) -> None:
    _stub_branch_and_checks(fake, [])
    field = repo_status._fetch_main_ci(_forge(), main_ci_workflow=None)  # noqa: SLF001
    assert field.provenance.status == "fresh"
    assert (field.green_count, field.red_count) == (0, 0)


def test_count_conclusions_splits_green_and_red() -> None:
    scoped = [{"conclusion": "success"}, {"conclusion": "failure"}, {"conclusion": "neutral"}]
    green, red = repo_status._count_conclusions(scoped)  # noqa: SLF001
    assert (green, red) == (1, 1)


def _engine_with_ci(*, workflow: str | None) -> EngineConfig:
    identity = RepoIdentity(owner="acme", repo="widget")
    return EngineConfig(
        repo=identity,
        forge=ForgeConfig(
            repo=identity,
            credential_env_name=_FORGE_CREDENTIAL,
            authoritative_checks=frozenset(),
        ),
        ci=None
        if workflow is None
        else CiConfig(main_ci_workflow=workflow, deploy_workflow="ci-deploy.yml"),
    )


def _recording_forge_fields(
    monkeypatch: pytest.MonkeyPatch, seen: list[str | None]
) -> None:
    def _fake(forge: ForgeTarget, pubsub: Any, *, main_ci_workflow: str | None) -> bool:
        del forge, pubsub
        seen.append(main_ci_workflow)
        return True

    monkeypatch.setattr(repo_status, "_read_forge_cache", lambda forge: None)
    monkeypatch.setattr(repo_status, "schedule_refresh", _fake)


def test_forge_fields_threads_the_engines_main_ci_workflow_into_the_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str | None] = []
    _recording_forge_fields(monkeypatch, seen)
    cfg = _engine_with_ci(workflow="tests.yml")
    assert cfg.ci is not None
    repo_status._forge_fields(  # noqa: SLF001
        _forge(), pubsub=cast(Any, None), main_ci_workflow=cfg.ci.main_ci_workflow
    )
    assert seen == ["tests.yml"]


def test_forge_fields_threads_no_workflow_when_the_engine_has_no_ci_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str | None] = []
    _recording_forge_fields(monkeypatch, seen)
    cfg = _engine_with_ci(workflow=None)
    repo_status._forge_fields(  # noqa: SLF001
        _forge(),
        pubsub=cast(Any, None),
        main_ci_workflow=None if cfg.ci is None else cfg.ci.main_ci_workflow,
    )
    assert seen == [None]


def test_read_forge_cache_discards_a_cache_written_with_the_retired_authoritative_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    forge = _forge()
    path = tmp_path / "repo_status_forge.json"
    path.write_text(
        json.dumps(
            {
                "fetched_at": "2026-09-19T00:00:00+00:00",
                "main_ci": {
                    "provenance": {"status": "fresh", "detail": "d"},
                    "green_count": 1,
                    "red_count": 0,
                    "counted_over": "authoritative_checks",
                },
                "open_prs": {"provenance": {"status": "fresh", "detail": "d"}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(repo_status, "_forge_cache_path", lambda f: path)
    assert repo_status._read_forge_cache(forge) is None  # noqa: SLF001
