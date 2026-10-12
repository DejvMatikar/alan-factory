"""Decide whether a deploy needs to restart the ai.alan service.

Thin argparse + GITHUB_OUTPUT wrapper around alan.deploy_restart, which is
the single classification implementation shared with the runtime restart-gate
probe (alan/teams/_deploy_restart_probe.py). See docs/architecture/deploy.md
for the fail-closed rationale and the safe-set classification table.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alan.deploy_restart import (  # noqa: E402
    SAFE_DIR_PREFIXES,  # noqa: F401
    SAFE_SUFFIXES,  # noqa: F401
    changed_paths,  # noqa: F401
    decide,
    is_safe_path,
    marker_reachable,  # noqa: F401
)


def _write_github_output(restart_required: bool) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        print("warning: GITHUB_OUTPUT is not set; skipping output write", file=sys.stderr)
        return
    with open(output_path, "a", encoding="utf-8") as fh:
        fh.write(f"restart_required={'true' if restart_required else 'false'}\n")


def _print_decision(restart_required: bool, reason: str, paths: list[str]) -> None:
    verdict = "true" if restart_required else "false"
    print(f"restart decision: {reason} (restart_required={verdict})")
    for path in paths:
        print(f"  {'safe' if is_safe_path(path) else 'hot'}: {path}")


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="deploy_restart_decision",
        description="Decide whether the ai.alan service needs a restart for this deploy.",
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument(
        "--marker", type=Path, default=Path.home() / ".alan" / "deploy" / "restarted_sha"
    )
    parser.add_argument("--service-loaded", default="false")
    args = parser.parse_args(argv)

    try:
        service_loaded = args.service_loaded.strip().lower() == "true"
        restart_required, reason, paths = decide(args.repo, args.marker, service_loaded)
        _print_decision(restart_required, reason, paths)
    except Exception:  # noqa: BLE001 - defense in depth around decide()'s own try/except
        restart_required = True
        _print_decision(True, "classifier_error", [])

    _write_github_output(restart_required)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
