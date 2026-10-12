from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from alan import branch_protection_compat as bpc
from alan import default_profile, repo_scaffold, secrets_store
from alan.engine_config import (
    EngineConfig,
    ForgeConfig,
    ForgeTarget,
    Posture,
    RepoIdentity,
    default_engine_config,
    forge_target_for_workspace,
)

_CREDENTIAL_ENV = "ACME_GH_TOKEN"  # noqa: S105
_REPO = RepoIdentity(owner="acme", repo="widget")
_MATRIX_BY_KEY = {row.key: row for row in bpc.COMPATIBILITY_MATRIX}
_REQUIRED_CHECKS_ROW = _MATRIX_BY_KEY["required_checks"]


@pytest.fixture(autouse=True)
def _state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))


@pytest.fixture(autouse=True)
def _engines_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "engines"))


def _completed(
    *, rc: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)


def _queued_run(
    monkeypatch: pytest.MonkeyPatch, responses: list[subprocess.CompletedProcess[str]]
) -> list[list[str]]:
    seen: list[list[str]] = []
    it = iter(responses)

    def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        seen.append(list(cmd))
        return next(it)

    monkeypatch.setattr(subprocess, "run", _run)
    return seen


def _with_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "tok")  # noqa: ARG005


def _pass_protection_for(checks: frozenset[str]) -> dict[str, Any]:
    return {
        "required_conversation_resolution": {"enabled": True},
        "required_pull_request_reviews": {"required_approving_review_count": 0},
        "required_status_checks": {"contexts": sorted(checks), "checks": []},
    }


_FORGE_NO_PIN = ForgeTarget(
    repo=_REPO,
    credential_env_name=_CREDENTIAL_ENV,
    authoritative_checks=frozenset({"gate", "SonarCloud Code Analysis"}),
)

_FORGE_PINNED = ForgeTarget(
    repo=_REPO,
    credential_env_name=_CREDENTIAL_ENV,
    authoritative_checks=frozenset({"gate"}),
    authoritative_check_apps={"gate": 15368},
    enforce_admins=True,
)

_FORGE_MIXED = ForgeTarget(
    repo=_REPO,
    credential_env_name=_CREDENTIAL_ENV,
    authoritative_checks=frozenset({"gate", "e2e"}),
    authoritative_check_apps={"gate": 15368},
)

_FORGE_NON_AUTHORITATIVE_PIN = ForgeTarget(
    repo=_REPO,
    credential_env_name=_CREDENTIAL_ENV,
    authoritative_checks=frozenset({"gate"}),
    authoritative_check_apps={"unrelated": 15368},
)

_FORGE_ADMIN_ONLY = ForgeTarget(
    repo=_REPO,
    credential_env_name=_CREDENTIAL_ENV,
    authoritative_checks=frozenset({"gate"}),
    enforce_admins=True,
)

_FORGE_PIN_AND_ADMIN = ForgeTarget(
    repo=_REPO,
    credential_env_name=_CREDENTIAL_ENV,
    authoritative_checks=frozenset({"gate"}),
    authoritative_check_apps={"gate": 15368},
    enforce_admins=True,
)


def test_protection_put_body_with_no_pins_matches_todays_literal_body() -> None:
    body = bpc._protection_put_body(_FORGE_NO_PIN)  # noqa: SLF001
    assert json.dumps(body) == json.dumps({
        "required_status_checks": {
            "strict": False,
            "contexts": sorted(_FORGE_NO_PIN.authoritative_checks),
        },
        "enforce_admins": None,
        "required_pull_request_reviews": {"required_approving_review_count": 0},
        "required_conversation_resolution": True,
        "restrictions": None,
    })


def test_protection_put_body_pinned_check_has_app_id_and_admin_enforced() -> None:
    body = bpc._protection_put_body(_FORGE_PINNED)  # noqa: SLF001
    assert body["required_status_checks"] == {
        "strict": False, "checks": [{"context": "gate", "app_id": 15368}],
    }
    assert "contexts" not in body["required_status_checks"]
    assert body["enforce_admins"] is True


def test_protection_put_body_mixed_pins_leaves_unpinned_checks_without_app_id() -> None:
    body = bpc._protection_put_body(_FORGE_MIXED)  # noqa: SLF001
    checks = {entry["context"]: entry for entry in body["required_status_checks"]["checks"]}
    assert checks["gate"] == {"context": "gate", "app_id": 15368}
    assert checks["e2e"] == {"context": "e2e"}


def test_protection_put_body_non_authoritative_pin_falls_back_to_contexts() -> None:
    body = bpc._protection_put_body(_FORGE_NON_AUTHORITATIVE_PIN)  # noqa: SLF001
    assert body["required_status_checks"] == {"strict": False, "contexts": ["gate"]}


def test_required_checks_drift_when_live_app_id_is_null() -> None:
    protection = {
        "required_status_checks": {
            "contexts": [], "checks": [{"context": "gate", "app_id": None}],
        },
    }
    finding = bpc._required_checks_finding(_REQUIRED_CHECKS_ROW, protection, _FORGE_PINNED)  # noqa: SLF001
    assert finding.status == "fail"
    assert "'gate'" in finding.remedy
    assert "15368" in finding.remedy


def test_required_checks_drift_when_live_app_id_differs() -> None:
    protection = {
        "required_status_checks": {
            "contexts": [], "checks": [{"context": "gate", "app_id": 999}],
        },
    }
    finding = bpc._required_checks_finding(_REQUIRED_CHECKS_ROW, protection, _FORGE_PINNED)  # noqa: SLF001
    assert finding.status == "fail"
    assert "15368" in finding.remedy


def test_required_checks_drift_when_check_only_listed_in_contexts() -> None:
    protection = {"required_status_checks": {"contexts": ["gate"], "checks": []}}
    finding = bpc._required_checks_finding(_REQUIRED_CHECKS_ROW, protection, _FORGE_PINNED)  # noqa: SLF001
    assert finding.status == "fail"
    assert "15368" in finding.remedy


@pytest.mark.parametrize(
    "checks",
    [None, "gate", {"context": "gate", "app_id": 15368}, 15368],
    ids=["null", "string", "object", "integer"],
)
def test_required_checks_drift_when_live_checks_is_not_a_list(checks: object) -> None:
    protection = {"required_status_checks": {"contexts": ["gate"], "checks": checks}}
    finding = bpc._required_checks_finding(_REQUIRED_CHECKS_ROW, protection, _FORGE_PINNED)  # noqa: SLF001
    assert finding.status == "fail"
    assert "15368" in finding.remedy


def test_required_checks_drift_when_live_checks_key_is_absent() -> None:
    protection = {"required_status_checks": {"contexts": ["gate"]}}
    finding = bpc._required_checks_finding(_REQUIRED_CHECKS_ROW, protection, _FORGE_PINNED)  # noqa: SLF001
    assert finding.status == "fail"
    assert "15368" in finding.remedy


def test_required_checks_drift_when_required_status_checks_block_is_absent() -> None:
    finding = bpc._required_checks_finding(_REQUIRED_CHECKS_ROW, {}, _FORGE_PINNED)  # noqa: SLF001
    assert finding.status == "fail"
    assert "15368" in finding.remedy


def test_required_checks_drift_when_every_live_check_entry_is_not_an_object() -> None:
    protection = {"required_status_checks": {"contexts": ["gate"], "checks": ["gate", None, 15368]}}
    finding = bpc._required_checks_finding(_REQUIRED_CHECKS_ROW, protection, _FORGE_PINNED)  # noqa: SLF001
    assert finding.status == "fail"
    assert "15368" in finding.remedy


def test_required_checks_passes_when_non_object_entries_sit_beside_the_pinned_entry() -> None:
    protection = {
        "required_status_checks": {
            "contexts": [],
            "checks": ["gate", None, {"context": "gate", "app_id": 15368}, 7],
        },
        "enforce_admins": {"enabled": True},
    }
    finding = bpc._required_checks_finding(_REQUIRED_CHECKS_ROW, protection, _FORGE_PINNED)  # noqa: SLF001
    assert finding.status == "pass"


def test_live_check_app_pairs_skips_non_object_entries() -> None:
    protection = {
        "required_status_checks": {
            "checks": [
                "gate",
                None,
                {"context": "gate", "app_id": 15368},
                {"context": "e2e", "app_id": None},
                {"context": "lint", "app_id": True},
            ],
        },
    }
    assert bpc._live_check_app_pairs(protection) == {("gate", 15368)}  # noqa: SLF001


def test_required_checks_passes_when_pin_satisfied() -> None:
    protection = {
        "required_status_checks": {
            "contexts": [], "checks": [{"context": "gate", "app_id": 15368}],
        },
        "enforce_admins": {"enabled": True},
    }
    finding = bpc._required_checks_finding(_REQUIRED_CHECKS_ROW, protection, _FORGE_PINNED)  # noqa: SLF001
    assert finding.status == "pass"
    assert finding.remedy == ""


@pytest.mark.parametrize(
    "protection",
    [
        _pass_protection_for(frozenset({"gate"})),
        {**_pass_protection_for(frozenset({"gate"})), "enforce_admins": {"enabled": False}},
        {**_pass_protection_for(frozenset({"gate"})), "enforce_admins": "not-a-dict"},
    ],
    ids=["missing_block", "enabled_false", "non_dict_block"],
)
def test_required_checks_drift_when_admin_enforcement_missing_or_off(
    protection: dict[str, Any],
) -> None:
    finding = bpc._required_checks_finding(_REQUIRED_CHECKS_ROW, protection, _FORGE_ADMIN_ONLY)  # noqa: SLF001
    assert finding.status == "fail"
    assert "administrators" in finding.remedy


def test_required_checks_passes_when_admin_enforcement_enabled() -> None:
    protection = {**_pass_protection_for(frozenset({"gate"})), "enforce_admins": {"enabled": True}}
    finding = bpc._required_checks_finding(_REQUIRED_CHECKS_ROW, protection, _FORGE_ADMIN_ONLY)  # noqa: SLF001
    assert finding.status == "pass"


def test_required_checks_remedy_joins_pin_and_admin_parts() -> None:
    protection = _pass_protection_for(frozenset({"gate"}))
    finding = bpc._required_checks_finding(  # noqa: SLF001
        _REQUIRED_CHECKS_ROW, protection, _FORGE_PIN_AND_ADMIN
    )
    assert finding.status == "fail"
    assert finding.remedy.count("; ") == 1
    assert "15368" in finding.remedy
    assert "administrators" in finding.remedy


def test_required_checks_remedy_is_exactly_row_remedy_for_superset_only_gap() -> None:
    forge = ForgeTarget(
        repo=_REPO, credential_env_name=_CREDENTIAL_ENV, authoritative_checks=frozenset({"gate"}),
    )
    protection: dict[str, Any] = {"required_status_checks": {"contexts": [], "checks": []}}
    finding = bpc._required_checks_finding(_REQUIRED_CHECKS_ROW, protection, forge)  # noqa: SLF001
    assert finding.remedy == _REQUIRED_CHECKS_ROW.remedy


def test_non_authoritative_pin_never_produces_a_drift_part() -> None:
    protection = {"required_status_checks": {"contexts": ["gate"], "checks": []}}
    finding = bpc._required_checks_finding(  # noqa: SLF001
        _REQUIRED_CHECKS_ROW, protection, _FORGE_NON_AUTHORITATIVE_PIN
    )
    assert finding.status == "pass"


_REPO_JSON_PASS: dict[str, Any] = {
    "default_branch": "main", "allow_squash_merge": True, "delete_branch_on_merge": True,
}


def test_engine_requesting_neither_setting_matches_todays_matrix_length_and_verdicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_token(monkeypatch)
    protection_json = _pass_protection_for(frozenset({"SonarCloud Code Analysis"}))
    _queued_run(monkeypatch, [
        _completed(stdout=json.dumps(_REPO_JSON_PASS)),
        _completed(stdout=json.dumps(protection_json)),
    ])
    findings = bpc.compare_branch_protection(_FORGE_NO_PIN)
    assert len(findings) == len(bpc.COMPATIBILITY_MATRIX)


def test_engine_requesting_neither_setting_reports_todays_fail_verdict_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_token(monkeypatch)
    protection_json = _pass_protection_for(frozenset())
    _queued_run(monkeypatch, [
        _completed(stdout=json.dumps(_REPO_JSON_PASS)),
        _completed(stdout=json.dumps(protection_json)),
    ])
    findings = bpc.compare_branch_protection(_FORGE_NO_PIN)
    by_check = {f.check: f for f in findings}
    assert by_check["required_checks"].status == "fail"
    assert by_check["required_checks"].remedy == _REQUIRED_CHECKS_ROW.remedy
    assert by_check["required_checks"].message == (
        f"does not satisfy: {_REQUIRED_CHECKS_ROW.required_setting}"
    )


def _pinned_cfg() -> EngineConfig:
    forge = ForgeConfig(
        repo=_REPO,
        credential_env_name=_CREDENTIAL_ENV,
        authoritative_checks=frozenset({"gate"}),
        authoritative_check_apps={"gate": 15368},
        enforce_admins=True,
    )
    return default_engine_config().model_copy(update={"repo": _REPO, "forge": forge})


def test_repo_scaffold_protect_sends_pinned_bytes_through_the_stubbed_put(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_token(monkeypatch)
    calls: list[list[str]] = []
    bodies: list[dict[str, Any]] = []

    def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        if "input" in kwargs:
            bodies.append(json.loads(kwargs["input"]))
        if len(calls) == 1:
            return _completed(stdout=json.dumps(_REPO_JSON_PASS))
        return _completed(stdout="{}")

    monkeypatch.setattr(subprocess, "run", _run)
    repo_scaffold.protect(_pinned_cfg())

    put_body = bodies[-1]
    assert put_body["required_status_checks"]["checks"] == [{"context": "gate", "app_id": 15368}]
    assert put_body["enforce_admins"] is True


def test_forge_target_for_workspace_carries_pins_and_admin_switch() -> None:
    cfg = _pinned_cfg()
    target = forge_target_for_workspace(cfg.repo.slug, cfg)
    assert target.authoritative_check_apps == {"gate": 15368}
    assert target.enforce_admins is True


def test_forge_target_reaches_the_default_engine_from_the_profile() -> None:
    profile = default_profile.DefaultProfile(
        owner="acme", repo="widget", posture=Posture.autonomous,
        authoritative_checks=frozenset({"gate"}),
        authoritative_check_apps={"gate": 15368}, enforce_admins=True,
    )
    resolved = default_profile.Resolved(profile, default_profile.Source.owner, None)
    target = ForgeTarget(**resolved.forge_fields())
    assert target.authoritative_check_apps == {"gate": 15368}
    assert target.enforce_admins is True


def test_compare_branch_protection_flags_pin_drift_through_the_stubbed_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forge = ForgeTarget(
        repo=_REPO, credential_env_name=_CREDENTIAL_ENV,
        authoritative_checks=frozenset({"gate"}), authoritative_check_apps={"gate": 15368},
    )
    _with_token(monkeypatch)
    protection_json = _pass_protection_for(frozenset({"gate"}))
    _queued_run(monkeypatch, [
        _completed(stdout=json.dumps(_REPO_JSON_PASS)),
        _completed(stdout=json.dumps(protection_json)),
    ])
    findings = bpc.compare_branch_protection(forge)
    by_check = {f.check: f for f in findings}
    assert by_check["required_checks"].status == "fail"
    assert "15368" in by_check["required_checks"].remedy
