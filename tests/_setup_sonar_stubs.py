from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

PROBES_LOG = "sonar_probes.jsonl"
GH_STATE = "sonar_gh_state.json"
GH_WRITES_LOG = "sonar_gh_writes.jsonl"
AUTOSCAN_KEY = "sonar.autoscan.enabled"
DENIED_TEXT = "gh: HTTP 403"
UNBOUND_REPOSITORY = "someone-else/other-repo"
_USER_LINE = re.compile(r'user = "(.*):"')


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _append(ctx: Any, log: str, row: dict[str, Any]) -> None:
    ctx.state.mkdir(parents=True, exist_ok=True)
    with (ctx.state / log).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def _bound_keys(ctx: Any) -> list[str]:
    key = str(ctx.flag("sonar_project_key", ""))
    count = int(ctx.flag("sonar_bound_projects", 1))
    return [key if index == 0 else f"{key}-{index}" for index in range(count)]


def _unbound_keys(ctx: Any) -> list[str]:
    return [f"unbound-project-{index}" for index in range(ctx.flag("sonar_unbound_projects", 0))]


def _account(ctx: Any, token: str) -> tuple[str, int] | None:
    if token == ctx.flag("sonar_admin_token", ""):
        return str(ctx.flag("sonar_admin_login", "")), int(ctx.flag("sonar_admin_status", 200))
    if token == ctx.flag("sonar_analysis_token", ""):
        return (
            str(ctx.flag("sonar_analysis_login", "")),
            int(ctx.flag("sonar_analysis_status", 200)),
        )
    return None


def _identity(ctx: Any, token: str) -> tuple[int, Any]:
    account = _account(ctx, token)
    if account is None:
        return 401, {}
    login, status = account
    if status != 200:
        return status, {}
    anonymous = ctx.flag("sonar_analysis_anonymous", False)
    if token == ctx.flag("sonar_analysis_token", "") and anonymous:
        return 200, {"isLoggedIn": False}
    return 200, {"isLoggedIn": True, "login": login}


def _binding(ctx: Any, project: str) -> tuple[int, Any]:
    if project in _bound_keys(ctx):
        return 200, {"repository": ctx.flag("secret_engine", "")}
    if project in _unbound_keys(ctx):
        return 200, {"repository": UNBOUND_REPOSITORY}
    return 404, {}


def _settings(ctx: Any, query: dict[str, str]) -> tuple[int, Any]:
    value = ctx.flag("sonar_autoscan", "false")
    rows = [] if value is None else [{"key": query.get("keys", AUTOSCAN_KEY), "value": value}]
    return 200, {"settings": rows}


def _admin_answer(ctx: Any, path: str, query: dict[str, str]) -> tuple[int, Any]:
    organization = ctx.flag("sonar_organization", "")
    if path == "/api/organizations/search":
        return 200, {"organizations": [{"key": organization}]}
    if path == "/api/projects/search":
        keys = [*_bound_keys(ctx), *_unbound_keys(ctx)]
        listed = keys if query.get("organization") == organization else []
        return 200, {"components": [{"key": key} for key in listed]}
    if path == "/api/alm_settings/get_binding":
        return _binding(ctx, query.get("project", ""))
    if path == "/api/settings/values":
        return _settings(ctx, query)
    return 404, {}


def _answer(ctx: Any, path: str, query: dict[str, str], token: str) -> tuple[int, Any]:
    if path == "/api/users/current":
        return _identity(ctx, token)
    if token != ctx.flag("sonar_admin_token", ""):
        return 401, {}
    status = int(ctx.flag("sonar_admin_status", 200))
    return (status, {}) if status != 200 else _admin_answer(ctx, path, query)


def curl(ctx: Any, args: list[str]) -> int:
    url = next(arg for arg in args if arg.startswith("http"))
    config = sys.stdin.read() if "--config" in args else ""
    if ctx.flag("sonar_unreachable", False):
        print("curl: (7) Failed to connect", file=sys.stderr)
        return 7
    matched = _USER_LINE.search(config)
    token = matched.group(1) if matched else ""
    parts = urlsplit(url)
    _append(ctx, PROBES_LOG, {"path": parts.path, "sha256": sha256(token)})
    status, body = _answer(ctx, parts.path, dict(parse_qsl(parts.query)), token)
    sys.stdout.write(json.dumps(body) + "\n" + str(status))
    return 0


def _gh_state(ctx: Any) -> dict[str, dict[str, str]]:
    path: Path = ctx.state / GH_STATE
    if not path.exists():
        return {"variables": {}, "secrets": {}}
    held: dict[str, dict[str, str]] = json.loads(path.read_text(encoding="utf-8"))
    return held


def _save_gh_state(ctx: Any, state: dict[str, dict[str, str]]) -> None:
    ctx.state.mkdir(parents=True, exist_ok=True)
    (ctx.state / GH_STATE).write_text(json.dumps(state), encoding="utf-8")


def gh_variable_set(ctx: Any, args: list[str]) -> int:
    if ctx.flag("gh_variable_fails", False):
        print(DENIED_TEXT, file=sys.stderr)
        return 1
    name, value = args[2], args[args.index("--body") + 1]
    state = _gh_state(ctx)
    state["variables"][name] = value
    _save_gh_state(ctx, state)
    _append(ctx, GH_WRITES_LOG, {"kind": "variable", "name": name, "value": value})
    return 0


def gh_secret_set(ctx: Any, args: list[str]) -> int:
    body = sys.stdin.read()
    if ctx.flag("gh_secret_fails", False):
        print(DENIED_TEXT, file=sys.stderr)
        return 1
    name, digest = args[2], sha256(body)
    state = _gh_state(ctx)
    state["secrets"][name] = digest
    _save_gh_state(ctx, state)
    _append(ctx, GH_WRITES_LOG, {"kind": "secret", "name": name, "sha256": digest})
    return 0


def gh_variable_list(ctx: Any, args: list[str]) -> int:
    rows = [{"name": name, "value": value} for name, value in _gh_state(ctx)["variables"].items()]
    print(json.dumps(rows))
    return 0


def gh_secret_list(ctx: Any, args: list[str]) -> int:
    print(json.dumps([{"name": name} for name in _gh_state(ctx)["secrets"]]))
    return 0
