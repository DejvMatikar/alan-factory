from __future__ import annotations

import base64
import json
import math
import os
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from alan import process_runner, recurring_forge, repo_status, secrets_store
from alan.engine_config import ForgeTarget, RepoIdentity
from alan.events import PubSub
from alan.forge import FileOutcome, FileRead, RowsOutcome
from alan.forge_gh import GhForge
from alan.teams import _base as teams_base
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, always, file_read, rows_read

_FORGE_CREDENTIAL = "ACME_GH_TOKEN"  # noqa: S105


def _forge(owner: str = "acme", repo: str = "widget") -> ForgeTarget:
    return ForgeTarget(
        repo=RepoIdentity(owner=owner, repo=repo),
        credential_env_name=_FORGE_CREDENTIAL,
        authoritative_checks=frozenset(),
    )


@pytest.fixture
def state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "state"
    monkeypatch.setenv("ALAN_STATE_ROOT", str(root))
    return root


@pytest.fixture
def fake(_active_fake_forge: FakeForge) -> FakeForge:
    return _active_fake_forge


@pytest.fixture
def script(fake: FakeForge) -> ForgeScript:
    return ForgeScript(fake)


def _content_file(text: str) -> FileRead:
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return file_read(json.dumps({"content": encoded, "encoding": "base64"}))


def _absent_file() -> FileRead:
    return file_read(None, outcome="absent", returncode=1, stderr="HTTP 404")


def _forbidden_file() -> FileRead:
    return file_read(None, outcome="failed", returncode=1, stderr="HTTP 403")


def _adapter_backed(
    fake: FakeForge, monkeypatch: pytest.MonkeyPatch, op: str, stdout: str
) -> None:
    def _run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", _run)
    fake.stub(op, getattr(GhForge(), op))


@pytest.fixture(autouse=True)
def _reset_inflight() -> Iterator[None]:
    repo_status._INFLIGHT_SLUGS.clear()  # noqa: SLF001
    yield
    repo_status._INFLIGHT_SLUGS.clear()  # noqa: SLF001


def test_fetch_file_text_or_404_propagates_an_absent_file(fake: FakeForge) -> None:
    always(fake, "repo_file", _absent_file())
    result = recurring_forge._fetch_file_text_or_404(_forge(), "x.yml", ref="main")  # noqa: SLF001
    assert result == (None, True)


@pytest.mark.parametrize("outcome", ["failed", "raised"])
def test_fetch_file_text_or_404_does_not_confirm_absence_for_a_failed_or_raised_read(
    fake: FakeForge, outcome: FileOutcome
) -> None:
    always(fake, "repo_file", file_read(None, outcome=outcome, returncode=1, stderr="HTTP 403"))
    result = recurring_forge._fetch_file_text_or_404(_forge(), "x.yml", ref="main")  # noqa: SLF001
    assert result == (None, False)


def test_fetch_file_text_or_404_missing_base64_fields(fake: FakeForge) -> None:
    always(fake, "repo_file", file_read(json.dumps({"content": "abc"})))
    result = recurring_forge._fetch_file_text_or_404(_forge(), "x.yml", ref="main")  # noqa: SLF001
    assert result == (None, False)


def test_fetch_file_text_or_404_bad_base64(fake: FakeForge) -> None:
    always(
        fake, "repo_file",
        file_read(json.dumps({"content": "not-base64!!", "encoding": "base64"})),
    )
    result = recurring_forge._fetch_file_text_or_404(_forge(), "x.yml", ref="main")  # noqa: SLF001
    assert result == (None, False)


@pytest.mark.parametrize("payload", ["not json", "[1, 2]", "", None])
def test_fetch_file_text_or_404_unusable_payload(fake: FakeForge, payload: str | None) -> None:
    always(fake, "repo_file", file_read(payload))
    result = recurring_forge._fetch_file_text_or_404(_forge(), "x.yml", ref="main")  # noqa: SLF001
    assert result == (None, False)


def test_fetch_file_text_or_404_success(fake: FakeForge) -> None:
    always(fake, "repo_file", _content_file("hello: world\n"))
    result = recurring_forge._fetch_file_text_or_404(_forge(), "x.yml", ref="main")  # noqa: SLF001
    assert result == ("hello: world\n", False)


def test_fetch_file_text_or_404_reads_the_path_at_the_ref_with_the_thirty_second_bound(
    fake: FakeForge,
) -> None:
    always(fake, "repo_file", _content_file("a: 1\n"))
    forge = _forge()
    recurring_forge._fetch_file_text_or_404(forge, ".github/workflows/x.yml", ref="trunk")  # noqa: SLF001
    assert fake.calls == [
        (
            "repo_file", (".github/workflows/x.yml",),
            {"forge_target": forge, "timeout": 30.0, "ref": "trunk"},
        ),
    ]


def test_fetch_file_text_discards_404_flag(fake: FakeForge) -> None:
    always(fake, "repo_file", _absent_file())
    assert recurring_forge._fetch_file_text(_forge(), "x.yml", ref="main") is None  # noqa: SLF001


def test_default_branch_none_when_repo_lookup_fails(fake: FakeForge) -> None:
    always(fake, "default_branch", None)
    assert recurring_forge._default_branch(_forge()) is None  # noqa: SLF001


def test_default_branch_returns_the_branch_the_port_reports(fake: FakeForge) -> None:
    always(fake, "default_branch", "trunk")
    forge = _forge()
    assert recurring_forge._default_branch(forge) == "trunk"  # noqa: SLF001
    assert fake.calls == [("default_branch", (), {"forge_target": forge, "timeout": 30.0})]


@pytest.mark.parametrize("outcome", ["failed", "raised"])
def test_list_workflow_files_none_when_fetch_fails(fake: FakeForge, outcome: RowsOutcome) -> None:
    always(fake, "workflows", rows_read(None, outcome=outcome, returncode=1))
    assert recurring_forge._list_workflow_files(_forge()) is None  # noqa: SLF001


def test_list_workflow_files_none_when_the_workflows_key_is_not_a_list(fake: FakeForge) -> None:
    always(fake, "workflows", rows_read(None))
    assert recurring_forge._list_workflow_files(_forge()) is None  # noqa: SLF001


def test_list_workflow_files_returns_the_rows_the_port_reports(fake: FakeForge) -> None:
    always(fake, "workflows", rows_read([{"id": 1}, {"id": 2}]))
    forge = _forge()
    assert recurring_forge._list_workflow_files(forge) == [{"id": 1}, {"id": 2}]  # noqa: SLF001
    assert fake.calls == [("workflows", (), {"forge_target": forge, "timeout": 30.0})]


def test_list_workflow_files_empty_list_is_not_a_failure(fake: FakeForge) -> None:
    always(fake, "workflows", rows_read([]))
    assert recurring_forge._list_workflow_files(_forge()) == []  # noqa: SLF001


def test_parse_yaml_dict_returns_none_on_yaml_error() -> None:
    assert recurring_forge._parse_yaml_dict(": : :") is None  # noqa: SLF001


def test_parse_yaml_dict_returns_none_on_non_dict() -> None:
    assert recurring_forge._parse_yaml_dict("- a\n- b\n") is None  # noqa: SLF001


def test_parse_yaml_dict_returns_dict() -> None:
    assert recurring_forge._parse_yaml_dict("a: 1\n") == {"a": 1}  # noqa: SLF001


def test_on_section_reads_string_key() -> None:
    assert recurring_forge._on_section({"on": {"push": None}}) == {"push": None}  # noqa: SLF001


def test_on_section_reads_yaml_boolean_true_key() -> None:
    doc = cast("dict[str, Any]", yaml_load_bool_on())
    assert recurring_forge._on_section(doc) == {"workflow_dispatch": None}  # noqa: SLF001


def yaml_load_bool_on() -> Any:
    import yaml

    return yaml.safe_load("on:\n  workflow_dispatch:\n")


def test_on_section_returns_none_when_absent() -> None:
    assert recurring_forge._on_section({"name": "x"}) is None  # noqa: SLF001


def test_schedule_crons_not_a_dict() -> None:
    assert recurring_forge._schedule_crons("push") == []  # noqa: SLF001


def test_schedule_crons_schedule_not_a_list() -> None:
    assert recurring_forge._schedule_crons({"schedule": "nope"}) == []  # noqa: SLF001


def test_schedule_crons_filters_bad_entries() -> None:
    on_section = {
        "schedule": [
            {"cron": "0 2 * * *"},
            {"cron": "  "},
            {"nope": "x"},
            "not-a-dict",
            {"cron": 5},
        ]
    }
    assert recurring_forge._schedule_crons(on_section) == ["0 2 * * *"]  # noqa: SLF001


def test_trigger_keys_dict() -> None:
    assert recurring_forge._trigger_keys({"push": None, "workflow_dispatch": None}) == {  # noqa: SLF001
        "push", "workflow_dispatch",
    }


def test_trigger_keys_list() -> None:
    assert recurring_forge._trigger_keys(["push", "workflow_dispatch"]) == {  # noqa: SLF001
        "push", "workflow_dispatch",
    }


def test_trigger_keys_string() -> None:
    assert recurring_forge._trigger_keys("push") == {"push"}  # noqa: SLF001


def test_trigger_keys_other_type() -> None:
    assert recurring_forge._trigger_keys(None) == frozenset()  # noqa: SLF001


def test_is_dispatch_only_true() -> None:
    assert recurring_forge._is_dispatch_only({"workflow_dispatch": None}) is True  # noqa: SLF001


def test_is_dispatch_only_false_with_other_triggers() -> None:
    assert recurring_forge._is_dispatch_only({"push": None, "workflow_dispatch": None}) is False  # noqa: SLF001


@pytest.mark.parametrize("outcome", ["failed", "raised"])
def test_fetch_latest_run_none_when_fetch_fails(fake: FakeForge, outcome: RowsOutcome) -> None:
    always(fake, "latest_workflow_run", rows_read(None, outcome=outcome, returncode=1))
    assert recurring_forge._fetch_latest_run(_forge(), 1) == (None, False)  # noqa: SLF001


def test_fetch_latest_run_confirmed_absent_when_no_runs(fake: FakeForge) -> None:
    always(fake, "latest_workflow_run", rows_read([]))
    assert recurring_forge._fetch_latest_run(_forge(), 1) == (None, True)  # noqa: SLF001


def test_fetch_latest_run_none_when_runs_not_a_list(fake: FakeForge) -> None:
    always(fake, "latest_workflow_run", rows_read(None))
    assert recurring_forge._fetch_latest_run(_forge(), 1) == (None, False)  # noqa: SLF001


def test_fetch_latest_run_reads_a_lone_non_object_run_as_confirmed_absent(
    fake: FakeForge, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = json.dumps({"workflow_runs": ["nope"]})
    _adapter_backed(fake, monkeypatch, "latest_workflow_run", body)
    assert recurring_forge._fetch_latest_run(_forge(), 1) == (None, True)  # noqa: SLF001


def test_fetch_latest_run_success(fake: FakeForge) -> None:
    always(fake, "latest_workflow_run", rows_read([{"status": "completed"}]))
    run, confirmed_absent = recurring_forge._fetch_latest_run(_forge(), 1)  # noqa: SLF001
    assert run == {"status": "completed"}
    assert confirmed_absent is False


def test_fetch_latest_run_asks_for_the_workflow_id_without_a_commit_filter(
    fake: FakeForge,
) -> None:
    always(fake, "latest_workflow_run", rows_read([]))
    forge = _forge()
    recurring_forge._fetch_latest_run(forge, 42)  # noqa: SLF001
    assert fake.calls == [
        (
            "latest_workflow_run", ("42",),
            {"forge_target": forge, "timeout": 30.0, "head_sha": None},
        ),
    ]


def test_run_provenance_unknown_when_none() -> None:
    assert recurring_forge._run_provenance(None).status == "unknown"  # noqa: SLF001


def test_run_provenance_fresh_when_present() -> None:
    assert recurring_forge._run_provenance({}).status == "fresh"  # noqa: SLF001


def test_run_started_at_none_when_run_none() -> None:
    assert recurring_forge._run_started_at(None) is None  # noqa: SLF001


def test_run_started_at_falls_back_to_created_at() -> None:
    run = {"created_at": "2026-01-01T00:00:00Z"}
    assert recurring_forge._run_started_at(run) == "2026-01-01T00:00:00Z"  # noqa: SLF001


def test_run_started_at_prefers_run_started_at() -> None:
    run = {"run_started_at": "2026-01-02T00:00:00Z", "created_at": "2026-01-01T00:00:00Z"}
    assert recurring_forge._run_started_at(run) == "2026-01-02T00:00:00Z"  # noqa: SLF001


def test_run_started_at_none_when_non_string() -> None:
    assert recurring_forge._run_started_at({"run_started_at": 5, "created_at": 5}) is None  # noqa: SLF001


def test_run_succeeded_none_when_run_none() -> None:
    assert recurring_forge._run_succeeded(None) is None  # noqa: SLF001


def test_run_succeeded_none_when_not_completed() -> None:
    assert recurring_forge._run_succeeded({"status": "in_progress"}) is None  # noqa: SLF001


def test_run_succeeded_true_on_success() -> None:
    assert recurring_forge._run_succeeded({"status": "completed", "conclusion": "success"}) is True  # noqa: SLF001


def test_run_succeeded_false_on_failure() -> None:
    assert recurring_forge._run_succeeded({"status": "completed", "conclusion": "failure"}) is False  # noqa: SLF001


def test_run_succeeded_none_when_conclusion_not_string() -> None:
    assert recurring_forge._run_succeeded({"status": "completed", "conclusion": 1}) is None  # noqa: SLF001


def test_run_outcome_none_when_run_none() -> None:
    assert recurring_forge._run_outcome(None) is None  # noqa: SLF001


def test_run_outcome_none_when_not_completed() -> None:
    assert recurring_forge._run_outcome({"status": "queued"}) is None  # noqa: SLF001


def test_run_outcome_returns_conclusion() -> None:
    run = {"status": "completed", "conclusion": "failure"}
    assert recurring_forge._run_outcome(run) == "failure"  # noqa: SLF001


def test_run_outcome_none_when_conclusion_not_string() -> None:
    assert recurring_forge._run_outcome({"status": "completed", "conclusion": 1}) is None  # noqa: SLF001


def test_run_duration_seconds_none_when_run_none() -> None:
    assert recurring_forge._run_duration_seconds(None) is None  # noqa: SLF001


def test_run_duration_seconds_none_when_not_completed() -> None:
    assert recurring_forge._run_duration_seconds({"status": "queued"}) is None  # noqa: SLF001


def test_run_duration_seconds_none_when_timestamps_missing() -> None:
    run = {"status": "completed", "run_started_at": None, "updated_at": None}
    assert recurring_forge._run_duration_seconds(run) is None  # noqa: SLF001


def test_run_duration_seconds_none_when_unparseable() -> None:
    run = {"status": "completed", "run_started_at": "nope", "updated_at": "nope"}
    assert recurring_forge._run_duration_seconds(run) is None  # noqa: SLF001


def test_run_duration_seconds_none_when_negative() -> None:
    run = {
        "status": "completed",
        "run_started_at": "2026-01-01T00:10:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    assert recurring_forge._run_duration_seconds(run) is None  # noqa: SLF001


def test_run_duration_seconds_measured() -> None:
    run = {
        "status": "completed",
        "run_started_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:05:00+00:00",
    }
    assert recurring_forge._run_duration_seconds(run) == pytest.approx(300.0)  # noqa: SLF001


def test_workflow_identity_valid() -> None:
    wf = {"id": 1, "path": ".github/workflows/x.yml", "name": "X"}
    assert recurring_forge._workflow_identity(wf) == (1, ".github/workflows/x.yml", "X")  # noqa: SLF001


def test_workflow_identity_invalid() -> None:
    assert recurring_forge._workflow_identity({"id": "not-int", "path": "x", "name": "n"}) is None  # noqa: SLF001


def test_workflow_triggers_none_when_fetch_fails(fake: FakeForge) -> None:
    always(fake, "repo_file", _absent_file())
    assert recurring_forge._workflow_triggers(_forge(), "x.yml", branch="main") is None  # noqa: SLF001


def test_workflow_triggers_none_when_unparseable(fake: FakeForge) -> None:
    always(fake, "repo_file", _content_file(": : :"))
    assert recurring_forge._workflow_triggers(_forge(), "x.yml", branch="main") is None  # noqa: SLF001


def test_workflow_triggers_scheduled(fake: FakeForge) -> None:
    always(fake, "repo_file", _content_file("on:\n  schedule:\n    - cron: '0 2 * * *'\n"))
    result = recurring_forge._workflow_triggers(_forge(), "x.yml", branch="main")  # noqa: SLF001
    assert result == (["0 2 * * *"], False)


def test_workflow_triggers_manual(fake: FakeForge) -> None:
    always(fake, "repo_file", _content_file("on:\n  workflow_dispatch:\n"))
    result = recurring_forge._workflow_triggers(_forge(), "x.yml", branch="main")  # noqa: SLF001
    assert result == ([], True)


def test_workflow_triggers_out_of_scope(fake: FakeForge) -> None:
    always(fake, "repo_file", _content_file("on:\n  push:\n"))
    assert recurring_forge._workflow_triggers(_forge(), "x.yml", branch="main") is None  # noqa: SLF001


def test_workflow_schedule_none_when_identity_invalid(fake: FakeForge) -> None:
    wf = {"id": "bad"}
    assert recurring_forge._workflow_schedule(_forge(), wf, branch="main") is None  # noqa: SLF001
    assert fake.calls == []


def test_workflow_schedule_none_when_triggers_none(fake: FakeForge) -> None:
    always(fake, "repo_file", _forbidden_file())
    wf = {"id": 1, "path": "x.yml", "name": "X"}
    assert recurring_forge._workflow_schedule(_forge(), wf, branch="main") is None  # noqa: SLF001


def test_workflow_schedule_success(fake: FakeForge, script: ForgeScript) -> None:
    script.queue(
        "repo_file", _content_file("on:\n  schedule:\n    - cron: '*/30 * * * *'\n")
    )
    script.queue(
        "latest_workflow_run",
        rows_read([{
            "status": "completed",
            "conclusion": "success",
            "run_started_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:05:00+00:00",
        }]),
    )
    wf = {"id": 1, "path": "x.yml", "name": "X"}
    schedule = recurring_forge._workflow_schedule(_forge(), wf, branch="main")  # noqa: SLF001
    assert schedule is not None
    assert schedule.manual is False
    assert schedule.cron_entries == ("*/30 * * * *",)
    assert schedule.last_run_succeeded is True
    assert schedule.last_run_duration_seconds == pytest.approx(300.0)
    assert [name for name, _args, _kwargs in fake.calls] == ["repo_file", "latest_workflow_run"]


def test_scheduled_ci_workflows_unknown_when_list_fails(fake: FakeForge) -> None:
    always(fake, "workflows", rows_read(None, outcome="failed", returncode=1))
    workflows, provenance = recurring_forge._scheduled_ci_workflows(  # noqa: SLF001
        _forge(), branch="main"
    )
    assert workflows == ()
    assert provenance.status == "unknown"
    assert provenance.detail == "could not list this repo's workflow configuration"


def test_scheduled_ci_workflows_filters_out_of_scope(
    fake: FakeForge, script: ForgeScript
) -> None:
    always(
        fake, "workflows",
        rows_read([
            {"id": 1, "path": "sched.yml", "name": "Sched"},
            {"id": 2, "path": "push.yml", "name": "Push"},
        ]),
    )
    script.queue(
        "repo_file",
        _content_file("on:\n  schedule:\n    - cron: '0 2 * * *'\n"),
        _content_file("on:\n  push:\n"),
    )
    script.queue("latest_workflow_run", rows_read([]))
    workflows, provenance = recurring_forge._scheduled_ci_workflows(  # noqa: SLF001
        _forge(), branch="main"
    )
    assert [w.name for w in workflows] == ["Sched"]
    assert provenance.status == "fresh"
    assert provenance.detail == "from gh actions workflow API"


def test_schedule_interval_none_when_schedule_not_dict() -> None:
    assert recurring_forge._schedule_interval({"schedule": "nope"}) is None  # noqa: SLF001


def test_schedule_interval_none_when_interval_not_string() -> None:
    assert recurring_forge._schedule_interval({"schedule": {"interval": 1}}) is None  # noqa: SLF001


def test_schedule_interval_success() -> None:
    assert recurring_forge._schedule_interval({"schedule": {"interval": "daily"}}) == "daily"  # noqa: SLF001


def test_parse_dependabot_updates_none_when_unparseable() -> None:
    assert recurring_forge._parse_dependabot_updates(": : :") is None  # noqa: SLF001


def test_parse_dependabot_updates_empty_when_updates_not_list() -> None:
    assert recurring_forge._parse_dependabot_updates("version: 2\n") == []  # noqa: SLF001


def test_parse_dependabot_updates_filters_bad_entries() -> None:
    text = (
        "updates:\n"
        "  - package-ecosystem: pip\n"
        "    schedule:\n"
        "      interval: daily\n"
        "  - package-ecosystem: ''\n"
        "  - not-a-mapping\n"
        "  - schedule:\n"
        "      interval: weekly\n"
    )
    entries = recurring_forge._parse_dependabot_updates(text)  # noqa: SLF001
    assert entries == [("pip", "daily")]


def test_fetch_dependabot_text_found_on_first_path(fake: FakeForge) -> None:
    always(fake, "repo_file", _content_file("updates: []\n"))
    result = recurring_forge._fetch_dependabot_text(_forge(), branch="main")  # noqa: SLF001
    assert result == ("updates: []\n", False)


def test_fetch_dependabot_text_falls_back_to_yaml_extension(
    fake: FakeForge, script: ForgeScript
) -> None:
    script.queue("repo_file", _absent_file(), _content_file("updates: []\n"))
    result = recurring_forge._fetch_dependabot_text(_forge(), branch="main")  # noqa: SLF001
    assert result == ("updates: []\n", False)
    assert [args for args, _kwargs in script.calls("repo_file")] == [
        (".github/dependabot.yml",), (".github/dependabot.yaml",),
    ]


def test_fetch_dependabot_text_confirmed_absent(script: ForgeScript) -> None:
    script.queue("repo_file", _absent_file(), _absent_file())
    result = recurring_forge._fetch_dependabot_text(_forge(), branch="main")  # noqa: SLF001
    assert result == (None, True)


def test_fetch_dependabot_text_non_404_failure_short_circuits(script: ForgeScript) -> None:
    script.queue("repo_file", _forbidden_file())
    result = recurring_forge._fetch_dependabot_text(_forge(), branch="main")  # noqa: SLF001
    assert result == (None, False)
    assert len(script.calls("repo_file")) == 1


def test_latest_dependabot_pr_none_when_empty(fake: FakeForge) -> None:
    always(fake, "prs_by_author", rows_read([]))
    assert recurring_forge._latest_dependabot_pr(_forge()) == (None, None)  # noqa: SLF001


@pytest.mark.parametrize("outcome", ["failed", "raised"])
def test_latest_dependabot_pr_none_when_the_listing_fails(
    fake: FakeForge, outcome: RowsOutcome
) -> None:
    always(fake, "prs_by_author", rows_read(None, outcome=outcome, returncode=1))
    assert recurring_forge._latest_dependabot_pr(_forge()) == (None, None)  # noqa: SLF001


def test_latest_dependabot_pr_none_when_rows_unparseable(fake: FakeForge) -> None:
    always(fake, "prs_by_author", rows_read(None))
    assert recurring_forge._latest_dependabot_pr(_forge()) == (None, None)  # noqa: SLF001


def test_latest_dependabot_pr_none_when_created_at_missing(fake: FakeForge) -> None:
    always(fake, "prs_by_author", rows_read([{"number": 1}]))
    assert recurring_forge._latest_dependabot_pr(_forge()) == (None, None)  # noqa: SLF001


def test_latest_dependabot_pr_success(fake: FakeForge) -> None:
    always(fake, "prs_by_author", rows_read([{"createdAt": "2026-01-01T00:00:00Z"}]))
    result = recurring_forge._latest_dependabot_pr(_forge())  # noqa: SLF001
    assert result == ("2026-01-01T00:00:00Z", None)


def test_latest_dependabot_pr_asks_for_the_newest_pr_in_any_state(fake: FakeForge) -> None:
    always(fake, "prs_by_author", rows_read([]))
    forge = _forge()
    recurring_forge._latest_dependabot_pr(forge)  # noqa: SLF001
    assert fake.calls == [
        (
            "prs_by_author", ("app/dependabot", ("createdAt", "url")),
            {"forge_target": forge, "state": "all", "limit": 1, "timeout": 30.0},
        ),
    ]


def test_latest_dependabot_pr_reads_past_a_non_object_row_through_the_adapter(
    fake: FakeForge, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = json.dumps(["nope", {"createdAt": "2026-01-01T00:00:00Z", "url": "u"}])
    _adapter_backed(fake, monkeypatch, "prs_by_author", body)
    assert recurring_forge._latest_dependabot_pr(_forge()) == ("2026-01-01T00:00:00Z", "u")  # noqa: SLF001


def test_dependency_ecosystem_updates_confirmed_absent(script: ForgeScript) -> None:
    script.queue("repo_file", _absent_file(), _absent_file())
    updates, provenance = recurring_forge._dependency_ecosystem_updates(  # noqa: SLF001
        _forge(), branch="main"
    )
    assert updates == ()
    assert provenance == repo_status.Provenance(
        status="fresh", detail="no dependabot.yml on the default branch"
    )


def test_dependency_ecosystem_updates_unknown_when_unreadable(script: ForgeScript) -> None:
    script.queue("repo_file", _forbidden_file())
    updates, provenance = recurring_forge._dependency_ecosystem_updates(  # noqa: SLF001
        _forge(), branch="main"
    )
    assert updates == ()
    assert provenance.status == "unknown"
    assert provenance.detail == "could not read this repo's dependabot configuration"


def test_dependency_ecosystem_updates_unknown_when_unparseable(fake: FakeForge) -> None:
    always(fake, "repo_file", _content_file(": : :"))
    updates, provenance = recurring_forge._dependency_ecosystem_updates(  # noqa: SLF001
        _forge(), branch="main"
    )
    assert updates == ()
    assert provenance.status == "unknown"


def test_dependency_ecosystem_updates_empty_declared(fake: FakeForge) -> None:
    always(fake, "repo_file", _content_file("version: 2\nupdates: []\n"))
    updates, provenance = recurring_forge._dependency_ecosystem_updates(  # noqa: SLF001
        _forge(), branch="main"
    )
    assert updates == ()
    assert provenance.status == "fresh"
    assert "no updates" in provenance.detail


def test_dependency_ecosystem_updates_success_with_pr(fake: FakeForge) -> None:
    always(
        fake, "repo_file",
        _content_file(
            "updates:\n  - package-ecosystem: pip\n    schedule:\n      interval: daily\n"
        ),
    )
    always(fake, "prs_by_author", rows_read([{"createdAt": "2026-01-01T00:00:00Z"}]))
    updates, provenance = recurring_forge._dependency_ecosystem_updates(  # noqa: SLF001
        _forge(), branch="main"
    )
    assert len(updates) == 1
    assert updates[0].ecosystem == "pip"
    assert updates[0].schedule_interval == "daily"
    assert updates[0].last_pr_at == "2026-01-01T00:00:00Z"
    assert updates[0].last_pr_provenance.status == "fresh"
    assert provenance.status == "fresh"
    assert provenance.detail == "from this repo's dependabot.yml"


def test_dependency_ecosystem_updates_success_without_pr(fake: FakeForge) -> None:
    always(
        fake, "repo_file",
        _content_file(
            "updates:\n  - package-ecosystem: npm\n    schedule:\n      interval: weekly\n"
        ),
    )
    always(fake, "prs_by_author", rows_read([]))
    updates, _provenance = recurring_forge._dependency_ecosystem_updates(  # noqa: SLF001
        _forge(), branch="main"
    )
    assert updates[0].last_pr_at is None
    assert updates[0].last_pr_provenance.status == "unknown"


def test_refresh_recurring_forge_status_unknown_when_no_credential_and_no_forge_operation_reached(
    monkeypatch: pytest.MonkeyPatch, fake: FakeForge
) -> None:
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: None)  # noqa: ARG005
    status = recurring_forge.refresh_recurring_forge_status(_forge())
    unknown = repo_status.Provenance(
        status="unknown", detail="no credential available for this repo's forge"
    )
    assert status.workflows_provenance == unknown
    assert status.dependency_updates_provenance == unknown
    assert fake.calls == []


def test_refresh_recurring_forge_status_treats_a_whitespace_only_secret_as_no_credential(
    monkeypatch: pytest.MonkeyPatch, fake: FakeForge
) -> None:
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "  \t\n")  # noqa: ARG005
    status = recurring_forge.refresh_recurring_forge_status(_forge())
    detail = "no credential available for this repo's forge"
    assert status.workflows_provenance.detail == detail
    assert status.dependency_updates_provenance.detail == detail
    assert fake.calls == []


def test_refresh_recurring_forge_status_unknown_when_branch_unreadable(
    monkeypatch: pytest.MonkeyPatch, fake: FakeForge
) -> None:
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "tok")  # noqa: ARG005
    always(fake, "default_branch", None)
    status = recurring_forge.refresh_recurring_forge_status(_forge())
    assert status.workflows_provenance.status == "unknown"
    assert status.workflows_provenance.detail == "could not read the repo's default branch"
    assert [name for name, _args, _kwargs in fake.calls] == ["default_branch"]


def test_refresh_recurring_forge_status_success(
    monkeypatch: pytest.MonkeyPatch, fake: FakeForge
) -> None:
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "tok")  # noqa: ARG005
    always(fake, "default_branch", "trunk")
    always(fake, "workflows", rows_read([]))
    always(fake, "repo_file", _absent_file())
    status = recurring_forge.refresh_recurring_forge_status(_forge())
    assert status.workflows == ()
    assert status.workflows_provenance.status == "fresh"
    assert status.workflows_provenance.detail == "from gh actions workflow API"
    assert status.dependency_updates == ()
    assert status.dependency_updates_provenance.detail == "no dependabot.yml on the default branch"


def test_forge_cache_roundtrip(state_root: Path) -> None:
    forge = _forge()
    status = recurring_forge.RecurringForgeStatus(
        fetched_at="2026-01-01T00:00:00+00:00",
        workflows_provenance=repo_status.Provenance(status="fresh", detail="x"),
        dependency_updates_provenance=repo_status.Provenance(status="fresh", detail="y"),
    )
    recurring_forge._write_forge_cache(forge, status)  # noqa: SLF001
    loaded = recurring_forge._read_forge_cache(forge)  # noqa: SLF001
    assert loaded == status


def test_read_forge_cache_returns_none_when_absent(state_root: Path) -> None:
    assert recurring_forge._read_forge_cache(_forge()) is None  # noqa: SLF001


def test_read_forge_cache_returns_none_on_corrupt_json(state_root: Path) -> None:
    path = recurring_forge._forge_cache_path(_forge())  # noqa: SLF001
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json", encoding="utf-8")
    assert recurring_forge._read_forge_cache(_forge()) is None  # noqa: SLF001


def test_write_forge_cache_swallows_oserror(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    status = recurring_forge.RecurringForgeStatus(
        fetched_at="2026-01-01T00:00:00+00:00",
        workflows_provenance=repo_status.Provenance(status="fresh", detail="x"),
        dependency_updates_provenance=repo_status.Provenance(status="fresh", detail="y"),
    )

    def _raise(*_a: Any, **_kw: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", _raise)
    recurring_forge._write_forge_cache(_forge(), status)  # noqa: SLF001


def test_cache_age_s_returns_inf_for_unparseable_timestamp() -> None:
    status = recurring_forge.RecurringForgeStatus(
        fetched_at="not-a-timestamp",
        workflows_provenance=repo_status.Provenance(status="fresh", detail="x"),
        dependency_updates_provenance=repo_status.Provenance(status="fresh", detail="y"),
    )
    assert recurring_forge._cache_age_s(status) == math.inf  # noqa: SLF001


def test_cache_age_s_returns_inf_for_naive_timestamp() -> None:
    status = recurring_forge.RecurringForgeStatus(
        fetched_at="2026-01-01T00:00:00",
        workflows_provenance=repo_status.Provenance(status="fresh", detail="x"),
        dependency_updates_provenance=repo_status.Provenance(status="fresh", detail="y"),
    )
    assert recurring_forge._cache_age_s(status) == math.inf  # noqa: SLF001


def test_cache_age_s_returns_finite_seconds_for_valid_timestamp() -> None:
    status = recurring_forge.RecurringForgeStatus(
        fetched_at=datetime.now(UTC).isoformat(),
        workflows_provenance=repo_status.Provenance(status="fresh", detail="x"),
        dependency_updates_provenance=repo_status.Provenance(status="fresh", detail="y"),
    )
    assert recurring_forge._cache_age_s(status) < 1.0  # noqa: SLF001


def test_inflight_key_is_namespaced() -> None:
    assert recurring_forge._inflight_key(_forge()) == "recurring:acme/widget"  # noqa: SLF001


def test_schedule_refresh_coalesces_duplicate_inflight(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, ...]] = []

    class _CapturingExecutor:
        def submit(self, fn: Any, *args: Any) -> None:
            calls.append((fn, *args))

    monkeypatch.setattr(recurring_forge, "REFRESH_EXECUTOR", _CapturingExecutor())
    forge = _forge()
    pubsub = PubSub()
    assert recurring_forge.schedule_refresh(forge, pubsub) is True
    assert recurring_forge.schedule_refresh(forge, pubsub) is False
    assert len(calls) == 1
    assert calls[0][0] is recurring_forge._do_refresh_and_publish  # noqa: SLF001


def test_do_refresh_and_publish_writes_cache_and_publishes(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: None)  # noqa: ARG005
    forge = _forge()
    bus = PubSub()
    events: list[teams_base.TeamEvent] = []
    bus.subscribe_all(events.append)
    recurring_forge._do_refresh_and_publish(forge, bus)  # noqa: SLF001
    assert len(events) == 1
    assert events[0].kind == "recurring_status_refreshed"
    assert events[0].payload == {"slug": "acme/widget"}
    assert recurring_forge._read_forge_cache(forge) is not None  # noqa: SLF001


def _recording_schedule_refresh(calls: list[ForgeTarget]) -> Any:
    def _fake(forge: ForgeTarget, pubsub: PubSub) -> bool:
        del pubsub
        calls.append(forge)
        return True

    return _fake


def test_forge_status_cache_miss_schedules_refresh_and_returns_none(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[ForgeTarget] = []
    monkeypatch.setattr(recurring_forge, "schedule_refresh", _recording_schedule_refresh(calls))
    result = recurring_forge.forge_status(_forge(), pubsub=PubSub())
    assert result is None
    assert calls == [_forge()]


def test_forge_status_stale_cache_schedules_refresh_and_returns_cached(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    forge = _forge()
    status = recurring_forge.RecurringForgeStatus(
        fetched_at=(
            datetime.now(UTC) - timedelta(seconds=recurring_forge.RECURRING_FORGE_TTL_S + 1)
        ).isoformat(),
        workflows_provenance=repo_status.Provenance(status="fresh", detail="x"),
        dependency_updates_provenance=repo_status.Provenance(status="fresh", detail="y"),
    )
    recurring_forge._write_forge_cache(forge, status)  # noqa: SLF001
    calls: list[ForgeTarget] = []
    monkeypatch.setattr(recurring_forge, "schedule_refresh", _recording_schedule_refresh(calls))
    result = recurring_forge.forge_status(forge, pubsub=PubSub())
    assert result == status
    assert calls == [forge]


def test_forge_status_fresh_cache_served_without_scheduling_refresh(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    forge = _forge()
    status = recurring_forge.RecurringForgeStatus(
        fetched_at=datetime.now(UTC).isoformat(),
        workflows_provenance=repo_status.Provenance(status="fresh", detail="x"),
        dependency_updates_provenance=repo_status.Provenance(status="fresh", detail="y"),
    )
    recurring_forge._write_forge_cache(forge, status)  # noqa: SLF001
    calls: list[ForgeTarget] = []
    monkeypatch.setattr(recurring_forge, "schedule_refresh", _recording_schedule_refresh(calls))
    result = recurring_forge.forge_status(forge, pubsub=PubSub())
    assert result == status
    assert calls == []
