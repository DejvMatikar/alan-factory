from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO, cast
from urllib.parse import urlencode

from alan import default_profile, default_profile_writer, roots, setup_transport

SONAR_HOST = default_profile.SONAR_HOST_URL
TOKEN_SECRET_NAME = default_profile.SONAR_TOKEN_ENV
ORGANIZATION_VARIABLE = "SONAR_ORGANIZATION"
PROJECT_KEY_VARIABLE = "SONAR_PROJECT_KEY"
AUTOSCAN_SETTING = "sonar.autoscan.enabled"
GH_EXECUTABLE = "gh"
TOOL_TIMEOUT_S = 30.0
ADMIN_PROMPT = "SonarCloud admin token (optional, press Enter to skip; input is hidden): "
ANALYSIS_PROMPT = "SonarCloud analysis token (input is hidden): "
RESUME_COMMAND = "scripts/setup_macos.sh --from sonar"
EXIT_SKIPPED = 3

_KEY_SHAPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]*")
_TOKEN_SHAPE = re.compile(r"[A-Za-z0-9_.-]{1,200}")

INSTRUCTIONS_TEXT = "\n".join(
    [
        "SonarCloud reviews every pull request for code quality. It advises; it never blocks a",
        "merge. It is optional: press Enter at the admin token prompt to skip it now and connect",
        "it later. Before you paste anything:",
        "  1. Sign in at https://sonarcloud.io (now named SonarQube Cloud) with GitHub and import",
        "     this repository: + (top right) > Analyze new project > your organization > tick the",
        "     repository > Set Up. Import it once: setup refuses several projects for one",
        "     repository.",
        "  2. Create an admin token: your avatar > My account > Access Tokens > Personal Tokens >",
        "     Generate Token, as a user who administers the organization. Setup uses it only to",
        "     find your project and to read the project's analysis settings; it is never stored.",
        "  3. Create an analysis token the same way, as a different SonarCloud user than the admin",
        "     token's. It is stored as a GitHub Actions secret of this repository, so CI can send",
        "     analyses. A token from the same user is refused unless you re-run with",
        "     --accept-sonar-user-token.",
        "Paste the admin token first, then the analysis token. Neither is shown, printed or passed",
        "on any command line.",
    ]
)
RISK_TEXT = "\n".join(
    [
        "You asked setup to accept an analysis token from the same SonarCloud user as the admin",
        "token (--accept-sonar-user-token). That token can do everything that user can, including",
        "administering the organization, and every workflow run in this repository can use it. It",
        "is stored as a GitHub Actions secret and stays there. A token from a separate SonarCloud",
        "user limits the damage if it leaks; revoke this one when you uninstall.",
    ]
)
ADMIN_REJECTED_INSTRUCTION = (
    "In your browser: open https://sonarcloud.io, go to My account > Access Tokens, generate a new "
    "token as a user who administers the organization, then re-run "
    f"{RESUME_COMMAND}."
)
NO_PROJECT_INSTRUCTION = (
    "In your browser: open https://sonarcloud.io, choose + > Analyze new project, import this "
    f"repository into your organization, then re-run {RESUME_COMMAND}."
)
SEVERAL_PROJECTS_INSTRUCTION = (
    "In your browser: open https://sonarcloud.io and keep exactly one project bound to this "
    "repository: open each extra project, choose Administration > Delete project, then re-run "
    f"{RESUME_COMMAND}."
)
AUTOSCAN_INSTRUCTION = (
    "SonarCloud did not confirm that Automatic Analysis is off for this project. In your "
    "browser: open the project on https://sonarcloud.io, choose Administration > Analysis "
    "Method and turn Automatic Analysis off, or the analysis CI sends is rejected. Setup "
    "carries on; nothing else waits for it."
)

_EMPTY_ANALYSIS_MESSAGE = (
    "no SonarCloud analysis token was entered (empty input or end of input); run setup in a "
    "terminal to type it, or pipe it on standard input"
)
_SHAPE_MESSAGE = (
    "the input does not look like a SonarCloud token; it holds characters a token never has"
)
_ADMIN_REJECTED_MESSAGE = (
    "SonarCloud rejected the admin token; it may be mistyped, expired, revoked or from a user "
    "who does not administer the organization"
)
_ANALYSIS_REJECTED_MESSAGE = (
    "SonarCloud rejected the analysis token; it may be mistyped, expired or revoked"
)
_ADMIN_UNKNOWN_MESSAGE = (
    "SonarCloud did not say which user the admin token belongs to; it must be a user token"
)
_ANALYSIS_UNKNOWN_MESSAGE = (
    "SonarCloud did not say which user the analysis token belongs to, so it cannot be checked "
    "against the admin token; it must be a user token"
)
_IDENTICAL_MESSAGE = (
    "the analysis token is the same token as the admin token; create a second token, from a "
    "different SonarCloud user, for CI"
)
_SAME_USER_MESSAGE = (
    "the analysis token belongs to the same SonarCloud user as the admin token; use a token "
    "from a different user, or re-run with --accept-sonar-user-token to accept the risk"
)
_NO_PROJECT_MESSAGE = "no SonarCloud project is bound to this repository"
_SEVERAL_PROJECTS_MESSAGE = "several SonarCloud projects are bound to this repository"
_UNREACHABLE_MESSAGE = "SonarCloud could not be reached"
_GH_UNAVAILABLE_MESSAGE = "gh could not be run to write the SonarCloud settings"
_UNREADABLE_MESSAGE = "SonarCloud's answer to {path} could not be read"
_HTTP_MESSAGE = "SonarCloud answered HTTP {status} to the request for {path}"
_GH_REFUSED_MESSAGE = (
    "GitHub refused to store {what} for the repository; check that the signed-in gh user "
    "administers it"
)
_SLUG_MESSAGE = "the engine must be written <owner>/<repo>"


class SonarRefused(RuntimeError):
    def __init__(self, message: str, instruction: str = "") -> None:
        super().__init__(message)
        self.instruction = instruction


class SonarSkipped(RuntimeError):
    pass


@dataclass(frozen=True)
class SonarProject:
    organization: str
    key: str


def _admin_rejected() -> SonarRefused:
    return SonarRefused(_ADMIN_REJECTED_MESSAGE, ADMIN_REJECTED_INSTRUCTION)


def _analysis_rejected() -> SonarRefused:
    return SonarRefused(_ANALYSIS_REJECTED_MESSAGE)


def _run(argv: list[str], stdin: str, unavailable: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=TOOL_TIMEOUT_S + 10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SonarRefused(unavailable) from None


def _decoded(path: str, body: str, code: str, rejected: Callable[[], SonarRefused]) -> object:
    status = int(code) if code.isdigit() else 0
    if status in (401, 403):
        raise rejected()
    if status == 404:
        return None
    if status != 200:
        raise SonarRefused(_HTTP_MESSAGE.format(status=status, path=path))
    try:
        return cast("object", json.loads(body))
    except json.JSONDecodeError:
        raise SonarRefused(_UNREADABLE_MESSAGE.format(path=path)) from None


def _get(
    token: str, path: str, params: dict[str, str], rejected: Callable[[], SonarRefused]
) -> object:
    url = f"{SONAR_HOST}{path}?{urlencode(params)}"
    argv = [
        "curl",
        "--silent",
        "--show-error",
        "--max-time",
        str(int(TOOL_TIMEOUT_S)),
        "--write-out",
        "\n%{http_code}",
        "--config",
        "-",
        url,
    ]
    done = _run(argv, f'user = "{token}:"\n', _UNREACHABLE_MESSAGE)
    if done.returncode != 0:
        raise SonarRefused(_UNREACHABLE_MESSAGE)
    body, _, code = done.stdout.rpartition("\n")
    return _decoded(path, body, code, rejected)


def _mapping(value: object) -> dict[str, object]:
    return cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _rows(value: object, name: str) -> list[dict[str, object]]:
    items = _mapping(value).get(name)
    if not isinstance(items, list):
        return []
    return [_mapping(item) for item in cast("list[object]", items)]


def _key_text(row: dict[str, object], name: str) -> str:
    value = row.get(name)
    return value if isinstance(value, str) and _KEY_SHAPE.fullmatch(value) else ""


def _login(token: str, rejected: Callable[[], SonarRefused], unknown: str) -> str:
    answer = _mapping(_get(token, "/api/users/current", {}, rejected))
    login = answer.get("login")
    if answer.get("isLoggedIn") is True and isinstance(login, str) and login:
        return login
    raise SonarRefused(unknown)


def _organizations(admin: str) -> list[str]:
    answer = _get(
        admin, "/api/organizations/search", {"member": "true", "ps": "100"}, _admin_rejected
    )
    return [key for row in _rows(answer, "organizations") if (key := _key_text(row, "key"))]


def _candidates(admin: str, organization: str, repo: str) -> list[str]:
    params = {"organization": organization, "q": repo, "ps": "100"}
    answer = _get(admin, "/api/projects/search", params, _admin_rejected)
    return [key for row in _rows(answer, "components") if (key := _key_text(row, "key"))]


def _bound_repository(admin: str, key: str) -> str:
    answer = _get(admin, "/api/alm_settings/get_binding", {"project": key}, _admin_rejected)
    repository = _mapping(answer).get("repository")
    return repository if isinstance(repository, str) else ""


def discover_project(admin: str, slug: str) -> SonarProject:
    repo = slug.partition("/")[2]
    found = [
        SonarProject(organization, key)
        for organization in _organizations(admin)
        for key in _candidates(admin, organization, repo)
        if _bound_repository(admin, key).casefold() == slug.casefold()
    ]
    if not found:
        raise SonarRefused(_NO_PROJECT_MESSAGE, NO_PROJECT_INSTRUCTION)
    if len(found) > 1:
        raise SonarRefused(_SEVERAL_PROJECTS_MESSAGE, SEVERAL_PROJECTS_INSTRUCTION)
    return found[0]


def autoscan_is_off(admin: str, project_key: str) -> bool:
    params = {"component": project_key, "keys": AUTOSCAN_SETTING}
    try:
        answer = _get(admin, "/api/settings/values", params, _admin_rejected)
    except SonarRefused:
        return False
    return any(
        row.get("key") == AUTOSCAN_SETTING and str(row.get("value")).lower() == "false"
        for row in _rows(answer, "settings")
    )


def check_analysis_token(
    analysis: str, admin: str, admin_login: str, *, accept_same_user: bool
) -> None:
    if analysis == admin:
        raise SonarRefused(_IDENTICAL_MESSAGE)
    login = _login(analysis, _analysis_rejected, _ANALYSIS_UNKNOWN_MESSAGE)
    if login.casefold() != admin_login.casefold():
        return
    if not accept_same_user:
        raise SonarRefused(_SAME_USER_MESSAGE)
    print(RISK_TEXT)


def _shaped(token: str) -> str:
    if _TOKEN_SHAPE.fullmatch(token) is None:
        raise SonarRefused(_SHAPE_MESSAGE)
    return token


def _admin_token(stream: TextIO) -> str:
    try:
        token = setup_transport.read_token(stream, prompt=ADMIN_PROMPT)
    except setup_transport.TransportRefused:
        raise SonarSkipped from None
    return _shaped(token)


def _analysis_token(stream: TextIO) -> str:
    token = setup_transport.read_token(
        stream, missing=_EMPTY_ANALYSIS_MESSAGE, prompt=ANALYSIS_PROMPT
    )
    return _shaped(token)


def _mark_restart(marker: Path) -> None:
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()


def _write_profile(slug: str, project: SonarProject, marker: Path) -> None:
    owner, _, repo = slug.partition("/")
    default_profile_writer.write_code_quality(
        roots.engines_root(),
        owner,
        repo,
        project.organization,
        project.key,
        before_write=lambda: _mark_restart(marker),
    )


def _gh_write(args: list[str], stdin: str, what: str) -> None:
    done = _run([GH_EXECUTABLE, *args], stdin, _GH_UNAVAILABLE_MESSAGE)
    if done.returncode != 0:
        raise SonarRefused(_GH_REFUSED_MESSAGE.format(what=what))


def _set_variable(slug: str, name: str, value: str) -> None:
    args = ["variable", "set", name, "--repo", slug, "--body", value]
    _gh_write(args, "", f"the repository variable {name}")


def _set_secret(slug: str, analysis: str) -> None:
    args = ["secret", "set", TOKEN_SECRET_NAME, "--repo", slug]
    _gh_write(args, analysis, f"the repository secret {TOKEN_SECRET_NAME}")


def apply_settings(slug: str, project: SonarProject, analysis: str, marker: Path) -> None:
    _write_profile(slug, project, marker)
    _set_variable(slug, ORGANIZATION_VARIABLE, project.organization)
    _set_variable(slug, PROJECT_KEY_VARIABLE, project.key)
    _set_secret(slug, analysis)


def run_configure(
    slug: str, marker: Path, *, accept_same_user: bool, stream: TextIO
) -> None:
    print(INSTRUCTIONS_TEXT)
    admin = _admin_token(stream)
    admin_login = _login(admin, _admin_rejected, _ADMIN_UNKNOWN_MESSAGE)
    project = discover_project(admin, slug)
    print(
        f"SonarCloud project {project.key} of organization {project.organization} "
        "is bound to this repository."
    )
    if not autoscan_is_off(admin, project.key):
        print(AUTOSCAN_INSTRUCTION)
    analysis = _analysis_token(stream)
    check_analysis_token(analysis, admin, admin_login, accept_same_user=accept_same_user)
    apply_settings(slug, project, analysis, marker)


def _gh_rows(args: list[str]) -> list[dict[str, object]]:
    done = _run([GH_EXECUTABLE, *args], "", _GH_UNAVAILABLE_MESSAGE)
    if done.returncode != 0:
        return []
    try:
        parsed = cast("object", json.loads(done.stdout))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [_mapping(item) for item in cast("list[object]", parsed)]


def _configured(slug: str) -> bool:
    owner, _, repo = slug.partition("/")
    block = default_profile_writer.installed_code_quality(roots.engines_root(), owner, repo)
    variables = {
        row.get("name"): row.get("value")
        for row in _gh_rows(["variable", "list", "--repo", slug, "--json", "name,value"])
    }
    listed = _gh_rows(["secret", "list", "--repo", slug, "--json", "name"])
    secrets = {row.get("name") for row in listed}
    return (
        block is not None
        and variables.get(ORGANIZATION_VARIABLE) == block.organization
        and variables.get(PROJECT_KEY_VARIABLE) == block.project_key
        and TOKEN_SECRET_NAME in secrets
    )


def is_configured(slug: str) -> bool:
    try:
        return _configured(slug)
    except (SonarRefused, default_profile.DefaultProfileError, OSError):
        return False


def _slug(value: str) -> str:
    owner, _, repo = value.partition("/")
    if re.fullmatch(default_profile.OWNER_NAME_PATTERN, owner) and re.fullmatch(
        default_profile.REPO_NAME_PATTERN, repo
    ):
        return value
    raise argparse.ArgumentTypeError(_SLUG_MESSAGE)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m alan.setup_sonar")
    commands = parser.add_subparsers(dest="command", required=True)
    configure = commands.add_parser("configure")
    configure.add_argument("--engine", type=_slug, required=True)
    configure.add_argument("--restart-marker", type=Path, required=True)
    configure.add_argument("--accept-sonar-user-token", action="store_true")
    configured = commands.add_parser("configured")
    configured.add_argument("--engine", type=_slug, required=True)
    return parser


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "configured":
        return 0 if is_configured(args.engine) else 1
    run_configure(
        args.engine,
        args.restart_marker,
        accept_same_user=args.accept_sonar_user_token,
        stream=sys.stdin,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _dispatch(args)
    except SonarSkipped:
        return EXIT_SKIPPED
    except SonarRefused as exc:
        if exc.instruction:
            print(exc.instruction)
        print(str(exc), file=sys.stderr)
        return 1
    except (setup_transport.TransportRefused, default_profile.DefaultProfileError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
