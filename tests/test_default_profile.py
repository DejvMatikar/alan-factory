from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path

import pytest
from pydantic import ValidationError

from alan import default_profile
from alan.default_profile import (
    OWNER_NAME_PATTERN,
    UNCONFIGURED_OWNER,
    UNCONFIGURED_REPO,
    DefaultProfile,
    DefaultProfileError,
    ProfileRequirement,
    Resolved,
    Source,
    resolve,
)
from alan.engine_config import (
    EngineConfig,
    Posture,
    RepoIdentity,
    default_engine_config,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURES = Path(__file__).parent / "fixtures"
_OTHER_REPO = _FIXTURES / "default_profile_other_repo.yaml"
_OWNER_STANDIN = _FIXTURES / "default_profile_owner_standin.yaml"
_MINIMAL = "owner: acme\nrepo: widget\nposture: observe\n"


def _state_dir(tmp_path: Path, profile: str | None = None) -> Path:
    state = tmp_path / "engines"
    state.mkdir()
    if profile is not None:
        (state / "default_profile.yaml").write_text(profile)
    return state


def _owner_file(tmp_path: Path, text: str | None = None) -> Path:
    owner_dir = tmp_path / "owner"
    owner_dir.mkdir()
    path = owner_dir / "default_profile.yaml"
    if text is not None:
        path.write_text(text)
    return path


def _resolve_text(tmp_path: Path, text: str) -> Resolved:
    return resolve(_state_dir(tmp_path, text), _owner_file(tmp_path))


def _unconfigured(tmp_path: Path) -> Resolved:
    return resolve(_state_dir(tmp_path), _owner_file(tmp_path))


def _engine_under(monkeypatch: pytest.MonkeyPatch, resolved: Resolved) -> EngineConfig:
    monkeypatch.setattr(default_profile, "resolved", lambda: resolved)
    return default_engine_config()


def test_profile_naming_another_repository_resolves_that_repositorys_default_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved = resolve(_state_dir(tmp_path, _OTHER_REPO.read_text()), _owner_file(tmp_path))
    engine = _engine_under(monkeypatch, resolved)
    assert resolved.source is Source.profile
    assert engine.repo == RepoIdentity(owner="acme-labs", repo="widget-app")
    assert engine.forge is not None
    assert engine.forge.repo == engine.repo
    assert engine.forge.authoritative_checks == frozenset({"Widget CI"})
    assert engine.code_quality is not None
    assert engine.code_quality.project_key == "acme-labs_widget-app"
    assert engine.code_quality.organization == "acme-labs"


def test_profile_engine_carries_the_profile_posture_ci_and_dependency_sweep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved = resolve(_state_dir(tmp_path, _OTHER_REPO.read_text()), _owner_file(tmp_path))
    engine = _engine_under(monkeypatch, resolved)
    assert engine.posture is Posture.self_merge
    assert engine.ci is not None
    assert engine.ci.main_ci_workflow == "ci.yml"
    assert engine.ci.deploy_workflow is None
    assert engine.dependency_sweep.enabled is True
    assert engine.dependency_sweep.enable_python_vuln_scan is True


def test_profile_without_dependency_sweep_switch_leaves_the_sweep_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _engine_under(monkeypatch, _resolve_text(tmp_path, _MINIMAL))
    assert engine.dependency_sweep.enabled is False
    assert engine.dependency_sweep.enable_python_vuln_scan is False


def test_profile_engine_keeps_the_owner_engine_shape_around_the_profile_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_shaped = default_engine_config()
    engine = _engine_under(monkeypatch, _resolve_text(tmp_path, _OTHER_REPO.read_text()))
    assert engine.roles == owner_shaped.roles
    assert engine.gate_commands == owner_shaped.gate_commands
    assert engine.warmup_commands == owner_shaped.warmup_commands
    assert engine.base_tools == owner_shaped.base_tools
    assert engine.team_tools == owner_shaped.team_tools
    assert engine.transition_graph == owner_shaped.transition_graph
    assert engine.flow_policy == owner_shaped.flow_policy


def test_scenario_1_no_profile_and_a_standin_owner_file_supply_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_file = _owner_file(tmp_path, _OWNER_STANDIN.read_text())
    resolved = resolve(_state_dir(tmp_path), owner_file)
    engine = _engine_under(monkeypatch, resolved)
    assert resolved.source is Source.owner
    assert resolved.path == owner_file
    assert engine.repo.slug == "standin-owner/standin-repo"
    assert engine.posture is Posture.propose
    assert engine.forge is not None
    assert engine.forge.authoritative_checks == frozenset({"Standin Checks"})
    assert engine.code_quality is not None
    assert engine.code_quality.project_key == "standin-org_standin-repo"
    assert engine.ci is not None
    assert engine.ci.deploy_workflow == "standin-deploy.yml"
    assert engine.dependency_sweep.enabled is False


def test_an_installed_profile_wins_over_the_owner_file(tmp_path: Path) -> None:
    owner_file = _owner_file(tmp_path, _OWNER_STANDIN.read_text())
    resolved = resolve(_state_dir(tmp_path, _OTHER_REPO.read_text()), owner_file)
    assert resolved.source is Source.profile
    assert resolved.profile.repo == "widget-app"


def test_scenario_7_no_profile_and_no_owner_file_resolve_to_unconfigured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved = _unconfigured(tmp_path)
    engine = _engine_under(monkeypatch, resolved)
    assert resolved.source is Source.unconfigured
    assert resolved.path is None
    assert engine.repo.slug == "unconfigured.invalid/unconfigured"
    assert engine.forge is None
    assert engine.code_quality is None
    assert engine.ci is None
    assert engine.posture is Posture.observe
    assert engine.dependency_sweep.enabled is False
    assert engine.dependency_sweep.enable_python_vuln_scan is False


def test_unconfigured_engine_keeps_the_owner_engine_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_shaped = default_engine_config()
    engine = _engine_under(monkeypatch, _unconfigured(tmp_path))
    assert engine.roles == owner_shaped.roles
    assert engine.gate_commands == owner_shaped.gate_commands
    assert engine.base_tools == owner_shaped.base_tools
    assert engine.team_tools == owner_shaped.team_tools
    assert engine.transition_graph == owner_shaped.transition_graph
    assert engine.flow_policy == owner_shaped.flow_policy


def test_unconfigured_profile_carries_no_install_settings() -> None:
    profile = default_profile.UNCONFIGURED_PROFILE
    assert profile.authoritative_checks == frozenset()
    assert profile.triage_sweep is False
    assert profile.dependency_sweep is False
    assert profile.prep_chat_origin is None
    assert profile.requirements == []
    assert profile.never_live_budget_s is None
    assert profile.codeowners_repos == frozenset()
    assert profile.ios_bundle_id is None
    assert profile.verifier_login is None


def test_unconfigured_placeholder_owner_is_not_a_legal_github_account_name() -> None:
    assert re.fullmatch(OWNER_NAME_PATTERN, UNCONFIGURED_OWNER) is None
    assert re.fullmatch(OWNER_NAME_PATTERN, "acme-labs") is not None
    with pytest.raises(ValidationError):
        DefaultProfile(owner=UNCONFIGURED_OWNER, repo=UNCONFIGURED_REPO, posture=Posture.observe)


@pytest.mark.parametrize("owner", ["-acme", "acme-", "ac--me", "ac me", "acme.io", "a" * 40, ""])
def test_owner_names_that_github_would_refuse_are_rejected(tmp_path: Path, owner: str) -> None:
    text = f"owner: '{owner}'\nrepo: widget\nposture: observe\n"
    with pytest.raises(DefaultProfileError, match="owner"):
        _resolve_text(tmp_path, text)


@pytest.mark.parametrize("repo", [".", "..", "wid get", "wid/get", "", "a" * 101])
def test_repository_names_that_github_would_refuse_are_rejected(
    tmp_path: Path, repo: str
) -> None:
    text = f"owner: acme\nrepo: '{repo}'\nposture: observe\n"
    with pytest.raises(DefaultProfileError, match="repo"):
        _resolve_text(tmp_path, text)


@pytest.mark.parametrize("repo", [".github", "widget", "my_widget-2.0", "a" * 100])
def test_repository_names_that_github_allows_are_accepted(tmp_path: Path, repo: str) -> None:
    text = f"owner: acme\nrepo: '{repo}'\nposture: observe\n"
    assert _resolve_text(tmp_path, text).profile.repo == repo


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("owner: acme\nrepo: widget\n", "posture: Field required"),
        ("repo: widget\nposture: observe\n", "owner: Field required"),
        ("owner: acme\nposture: observe\n", "repo: Field required"),
        ("owner: acme\nrepo: widget\nposture: supervised\n", "posture: Input should be"),
        (_MINIMAL + "surprise: 1\n", "surprise: Extra inputs are not permitted"),
        (
            _MINIMAL + "code_quality:\n  organization: acme\n  project_key: p\n  host: x\n",
            "code_quality.host: Extra inputs are not permitted",
        ),
        (_MINIMAL + "code_quality:\n  organization: acme\n", "code_quality.project_key"),
        (_MINIMAL + "deploy_workflow: ship.yml\n", "deploy_workflow requires main_ci_workflow"),
        (_MINIMAL + "main_ci_workflow: ''\n", "main_ci_workflow"),
        (_MINIMAL + "never_live_budget_s: 0\n", "never_live_budget_s: Input should be greater"),
        (_MINIMAL + "never_live_budget_s: -5\n", "never_live_budget_s"),
        (_MINIMAL + "dependency_sweep: 'yes'\n", "dependency_sweep"),
        (_MINIMAL + "triage_sweep: 1\n", "triage_sweep"),
        (_MINIMAL + "requirements: not-a-list\n", "requirements"),
        (_MINIMAL + "requirements:\n  - nonsense\n", "requirements.0: Input should be"),
        (_MINIMAL + "authoritative_checks: 3\n", "authoritative_checks"),
        (_MINIMAL + "prep_chat_origin: ''\n", "prep_chat_origin"),
        (_MINIMAL + "codeowners_repos: acme/widget\n", "codeowners_repos"),
        (
            _MINIMAL + "codeowners_repos:\n  - widget\n",
            "codeowners_repos: Value error, codeowners_repos entries must be owner/repo slugs",
        ),
        (_MINIMAL + "ios_bundle_id: ''\n", "ios_bundle_id: String should match pattern"),
        (_MINIMAL + "ios_bundle_id: 3\n", "ios_bundle_id"),
    ],
)
def test_a_malformed_profile_fails_naming_the_file_and_the_problem(
    tmp_path: Path, text: str, problem: str
) -> None:
    with pytest.raises(DefaultProfileError) as raised:
        _resolve_text(tmp_path, text)
    message = str(raised.value)
    assert message.startswith("engines/default_profile.yaml: ")
    assert problem in message


def test_a_malformed_owner_file_names_the_owner_file(tmp_path: Path) -> None:
    owner_file = _owner_file(tmp_path, "owner: acme\nrepo: widget\n")
    with pytest.raises(DefaultProfileError) as raised:
        resolve(_state_dir(tmp_path), owner_file)
    assert str(raised.value).startswith("owner/default_profile.yaml: ")


def test_a_deploy_workflow_without_a_main_one_is_reported_against_the_whole_profile(
    tmp_path: Path,
) -> None:
    with pytest.raises(DefaultProfileError) as raised:
        _resolve_text(tmp_path, _MINIMAL + "deploy_workflow: ship.yml\n")
    assert str(raised.value).startswith("engines/default_profile.yaml: profile: Value error")


def test_every_problem_in_a_malformed_profile_is_reported_together(tmp_path: Path) -> None:
    with pytest.raises(DefaultProfileError) as raised:
        _resolve_text(tmp_path, "owner: acme\nrepo: widget\nposture: nope\nextra: 1\n")
    message = str(raised.value)
    assert "posture" in message
    assert "extra" in message


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("", "expected mapping at root"),
        ("- a\n- b\n", "expected mapping at root"),
        ("just a string\n", "expected mapping at root"),
        ("owner: [unclosed\n", "invalid YAML"),
    ],
)
def test_a_profile_that_is_not_a_yaml_mapping_fails_naming_the_file(
    tmp_path: Path, text: str, problem: str
) -> None:
    with pytest.raises(DefaultProfileError) as raised:
        _resolve_text(tmp_path, text)
    assert str(raised.value) == f"engines/default_profile.yaml: {problem}"


def test_a_profile_that_is_not_utf8_fails_as_unreadable(tmp_path: Path) -> None:
    state = _state_dir(tmp_path)
    (state / "default_profile.yaml").write_bytes(b"\xff\xfe\x00owner")
    with pytest.raises(DefaultProfileError, match="cannot read file"):
        resolve(state, _owner_file(tmp_path))


def test_a_profile_that_cannot_be_read_fails_as_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_read_text = Path.read_text

    def _refuse(self: Path, *args: object, **kwargs: object) -> str:
        if self.name != "default_profile.yaml":
            return real_read_text(self, *args, **kwargs)  # type: ignore[arg-type]
        raise PermissionError(13, "denied", str(self))

    state = _state_dir(tmp_path, _MINIMAL)
    monkeypatch.setattr(Path, "read_text", _refuse)
    with pytest.raises(DefaultProfileError, match="engines/default_profile.yaml: cannot read file"):
        resolve(state, _owner_file(tmp_path))


def test_a_profile_path_that_is_a_directory_is_not_a_profile(tmp_path: Path) -> None:
    state = _state_dir(tmp_path)
    (state / "default_profile.yaml").mkdir()
    assert resolve(state, _owner_file(tmp_path)).source is Source.unconfigured


_CANARY = "TOPSECRETVALUE99"


@pytest.mark.parametrize(
    "text",
    [
        f"owner: ghp_{_CANARY}\nrepo: widget\nposture: observe\n",
        f"owner: acme\nrepo: widget\nposture: {_CANARY}\n",
        f"{_MINIMAL}requirements: {_CANARY}\n",
        f"{_MINIMAL}requirements:\n  - {_CANARY}\n",
        f"{_MINIMAL}never_live_budget_s: {_CANARY}\n",
        f"{_MINIMAL}dependency_sweep: {_CANARY}\n",
        f"{_MINIMAL}prep_chat_origin: {{a: {_CANARY}}}\n",
        f"{_MINIMAL}codeowners_repos: {_CANARY}\n",
        f"{_MINIMAL}codeowners_repos:\n  - {_CANARY}\n",
        f"{_MINIMAL}codeowners_repos:\n  - acme/{_CANARY}/x\n",
        f"{_MINIMAL}ios_bundle_id: {_CANARY}\n",
        f"{_MINIMAL}ios_bundle_id: com.{_CANARY}!\n",
        f"{_MINIMAL}verifier_login: -{_CANARY}\n",
        f"{_MINIMAL}verifier_login: {_CANARY * 3}\n",
        f"{_MINIMAL}code_quality: {_CANARY}\n",
        f"owner: acme\nrepo: widget\nposture: [{_CANARY}\n",
    ],
)
def test_no_field_value_appears_in_the_error_or_its_traceback(
    tmp_path: Path, text: str
) -> None:
    with pytest.raises(DefaultProfileError) as raised:
        _resolve_text(tmp_path, text)
    rendered = "".join(traceback.format_exception(raised.value))
    assert _CANARY not in str(raised.value)
    assert _CANARY not in rendered


def test_optional_install_settings_are_carried_on_the_profile(tmp_path: Path) -> None:
    text = (
        _MINIMAL
        + "prep_chat_origin: https://prep.example.test\n"
        + "requirements:\n  - reviewer_credential\n  - code_quality\n"
        + "never_live_budget_s: 3600\n"
        + "triage_sweep: true\n"
    )
    profile = _resolve_text(tmp_path, text).profile
    assert profile.prep_chat_origin == "https://prep.example.test"
    assert profile.requirements == [
        ProfileRequirement.reviewer_credential,
        ProfileRequirement.code_quality,
    ]
    assert profile.never_live_budget_s == 3600.0
    assert profile.triage_sweep is True


def test_a_minimal_profile_defaults_every_optional_field(tmp_path: Path) -> None:
    profile = _resolve_text(tmp_path, _MINIMAL).profile
    assert profile.authoritative_checks == frozenset()
    assert profile.code_quality is None
    assert profile.main_ci_workflow is None
    assert profile.deploy_workflow is None
    assert profile.dependency_sweep is False
    assert profile.triage_sweep is False
    assert profile.prep_chat_origin is None
    assert profile.requirements == []
    assert profile.never_live_budget_s is None
    assert profile.codeowners_repos == frozenset()
    assert profile.ios_bundle_id is None
    assert profile.verifier_login is None


@pytest.mark.parametrize("login", ["alan-verifier", "a", "Bot-2", "a" * 39])
def test_a_github_account_name_is_accepted_as_the_verifier_login(
    tmp_path: Path, login: str
) -> None:
    profile = _resolve_text(tmp_path, _MINIMAL + f"verifier_login: '{login}'\n").profile
    assert profile.verifier_login == login


@pytest.mark.parametrize(
    "login", ["", "-bot", "bot-", "bo--t", "bo t", "bot.name", "bot_name", "a" * 40, "bot\\n"]
)
def test_a_name_github_would_refuse_is_rejected_as_the_verifier_login(
    tmp_path: Path, login: str
) -> None:
    with pytest.raises(DefaultProfileError, match="verifier_login"):
        _resolve_text(tmp_path, _MINIMAL + f"verifier_login: '{login}'\n")


@pytest.mark.parametrize("value", ["3", "[a]", "{a: b}"])
def test_a_verifier_login_that_is_not_a_string_is_rejected(tmp_path: Path, value: str) -> None:
    with pytest.raises(DefaultProfileError, match="verifier_login"):
        _resolve_text(tmp_path, _MINIMAL + f"verifier_login: {value}\n")


def test_the_verifier_login_stays_out_of_the_engine_fields(tmp_path: Path) -> None:
    fields = _resolve_text(tmp_path, _MINIMAL + "verifier_login: alan-verifier\n").engine_fields()
    assert "verifier_login" not in fields


def test_an_installed_profile_is_not_merged_with_the_owner_verifier_login(
    tmp_path: Path,
) -> None:
    owner_file = _owner_file(tmp_path, _MINIMAL + "verifier_login: owner-bot\n")
    installed = resolve(_state_dir(tmp_path, _MINIMAL), owner_file)
    assert installed.source is Source.profile
    assert installed.profile.verifier_login is None


def test_an_installed_verifier_login_wins_over_the_owner_file(tmp_path: Path) -> None:
    owner_file = _owner_file(tmp_path, _MINIMAL + "verifier_login: owner-bot\n")
    installed = resolve(_state_dir(tmp_path, _MINIMAL + "verifier_login: own-bot\n"), owner_file)
    assert installed.profile.verifier_login == "own-bot"


def test_the_owner_file_supplies_the_verifier_login_when_nothing_is_installed(
    tmp_path: Path,
) -> None:
    owner_file = _owner_file(tmp_path, _MINIMAL + "verifier_login: owner-bot\n")
    resolved = resolve(_state_dir(tmp_path), owner_file)
    assert resolved.source is Source.owner
    assert resolved.profile.verifier_login == "owner-bot"


def test_codeowners_repositories_and_the_ios_bundle_id_are_carried_on_the_profile(
    tmp_path: Path,
) -> None:
    text = (
        _MINIMAL
        + "codeowners_repos:\n  - example-org/example-repo\n  - example-org/other-repo\n"
        + "ios_bundle_id: com.example.app\n"
    )
    profile = _resolve_text(tmp_path, text).profile
    assert profile.codeowners_repos == frozenset(
        {"example-org/example-repo", "example-org/other-repo"}
    )
    assert profile.ios_bundle_id == "com.example.app"


def test_codeowners_repositories_are_stored_casefolded(tmp_path: Path) -> None:
    text = (
        _MINIMAL + "codeowners_repos:\n  - Example-Org/Example-Repo\n  - EXAMPLE-ORG/example-repo\n"
    )
    profile = _resolve_text(tmp_path, text).profile
    assert profile.codeowners_repos == frozenset({"example-org/example-repo"})


@pytest.mark.parametrize(
    "slug",
    [
        "",
        "example-org",
        "/example-repo",
        "example-org/",
        "example-org/example/repo",
        "-example/repo",
        "example--org/repo",
        "example.org/repo",
        "example-org/wid get",
        "example-org/..",
        "example-org/.",
        f"{'a' * 40}/repo",
        f"example-org/{'a' * 101}",
    ],
)
def test_a_codeowners_entry_that_is_not_an_owner_repo_slug_is_rejected(
    tmp_path: Path, slug: str
) -> None:
    with pytest.raises(DefaultProfileError, match="codeowners_repos entries must be owner/repo"):
        _resolve_text(tmp_path, _MINIMAL + f"codeowners_repos:\n  - '{slug}'\n")


@pytest.mark.parametrize(
    "slug", ["example-org/.github", "e/r", f"{'a' * 39}/{'b' * 100}", "example-org/my_repo-2.0"]
)
def test_a_codeowners_entry_that_github_allows_is_accepted(tmp_path: Path, slug: str) -> None:
    profile = _resolve_text(tmp_path, _MINIMAL + f"codeowners_repos:\n  - '{slug}'\n").profile
    assert profile.codeowners_repos == frozenset({slug})


def test_one_bad_codeowners_entry_fails_the_whole_profile(tmp_path: Path) -> None:
    text = _MINIMAL + "codeowners_repos:\n  - example-org/example-repo\n  - nonsense\n"
    with pytest.raises(DefaultProfileError, match="codeowners_repos"):
        _resolve_text(tmp_path, text)


@pytest.mark.parametrize(
    "bundle_id", ["com.example.app", "Example-Inc.App.dev-1", "a.b", "com.example.app.v2"]
)
def test_a_reverse_dns_ios_bundle_id_is_accepted(tmp_path: Path, bundle_id: str) -> None:
    profile = _resolve_text(tmp_path, _MINIMAL + f"ios_bundle_id: '{bundle_id}'\n").profile
    assert profile.ios_bundle_id == bundle_id


@pytest.mark.parametrize(
    "bundle_id",
    [
        "example",
        ".com.example",
        "com.example.",
        "com..example",
        "com.exam ple.app",
        "com.example_app.x",
        "com.example.app`; rm -rf /",
        "com.example.app\\n",
    ],
)
def test_an_ios_bundle_id_that_is_not_reverse_dns_is_rejected(
    tmp_path: Path, bundle_id: str
) -> None:
    with pytest.raises(DefaultProfileError, match="ios_bundle_id: String should match pattern"):
        _resolve_text(tmp_path, _MINIMAL + f"ios_bundle_id: '{bundle_id}'\n")


def test_install_scoped_owner_values_stay_out_of_the_engine_fields(tmp_path: Path) -> None:
    text = (
        _MINIMAL
        + "codeowners_repos:\n  - example-org/example-repo\n"
        + "ios_bundle_id: com.example.app\n"
    )
    fields = _resolve_text(tmp_path, text).engine_fields()
    assert "codeowners_repos" not in fields
    assert "ios_bundle_id" not in fields


def test_a_profile_without_ci_workflows_yields_an_engine_without_ci(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _engine_under(monkeypatch, _resolve_text(tmp_path, _MINIMAL))
    assert engine.ci is None
    assert engine.code_quality is None
    assert engine.forge is not None
    assert engine.forge.authoritative_checks == frozenset()


def test_a_profile_with_both_workflows_yields_an_engine_with_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = _MINIMAL + "main_ci_workflow: main.yml\ndeploy_workflow: ship.yml\n"
    engine = _engine_under(monkeypatch, _resolve_text(tmp_path, text))
    assert engine.ci is not None
    assert (engine.ci.main_ci_workflow, engine.ci.deploy_workflow) == ("main.yml", "ship.yml")


def test_a_profile_overriding_a_present_owner_file_logs_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    owner_file = _owner_file(tmp_path, _OWNER_STANDIN.read_text())
    with caplog.at_level(logging.WARNING, logger="alan.default_profile"):
        resolve(_state_dir(tmp_path, _OTHER_REPO.read_text()), owner_file)
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == [
        "default profile engines/default_profile.yaml overrides the owner profile "
        "owner/default_profile.yaml"
    ]


def test_the_override_warning_does_not_echo_any_profile_value(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    owner_file = _owner_file(tmp_path, _OWNER_STANDIN.read_text())
    with caplog.at_level(logging.WARNING, logger="alan.default_profile"):
        resolve(_state_dir(tmp_path, _OTHER_REPO.read_text()), owner_file)
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "acme-labs" not in text
    assert "standin" not in text


def test_no_warning_when_only_a_profile_is_present(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="alan.default_profile"):
        resolve(_state_dir(tmp_path, _OTHER_REPO.read_text()), _owner_file(tmp_path))
    assert caplog.records == []


def test_no_warning_when_only_the_owner_file_is_present(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    owner_file = _owner_file(tmp_path, _OWNER_STANDIN.read_text())
    with caplog.at_level(logging.WARNING, logger="alan.default_profile"):
        resolve(_state_dir(tmp_path), owner_file)
    assert caplog.records == []


def test_resolution_happens_once_per_process_even_after_the_engines_root_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = default_profile.resolved()
    moved = tmp_path / "moved-engines"
    moved.mkdir()
    (moved / "default_profile.yaml").write_text(_OTHER_REPO.read_text())
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(moved))
    assert default_profile.resolved() is first
    assert default_engine_config().repo == RepoIdentity(owner="DejvMatikar", repo="alan")


def test_the_known_requirement_names_are_the_reviewer_credential_and_code_quality() -> None:
    assert {member.value for member in ProfileRequirement} == {
        "reviewer_credential",
        "code_quality",
    }


@pytest.mark.parametrize("name", ["Python 3.11", "reviewer", "Reviewer_Credential", "", "uv"])
def test_an_unknown_requirement_name_is_a_malformed_profile_error(
    tmp_path: Path, name: str
) -> None:
    with pytest.raises(DefaultProfileError, match=r"requirements\.0"):
        _resolve_text(tmp_path, _MINIMAL + f"requirements:\n  - '{name}'\n")


def test_a_required_code_quality_without_a_sonarcloud_project_still_loads(
    tmp_path: Path,
) -> None:
    profile = _resolve_text(tmp_path, _MINIMAL + "requirements:\n  - code_quality\n").profile
    assert profile.requirements == [ProfileRequirement.code_quality]
    assert profile.code_quality is None


def test_start_up_fails_on_an_unknown_requirement_name(tmp_path: Path) -> None:
    state = _state_dir(tmp_path, _MINIMAL + "requirements:\n  - nonsense\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("ALAN_")}
    env["HOME"] = str(tmp_path)
    env["ALAN_ENGINES_ROOT"] = str(state)
    done = subprocess.run(
        [sys.executable, "-c", "import alan.engine_config"],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode != 0
    assert "DefaultProfileError" in done.stderr
    assert "requirements.0" in done.stderr
