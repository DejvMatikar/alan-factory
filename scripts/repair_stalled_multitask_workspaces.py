"""CLI wrapper for the out-of-band subtask completion endpoint.

Posts to ``POST {base_url}/api/workspaces/{workspace}/tasks/{subtask}/complete``
so a human can recover a workspace whose delivery happened out of band (a
hand-delivered pull request, or a sibling pull request that superseded it)
without hand-editing meta.json. The endpoint itself verifies the PR is
actually merged before completing anything; pass --force only when the PR
cannot be verified against GitHub.

Usage:
    uv run --no-active python scripts/repair_stalled_multitask_workspaces.py \\
        --workspace my-task --subtask sub-a --pr 123
    uv run --no-active python scripts/repair_stalled_multitask_workspaces.py \\
        --workspace my-task --subtask sub-a --pr 123 --force --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

_DEFAULT_BASE_URL = "http://127.0.0.1:8585"
_TIMEOUT_S = 30


def _build_url(base_url: str, workspace: str, subtask: str) -> str:
    return f"{base_url.rstrip('/')}/api/workspaces/{workspace}/tasks/{subtask}/complete"


def complete_out_of_band(
    base_url: str, workspace: str, subtask: str, pr_number: int, *, force: bool
) -> dict[str, object]:
    url = _build_url(base_url, workspace, subtask)
    body = json.dumps({"pr_number": pr_number, "force": force}).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310  # nosec B310
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as resp:  # noqa: S310  # nosec B310
        parsed: object = json.loads(resp.read().decode("utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, help="workspace task_id")
    parser.add_argument("--subtask", required=True, help="subtask id within the workspace")
    parser.add_argument(
        "--pr", required=True, type=int, dest="pr_number", help="delivered PR number"
    )
    parser.add_argument(
        "--force", action="store_true", help="skip GitHub merge verification"
    )
    parser.add_argument("--base-url", default=_DEFAULT_BASE_URL, help="alan server base URL")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the request without sending it"
    )
    return parser.parse_args(argv)


def _print_dry_run(args: argparse.Namespace) -> None:
    url = _build_url(args.base_url, args.workspace, args.subtask)
    print(
        f"[dry-run] POST {url} "
        f"{{'pr_number': {args.pr_number}, 'force': {args.force}}}"
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if args.dry_run:
        _print_dry_run(args)
        return 0
    try:
        result = complete_out_of_band(
            args.base_url, args.workspace, args.subtask, args.pr_number, force=args.force
        )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"ERROR: {exc.code} {exc.reason}: {detail}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"ERROR: could not reach {args.base_url}: {exc.reason}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
