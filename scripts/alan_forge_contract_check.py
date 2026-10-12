"""Scheduled canary for the unresolved-feedback merge gate's GraphQL contract.

Re-runs alan.forge_gh._FEEDBACK_QUERY (imported, never copied) against
a real PR in this repo, and re-checks alan.teams._pr_feedback._COPILOT_GRAPHQL_LOGIN
(imported, never copied) against the actor data that query returns. Importing both
literals instead of re-declaring them is what makes this canary detect drift: a
GraphQL schema change that breaks the query shape (undefinedField) or an upstream
rename of the Copilot login shows up here automatically, with nothing new to keep
in sync by hand.

Never wired to pull_request or push — see .github/workflows/forge-contract.yml.
A network-dependent check is inherently flaky, so this only ever files an
advisory issue on a schedule; it must never be able to block a merge.

Three outcomes, not two: VERIFIED (the contract holds), DRIFT (the query shape
was rejected, or the pinned identity resolved wrong — the contract actually
rotted), and UNVERIFIABLE (missing tooling, a timeout, a network or rate-limit
fault, or any other environment failure that says nothing about the
contract). An UNVERIFIABLE outcome must never be reported as DRIFT — a canary
that cannot run is not evidence the thing it watches has changed. It still
turns the job red (see main()'s exit-code map), so a run of UNVERIFIABLE
outcomes is visible in the workflow's own run history rather than silent;
scripts/alan_forge_contract_staleness.py turns three consecutive red days
into its own distinct finding.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

import alan.teams._pr_forge as _pr_forge
from alan import process_runner
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.forge_gh import _FEEDBACK_QUERY
from alan.teams._pr_feedback import _COPILOT_GRAPHQL_LOGIN, GraphQLActorLogin
from alan.teams._pr_json import _JsonObject, _nav_json_path, safe_json

# Anchor PR the identity mismatch (GraphQL vs REST vs the old hard-coded
# constant) was verified against — see the repair plan's Anchors section. A
# fixed, permanent PR avoids flakiness from open-PR churn.
_CANARY_PR_NUMBER = 1017
_EXPECTED_COPILOT_TYPENAME = "Bot"


class Outcome(StrEnum):
    VERIFIED = "verified"
    DRIFT = "drift"
    UNVERIFIABLE = "unverifiable"


@dataclass(frozen=True)
class Verdict:
    outcome: Outcome
    reason: str


def _verified() -> Verdict:
    return Verdict(Outcome.VERIFIED, "")


def _drift(reason: str) -> Verdict:
    return Verdict(Outcome.DRIFT, reason)


def _unverifiable(reason: str) -> Verdict:
    return Verdict(Outcome.UNVERIFIABLE, reason)


_EXIT_CODES: dict[Outcome, int] = {
    Outcome.VERIFIED: 0,
    Outcome.DRIFT: 1,
    Outcome.UNVERIFIABLE: 2,
}


def fetch_feedback_raw(pr_number: int) -> tuple[str, str]:
    owner = DEFAULT_FORGE_TARGET.repo.owner
    name = DEFAULT_FORGE_TARGET.repo.repo
    try:
        result = process_runner.run_subprocess(
            [
                "gh", "api", "graphql",
                "-f", f"query={_FEEDBACK_QUERY}",
                "-F", f"owner={owner}",
                "-F", f"name={name}",
                "-F", f"n={pr_number}",
            ],
            cwd=None, timeout=_pr_forge.GH_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        return ("", f"gh api graphql timed out: {exc}")
    except FileNotFoundError as exc:
        return ("", f"gh api graphql raised {exc}")
    if result.returncode != 0:
        return (
            result.stdout or "",
            f"gh api graphql rc={result.returncode}: {(result.stderr or '')[:200]}",
        )
    return (result.stdout or "", "")


def _offending_field(error: _JsonObject) -> str:
    path = error.get("path")
    if isinstance(path, list) and path:
        return ".".join(str(p) for p in cast("list[Any]", path))
    message = error.get("message")
    return str(message) if message else "<unnamed field>"


def check_query_shape(stdout: str) -> Verdict:
    parsed = safe_json(stdout)
    if not isinstance(parsed, dict):
        return _unverifiable("feedback response body did not parse as a JSON object")
    errors = cast(_JsonObject, parsed).get("errors")
    if isinstance(errors, list) and errors:
        offending = [
            _offending_field(cast(_JsonObject, e)) for e in errors if isinstance(e, dict)
        ]
        return _drift(f"GraphQL query rejected — offending field(s): {', '.join(offending)}")
    if _nav_json_path(stdout, "data", "repository", "pullRequest") is None:
        return _unverifiable("graphql response missing data.repository.pullRequest")
    return _verified()


def _iter_author_nodes(payload: object) -> list[_JsonObject]:
    found: list[_JsonObject] = []
    if isinstance(payload, dict):
        obj = cast(_JsonObject, payload)
        author = obj.get("author")
        if isinstance(author, dict):
            found.append(cast(_JsonObject, author))
        for value in obj.values():
            found.extend(_iter_author_nodes(value))
    elif isinstance(payload, list):
        for item in cast("list[Any]", payload):
            found.extend(_iter_author_nodes(item))
    return found


def check_pinned_identity(stdout: str, pinned: GraphQLActorLogin) -> str:
    typenames = {
        str(author.get("__typename"))
        for author in _iter_author_nodes(safe_json(stdout))
        if author.get("login") == pinned.login
    }
    if _EXPECTED_COPILOT_TYPENAME in typenames:
        return ""
    if typenames:
        return (
            f"pinned Copilot login {pinned.login!r} resolved to actor type(s) "
            f"{sorted(typenames)} instead of {_EXPECTED_COPILOT_TYPENAME!r}"
        )
    return (
        f"pinned Copilot login {pinned.login!r} did not appear as any PR feedback "
        "author — possible upstream rename"
    )


def run_check(pr_number: int = _CANARY_PR_NUMBER) -> Verdict:
    stdout, fetch_reason = fetch_feedback_raw(pr_number)
    if fetch_reason:
        shape = check_query_shape(stdout)
        if shape.outcome is Outcome.DRIFT:
            return shape
        return _unverifiable(fetch_reason)
    shape = check_query_shape(stdout)
    if shape.outcome is not Outcome.VERIFIED:
        return shape
    identity_reason = check_pinned_identity(stdout, _COPILOT_GRAPHQL_LOGIN)
    if identity_reason:
        return _drift(identity_reason)
    return _verified()


def _write_github_output(outcome: Outcome) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as fh:
        fh.write(f"status={outcome.value}\n")


def main() -> int:
    try:
        verdict = run_check(_CANARY_PR_NUMBER)
    except Exception as exc:  # noqa: BLE001 - any unclassified fault must report unverifiable, never leave status unset
        verdict = _unverifiable(f"unexpected error: {exc}")
    _write_github_output(verdict.outcome)
    if verdict.outcome is Outcome.VERIFIED:
        print(
            f"forge-contract check OK (PR #{_CANARY_PR_NUMBER}, "
            f"pinned login={_COPILOT_GRAPHQL_LOGIN.login!r})"
        )
    else:
        print(
            f"forge-contract check {verdict.outcome.value.upper()} "
            f"(PR #{_CANARY_PR_NUMBER}): {verdict.reason}",
            file=sys.stderr,
        )
    return _EXIT_CODES[verdict.outcome]


if __name__ == "__main__":
    sys.exit(main())
