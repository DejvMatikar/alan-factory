from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

MATRIX_KEYS = (
    "conversation_resolution",
    "required_approvals",
    "squash_merge_enabled",
    "delete_branch_on_merge",
    "required_checks",
)
CONTROL_HEADER = "X-Alan-Control-Token"
SECRETS_LOG = "secrets_written.jsonl"
IDENTITY_LOG = "identity_probes.jsonl"
WRITES_LOG = "gh_writes.jsonl"
MODES_LOG = "private_file_modes.jsonl"
DENIED_TEXT = "gh: HTTP 403"
_ENGINE_PATH = re.compile(r"/api/engines/([^/]+)/([^/]+)(/preflight|/protect)?")
_REPO_PATH = re.compile(r"repos/([^/]+)/([^/]+)(/interaction-limits)?")


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def ci_stubs() -> Any:
    path = Path(__file__).with_name("_setup_ci_stubs.py")
    spec = importlib.util.spec_from_file_location("_setup_ci_stubs", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def finish_stubs() -> Any:
    path = Path(__file__).with_name("_setup_finish_stubs.py")
    spec = importlib.util.spec_from_file_location("_setup_finish_stubs", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class World:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.path: Path = ctx.state / "world.json"

    def _held(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        held: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
        return held

    def get(self, name: str, default: Any = None) -> Any:
        held = self._held()
        return held[name] if name in held else self.ctx.flag(name, default)

    def set(self, name: str, value: Any) -> None:
        held = self._held()
        held[name] = value
        self.ctx.state.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(held), encoding="utf-8")

    def append(self, log: str, row: dict[str, Any]) -> None:
        self.ctx.state.mkdir(parents=True, exist_ok=True)
        with (self.ctx.state / log).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")


def _repo_body(world: World, slug: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "full_name": slug,
        "visibility": world.get("visibility", "public"),
        "default_branch": "main",
    }
    features = {
        "secret_scanning": world.get("scanning", "enabled"),
        "secret_scanning_push_protection": world.get("push_protection", "enabled"),
    }
    analysis = {key: {"status": value} for key, value in features.items() if value is not None}
    if world.get("offers_security_settings", True):
        body["security_and_analysis"] = analysis
    policy = world.get("pr_policy", "collaborators_only")
    if policy is not None:
        body["pull_request_creation_policy"] = policy
    return body


def _limits_body(world: World) -> dict[str, Any]:
    limit = world.get("interaction_limit", "collaborators_only")
    if limit is None:
        return {}
    return {"limit": limit, "origin": "repository", "expires_at": "2099-01-01T00:00:00Z"}


def _gh_user(ctx: Any) -> int:
    if ctx.flag("gh_api_fails", False):
        print("gh: HTTP 401", file=sys.stderr)
        return 1
    print(ctx.flag("gh_login", "example-owner"))
    return 0


def _gh_identity(world: World) -> int:
    token = os.environ.get("GH_TOKEN")
    if token is None:
        kind, login = "classic", world.get("gh_login", "example-owner")
    else:
        world.append(IDENTITY_LOG, {"sha256": sha256(token)})
        kind, login = world.get("identity_kind", "fine-grained"), world.get("token_login", "")
    if kind == "rejected":
        print("gh: Bad credentials (HTTP 401)", file=sys.stderr)
        return 1
    head = "HTTP/2.0 200 OK\r\ncontent-type: application/json; charset=utf-8\r\n"
    if kind == "classic":
        head += "x-oauth-scopes: repo, workflow\r\n"
    body = {"id": 1} if kind == "no-login" else {"login": login}
    sys.stdout.write(head + "\r\n" + json.dumps(body) + "\n")
    return 0


def _gh_read(world: World, endpoint: str) -> int:
    matched = _REPO_PATH.fullmatch(endpoint)
    if matched is None:
        print("stub: unexpected gh api read", file=sys.stderr)
        return 86
    slug = f"{matched.group(1)}/{matched.group(2)}"
    if matched.group(3) is not None:
        failing, body = world.get("limits_read_fails", False), _limits_body(world)
    else:
        failing, body = world.get("repo_read_fails", False), _repo_body(world, slug)
    if failing:
        print(DENIED_TEXT, file=sys.stderr)
        return 1
    print(json.dumps(body))
    return 0


def _written_setting(endpoint: str, body: dict[str, Any]) -> str:
    analysis = body.get("security_and_analysis", {})
    if endpoint.endswith("/interaction-limits"):
        return "interaction_limits"
    if "secret_scanning" in analysis:
        return "secret_scanning"
    if "secret_scanning_push_protection" in analysis:
        return "push_protection"
    if "pull_request_creation_policy" in body:
        return "pr_policy"
    return "unknown"


def _apply_write(world: World, setting: str, body: dict[str, Any]) -> None:
    if setting == "secret_scanning":
        world.set("scanning", "enabled")
    if setting == "push_protection":
        world.set("push_protection", "enabled")
    if setting == "interaction_limits":
        world.set("interaction_limit", body["limit"])
    if setting == "pr_policy" and world.get("pr_policy_sticks", True):
        world.set("pr_policy", body["pull_request_creation_policy"])


def _gh_write(world: World, rest: list[str]) -> int:
    method = rest[rest.index("-X") + 1]
    endpoint = next(arg for arg in rest if arg.startswith("repos/"))
    body: dict[str, Any] = json.loads(sys.stdin.read() or "{}")
    world.append(WRITES_LOG, {"method": method, "endpoint": endpoint, "body": body})
    setting = _written_setting(endpoint, body)
    if setting in world.get("writes_fail", []):
        print(DENIED_TEXT, file=sys.stderr)
        return 1
    _apply_write(world, setting, body)
    return 0


def gh_api(ctx: Any, args: list[str]) -> int:
    world = World(ctx)
    rest = args[1:]
    finish = finish_stubs()
    if finish.is_bot_call(world):
        return int(finish.gh_api(world, rest))
    if rest and "/commits/" in rest[0]:
        return int(ci_stubs().commit_api(world.ctx, rest[0]))
    if "-X" in rest:
        return _gh_write(world, rest)
    if rest[:1] == ["-i"]:
        return _gh_identity(world)
    if rest[:1] == ["user"]:
        return _gh_user(ctx)
    return _gh_read(world, rest[0])


@dataclass
class Request:
    method: str = "GET"
    url: str = ""
    out: str | None = None
    write_out: str | None = None
    header_files: list[str] = field(default_factory=list)
    reads_stdin: bool = False


_VALUE_FLAGS = {"-X": "method", "-o": "out", "-w": "write_out", "--max-time": None}


def _parse_curl(args: list[str]) -> Request:
    request = Request()
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in _VALUE_FLAGS:
            attribute = _VALUE_FLAGS[arg]
            if attribute is not None:
                setattr(request, attribute, args[index + 1])
            index += 2
        elif arg == "-H":
            header = args[index + 1]
            if header.startswith("@"):
                request.header_files.append(header[1:])
            index += 2
        elif arg == "--data-binary":
            request.reads_stdin = args[index + 1] == "@-"
            index += 2
        else:
            request.url = arg if arg.startswith("http") else request.url
            index += 1
    return request


def _record_private_file(world: World, kind: str, path: Path) -> None:
    row = {"kind": kind, "mode": stat.S_IMODE(path.stat().st_mode)}
    row["dir_mode"] = stat.S_IMODE(path.parent.stat().st_mode)
    world.append(MODES_LOG, row)


def _supplied_token(world: World, request: Request) -> str | None:
    for name in request.header_files:
        path = Path(name)
        _record_private_file(world, "header", path)
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.lower().startswith(CONTROL_HEADER.lower() + ":"):
                return line.partition(":")[2].strip()
    return None


def _control_token() -> str:
    path = Path(os.environ["HOME"]) / ".alan" / "state" / "control_token"
    return path.read_text(encoding="utf-8").strip()


def _unauthorized(world: World, request: Request) -> tuple[int, dict[str, Any]] | None:
    supplied = _supplied_token(world, request)
    if supplied is None:
        return 401, {"detail": "control token missing"}
    forced = int(world.get("control_token_status", 0))
    if forced:
        return forced, {"detail": "control token refused"}
    if supplied != _control_token():
        return 403, {"detail": "control token mismatch"}
    return None


def _engine_body(world: World, owner: str, repo: str) -> tuple[int, dict[str, Any]]:
    if not world.get("engine_registered", True):
        return 404, {"detail": "engine not found"}
    identity = {"owner": owner, "repo": repo}
    forge = {"repo": identity} if world.get("engine_has_forge", True) else None
    return 200, {"repo": identity, "forge": forge}


def _secret_rows(world: World) -> list[dict[str, str]]:
    held = world.get("secrets", None)
    if held is not None:
        rows: list[dict[str, str]] = held
        return rows
    slug = world.get("secret_engine", "example-owner/example-repo")
    purposes = [
        purpose
        for purpose, stored in (
            ("transport", world.get("transport_stored", True)),
            ("reviewer", world.get("reviewer_stored", False)),
        )
        if stored
    ]
    return [{"name": f"{slug}:{purpose}", "fingerprint": "0" * 12} for purpose in purposes]


def _secrets_list(world: World) -> tuple[int, Any]:
    if world.get("secrets_list_garbled", False):
        return 200, "this is not json"
    return 200, {"secrets": _secret_rows(world)}


def _record_stdin_mode(world: World) -> None:
    mode = os.fstat(0).st_mode
    if stat.S_ISREG(mode):
        world.append(MODES_LOG, {"kind": "stdin", "mode": stat.S_IMODE(mode)})


def _secret_post(world: World) -> tuple[int, dict[str, Any]]:
    _record_stdin_mode(world)
    payload: dict[str, Any] = json.loads(sys.stdin.read())
    status = int(world.get("secret_post_status", 201))
    if status != 201:
        return status, {"detail": [{"loc": ["body", "value"], "input": payload["value"]}]}
    digest = sha256(payload["value"])
    logged = {"engine": payload["engine"], "purpose": payload["purpose"], "sha256": digest}
    if "expires_at" in payload:
        logged["expires_at"] = payload["expires_at"]
    world.append(SECRETS_LOG, logged)
    name = f"{payload['engine']}:{payload['purpose']}"
    world.set("secrets", [*_secret_rows(world), {"name": name, "fingerprint": digest[:12]}])
    return 201, {"name": name, "fingerprint": digest[:12]}


def _finding(check: str, status: str, message: str = "") -> dict[str, str]:
    return {"check": check, "status": status, "message": message, "remedy": "", "remedy_url": ""}


def _protection_findings(world: World) -> list[dict[str, str]]:
    if world.get("protection_unreadable", False):
        return [_finding("branch_protection_readable", "fail", "cannot read branch protection")]
    held = world.get("protection_matching", True)
    status = "pass" if held else "fail"
    message = "" if held else "does not satisfy the required setting"
    return [_finding(key, status, message) for key in MATRIX_KEYS]


def _distinct_finding(world: World) -> dict[str, str]:
    if any(row["name"].endswith(":reviewer") for row in _secret_rows(world)):
        return _finding("reviewer_distinct_from_author", "pass")
    return _finding("reviewer_distinct_from_author", "unknown", "could not resolve both identities")


def _preflight_body(world: World, slug: str) -> dict[str, Any]:
    stored = any(row["name"].endswith(":transport") for row in _secret_rows(world))
    base = "pass" if stored else "fail"
    findings = [
        _finding("author_token_valid", base),
        _distinct_finding(world),
        _finding("repo_clonable", base),
        _finding("gate_warmup_tools_reachable", "pass"),
        _finding("flow_graph_and_posture_valid", "pass"),
        *_protection_findings(world),
        _finding("dispatch_readiness", "fail", "reviewer_credential: missing; code_quality: unset"),
    ]
    overrides = world.get("preflight_status", {})
    for row in findings:
        row["status"] = overrides.get(row["check"], row["status"])
    world.set(
        "last_distinct",
        next(row["status"] for row in findings if row["check"] == "reviewer_distinct_from_author"),
    )
    return {"slug": slug, "overall": "fail", "findings": findings, "checked_at": "2026-01-01"}


def _deferred(key: str) -> dict[str, str]:
    message = "cannot read branch protection: token lacks admin scope"
    return {"check": key, "status": "fail", "message": message, "remedy": "", "remedy_url": ""}


def _protect_body(world: World, slug: str) -> tuple[int, Any]:
    result = world.get("protect_result", "applied")
    base: dict[str, Any] = {"slug": slug, "protection_applied_keys": [], "protection_deferred": []}
    if result == "applied":
        world.set("protection_matching", True)
        return 200, {**base, "protection_applied_keys": list(MATRIX_KEYS)}
    if result == "deferred":
        return 200, {**base, "protection_deferred": [_deferred(key) for key in MATRIX_KEYS]}
    if result == "partial":
        applied, rest = MATRIX_KEYS[:3], MATRIX_KEYS[3:]
        return 200, {
            **base,
            "protection_applied_keys": list(applied),
            "protection_deferred": [_deferred(key) for key in rest],
        }
    if result == "unprocessable":
        return 422, {"detail": "engine has no forge configured"}
    if result == "garbled":
        return 200, "this is not json"
    return 500, {"detail": "boom"}


def _engine_route(world: World, request: Request, matched: re.Match[str]) -> tuple[int, Any]:
    owner, repo, action = matched.group(1), matched.group(2), matched.group(3)
    slug = f"{owner}/{repo}"
    if action is None:
        return _engine_body(world, owner, repo)
    denied = _unauthorized(world, request)
    if denied is not None:
        return denied
    if action == "/preflight" and world.get("preflight_garbled", False):
        return 200, "this is not json"
    if action == "/preflight":
        return 200, _preflight_body(world, slug)
    return _protect_body(world, slug)


def _route(world: World, request: Request) -> tuple[int, Any]:
    path = urlsplit(request.url).path
    matched = _ENGINE_PATH.fullmatch(path)
    if matched is not None:
        return _engine_route(world, request, matched)
    if path == "/api/secrets" and request.method == "GET":
        return _secrets_list(world)
    if path == "/api/secrets":
        denied = _unauthorized(world, request)
        return denied if denied is not None else _secret_post(world)
    return 404, {"detail": "not found"}


def curl(ctx: Any, args: list[str]) -> int:
    request = _parse_curl(args)
    world = World(ctx)
    if not world.get("api_reachable", True):
        sys.stdout.write("000")
        return 7
    status, body = _route(world, request)
    text = body if isinstance(body, str) else json.dumps(body)
    if request.out is not None:
        Path(request.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    if request.write_out is not None:
        sys.stdout.write(request.write_out.replace("%{http_code}", str(status)))
    return 0
