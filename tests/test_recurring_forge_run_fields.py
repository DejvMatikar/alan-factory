from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, cast

import pytest

from alan import recurring_forge
from alan.engine_config import ForgeTarget, RepoIdentity
from alan.forge import FileRead, RowsOutcome
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, always, file_read, rows_read

_FORGE_CREDENTIAL = "ACME_GH_TOKEN"  # noqa: S105


def _forge(owner: str = "acme", repo: str = "widget") -> ForgeTarget:
    return ForgeTarget(
        repo=RepoIdentity(owner=owner, repo=repo),
        credential_env_name=_FORGE_CREDENTIAL,
        authoritative_checks=frozenset(),
    )


def _content_file(text: str) -> FileRead:
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return file_read(json.dumps({"content": encoded, "encoding": "base64"}))


@pytest.fixture
def fake(_active_fake_forge: FakeForge) -> FakeForge:
    return _active_fake_forge


@pytest.fixture
def script(fake: FakeForge) -> ForgeScript:
    return ForgeScript(fake)


@pytest.fixture
def state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "state"
    monkeypatch.setenv("ALAN_STATE_ROOT", str(root))
    return root


def test_run_status_none_when_run_none() -> None:
    assert recurring_forge._run_status(None) is None  # noqa: SLF001


def test_run_status_none_when_key_missing() -> None:
    assert recurring_forge._run_status({}) is None  # noqa: SLF001


def test_run_status_none_when_non_string() -> None:
    assert recurring_forge._run_status({"status": 1}) is None  # noqa: SLF001


def test_run_status_returns_in_progress() -> None:
    assert recurring_forge._run_status({"status": "in_progress"}) == "in_progress"  # noqa: SLF001


def test_run_status_returns_queued() -> None:
    assert recurring_forge._run_status({"status": "queued"}) == "queued"  # noqa: SLF001


def test_run_status_returns_completed() -> None:
    assert recurring_forge._run_status({"status": "completed"}) == "completed"  # noqa: SLF001


def test_run_html_url_none_when_run_none() -> None:
    assert recurring_forge._run_html_url(None) is None  # noqa: SLF001


def test_run_html_url_none_when_key_missing() -> None:
    assert recurring_forge._run_html_url({}) is None  # noqa: SLF001


def test_run_html_url_none_when_non_string() -> None:
    assert recurring_forge._run_html_url({"html_url": 1}) is None  # noqa: SLF001


def test_run_html_url_returns_value() -> None:
    run = {"html_url": "https://github.com/acme/widget/actions/runs/1"}
    assert recurring_forge._run_html_url(run) == "https://github.com/acme/widget/actions/runs/1"  # noqa: SLF001


def test_latest_dependabot_pr_empty_list_returns_none_tuple(fake: FakeForge) -> None:
    always(fake, "prs_by_author", rows_read([]))
    assert recurring_forge._latest_dependabot_pr(_forge()) == (None, None)  # noqa: SLF001


@pytest.mark.parametrize("outcome", ["failed", "raised"])
def test_latest_dependabot_pr_failure_returns_none_tuple(
    fake: FakeForge, outcome: RowsOutcome
) -> None:
    always(
        fake, "prs_by_author",
        rows_read(None, outcome=outcome, returncode=1, stderr="gh: Forbidden (HTTP 403)"),
    )
    assert recurring_forge._latest_dependabot_pr(_forge()) == (None, None)  # noqa: SLF001


def test_latest_dependabot_pr_returns_created_at_and_url(fake: FakeForge) -> None:
    always(
        fake, "prs_by_author",
        rows_read([{
            "createdAt": "2026-01-01T00:00:00Z",
            "url": "https://github.com/acme/widget/pull/9",
        }]),
    )
    result = recurring_forge._latest_dependabot_pr(_forge())  # noqa: SLF001
    assert result == ("2026-01-01T00:00:00Z", "https://github.com/acme/widget/pull/9")


def test_latest_dependabot_pr_url_none_when_non_string(fake: FakeForge) -> None:
    always(fake, "prs_by_author", rows_read([{"createdAt": "2026-01-01T00:00:00Z", "url": 1}]))
    result = recurring_forge._latest_dependabot_pr(_forge())  # noqa: SLF001
    assert result == ("2026-01-01T00:00:00Z", None)


def test_workflow_schedule_wires_status_conclusion_and_url(
    fake: FakeForge, script: ForgeScript
) -> None:
    script.queue(
        "repo_file", _content_file("on:\n  schedule:\n    - cron: '*/30 * * * *'\n")
    )
    script.queue(
        "latest_workflow_run",
        rows_read([{
            "status": "completed",
            "conclusion": "failure",
            "html_url": "https://github.com/acme/widget/actions/runs/7",
            "run_started_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:05:00+00:00",
        }]),
    )
    wf = {"id": 1, "path": "x.yml", "name": "X"}
    schedule = recurring_forge._workflow_schedule(_forge(), wf, branch="main")  # noqa: SLF001
    assert schedule is not None
    assert schedule.last_run_status == "completed"
    assert schedule.last_run_conclusion == "failure"
    assert schedule.last_run_url == "https://github.com/acme/widget/actions/runs/7"


def test_workflow_schedule_new_fields_none_when_no_run(
    fake: FakeForge, script: ForgeScript
) -> None:
    script.queue("repo_file", _content_file("on:\n  workflow_dispatch:\n"))
    script.queue("latest_workflow_run", rows_read([]))
    wf = {"id": 1, "path": "x.yml", "name": "X"}
    schedule = recurring_forge._workflow_schedule(_forge(), wf, branch="main")  # noqa: SLF001
    assert schedule is not None
    assert schedule.last_run_status is None
    assert schedule.last_run_conclusion is None
    assert schedule.last_run_url is None
    assert schedule.last_run_confirmed_absent is True


def test_dependency_ecosystem_updates_carries_last_pr_url(
    fake: FakeForge, script: ForgeScript
) -> None:
    script.queue(
        "repo_file",
        _content_file(
            "updates:\n  - package-ecosystem: pip\n    schedule:\n      interval: daily\n"
        ),
    )
    script.queue(
        "prs_by_author",
        rows_read([{
            "createdAt": "2026-01-01T00:00:00Z",
            "url": "https://github.com/acme/widget/pull/3",
        }]),
    )
    updates, _provenance = recurring_forge._dependency_ecosystem_updates(  # noqa: SLF001
        _forge(), branch="main"
    )
    assert updates[0].last_pr_url == "https://github.com/acme/widget/pull/3"


def test_dependency_ecosystem_updates_last_pr_url_none_when_no_pr(
    fake: FakeForge, script: ForgeScript
) -> None:
    script.queue(
        "repo_file",
        _content_file(
            "updates:\n  - package-ecosystem: npm\n    schedule:\n      interval: weekly\n"
        ),
    )
    script.queue("prs_by_author", rows_read([]))
    updates, _provenance = recurring_forge._dependency_ecosystem_updates(  # noqa: SLF001
        _forge(), branch="main"
    )
    assert updates[0].last_pr_url is None


def test_forge_cache_validates_payload_missing_new_fields(state_root: Path) -> None:
    forge = _forge()
    legacy_payload = {
        "fetched_at": "2026-01-01T00:00:00+00:00",
        "workflows": [
            {
                "workflow_id": 1,
                "name": "Nightly",
                "manual": False,
                "cron_entries": ["0 2 * * *"],
                "last_run_provenance": {"status": "fresh", "detail": "x"},
                "last_run_at": None,
                "last_run_succeeded": None,
                "last_run_outcome": None,
                "last_run_duration_seconds": None,
            }
        ],
        "workflows_provenance": {"status": "fresh", "detail": "x"},
        "dependency_updates": [
            {
                "ecosystem": "pip",
                "schedule_interval": "daily",
                "last_pr_provenance": {"status": "fresh", "detail": "y"},
                "last_pr_at": None,
            }
        ],
        "dependency_updates_provenance": {"status": "fresh", "detail": "y"},
    }
    path = recurring_forge._forge_cache_path(forge)  # noqa: SLF001
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(legacy_payload), encoding="utf-8")
    loaded = recurring_forge._read_forge_cache(forge)  # noqa: SLF001
    assert loaded is not None
    assert loaded.workflows[0].last_run_status is None
    assert loaded.workflows[0].last_run_conclusion is None
    assert loaded.workflows[0].last_run_url is None
    assert loaded.workflows[0].last_run_confirmed_absent is False
    assert loaded.dependency_updates[0].last_pr_url is None


def test_run_status_and_html_url_are_siblings_of_run_succeeded() -> None:
    run = cast(
        "dict[str, Any]",
        {
            "status": "in_progress",
            "conclusion": None,
            "html_url": "https://github.com/acme/widget/actions/runs/2",
        },
    )
    assert recurring_forge._run_succeeded(run) is None  # noqa: SLF001
    assert recurring_forge._run_status(run) == "in_progress"  # noqa: SLF001
    assert recurring_forge._run_html_url(run) == "https://github.com/acme/widget/actions/runs/2"  # noqa: SLF001
