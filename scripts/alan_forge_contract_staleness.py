"""Detects when the forge-contract canary has gone quiet.

scripts/alan_forge_contract_check.py can legitimately return UNVERIFIABLE
forever without ever filing a drift finding — that is by design (an
environment fault is not evidence of contract drift). But a canary that
never actually verifies anything is exactly the silent-failure mode the
whole canary exists to catch, so this script watches the workflow's own run
history for a run of consecutive non-green outcomes and raises a distinct
finding once the gap crosses a threshold.

Deliberately reads GitHub's REST API directly via urllib rather than
shelling out to `gh`: forge-contract.yml runs where no `gh` login is
guaranteed, and this detector runs from the very job whose `check` step may
have just failed for that exact reason — it must not itself depend on the
tool that might be missing.

No new persistent state store: staleness is derived entirely from the
workflow's own run history via the REST API, never written anywhere by this
repository. Any network, auth, or parse failure fails soft (stale=false,
exit 0) rather than inventing a finding — the job is already red from the
check step in that case, and a blind detector must not compound one failure
into a second, possibly-wrong one.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

_JsonObject = dict[str, object]

_STALE_AFTER_S = 3 * 24 * 3600 + 3600
_WORKFLOW_FILE = "forge-contract.yml"
_DEFAULT_API_URL = "https://api.github.com"


def _runs_url(api_url: str, repo: str) -> str:
    return (
        f"{api_url}/repos/{repo}/actions/workflows/{_WORKFLOW_FILE}/runs"
        "?branch=main&status=success&per_page=1"
    )


def fetch_last_success(api_url: str, repo: str, token: str) -> str | None:
    request = urllib.request.Request(  # noqa: S310 - fixed api_url from GITHUB_API_URL, not user input
        _runs_url(api_url, repo),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        raw: object = json.loads(response.read())
    if not isinstance(raw, dict):
        raise ValueError("workflow runs response was not a JSON object")
    payload = cast(_JsonObject, raw)
    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        raise ValueError("workflow runs response missing 'workflow_runs' list")
    if not runs:
        return None
    first = runs[0]
    if not isinstance(first, dict):
        raise ValueError("workflow run entry was not a JSON object")
    started = cast(_JsonObject, first).get("run_started_at")
    return str(started) if started else None


def is_stale(last_success: str | None, *, now: datetime) -> bool:
    if last_success is None:
        return True
    started = datetime.fromisoformat(last_success)
    return (now - started).total_seconds() > _STALE_AFTER_S


def _body(last_success: str | None, *, run_url: str) -> str:
    if last_success is None:
        return (
            "The scheduled forge-contract canary "
            "(.github/workflows/forge-contract.yml, "
            "scripts/alan_forge_contract_check.py) has never completed a "
            f"verification. See the run log: {run_url}\n"
        )
    return (
        "The scheduled forge-contract canary "
        "(.github/workflows/forge-contract.yml, "
        "scripts/alan_forge_contract_check.py) has not completed a "
        f"verification since {last_success}. The missed runs could not "
        "verify the contract — this is not the same as finding drift, and "
        "the two must not be confused. "
        f"See the run log: {run_url}\n"
    )


def _run_url() -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    return f"{server}/{repo}/actions/runs/{run_id}"


def _write_github_output(pairs: dict[str, str]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as fh:
        for key, value in pairs.items():
            fh.write(f"{key}={value}\n")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--body-path", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, now: datetime | None = None) -> int:
    args = _parse_args(argv)
    moment = now if now is not None else datetime.now(UTC)
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    api_url = os.environ.get("GITHUB_API_URL", _DEFAULT_API_URL)
    token = os.environ.get("GITHUB_TOKEN", "")

    try:
        last_success = fetch_last_success(api_url, repo, token)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        print(f"forge-contract staleness check could not run: {exc}", file=sys.stderr)
        _write_github_output({"stale": "false"})
        return 0

    stale = is_stale(last_success, now=moment)
    Path(args.body_path).write_text(_body(last_success, run_url=_run_url()), encoding="utf-8")
    _write_github_output(
        {
            "stale": "true" if stale else "false",
            "last_success": last_success or "never",
            "today": moment.date().isoformat(),
        }
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
