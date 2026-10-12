"""Refresh the deploy checkout onto main's tip without racing a shared ref.

Thin argparse + $GITHUB_ENV wrapper around alan.deploy_refresh. See
docs/architecture/deploy.md's "Private-ref refresh" section for the
private-ref rationale this CLI exists to enforce.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alan.deploy_refresh import (  # noqa: E402
    NotContainedError,
    PushedShaAbsentError,
    ShallowRepositoryError,
    refresh,
)


def _write_deploy_sha_env(target_sha: str) -> None:
    env_path = os.environ.get("GITHUB_ENV")
    if not env_path:
        print("warning: GITHUB_ENV is not set; skipping output write", file=sys.stderr)
        return
    with open(env_path, "a", encoding="utf-8") as fh:
        fh.write(f"DEPLOY_SHA={target_sha}\n")


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="deploy_refresh",
        description="Refresh the deploy checkout onto main's tip without racing a shared ref.",
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--pushed-sha", default=None)
    args = parser.parse_args(argv)
    pushed_sha = (
        args.pushed_sha if args.pushed_sha is not None else os.environ.get("GITHUB_SHA", "")
    )

    try:
        target_sha = refresh(args.repo, pushed_sha)
    except ShallowRepositoryError as exc:
        print(
            f"DEPLOY REFRESH: repository is shallow, cannot verify ancestry: {exc}",
            file=sys.stderr,
        )
        return 1
    except PushedShaAbsentError as exc:
        print(
            f"DEPLOY REFRESH: pushed commit not present in repository: {exc.pushed_sha}",
            file=sys.stderr,
        )
        return 1
    except NotContainedError as exc:
        print(
            f"DEPLOY REFRESH: target {exc.target_sha} does not contain "
            f"pushed commit {exc.pushed_sha}",
            file=sys.stderr,
        )
        return 1

    print(f"deploy target: {target_sha}")
    _write_deploy_sha_env(target_sha)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
