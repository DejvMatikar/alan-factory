from __future__ import annotations

import io
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pytest

from alan import default_profile, default_profile_writer, setup_sonar, setup_transport
from alan.store import write_text_atomic
from tests._setup_support import leaks

ADMIN = "SENTINEL-SONAR-ADMIN-4d2b71"
ANALYSIS = "SENTINEL-SONAR-ANALYSIS-9a6c30"
OWNER = "example-owner"
REPO = "example-repo"
SLUG = f"{OWNER}/{REPO}"
ORG = "example-org"
KEY = "example-org_example-repo"
TYPED = f"{ADMIN}\n{ANALYSIS}\n"
ADMIN_PATHS = (
    "/api/organizations/search",
    "/api/projects/search",
    "/api/alm_settings/get_binding",
    "/api/settings/values",
)


def _done(argv: list[str], code: int, out: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, code, stdout=out, stderr="")


@dataclass
class World:
    logins: dict[str, str] = field(
        default_factory=lambda: {ADMIN: "admin-user", ANALYSIS: "analysis-user"}
    )
    organizations: list[str] = field(default_factory=lambda: [ORG])
    candidates: dict[str, list[str]] = field(default_factory=lambda: {ORG: [KEY]})
    bindings: dict[str, str] = field(default_factory=lambda: {KEY: SLUG})
    autoscan: str | None = "false"
    forced: dict[str, tuple[int, object]] = field(default_factory=dict)
    curl_code: int = 0
    anonymous: set[str] = field(default_factory=set)
    gh_failing: set[str] = field(default_factory=set)
    variables: dict[str, str] = field(default_factory=dict)
    secrets: dict[str, str] = field(default_factory=dict)
    calls: list[tuple[list[str], str]] = field(default_factory=list)

    def __call__(
        self, argv: list[str], stdin: str, unavailable: str
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(argv), stdin))
        if argv[0] == "curl":
            return self._curl(argv, stdin)
        return self._gh(argv[1:], stdin)

    def curl_paths(self) -> list[str]:
        return [urlsplit(argv[-1]).path for argv, _ in self.calls if argv[0] == "curl"]

    def gh_calls(self) -> list[list[str]]:
        return [argv[1:] for argv, _ in self.calls if argv[0] == "gh"]

    def _curl(self, argv: list[str], stdin: str) -> subprocess.CompletedProcess[str]:
        if self.curl_code:
            return _done(argv, self.curl_code)
        parts = urlsplit(argv[-1])
        matched = re.fullmatch(r'user = "(.*):"\n', stdin)
        token = matched.group(1) if matched else ""
        status, body = self._answer(parts.path, dict(parse_qsl(parts.query)), token)
        text = body if isinstance(body, str) else json.dumps(body)
        return _done(argv, 0, f"{text}\n{status}")

    def _answer(self, path: str, query: dict[str, str], token: str) -> tuple[int, object]:
        if path in self.forced:
            return self.forced[path]
        if token not in self.logins:
            return 401, {}
        if path == "/api/users/current":
            return 200, self._identity(token)
        if token != ADMIN:
            return 403, {}
        return 200, self._admin_answer(path, query)

    def _identity(self, token: str) -> dict[str, object]:
        if token in self.anonymous:
            return {"isLoggedIn": False}
        return {"isLoggedIn": True, "login": self.logins[token]}

    def _admin_answer(self, path: str, query: dict[str, str]) -> object:
        if path == "/api/organizations/search":
            return {"organizations": [{"key": key} for key in self.organizations]}
        if path == "/api/projects/search":
            keys = self.candidates.get(query["organization"], [])
            return {"components": [{"key": key} for key in keys]}
        if path == "/api/alm_settings/get_binding":
            return {"repository": self.bindings.get(query["project"], "")}
        rows = [] if self.autoscan is None else [{"key": query["keys"], "value": self.autoscan}]
        return {"settings": rows}

    def _gh(self, args: list[str], stdin: str) -> subprocess.CompletedProcess[str]:
        verb, name = args[0:2], args[2] if len(args) > 2 else ""
        if verb == ["variable", "set"] and name not in self.gh_failing:
            self.variables[name] = args[args.index("--body") + 1]
        elif verb == ["secret", "set"] and name not in self.gh_failing:
            self.secrets[name] = stdin
        elif verb[1] == "set":
            return _done(args, 1)
        elif "list" in self.gh_failing:
            return _done(args, 1)
        elif verb == ["variable", "list"]:
            rows = [{"name": key, "value": value} for key, value in self.variables.items()]
            return _done(args, 0, json.dumps(rows))
        elif verb == ["secret", "list"]:
            return _done(args, 0, json.dumps([{"name": key} for key in self.secrets]))
        return _done(args, 0)


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    fake = World()
    monkeypatch.setattr(setup_sonar, "_run", fake)
    return fake


@pytest.fixture
def engines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "engines"
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(root))
    return root


def _seed(engines: Path, owner: str = OWNER, repo: str = REPO) -> None:
    default_profile_writer.write_profile(engines, owner, repo)


def _profile_bytes(engines: Path) -> bytes:
    return (engines / default_profile.PROFILE_FILE_NAME).read_bytes()


def _configure(
    tmp_path: Path, text: str = TYPED, *, accept: bool = False, slug: str = SLUG
) -> Path:
    marker = tmp_path / "home" / "setup.restart_pending"
    setup_sonar.run_configure(slug, marker, accept_same_user=accept, stream=io.StringIO(text))
    return marker


def _writes(world: World) -> list[list[str]]:
    return [call for call in world.gh_calls() if call[1] == "set"]


def test_one_bound_project_yields_the_profile_block_and_both_variables_and_the_secret(
    world: World, engines: Path, tmp_path: Path
) -> None:
    _seed(engines)
    marker = _configure(tmp_path)
    assert default_profile_writer.installed_code_quality(engines, OWNER, REPO) == (
        default_profile.CodeQualityProfile(organization=ORG, project_key=KEY)
    )
    assert world.variables == {"SONAR_ORGANIZATION": ORG, "SONAR_PROJECT_KEY": KEY}
    assert world.secrets == {"SONAR_TOKEN": ANALYSIS}
    assert marker.exists()


def test_the_writes_go_in_the_order_profile_variables_secret(
    world: World, engines: Path, tmp_path: Path
) -> None:
    _seed(engines)
    _configure(tmp_path)
    assert _writes(world) == [
        ["variable", "set", "SONAR_ORGANIZATION", "--repo", SLUG, "--body", ORG],
        ["variable", "set", "SONAR_PROJECT_KEY", "--repo", SLUG, "--body", KEY],
        ["secret", "set", "SONAR_TOKEN", "--repo", SLUG],
    ]


@pytest.mark.parametrize(
    ("organization", "key"),
    [("example-org", "example-org_example-repo"), ("other-org", "other-org_renamed")],
)
def test_the_organization_and_key_follow_what_sonarcloud_reports(
    world: World, engines: Path, tmp_path: Path, organization: str, key: str
) -> None:
    _seed(engines)
    world.organizations = [organization]
    world.candidates = {organization: [key]}
    world.bindings = {key: SLUG}
    _configure(tmp_path)
    block = default_profile_writer.installed_code_quality(engines, OWNER, REPO)
    assert block is not None
    assert (block.organization, block.project_key) == (organization, key)
    assert world.variables == {"SONAR_ORGANIZATION": organization, "SONAR_PROJECT_KEY": key}


def test_the_admin_token_reaches_only_sonarcloud_and_the_analysis_token_only_the_secret(
    world: World, engines: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(engines)
    _configure(tmp_path)
    printed = capsys.readouterr()
    for token in (ADMIN, ANALYSIS):
        assert token not in printed.out + printed.err
        assert token not in json.dumps([argv for argv, _ in world.calls])
    gh_stdin = [stdin for argv, stdin in world.calls if argv[0] == "gh"]
    assert ADMIN not in "".join(gh_stdin)
    assert [stdin for stdin in gh_stdin if stdin] == [ANALYSIS]
    assert profile_text_lacks(engines, ADMIN, ANALYSIS)


def profile_text_lacks(engines: Path, *tokens: str) -> bool:
    text = _profile_bytes(engines).decode("utf-8")
    return all(token not in text for token in tokens)


def test_the_instructions_are_printed_before_either_token_is_asked_for(
    world: World, engines: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(engines)
    _configure(tmp_path)
    printed = capsys.readouterr().out
    assert setup_sonar.INSTRUCTIONS_TEXT in printed
    assert f"SonarCloud project {KEY} of organization {ORG} is bound to this repository." in printed


@pytest.mark.parametrize(
    ("scenario", "instruction"),
    [
        ("rejected", setup_sonar.ADMIN_REJECTED_INSTRUCTION),
        ("no-project", setup_sonar.NO_PROJECT_INSTRUCTION),
        ("several", setup_sonar.SEVERAL_PROJECTS_INSTRUCTION),
    ],
)
def test_a_failed_discovery_writes_nothing_and_says_what_to_do_in_the_browser(
    world: World,
    engines: Path,
    tmp_path: Path,
    scenario: str,
    instruction: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed(engines)
    before = _profile_bytes(engines)
    if scenario == "rejected":
        world.logins = {}
    elif scenario == "no-project":
        world.bindings = {KEY: "someone-else/other"}
    else:
        world.candidates = {ORG: [KEY, f"{KEY}-copy"]}
        world.bindings = {KEY: SLUG, f"{KEY}-copy": SLUG.upper()}
    marker = tmp_path / "home" / "setup.restart_pending"
    argv = ["configure", "--engine", SLUG, "--restart-marker", str(marker)]
    sys.stdin = io.StringIO(TYPED)
    assert setup_sonar.main(argv) == 1
    printed = capsys.readouterr()
    assert instruction in printed.out
    assert printed.err.strip() != ""
    assert _writes(world) == []
    assert _profile_bytes(engines) == before
    assert not marker.exists()


def test_the_analysis_token_is_not_read_after_a_failed_discovery(
    world: World, engines: Path, tmp_path: Path
) -> None:
    _seed(engines)
    world.bindings = {}
    stream = io.StringIO(TYPED)
    with pytest.raises(setup_sonar.SonarRefused):
        setup_sonar.run_configure(
            SLUG, tmp_path / "marker", accept_same_user=False, stream=stream
        )
    assert stream.read() == f"{ANALYSIS}\n"


def test_a_rejected_admin_token_is_named_in_the_reason(world: World, tmp_path: Path) -> None:
    world.logins = {}
    with pytest.raises(setup_sonar.SonarRefused, match="rejected the admin token") as raised:
        _configure(tmp_path)
    assert raised.value.instruction == setup_sonar.ADMIN_REJECTED_INSTRUCTION


@pytest.mark.parametrize("status", [401, 403])
def test_an_admin_call_that_sonarcloud_refuses_names_the_admin_token(
    world: World, tmp_path: Path, status: int
) -> None:
    world.forced["/api/organizations/search"] = (status, {})
    with pytest.raises(setup_sonar.SonarRefused, match="rejected the admin token"):
        _configure(tmp_path)


def test_an_admin_token_that_is_not_a_user_token_is_refused(
    world: World, tmp_path: Path
) -> None:
    world.anonymous = {ADMIN}
    with pytest.raises(setup_sonar.SonarRefused, match="must be a user token"):
        _configure(tmp_path)
    assert world.curl_paths() == ["/api/users/current"]


def test_a_user_answer_without_a_login_is_refused(world: World, tmp_path: Path) -> None:
    world.forced["/api/users/current"] = (200, {"isLoggedIn": True})
    with pytest.raises(setup_sonar.SonarRefused, match="did not say which user"):
        _configure(tmp_path)


def test_a_missing_user_answer_is_refused(world: World, tmp_path: Path) -> None:
    world.forced["/api/users/current"] = (404, {})
    with pytest.raises(setup_sonar.SonarRefused, match="did not say which user"):
        _configure(tmp_path)


def test_only_organizations_projects_and_bindings_are_read_for_discovery(
    world: World, engines: Path, tmp_path: Path
) -> None:
    _seed(engines)
    _configure(tmp_path)
    assert world.curl_paths() == [
        "/api/users/current",
        "/api/organizations/search",
        "/api/projects/search",
        "/api/alm_settings/get_binding",
        "/api/settings/values",
        "/api/users/current",
    ]


def test_projects_are_searched_by_the_repository_name_in_every_member_organization(
    world: World, tmp_path: Path
) -> None:
    world.organizations = [ORG, "second-org"]
    world.candidates = {ORG: [], "second-org": ["second-org_example-repo"]}
    world.bindings = {"second-org_example-repo": SLUG}
    found = setup_sonar.discover_project(ADMIN, SLUG)
    assert found == setup_sonar.SonarProject("second-org", "second-org_example-repo")
    searches = [
        parse_qsl(urlsplit(argv[-1]).query)
        for argv, _ in world.calls
        if urlsplit(argv[-1]).path == "/api/projects/search"
    ]
    assert [dict(query)["q"] for query in searches] == [REPO, REPO]
    assert [dict(query)["organization"] for query in searches] == [ORG, "second-org"]


def test_the_binding_match_ignores_case(world: World) -> None:
    world.bindings = {KEY: SLUG.upper()}
    assert setup_sonar.discover_project(ADMIN, SLUG).key == KEY


def test_a_project_bound_to_another_repository_is_not_this_ones(world: World) -> None:
    world.candidates = {ORG: [KEY, "example-org_other"]}
    world.bindings = {KEY: SLUG, "example-org_other": "example-owner/other"}
    assert setup_sonar.discover_project(ADMIN, SLUG).key == KEY


def test_two_projects_bound_in_different_organizations_are_several(world: World) -> None:
    world.organizations = [ORG, "second-org"]
    world.candidates = {ORG: [KEY], "second-org": ["second-org_example-repo"]}
    world.bindings = {KEY: SLUG, "second-org_example-repo": SLUG}
    with pytest.raises(setup_sonar.SonarRefused, match="several SonarCloud projects") as raised:
        setup_sonar.discover_project(ADMIN, SLUG)
    assert raised.value.instruction == setup_sonar.SEVERAL_PROJECTS_INSTRUCTION


def test_no_organization_means_no_bound_project(world: World) -> None:
    world.organizations = []
    with pytest.raises(setup_sonar.SonarRefused, match="no SonarCloud project") as raised:
        setup_sonar.discover_project(ADMIN, SLUG)
    assert raised.value.instruction == setup_sonar.NO_PROJECT_INSTRUCTION


@pytest.mark.parametrize("shape", [["a"], {"organizations": "text"}, [], None, 5])
def test_an_answer_of_the_wrong_shape_finds_no_project(world: World, shape: object) -> None:
    world.forced["/api/organizations/search"] = (200, shape)
    with pytest.raises(setup_sonar.SonarRefused, match="no SonarCloud project"):
        setup_sonar.discover_project(ADMIN, SLUG)


def test_a_key_with_characters_setup_cannot_pass_on_is_never_used(world: World) -> None:
    world.candidates = {ORG: ["--flag", "has space", KEY]}
    world.bindings = {"--flag": SLUG, "has space": SLUG, KEY: SLUG}
    assert setup_sonar.discover_project(ADMIN, SLUG).key == KEY


def test_a_binding_answer_without_a_repository_is_not_a_match(world: World) -> None:
    world.forced["/api/alm_settings/get_binding"] = (200, {"repository": 5})
    with pytest.raises(setup_sonar.SonarRefused, match="no SonarCloud project"):
        setup_sonar.discover_project(ADMIN, SLUG)


def test_an_unbound_project_answers_404_and_is_not_a_match(world: World) -> None:
    world.forced["/api/alm_settings/get_binding"] = (404, {})
    with pytest.raises(setup_sonar.SonarRefused, match="no SonarCloud project"):
        setup_sonar.discover_project(ADMIN, SLUG)


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (500, {}, "answered HTTP 500 to the request for /api/organizations/search"),
        (200, "not json", "answer to /api/organizations/search could not be read"),
    ],
)
def test_an_answer_setup_cannot_use_is_refused_with_the_path(
    world: World, status: int, body: object, message: str
) -> None:
    world.forced["/api/organizations/search"] = (status, body)
    with pytest.raises(setup_sonar.SonarRefused, match=message):
        setup_sonar.discover_project(ADMIN, SLUG)


def test_an_answer_without_a_status_line_counts_as_status_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(setup_sonar, "_run", lambda argv, stdin, unavailable: _done(argv, 0, "x"))
    with pytest.raises(setup_sonar.SonarRefused, match="HTTP 0"):
        setup_sonar.discover_project(ADMIN, SLUG)


def test_a_sonarcloud_that_cannot_be_reached_is_refused(world: World) -> None:
    world.curl_code = 7
    with pytest.raises(setup_sonar.SonarRefused, match="could not be reached"):
        setup_sonar.discover_project(ADMIN, SLUG)


def test_the_token_reaches_curl_only_through_its_standard_input(world: World) -> None:
    setup_sonar.discover_project(ADMIN, SLUG)
    for argv, stdin in world.calls:
        assert ADMIN not in " ".join(argv)
        assert stdin == f'user = "{ADMIN}:"\n'
        assert argv[argv.index("--config") + 1] == "-"


@pytest.mark.parametrize(
    ("value", "confirmed"),
    [("false", True), ("False", True), ("true", False), (None, False)],
    ids=["false", "capitalized", "true", "unset"],
)
def test_automatic_analysis_is_confirmed_off_only_when_sonarcloud_says_false(
    world: World, value: str | None, confirmed: bool
) -> None:
    world.autoscan = value
    assert setup_sonar.autoscan_is_off(ADMIN, KEY) is confirmed


@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_a_read_back_that_fails_is_not_a_confirmation(world: World, status: int) -> None:
    world.forced["/api/settings/values"] = (status, {})
    assert setup_sonar.autoscan_is_off(ADMIN, KEY) is False


def test_a_read_back_about_another_setting_is_not_a_confirmation(world: World) -> None:
    world.forced["/api/settings/values"] = (
        200,
        {"settings": [{"key": "sonar.other", "value": "false"}]},
    )
    assert setup_sonar.autoscan_is_off(ADMIN, KEY) is False


def test_automatic_analysis_confirmed_off_prints_no_instruction(
    world: World, engines: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(engines)
    _configure(tmp_path)
    assert "Automatic Analysis" not in capsys.readouterr().out


def test_automatic_analysis_not_confirmed_prints_the_browser_instruction_and_goes_on(
    world: World, engines: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(engines)
    world.autoscan = "true"
    _configure(tmp_path)
    assert setup_sonar.AUTOSCAN_INSTRUCTION in capsys.readouterr().out
    assert world.secrets == {"SONAR_TOKEN": ANALYSIS}
    assert default_profile_writer.installed_code_quality(engines, OWNER, REPO) is not None


def test_a_token_from_a_different_user_is_accepted_without_a_risk_text(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    setup_sonar.check_analysis_token(ANALYSIS, ADMIN, "admin-user", accept_same_user=False)
    assert capsys.readouterr().out == ""


def test_a_token_from_the_admin_users_own_account_is_refused_without_the_flag(
    world: World, tmp_path: Path, engines: Path
) -> None:
    _seed(engines)
    world.logins[ANALYSIS] = "Admin-User"
    with pytest.raises(setup_sonar.SonarRefused, match="same SonarCloud user") as raised:
        _configure(tmp_path)
    assert "--accept-sonar-user-token" in str(raised.value)
    assert _writes(world) == []


def test_the_flag_accepts_the_same_user_and_prints_the_risk_text(
    world: World, tmp_path: Path, engines: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(engines)
    world.logins[ANALYSIS] = "admin-user"
    _configure(tmp_path, accept=True)
    assert setup_sonar.RISK_TEXT in capsys.readouterr().out
    assert world.secrets == {"SONAR_TOKEN": ANALYSIS}


def test_the_risk_text_is_not_printed_for_a_different_user_even_with_the_flag(
    world: World, tmp_path: Path, engines: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(engines)
    _configure(tmp_path, accept=True)
    assert setup_sonar.RISK_TEXT not in capsys.readouterr().out


@pytest.mark.parametrize("accept", [False, True])
def test_a_byte_identical_token_is_refused_with_or_without_the_flag(
    world: World, tmp_path: Path, engines: Path, accept: bool
) -> None:
    _seed(engines)
    before = _profile_bytes(engines)
    with pytest.raises(setup_sonar.SonarRefused, match="same token as the admin token"):
        _configure(tmp_path, f"{ADMIN}\n{ADMIN}\n", accept=accept)
    assert _writes(world) == []
    assert _profile_bytes(engines) == before
    assert world.curl_paths().count("/api/users/current") == 1


def test_an_analysis_token_sonarcloud_rejects_is_refused_before_anything_is_written(
    world: World, tmp_path: Path, engines: Path
) -> None:
    _seed(engines)
    world.logins = {ADMIN: "admin-user"}
    with pytest.raises(setup_sonar.SonarRefused, match="rejected the analysis token") as raised:
        _configure(tmp_path)
    assert raised.value.instruction == ""
    assert _writes(world) == []


def test_an_analysis_token_with_no_user_is_refused_because_it_cannot_be_compared(
    world: World, tmp_path: Path, engines: Path
) -> None:
    _seed(engines)
    world.anonymous = {ANALYSIS}
    with pytest.raises(setup_sonar.SonarRefused, match="did not say which user the analysis"):
        _configure(tmp_path)
    assert _writes(world) == []


@pytest.mark.parametrize("text", ["", "\n", "  \n"], ids=["eof", "blank", "spaces"])
def test_an_empty_admin_answer_skips_before_any_call(
    world: World, tmp_path: Path, text: str
) -> None:
    with pytest.raises(setup_sonar.SonarSkipped):
        _configure(tmp_path, text)
    assert world.calls == []


def test_no_analysis_token_is_refused_before_anything_is_written(
    world: World, tmp_path: Path, engines: Path
) -> None:
    _seed(engines)
    with pytest.raises(setup_transport.TransportRefused, match="no SonarCloud analysis token"):
        _configure(tmp_path, f"{ADMIN}\n")
    assert _writes(world) == []


@pytest.mark.parametrize("bad", ['quo"te', "with space", "semi;colon", "a" * 201, "newé"])
def test_a_token_with_characters_a_token_never_has_is_refused_unused_and_unechoed(
    world: World, tmp_path: Path, bad: str
) -> None:
    match = "does not look like a SonarCloud token"
    with pytest.raises(setup_sonar.SonarRefused, match=match) as raised:
        _configure(tmp_path, f"{bad}\n{ANALYSIS}\n")
    assert bad not in str(raised.value)
    assert world.calls == []


def test_a_foreign_profile_is_refused_before_any_github_write_and_left_byte_identical(
    world: World, engines: Path, tmp_path: Path
) -> None:
    _seed(engines, "other-owner", "other-repo")
    before = _profile_bytes(engines)
    with pytest.raises(default_profile_writer.ProfileWriterError, match="different repository"):
        _configure(tmp_path)
    assert _writes(world) == []
    assert _profile_bytes(engines) == before
    assert not (tmp_path / "home" / "setup.restart_pending").exists()


def test_a_missing_profile_is_refused_before_any_github_write(
    world: World, engines: Path, tmp_path: Path
) -> None:
    with pytest.raises(default_profile_writer.ProfileWriterError, match="no installed profile"):
        _configure(tmp_path)
    assert _writes(world) == []
    assert not engines.exists()


def test_the_restart_marker_exists_before_the_profile_changes(
    world: World, engines: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(engines)
    marker = tmp_path / "home" / "setup.restart_pending"
    seen: list[bool] = []
    real = write_text_atomic

    def watched(path: Path, text: str) -> None:
        seen.append(marker.exists())
        real(path, text)

    monkeypatch.setattr(default_profile_writer, "write_text_atomic", watched)
    _configure(tmp_path)
    assert seen == [True]


def test_no_restart_marker_is_made_when_the_profile_already_holds_the_block(
    world: World, engines: Path, tmp_path: Path
) -> None:
    _seed(engines)
    _configure(tmp_path)
    (tmp_path / "home" / "setup.restart_pending").unlink()
    world.variables.clear()
    _configure(tmp_path)
    assert not (tmp_path / "home" / "setup.restart_pending").exists()
    assert world.variables == {"SONAR_ORGANIZATION": ORG, "SONAR_PROJECT_KEY": KEY}


@pytest.mark.parametrize("failing", ["SONAR_ORGANIZATION", "SONAR_PROJECT_KEY", "SONAR_TOKEN"])
def test_a_refused_github_write_is_named_and_carries_no_token(
    world: World, engines: Path, tmp_path: Path, failing: str
) -> None:
    _seed(engines)
    world.gh_failing = {failing}
    with pytest.raises(setup_sonar.SonarRefused, match=f"refused to store .*{failing}") as raised:
        _configure(tmp_path)
    assert ANALYSIS not in str(raised.value)
    assert ADMIN not in str(raised.value)


def test_a_gh_that_cannot_be_run_is_refused(
    engines: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(engines)

    def unavailable(argv: list[str], stdin: str, message: str) -> subprocess.CompletedProcess[str]:
        if argv[0] == "gh":
            raise setup_sonar.SonarRefused(message)
        return World()(argv, stdin, message)

    monkeypatch.setattr(setup_sonar, "_run", unavailable)
    with pytest.raises(setup_sonar.SonarRefused, match="gh could not be run"):
        _configure(tmp_path)


def test_the_configured_probe_needs_the_block_both_variables_and_the_secret(
    world: World, engines: Path, tmp_path: Path
) -> None:
    assert setup_sonar.is_configured(SLUG) is False
    _seed(engines)
    assert setup_sonar.is_configured(SLUG) is False
    _configure(tmp_path)
    assert setup_sonar.is_configured(SLUG) is True


@pytest.mark.parametrize(
    "mutate",
    [
        lambda w: w.variables.pop("SONAR_ORGANIZATION"),
        lambda w: w.variables.pop("SONAR_PROJECT_KEY"),
        lambda w: w.variables.update(SONAR_ORGANIZATION="drifted"),
        lambda w: w.variables.update(SONAR_PROJECT_KEY="drifted"),
        lambda w: w.secrets.clear(),
        lambda w: w.gh_failing.add("list"),
    ],
    ids=["no-org", "no-key", "org-drift", "key-drift", "no-secret", "gh-fails"],
)
def test_the_configured_probe_fails_on_any_missing_or_drifted_piece(
    world: World, engines: Path, tmp_path: Path, mutate: object
) -> None:
    _seed(engines)
    _configure(tmp_path)
    assert callable(mutate)
    mutate(world)
    assert setup_sonar.is_configured(SLUG) is False


def test_the_configured_probe_treats_an_answer_that_is_not_a_list_as_nothing(
    engines: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(engines)
    fake = World()
    monkeypatch.setattr(setup_sonar, "_run", fake)
    _configure(tmp_path)
    monkeypatch.setattr(
        setup_sonar, "_run", lambda argv, stdin, unavailable: _done(argv, 0, '{"a": 1}')
    )
    assert setup_sonar.is_configured(SLUG) is False
    monkeypatch.setattr(
        setup_sonar, "_run", lambda argv, stdin, unavailable: _done(argv, 0, "not json")
    )
    assert setup_sonar.is_configured(SLUG) is False


def test_the_configured_probe_is_false_when_gh_cannot_be_run(
    engines: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(engines)

    def unavailable(argv: list[str], stdin: str, message: str) -> subprocess.CompletedProcess[str]:
        raise setup_sonar.SonarRefused(message)

    monkeypatch.setattr(setup_sonar, "_run", unavailable)
    assert setup_sonar.is_configured(SLUG) is False


def test_the_configured_probe_is_false_for_an_unusable_installed_profile(
    world: World, engines: Path
) -> None:
    engines.mkdir()
    (engines / default_profile.PROFILE_FILE_NAME).write_bytes(b"owner: [unclosed\n")
    assert setup_sonar.is_configured(SLUG) is False


def test_the_configured_probe_is_false_when_the_profile_cannot_be_read(
    world: World, engines: Path
) -> None:
    (engines / default_profile.PROFILE_FILE_NAME).mkdir(parents=True)
    assert setup_sonar.is_configured(SLUG) is False


def test_the_configured_probe_is_false_for_a_profile_of_another_repository(
    world: World, engines: Path, tmp_path: Path
) -> None:
    _seed(engines, "other-owner", "other-repo")
    assert setup_sonar.is_configured(SLUG) is False


def test_the_configured_probe_never_writes(world: World, engines: Path, tmp_path: Path) -> None:
    _seed(engines)
    _configure(tmp_path)
    before = len(_writes(world))
    setup_sonar.is_configured(SLUG)
    assert len(_writes(world)) == before


def _main(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, text: str, *extra: str
) -> tuple[int, Path]:
    marker = tmp_path / "home" / "setup.restart_pending"
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))
    argv = ["configure", "--engine", SLUG, "--restart-marker", str(marker), *extra]
    return setup_sonar.main(argv), marker


def test_main_configures_and_exits_zero(
    world: World,
    engines: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed(engines)
    code, marker = _main(monkeypatch, tmp_path, TYPED)
    assert code == 0
    assert marker.exists()
    printed = capsys.readouterr()
    assert printed.err == ""
    assert leaks(printed.out) == {}


def test_main_passes_the_flag_on(
    world: World, engines: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(engines)
    world.logins[ANALYSIS] = "admin-user"
    code, _ = _main(monkeypatch, tmp_path, TYPED, "--accept-sonar-user-token")
    assert code == 0
    assert world.secrets == {"SONAR_TOKEN": ANALYSIS}


def test_main_exits_three_and_prints_no_reason_for_an_empty_admin_answer(
    world: World,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, _ = _main(monkeypatch, tmp_path, "")
    assert code == setup_sonar.EXIT_SKIPPED
    assert capsys.readouterr().err == ""


def test_main_prints_only_the_reason_when_a_refusal_carries_no_instruction(
    world: World,
    engines: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed(engines)
    world.logins = {ADMIN: "admin-user"}
    code, _ = _main(monkeypatch, tmp_path, TYPED)
    printed = capsys.readouterr()
    assert code == 1
    assert "rejected the analysis token" in printed.err
    assert "In your browser" not in printed.out


def test_main_prints_a_refused_profile_and_exits_one(
    world: World,
    engines: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed(engines, "other-owner", "other-repo")
    code, _ = _main(monkeypatch, tmp_path, TYPED)
    assert code == 1
    assert "different repository" in capsys.readouterr().err


def test_main_prints_an_operating_system_error_and_exits_one(
    world: World,
    engines: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed(engines)

    def refuse(marker: Path) -> None:
        raise OSError("marker directory is read only")

    monkeypatch.setattr(setup_sonar, "_mark_restart", refuse)
    code, _ = _main(monkeypatch, tmp_path, TYPED)
    assert code == 1
    assert "marker directory is read only" in capsys.readouterr().err
    assert _writes(world) == []


def test_main_answers_the_configured_probe_with_its_exit_code(
    world: World, engines: Path, tmp_path: Path
) -> None:
    argv = ["configured", "--engine", SLUG]
    assert setup_sonar.main(argv) == 1
    _seed(engines)
    _configure(tmp_path)
    assert setup_sonar.main(argv) == 0


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["unknown"],
        ["configure", "--engine", SLUG],
        ["configure", "--restart-marker", "x"],
        ["configured"],
        ["configured", "--engine", "not-a-slug"],
        ["configured", "--engine", "a/b/c"],
        ["configured", "--engine", "bad owner/repo"],
        ["configured", "--engine", "owner/"],
    ],
)
def test_main_exits_2_on_a_usage_error(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        setup_sonar.main(argv)
    assert raised.value.code == 2


def test_the_real_runner_feeds_standard_input_and_returns_the_output() -> None:
    done = setup_sonar._run(  # noqa: SLF001
        [sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"], "typed", "unused"
    )
    assert done.returncode == 0
    assert done.stdout.strip() == "TYPED"


def test_the_real_runner_reports_a_tool_that_does_not_exist() -> None:
    with pytest.raises(setup_sonar.SonarRefused, match="the message") as raised:
        setup_sonar._run(["/nonexistent/tool"], "", "the message")  # noqa: SLF001
    assert raised.value.__cause__ is None


def test_the_real_runner_reports_a_tool_that_takes_too_long(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(setup_sonar, "TOOL_TIMEOUT_S", -9.9)
    with pytest.raises(setup_sonar.SonarRefused, match="too slow"):
        setup_sonar._run(  # noqa: SLF001
            [sys.executable, "-c", "import time; time.sleep(30)"], "", "too slow"
        )


@pytest.mark.parametrize(
    "text",
    [
        setup_sonar.INSTRUCTIONS_TEXT,
        setup_sonar.RISK_TEXT,
        setup_sonar.ADMIN_REJECTED_INSTRUCTION,
        setup_sonar.NO_PROJECT_INSTRUCTION,
        setup_sonar.SEVERAL_PROJECTS_INSTRUCTION,
        setup_sonar.AUTOSCAN_INSTRUCTION,
    ],
)
def test_the_printed_texts_carry_no_credential_or_identity_pattern(text: str) -> None:
    assert leaks(text) == {}


def test_the_instructions_name_both_tokens_the_flag_and_that_nothing_is_echoed() -> None:
    text = " ".join(setup_sonar.INSTRUCTIONS_TEXT.split())
    assert "admin token" in text
    assert "analysis token" in text
    assert "different SonarCloud user" in text
    assert "--accept-sonar-user-token" in text
    assert "never stored" in text
    assert "Neither is shown, printed or passed on any command line" in text


def test_every_browser_instruction_names_the_resume_command() -> None:
    for text in (
        setup_sonar.ADMIN_REJECTED_INSTRUCTION,
        setup_sonar.NO_PROJECT_INSTRUCTION,
        setup_sonar.SEVERAL_PROJECTS_INSTRUCTION,
    ):
        assert text.startswith("In your browser:")
        assert setup_sonar.RESUME_COMMAND in text


def test_the_module_reads_no_environment_variable_of_its_own() -> None:
    source = Path(setup_sonar.__file__).read_text(encoding="utf-8")
    assert "os.environ" not in source
    assert "getenv" not in source


def test_every_github_call_runs_the_gh_executable_and_every_sonarcloud_call_runs_curl(
    world: World, engines: Path, tmp_path: Path
) -> None:
    _seed(engines)
    _configure(tmp_path)
    setup_sonar.is_configured(SLUG)
    assert {argv[0] for argv, _ in world.calls} == {"curl", setup_sonar.GH_EXECUTABLE}
    assert setup_sonar.GH_EXECUTABLE == "gh"
