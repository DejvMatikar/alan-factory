from __future__ import annotations

import json
import logging
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from alan import default_profile, engine_config, engine_registry_store, roots, secrets_store
from alan import engine_preflight as ep
from alan.errors import AppError
from tests.fakes.forge import FakeForge
from tests.readiness_support import (
    CODE_QUALITY,
    CONNECTED_SLUG,
    DEFAULT_SLUG,
    NOT_READY_CASES,
    ORGANIZATION,
    PROJECT_KEY,
    REVIEWER,
    REVIEWER_FINGERPRINT,
    Install,
    yesterday,
)


@pytest.fixture
def install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Install:
    return Install.isolated(tmp_path, monkeypatch)


def _states(issues: tuple[ep.ReadinessIssue, ...]) -> dict[str, str]:
    return {issue.requirement: issue.state for issue in issues}


def _far_future() -> str:
    return (datetime.now(UTC).date() + timedelta(days=30)).isoformat()


def _tomorrow() -> str:
    return (datetime.now(UTC).date() + timedelta(days=1)).isoformat()


def _reviewer_path() -> Path:
    return secrets_store._key_path(DEFAULT_SLUG, secrets_store.REVIEWER_PURPOSE)  # noqa: SLF001


def _refuse_reads_of(monkeypatch: pytest.MonkeyPatch, target: Path) -> None:
    real_read_text = Path.read_text

    def _refuse(self: Path, *args: Any, **kwargs: Any) -> str:
        if self == target:
            raise PermissionError(13, "denied", str(self))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _refuse)


def test_an_install_without_requirements_is_ready_and_reads_nothing(
    install: Install, monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    install.require()

    def _fail(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a requirement-free install performed a read")

    monkeypatch.setattr(secrets_store, "reviewer_record_state", _fail)
    monkeypatch.setattr(secrets_store, "reviewer_record_detail", _fail)
    monkeypatch.setattr(ep, "read_code_quality_latch", _fail)
    monkeypatch.setattr(ep, "load_last_preflight", _fail)
    assert ep.evaluate_readiness(install.default_cfg()) == ()
    assert ep.readiness_report() == []
    assert _active_fake_forge.calls == []


def test_an_unconfigured_install_is_always_not_ready(install: Install) -> None:
    install.leave_unconfigured()
    (issue,) = ep.evaluate_readiness(install.default_cfg())
    assert issue.state == "not_ready"
    assert issue.requirement == ep.DEFAULT_ENGINE_REQUIREMENT
    assert issue.fix == "./setup.sh"


def test_a_missing_reviewer_credential_is_not_ready(install: Install) -> None:
    install.require(REVIEWER)
    (issue,) = ep.evaluate_readiness(install.default_cfg())
    assert (issue.engine, issue.requirement, issue.state) == (
        DEFAULT_SLUG,
        "reviewer_credential",
        "not_ready",
    )


def test_the_environment_credential_never_counts_as_a_stored_reviewer(
    install: Install, monkeypatch: pytest.MonkeyPatch
) -> None:
    install.require(REVIEWER)
    monkeypatch.setenv("ALAN_VERIFIER_GH_TOKEN", "signed-in-account-token")
    (issue,) = ep.evaluate_readiness(install.default_cfg())
    assert issue.state == "not_ready"


def test_a_stored_reviewer_with_no_value_is_not_ready(install: Install) -> None:
    install.require(REVIEWER)
    install.store_reviewer()
    _reviewer_path().write_text(json.dumps({"value": "  "}), encoding="utf-8")
    (issue,) = ep.evaluate_readiness(install.default_cfg())
    assert issue.state == "not_ready"


@pytest.mark.parametrize("expires_at", [yesterday(), datetime.now(UTC).date().isoformat()])
def test_a_reviewer_credential_on_or_after_its_expiry_date_is_not_ready(
    install: Install, expires_at: str
) -> None:
    install.require(REVIEWER)
    install.store_reviewer(expires_at=expires_at)
    install.save_preflight("pass")
    (issue,) = ep.evaluate_readiness(install.default_cfg())
    assert issue.state == "not_ready"
    assert "expired" in issue.message


@pytest.mark.parametrize("expires_at", [None, _far_future()])
def test_a_reviewer_credential_before_its_expiry_or_without_one_is_not_expired(
    install: Install, expires_at: str | None
) -> None:
    install.require(REVIEWER)
    install.store_reviewer(expires_at=expires_at)
    install.save_preflight("pass")
    assert ep.evaluate_readiness(install.default_cfg()) == ()


def test_an_unparseable_recorded_expiry_counts_as_not_expired(install: Install) -> None:
    install.require(REVIEWER)
    install.store_reviewer()
    _reviewer_path().write_text(
        json.dumps(
            {"value": "v", "expires_at": "garbage", "fingerprint": REVIEWER_FINGERPRINT}
        ),
        encoding="utf-8",
    )
    install.save_preflight("pass")
    assert ep.evaluate_readiness(install.default_cfg()) == ()


def test_an_unreadable_reviewer_record_is_unknown(
    install: Install, monkeypatch: pytest.MonkeyPatch
) -> None:
    install.require(REVIEWER)
    install.store_reviewer()
    _refuse_reads_of(monkeypatch, _reviewer_path())
    (issue,) = ep.evaluate_readiness(install.default_cfg())
    assert issue.state == "unknown"
    assert "cannot be read" in issue.message


@pytest.mark.parametrize("content", ["not json", "[1, 2]"])
def test_a_reviewer_record_that_is_not_a_json_object_is_unknown(
    install: Install, content: str
) -> None:
    install.require(REVIEWER)
    install.store_reviewer()
    _reviewer_path().write_text(content, encoding="utf-8")
    (issue,) = ep.evaluate_readiness(install.default_cfg())
    assert issue.state == "unknown"


def test_a_reviewer_record_without_a_fingerprint_cannot_be_confirmed_and_is_unknown(
    install: Install,
) -> None:
    install.require(REVIEWER)
    _reviewer_path().parent.mkdir(parents=True, exist_ok=True)
    _reviewer_path().write_text(json.dumps({"value": "v"}), encoding="utf-8")
    install.save_preflight("pass")
    (issue,) = ep.evaluate_readiness(install.default_cfg())
    assert issue.state == "unknown"


def test_no_saved_preflight_leaves_distinctness_unknown(install: Install) -> None:
    install.require(REVIEWER)
    install.store_reviewer()
    (issue,) = ep.evaluate_readiness(install.default_cfg())
    assert issue.state == "unknown"
    assert "preflight" in issue.message


def test_a_saved_preflight_that_found_the_reviewer_identical_is_not_ready(
    install: Install,
) -> None:
    install.require(REVIEWER)
    install.store_reviewer()
    install.save_preflight("fail")
    (issue,) = ep.evaluate_readiness(install.default_cfg())
    assert issue.state == "not_ready"
    assert "same account" in issue.message


def test_a_saved_preflight_that_found_the_reviewer_distinct_is_ready(install: Install) -> None:
    install.require(REVIEWER)
    install.store_reviewer()
    install.save_preflight("pass")
    assert ep.evaluate_readiness(install.default_cfg()) == ()


def test_a_saved_preflight_that_could_not_tell_leaves_distinctness_unknown(
    install: Install,
) -> None:
    install.require(REVIEWER)
    install.store_reviewer()
    install.save_preflight("unknown")
    (issue,) = ep.evaluate_readiness(install.default_cfg())
    assert issue.state == "unknown"


def test_a_saved_preflight_without_a_distinctness_finding_is_unknown(install: Install) -> None:
    install.require(REVIEWER)
    install.store_reviewer()
    saved = install.save_preflight("fail")
    stripped = saved.model_copy(update={"findings": ()})
    (issue,) = ep.evaluate_readiness(install.default_cfg(), preflight=stripped)
    assert issue.state == "unknown"


def test_a_saved_verdict_goes_stale_when_the_reviewer_credential_is_rotated(
    install: Install,
) -> None:
    install.require(REVIEWER)
    install.store_reviewer()
    install.save_preflight("fail")
    secrets_store.write_secret(DEFAULT_SLUG, secrets_store.REVIEWER_PURPOSE, "rotated-value")
    (issue,) = ep.evaluate_readiness(install.default_cfg())
    assert issue.state == "unknown"


def test_a_saved_preflight_taken_without_a_stored_reviewer_cannot_vouch_for_one(
    install: Install,
) -> None:
    install.require(REVIEWER)
    install.save_preflight("pass", fingerprint=None)
    install.store_reviewer()
    (issue,) = ep.evaluate_readiness(install.default_cfg())
    assert issue.state == "unknown"


def test_a_saved_verdict_survives_an_unrelated_rewrite_of_the_reviewer_record(
    install: Install,
) -> None:
    install.require(REVIEWER)
    install.store_reviewer(expires_at=_tomorrow())
    install.save_preflight("fail")
    warned = secrets_store.due_expiry_warnings(datetime.now(UTC))
    assert [meta.name for meta in warned] == [f"{DEFAULT_SLUG}:reviewer"]
    issues = ep.evaluate_readiness(install.default_cfg())
    assert any(issue.state == "not_ready" for issue in issues)


def test_a_fresh_preflight_can_be_supplied_directly(install: Install) -> None:
    install.require(REVIEWER)
    install.store_reviewer()
    fresh = install.save_preflight("pass")
    assert ep.evaluate_readiness(install.default_cfg(), preflight=fresh) == ()


def test_a_required_code_quality_without_a_project_in_the_profile_is_not_ready(
    install: Install,
) -> None:
    install.require(CODE_QUALITY, code_quality=False)
    (issue,) = ep.evaluate_readiness(install.default_cfg())
    assert (issue.requirement, issue.state) == ("code_quality", "not_ready")
    assert "no SonarCloud project" in issue.message


def test_code_quality_is_not_ready_until_sonarcloud_has_reported(install: Install) -> None:
    install.require(CODE_QUALITY)
    (issue,) = ep.evaluate_readiness(install.default_cfg())
    assert issue.state == "not_ready"
    assert "has not reported" in issue.message


@pytest.mark.parametrize(
    ("organization", "project_key"),
    [("another-org", PROJECT_KEY), (ORGANIZATION, "acme-org_other"), ("another-org", "x")],
)
def test_a_latch_recorded_for_a_different_organization_or_project_does_not_count(
    install: Install, organization: str, project_key: str
) -> None:
    install.require(CODE_QUALITY)
    install.record_latch(organization, project_key)
    (issue,) = ep.evaluate_readiness(install.default_cfg())
    assert issue.state == "not_ready"
    assert "different organization or project" in issue.message


def test_a_latch_matching_the_profile_makes_code_quality_ready(install: Install) -> None:
    install.require(CODE_QUALITY)
    install.record_latch()
    assert ep.evaluate_readiness(install.default_cfg()) == ()


def test_a_latch_stops_counting_when_the_profile_project_changes(
    install: Install, monkeypatch: pytest.MonkeyPatch
) -> None:
    install.require(CODE_QUALITY)
    install.record_latch()
    assert ep.evaluate_readiness(install.default_cfg()) == ()
    moved = default_profile.CodeQualityProfile(organization=ORGANIZATION, project_key="renamed")
    profile = default_profile.resolved().profile.model_copy(update={"code_quality": moved})
    resolved = default_profile.Resolved(profile, default_profile.Source.profile)
    monkeypatch.setattr(default_profile, "resolved", lambda: resolved)
    (issue,) = ep.evaluate_readiness(install.default_cfg())
    assert issue.state == "not_ready"


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps({"version": 2, "organization": "o", "project_key": "p", "recorded_at": "t"}),
        json.dumps({"version": 1, "organization": "o", "project_key": "p"}),
        json.dumps(
            {"version": 1, "organization": "o", "project_key": "p", "recorded_at": "t", "x": 1}
        ),
    ],
)
def test_a_malformed_latch_is_unknown_not_ready(install: Install, content: str) -> None:
    install.require(CODE_QUALITY)
    install.record_latch()
    ep.code_quality_latch_path().write_text(content, encoding="utf-8")
    (issue,) = ep.evaluate_readiness(install.default_cfg())
    assert issue.state == "unknown"


def _rewrite_latch_timestamp(recorded_at: str) -> None:
    path = ep.code_quality_latch_path()
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["recorded_at"] = recorded_at
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.parametrize(
    "recorded_at",
    ["not-a-timestamp", "", "2026-09-27T00:00:00", "2026-09-27T00:00:00+02:00"],
)
def test_a_latch_without_a_utc_timestamp_is_unknown_not_ready(
    install: Install, recorded_at: str
) -> None:
    install.require(CODE_QUALITY)
    install.record_latch()
    _rewrite_latch_timestamp(recorded_at)
    (issue,) = ep.evaluate_readiness(install.default_cfg())
    assert issue.state == "unknown"


@pytest.mark.parametrize("recorded_at", ["2026-09-27T00:00:00Z", "2026-09-27T00:00:00+00:00"])
def test_a_latch_stamped_in_utc_counts(install: Install, recorded_at: str) -> None:
    install.require(CODE_QUALITY)
    install.record_latch()
    _rewrite_latch_timestamp(recorded_at)
    assert ep.evaluate_readiness(install.default_cfg()) == ()


def test_an_unreadable_latch_is_unknown(install: Install, monkeypatch: pytest.MonkeyPatch) -> None:
    install.require(CODE_QUALITY)
    install.record_latch()
    _refuse_reads_of(monkeypatch, ep.code_quality_latch_path())
    (issue,) = ep.evaluate_readiness(install.default_cfg())
    assert issue.state == "unknown"


def test_the_latch_is_versioned_json_at_the_engines_root_with_owner_only_mode(
    install: Install,
) -> None:
    latch = ep.write_code_quality_latch(ORGANIZATION, PROJECT_KEY)
    path = roots.engines_root() / "code_quality.latch"
    assert ep.code_quality_latch_path() == path
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {"version", "organization", "project_key", "recorded_at"}
    assert payload["version"] == 1
    assert (payload["organization"], payload["project_key"]) == (ORGANIZATION, PROJECT_KEY)
    assert datetime.fromisoformat(payload["recorded_at"]).tzinfo is not None
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert ep.read_code_quality_latch() == latch


def test_the_latch_can_never_be_mistaken_for_an_engine_record(install: Install) -> None:
    ep.write_code_quality_latch(ORGANIZATION, PROJECT_KEY)
    assert ep.code_quality_latch_path().suffix != ".json"
    assert list(roots.engines_root().glob("*.json")) == []
    assert engine_registry_store.list_engines() == []


def test_writing_the_latch_again_replaces_the_previous_record(install: Install) -> None:
    ep.write_code_quality_latch("first-org", "first")
    ep.write_code_quality_latch(ORGANIZATION, PROJECT_KEY)
    latch = ep.read_code_quality_latch()
    assert latch is not None
    assert (latch.organization, latch.project_key) == (ORGANIZATION, PROJECT_KEY)
    assert [p.name for p in roots.engines_root().iterdir()] == ["code_quality.latch"]


def test_reading_an_absent_latch_returns_none(install: Install) -> None:
    assert ep.read_code_quality_latch() is None


def test_duplicate_requirement_names_are_evaluated_once(install: Install) -> None:
    install.require(REVIEWER, REVIEWER)
    assert len(ep.evaluate_readiness(install.default_cfg())) == 1


def test_a_checker_that_raises_becomes_unknown_and_the_other_requirements_still_run(
    install: Install, monkeypatch: pytest.MonkeyPatch
) -> None:
    install.require(REVIEWER, CODE_QUALITY)

    def _boom(_engine: str) -> Any:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(secrets_store, "reviewer_record_state", _boom)
    issues = ep.evaluate_readiness(install.default_cfg())
    assert _states(issues) == {"reviewer_credential": "unknown", "code_quality": "not_ready"}
    assert "RuntimeError" in issues[0].message
    assert "disk on fire" not in issues[0].message


def test_an_error_outside_any_checker_becomes_unknown_and_never_propagates(
    install: Install, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom() -> Any:
        raise OSError("profile store gone")

    cfg = install.default_cfg()
    monkeypatch.setattr(default_profile, "resolved", _boom)
    (issue,) = ep.evaluate_readiness(cfg)
    assert (issue.requirement, issue.state) == (ep.DEFAULT_ENGINE_REQUIREMENT, "unknown")
    assert "OSError" in issue.message


def test_evaluating_readiness_never_reads_from_github(
    install: Install, _active_fake_forge: FakeForge
) -> None:
    for arrange, _requirement in NOT_READY_CASES.values():
        arrange(install)
        ep.evaluate_readiness(install.default_cfg())
        ep.readiness_report()
        ep.blocking_issues(install.default_cfg())
    install.satisfy_everything()
    ep.evaluate_readiness(install.default_cfg())
    assert _active_fake_forge.calls == []


def test_the_default_engine_and_install_requirements_are_fixed_by_setup(
    install: Install,
) -> None:
    install.require(REVIEWER, CODE_QUALITY)
    cfg = install.default_cfg()
    assert ep.readiness_fix("reviewer_credential", cfg) == "./setup.sh"
    assert ep.readiness_fix("code_quality", cfg) == "./setup.sh"
    assert ep.readiness_fix(ep.DEFAULT_ENGINE_REQUIREMENT, cfg) == "./setup.sh"


def test_a_connected_repositorys_reviewer_is_fixed_by_storing_a_credential_for_it(
    install: Install,
) -> None:
    install.require(REVIEWER, CODE_QUALITY)
    connected = install.connect()
    assert (
        ep.readiness_fix("reviewer_credential", connected)
        == f"store a reviewer credential for {CONNECTED_SLUG}"
    )
    assert ep.readiness_fix("code_quality", connected) == "./setup.sh"


def test_requirements_apply_to_every_engine_including_one_connected_later(
    install: Install,
) -> None:
    install.require(REVIEWER)
    install.store_reviewer()
    install.save_preflight("pass")
    connected = install.connect()
    assert ep.evaluate_readiness(install.default_cfg()) == ()
    (issue,) = ep.evaluate_readiness(connected)
    assert (issue.engine, issue.requirement, issue.state) == (
        CONNECTED_SLUG,
        "reviewer_credential",
        "not_ready",
    )
    assert issue.fix == f"store a reviewer credential for {CONNECTED_SLUG}"


def test_a_connected_engines_readiness_is_judged_from_its_own_records(
    install: Install,
) -> None:
    install.require(REVIEWER)
    connected = install.connect()
    install.store_reviewer(CONNECTED_SLUG)
    install.save_preflight("pass", cfg=connected)
    assert ep.evaluate_readiness(connected) == ()


def test_the_report_is_empty_when_every_engine_is_ready(install: Install) -> None:
    install.satisfy_everything()
    assert ep.readiness_report() == []


def test_the_report_names_each_failing_requirement_per_engine_with_its_fix(
    install: Install,
) -> None:
    install.require(REVIEWER, CODE_QUALITY)
    install.store_reviewer()
    install.save_preflight("pass")
    install.connect()
    report = ep.readiness_report()
    assert {(i.engine, i.requirement, i.state) for i in report} == {
        (DEFAULT_SLUG, "code_quality", "not_ready"),
        (CONNECTED_SLUG, "reviewer_credential", "not_ready"),
        (CONNECTED_SLUG, "code_quality", "not_ready"),
    }
    fixes = {(i.engine, i.requirement): i.fix for i in report}
    assert fixes[(CONNECTED_SLUG, "reviewer_credential")] == (
        f"store a reviewer credential for {CONNECTED_SLUG}"
    )
    assert fixes[(DEFAULT_SLUG, "code_quality")] == "./setup.sh"


def test_an_engine_registered_as_the_default_is_reported_once(install: Install) -> None:
    install.require(CODE_QUALITY)
    engine_config.register(install.default_cfg(), default=True, validate_env=False)
    report = ep.readiness_report()
    assert [(i.engine, i.requirement) for i in report] == [(DEFAULT_SLUG, "code_quality")]


def test_an_engine_that_is_not_ready_is_refused_naming_the_engine_requirement_and_fix(
    install: Install,
) -> None:
    install.require(CODE_QUALITY)
    with pytest.raises(AppError) as raised:
        ep.assert_engine_ready(install.default_cfg())
    assert raised.value.status_code == 409
    assert DEFAULT_SLUG in raised.value.detail
    assert "code_quality" in raised.value.detail
    assert "./setup.sh" in raised.value.detail


def test_a_connected_engine_refusal_carries_the_store_a_reviewer_credential_fix(
    install: Install,
) -> None:
    install.require(REVIEWER)
    connected = install.connect()
    with pytest.raises(AppError) as raised:
        ep.assert_engine_ready(connected)
    assert f"store a reviewer credential for {CONNECTED_SLUG}" in raised.value.detail


def test_an_unknown_readiness_is_allowed_through(install: Install) -> None:
    install.require(REVIEWER)
    install.store_reviewer()
    ep.assert_engine_ready(install.default_cfg())
    assert ep.blocking_issues(install.default_cfg()) == ()


def test_a_ready_engine_passes_the_assertion(install: Install) -> None:
    install.satisfy_everything()
    ep.assert_engine_ready(install.default_cfg())


def test_readiness_logs_one_line_per_change_and_one_on_recovery(
    install: Install, caplog: pytest.LogCaptureFixture
) -> None:
    install.require(REVIEWER)
    cfg = install.default_cfg()
    with caplog.at_level(logging.INFO, logger="alan.engine_preflight"):
        ep.evaluate_readiness(cfg)
        ep.evaluate_readiness(cfg)
        install.store_reviewer()
        install.save_preflight("pass")
        ep.evaluate_readiness(cfg)
        ep.evaluate_readiness(cfg)
    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == 2
    assert DEFAULT_SLUG in lines[0]
    assert "reviewer_credential=not_ready" in lines[0]
    assert "ready to dispatch" in lines[1]


def test_an_unknown_readiness_is_logged_as_a_warning(
    install: Install, caplog: pytest.LogCaptureFixture
) -> None:
    install.require(REVIEWER)
    install.store_reviewer()
    with caplog.at_level(logging.INFO, logger="alan.engine_preflight"):
        ep.evaluate_readiness(install.default_cfg())
    (record,) = caplog.records
    assert record.levelno == logging.WARNING
    assert "reviewer_credential=unknown" in record.getMessage()


def test_a_requirement_free_install_logs_nothing(
    install: Install, caplog: pytest.LogCaptureFixture
) -> None:
    install.require()
    with caplog.at_level(logging.DEBUG, logger="alan.engine_preflight"):
        ep.evaluate_readiness(install.default_cfg())
    assert caplog.records == []


def test_no_log_line_or_report_carries_a_credential_value(
    install: Install, caplog: pytest.LogCaptureFixture
) -> None:
    install.require(REVIEWER, CODE_QUALITY)
    install.store_reviewer(expires_at=yesterday())
    with caplog.at_level(logging.DEBUG, logger="alan.engine_preflight"):
        report = ep.readiness_report()
    rendered = json.dumps([i.model_dump() for i in report]) + caplog.text
    assert "reviewer-fixture-value" not in rendered
    assert REVIEWER_FINGERPRINT not in rendered
