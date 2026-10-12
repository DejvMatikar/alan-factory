"""CLI to resolve, acknowledge, or ack-comment a Copilot review item.

Thread modes (review threads):
    uv run python scripts/alan_copilot_resolve.py [--repo owner/name] \
        <pr_number> <thread_id> fix|acknowledge <body>
    fix         -- resolve the thread without posting a reply
    acknowledge -- post a reply then resolve the thread

Comment mode (PR-level issue comments / review bodies — no GitHub
`resolveIssueComment` mutation exists, so we ack via reply + a local
acked-id store namespaced per repository under the state root):
    uv run python scripts/alan_copilot_resolve.py [--repo owner/name] \
        comment_ack <pr_number> <database_id> <body>

--repo defaults to the default engine's own repository.
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime

import alan.teams._pr_audit_log as _pr_audit_log
import alan.teams._pr_state_paths as _pr_state_paths
import alan.teams._pr_threads as _pr_threads
from alan.engine_config import DEFAULT_FORGE_TARGET, ForgeTarget, RepoIdentity
from alan.forge import set_active_forge
from alan.forge_gh import GhForge
from alan.teams._copilot_resolution import post_pr_comment, post_reply

_PLACEHOLDER_CREDENTIAL_ENV = "ALAN_COPILOT_RESOLVE_PLACEHOLDER_TOKEN"  # noqa: S105  # nosec B105


def _resolve_and_audit(
    mode: str, pr_number: int, thread_id: str, body: str
) -> int:
    if mode == "acknowledge" and not post_reply(thread_id, body):
        print(
            f"Failed to post acknowledgement reply on thread {thread_id}",
            file=sys.stderr,
        )
        _pr_audit_log.append_copilot_audit({
            "action": mode,
            "pr_number": pr_number,
            "thread_id": thread_id,
            "comment_body": body,
            "reply_posted": False,
            "resolved": False,
            "resolved_at": datetime.now(UTC).isoformat(),
        })
        return 1
    resolved = _pr_threads.resolve_review_thread(thread_id)
    _pr_audit_log.append_copilot_audit({
        "action": mode,
        "pr_number": pr_number,
        "thread_id": thread_id,
        "comment_body": body,
        "resolved": resolved,
        "resolved_at": datetime.now(UTC).isoformat(),
    })
    return 0 if resolved else 1


def _comment_ack(
    pr_number: int, database_id: int, body: str, forge_target: ForgeTarget,
) -> int:
    reply_ok = post_pr_comment(pr_number, body, forge_target=forge_target)
    if reply_ok:
        _pr_state_paths.save_acked_comment_id(pr_number, database_id, forge_target)
    _pr_audit_log.append_copilot_audit({
        "action": "comment_ack",
        "pr_number": pr_number,
        "database_id": database_id,
        "comment_body": body,
        "reply_posted": reply_ok,
        "acked": reply_ok,
        "acked_at": datetime.now(UTC).isoformat(),
    })
    if not reply_ok:
        print(
            f"Failed to post acknowledgement reply on PR #{pr_number} "
            f"(database_id={database_id})",
            file=sys.stderr,
        )
        return 1
    return 0


_THREAD_MODES = ("fix", "acknowledge")
_USAGE = (
    "Usage: alan_copilot_resolve [--repo owner/name] <pr_number> <thread_id> "
    "fix|acknowledge <body>\n"
    "       alan_copilot_resolve [--repo owner/name] comment_ack <pr_number> "
    "<database_id> <body>"
)


def _parse_int(name: str, raw: str) -> int | None:
    try:
        return int(raw)
    except ValueError:
        print(f"Invalid {name}: {raw!r} — expected an integer", file=sys.stderr)
        return None


def _extract_repo_flag(argv: list[str]) -> tuple[str | None, list[str]] | None:
    if "--repo" not in argv:
        return None, argv
    idx = argv.index("--repo")
    if idx + 1 >= len(argv):
        return None
    return argv[idx + 1], [*argv[:idx], *argv[idx + 2 :]]


def _forge_target(repo: str | None) -> ForgeTarget | None:
    if repo is None:
        return DEFAULT_FORGE_TARGET
    owner, sep, name = repo.partition("/")
    if not sep or not owner or not name:
        print(f"Invalid --repo value: {repo!r} — expected owner/name", file=sys.stderr)
        return None
    return ForgeTarget(
        repo=RepoIdentity(owner=owner, repo=name),
        credential_env_name=_PLACEHOLDER_CREDENTIAL_ENV,
    )


def _dispatch_comment_ack(argv: list[str], forge_target: ForgeTarget) -> int:
    pr = _parse_int("pr_number", argv[1])
    db = _parse_int("database_id", argv[2])
    if pr is None or db is None:
        return 2
    body = " ".join(argv[3:])
    return _comment_ack(pr, db, body, forge_target)


def _dispatch_thread(argv: list[str]) -> int:
    pr = _parse_int("pr_number", argv[0])
    if pr is None:
        return 2
    mode = argv[2]
    if mode not in _THREAD_MODES:
        print(f"Unknown mode: {mode!r}. Expected fix|acknowledge", file=sys.stderr)
        return 2
    body = " ".join(argv[3:])
    return _resolve_and_audit(mode, pr, argv[1], body)


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    extracted = _extract_repo_flag(argv)
    if extracted is None:
        print(_USAGE, file=sys.stderr)
        return 2
    repo, argv = extracted
    forge_target = _forge_target(repo)
    if forge_target is None:
        return 2
    if len(argv) < 4:
        print(_USAGE, file=sys.stderr)
        return 2
    set_active_forge(GhForge())
    if argv[0] == "comment_ack":
        return _dispatch_comment_ack(argv, forge_target)
    return _dispatch_thread(argv)


if __name__ == "__main__":
    sys.exit(main())
