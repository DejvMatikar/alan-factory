from __future__ import annotations

import hashlib
import json
import os
import sys
from typing import Any

BOT_LOG = "bot_probes.jsonl"
UPDATE_LABEL = "ai.alan.update"
PERMISSION_FLAGS = {
    "admin": ("admin", "maintain", "push", "triage", "pull"),
    "maintain": ("maintain", "push", "triage", "pull"),
    "write": ("push", "triage", "pull"),
    "read": ("pull",),
}


def is_bot_call(world: Any) -> bool:
    token = os.environ.get("GH_TOKEN")
    return token is not None and token == world.get("bot_token", "")


def _record(world: Any, call: str) -> None:
    digest = hashlib.sha256(os.environ["GH_TOKEN"].encode("utf-8")).hexdigest()
    world.append(BOT_LOG, {"call": call, "sha256": digest})


def _identity(world: Any) -> int:
    _record(world, "identity")
    kind = world.get("bot_identity_kind", "classic")
    if kind == "rejected":
        print("gh: Bad credentials (HTTP 401)", file=sys.stderr)
        return 1
    head = "HTTP/2.0 200 OK\r\ncontent-type: application/json; charset=utf-8\r\n"
    if kind == "classic":
        head += "x-oauth-scopes: repo\r\n"
    expiry = world.get("bot_expiry")
    if expiry is not None:
        head += f"github-authentication-token-expiration: {expiry}\r\n"
    body = json.dumps({"login": world.get("bot_login", "example-verifier")})
    sys.stdout.write(head + "\r\n" + body + "\n")
    return 0


def _repository(world: Any) -> int:
    _record(world, "repository")
    if not world.get("bot_repo_visible", True):
        print("gh: Not Found (HTTP 404)", file=sys.stderr)
        return 1
    granted = PERMISSION_FLAGS[world.get("bot_permission", "write")]
    flags = {name: name in granted for name in ("admin", "maintain", "push", "triage", "pull")}
    print(json.dumps({"full_name": world.get("secret_engine", ""), "permissions": flags}))
    return 0


def _invitations(world: Any) -> int:
    _record(world, "invitations")
    slug = world.get("secret_engine", "example-owner/example-repo")
    invited = world.get("bot_invited", False)
    print(json.dumps([{"repository": {"full_name": slug}}] if invited else []))
    return 0


def gh_api(world: Any, rest: list[str]) -> int:
    if rest[:1] == ["-i"]:
        return _identity(world)
    if rest[:1] == ["user/repository_invitations"]:
        return _invitations(world)
    if rest[0].startswith("repos/"):
        return _repository(world)
    print("stub: unexpected gh api call for the bot token", file=sys.stderr)
    return 86


def bootstrap_update(ctx: Any, checkout: str) -> int:
    if ctx.flag("update_bootstrap_fails", False):
        print("Bootstrap failed: 5: Input/output error", file=sys.stderr)
        return 5
    ctx.save_agent(
        {
            "label": UPDATE_LABEL,
            "checkout": checkout,
            "runner": "update_runner.sh",
            "pid": 4243,
            "served_sha": "",
            "up": True,
            "raw": None,
        }
    )
    return 0
