from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, cast

_Json = dict[str, Any]
_Verdict = tuple[int, list[str]]
_Row = tuple[str, str, str, str]

COLLABORATORS_ONLY = "collaborators_only"
PROTECTION_UNREADABLE_CHECK = "branch_protection_readable"
MATRIX_CHECKS: tuple[str, ...] = (
    "conversation_resolution",
    "required_approvals",
    "squash_merge_enabled",
    "delete_branch_on_merge",
    "required_checks",
)
CREDENTIAL_CHECKS: tuple[str, ...] = ("author_token_valid", "repo_clonable")
OWNED_CHECKS: frozenset[str] = frozenset(
    {*CREDENTIAL_CHECKS, PROTECTION_UNREADABLE_CHECK, *MATRIX_CHECKS}
)
PENDING_CHECKS: frozenset[str] = frozenset({"reviewer_distinct_from_author", "dispatch_readiness"})
_MESSAGE_LIMIT = 200
_FEATURES: tuple[tuple[str, str, str], ...] = (
    ("secret_scanning", "secret_scanning", "secret scanning"),
    ("push_protection", "secret_scanning_push_protection", "push protection"),
)
_NOT_PUBLIC_REASON = "the repository is not public"
_NO_FINDING_MESSAGE = "the preflight reported no finding"

GATE_JOB = "gate"
SONAR_JOB_MARK = "sonar"
SONAR_APP_SLUGS: frozenset[str] = frozenset({"sonarqubecloud"})
SONAR_STATUS_CREATORS: frozenset[str] = frozenset({"sonarqubecloud[bot]", "sonarcloud[bot]"})
_GREEN_JOB_RESULTS: frozenset[str] = frozenset({"success", "skipped", "neutral"})
_SONAR_STATUS_STATES: frozenset[str] = frozenset({"success", "failure", "error"})
DISTINCT_CHECK = "reviewer_distinct_from_author"
STEP_BY_REQUIREMENT: dict[str, str] = {
    "reviewer_credential": "verifier",
    "code_quality": "latch",
    "default_engine": "profile",
}
UNMAPPED_REQUIREMENT_STEP = "confirm"
FREE_PLAN_TEXT = (
    "SonarCloud's Free plan does not analyse private projects, which may be why the Sonar job "
    "failed on this private repository"
)
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_RUN_URL = re.compile(r"https://github\.com/[A-Za-z0-9._/-]+")
_SHA = re.compile(r"[0-9a-f]{40}")


class VerdictError(ValueError):
    pass


def _load(text: str) -> _Json:
    try:
        parsed: object = json.loads(text)
    except json.JSONDecodeError:
        raise VerdictError("the input is not JSON") from None
    if not isinstance(parsed, dict):
        raise VerdictError("the input is not a JSON object")
    return cast("_Json", parsed)


def _dig(data: object, *path: str) -> object:
    for key in path:
        if not isinstance(data, dict):
            return None
        data = cast("_Json", data).get(key)
    return data


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _items(value: object) -> list[object]:
    return cast("list[object]", value) if isinstance(value, list) else []


def _one_line(text: str) -> str:
    return " ".join(text.split())[:_MESSAGE_LIMIT]


def _first_arg(args: list[str]) -> str:
    if not args:
        raise VerdictError("this verdict needs an argument")
    return args[0]


def engine_slug(args: list[str], text: str) -> _Verdict:
    del args
    repo = _dig(_load(text), "forge", "repo")
    owner, name = _text(_dig(repo, "owner")), _text(_dig(repo, "repo"))
    if not owner or not name:
        raise VerdictError("the engine record names no forge repository")
    return 0, [f"{owner}/{name}"]


def has_secret(args: list[str], text: str) -> _Verdict:
    wanted = _first_arg(args)
    names = [_dig(item, "name") for item in _items(_dig(_load(text), "secrets"))]
    return 0, ["yes" if wanted in names else "no"]


def visibility(args: list[str], text: str) -> _Verdict:
    del args
    return 0, [_text(_dig(_load(text), "visibility")) or "unknown"]


def _feature_row(repo: _Json, setting: str, key: str, label: str) -> tuple[str, str, str]:
    if not isinstance(_dig(repo, "security_and_analysis", key), dict):
        return setting, "unavailable", f"GitHub does not offer {label} for this repository"
    enabled = _dig(repo, "security_and_analysis", key, "status") == "enabled"
    return setting, "already" if enabled else "apply", ""


def _limits_row(limits: _Json) -> tuple[str, str, str]:
    held = _dig(limits, "limit") == COLLABORATORS_ONLY
    return "interaction_limits", "already" if held else "apply", ""


def _public_rows(repo: _Json, limits: _Json) -> list[tuple[str, str, str]]:
    rows = [_feature_row(repo, *feature) for feature in _FEATURES]
    return [*rows, _limits_row(limits)]


def _not_public_rows() -> list[tuple[str, str, str]]:
    names = ("secret_scanning", "push_protection", "interaction_limits")
    return [(name, "unavailable", _NOT_PUBLIC_REASON) for name in names]


def _read_limits(args: list[str]) -> _Json:
    if not args:
        return {}
    try:
        return _load(Path(args[0]).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _pr_policy_row(repo: _Json) -> tuple[str, str, str]:
    held = _dig(repo, "pull_request_creation_policy") == COLLABORATORS_ONLY
    return "pr_policy", "already" if held else "apply", ""


def _issues_row(repo: _Json) -> tuple[str, str, str]:
    return "issues", "already" if _dig(repo, "has_issues") is True else "apply", ""


def harden_plan(args: list[str], text: str) -> _Verdict:
    repo = _load(text)
    if _dig(repo, "visibility") == "public":
        rows = _public_rows(repo, _read_limits(args))
    else:
        rows = _not_public_rows()
    rows.append(_pr_policy_row(repo))
    rows.append(_issues_row(repo))
    return 0, [f"{setting} {state} {reason}".rstrip() for setting, state, reason in rows]


def _findings(text: str) -> list[_Json]:
    items = _items(_dig(_load(text), "findings"))
    return [cast("_Json", item) for item in items if isinstance(item, dict)]


def protection_probe(args: list[str], text: str) -> _Verdict:
    del args
    statuses = {_text(f.get("check")): _text(f.get("status")) for f in _findings(text)}
    if PROTECTION_UNREADABLE_CHECK in statuses:
        return 0, ["differs"]
    held = all(statuses.get(check) == "pass" for check in MATRIX_CHECKS)
    return 0, ["match" if held else "differs"]


def _deferred_line(finding: object) -> str:
    check = _text(_dig(finding, "check")) or "unknown"
    return f"{check}: {_one_line(_text(_dig(finding, 'message'))) or 'no reason given'}"


def protect_outcome(args: list[str], text: str) -> _Verdict:
    del args
    state = _load(text)
    deferred = _items(_dig(state, "protection_deferred"))
    if deferred:
        return 0, ["deferred", *(_deferred_line(item) for item in deferred)]
    if not _items(_dig(state, "protection_applied_keys")):
        return 0, ["deferred", "the protect action reported no applied settings"]
    return 0, ["applied"]


def _missing_owned(seen: set[str]) -> list[str]:
    required = CREDENTIAL_CHECKS
    if PROTECTION_UNREADABLE_CHECK not in seen:
        required = (*CREDENTIAL_CHECKS, *MATRIX_CHECKS)
    return [check for check in required if check not in seen]


def _kind(check: str) -> str:
    if check in OWNED_CHECKS:
        return "owned"
    return "pending" if check in PENDING_CHECKS else "other"


def _finding_row(finding: _Json) -> _Row:
    check = _text(finding.get("check")) or "unknown"
    status = _text(finding.get("status")) or "unknown"
    return _kind(check), status, check, _one_line(_text(finding.get("message")))


def _missing_rows(rows: list[_Row]) -> list[_Row]:
    seen = {check for _, _, check, _ in rows}
    return [("owned", "unknown", check, _NO_FINDING_MESSAGE) for check in _missing_owned(seen)]


def _owned_row_failed(row: _Row) -> bool:
    return row[0] == "owned" and row[1] != "pass"


def confirm(args: list[str], text: str) -> _Verdict:
    del args
    rows = [_finding_row(finding) for finding in _findings(text)]
    rows.extend(_missing_rows(rows))
    failed = any(_owned_row_failed(row) for row in rows)
    return (1 if failed else 0), [" ".join(part for part in row if part) for row in rows]


def _load_list(text: str) -> list[object]:
    try:
        parsed: object = json.loads(text)
    except json.JSONDecodeError:
        raise VerdictError("the input is not JSON") from None
    if not isinstance(parsed, list):
        raise VerdictError("the input is not a JSON array")
    return cast("list[object]", parsed)


def _mappings(values: list[object]) -> list[_Json]:
    return [cast("_Json", value) for value in values if isinstance(value, dict)]


def _number(value: object) -> int:
    return value if isinstance(value, int) else 0


def _after_id(args: list[str]) -> int:
    if not args:
        return 0
    try:
        return int(args[0])
    except ValueError:
        raise VerdictError("the run number to start after is not a number") from None


def _run_url(run: _Json) -> str:
    url = _text(run.get("url"))
    if _RUN_URL.fullmatch(url) is None:
        raise VerdictError("the workflow run has no readable address")
    return url


def _run_fields(run: _Json) -> str:
    created = _text(run.get("createdAt"))
    if _TIMESTAMP.fullmatch(created) is None:
        raise VerdictError("the workflow run has no readable creation time")
    return f"{_number(run.get('databaseId'))} {created} {_run_url(run)}"


def run_newest(args: list[str], text: str) -> _Verdict:
    after = _after_id(args)
    runs = [run for run in _mappings(_load_list(text)) if _number(run.get("databaseId")) > after]
    if not runs:
        return 1, []
    return 0, [_run_fields(max(runs, key=lambda run: _number(run.get("databaseId"))))]


def _jobs(run: _Json) -> list[_Json]:
    return _mappings(_items(run.get("jobs")))


def _red_jobs(jobs: list[_Json]) -> list[str]:
    return [
        _one_line(_text(job.get("name"))) or "unnamed"
        for job in jobs
        if _text(job.get("name")) != GATE_JOB
        and _text(job.get("status")) == "completed"
        and _text(job.get("conclusion")) not in _GREEN_JOB_RESULTS
    ]


def run_progress(args: list[str], text: str) -> _Verdict:
    del args
    states = [_text(job.get("status")) for job in _jobs(_load(text))]
    done, running = states.count("completed"), states.count("in_progress")
    queued = len(states) - done - running
    return 0, [f"{done} of {len(states)} jobs done, {running} running, {queued} queued"]


def _gate_is_green(jobs: list[_Json]) -> bool:
    return any(
        _text(job.get("name")) == GATE_JOB and _text(job.get("conclusion")) == "success"
        for job in jobs
    )


def _failure_line(run: _Json, jobs: list[_Json], *, private: bool) -> str:
    red = _red_jobs(jobs)
    line = f"failed job {red[0] if red else GATE_JOB} did not succeed in the CI run {_run_url(run)}"
    if private and any(SONAR_JOB_MARK in name.lower() for name in red):
        line += f"; {FREE_PLAN_TEXT}"
    return line


def _head_sha(run: _Json) -> str:
    sha = _text(run.get("headSha"))
    if _SHA.fullmatch(sha) is None:
        raise VerdictError("the workflow run names no readable head commit")
    return sha


def run_status(args: list[str], text: str) -> _Verdict:
    run = _load(text)
    jobs = _jobs(run)
    if _text(run.get("status")) != "completed":
        return 0, ["waiting"]
    if _gate_is_green(jobs):
        return 0, [f"success {_head_sha(run)}"]
    return 0, [_failure_line(run, jobs, private=args[:1] == ["private"])]


def _moment(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(_text(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _later_than(stamp: object, since: datetime) -> bool:
    moment = _moment(stamp)
    return moment is not None and moment > since


def _sonar_check_run(run: _Json, since: datetime) -> bool:
    return (
        _text(_dig(run, "app", "slug")) in SONAR_APP_SLUGS
        and _text(run.get("status")) == "completed"
        and _later_than(run.get("started_at"), since)
    )


def _sonar_status(status: _Json, since: datetime) -> bool:
    return (
        _text(_dig(status, "creator", "login")).lower() in SONAR_STATUS_CREATORS
        and _text(status.get("state")) in _SONAR_STATUS_STATES
        and _later_than(status.get("created_at"), since)
    )


def _statuses(args: list[str]) -> list[_Json]:
    if not args:
        return []
    try:
        return _mappings(_load_list(Path(args[0]).read_text(encoding="utf-8")))
    except (OSError, VerdictError):
        return []


def sonar_evidence(args: list[str], text: str) -> _Verdict:
    since = _moment(_first_arg(args))
    if since is None:
        raise VerdictError("the trigger time is not a timestamp")
    runs = _mappings(_items(_dig(_load(text), "check_runs")))
    reported = any(_sonar_check_run(run, since) for run in runs) or any(
        _sonar_status(status, since) for status in _statuses(args[1:])
    )
    return 0, ["yes" if reported else "no"]


def reviewer_distinct(args: list[str], text: str) -> _Verdict:
    del args
    for finding in _findings(text):
        if _text(finding.get("check")) == DISTINCT_CHECK:
            return 0, [_text(finding.get("status")) or "unknown"]
    return 0, ["absent"]


def _word(value: object) -> str:
    return "_".join(_text(value).split()) or "unknown"


def _readiness_row(entry: object) -> str:
    fields = cast("_Json", entry) if isinstance(entry, dict) else {}
    state, requirement = _text(fields.get("state")), _word(fields.get("requirement"))
    warns = state == "warning"
    step = "-" if warns else STEP_BY_REQUIREMENT.get(requirement, UNMAPPED_REQUIREMENT_STEP)
    message = _one_line(_text(fields.get("message"))) or "no reason given"
    engine = _word(fields.get("engine"))
    kind = "warning" if warns else "blocked"
    return f"{kind} {step} {engine} {requirement} {_word(state)} {message}"


def readiness(args: list[str], text: str) -> _Verdict:
    del args
    entries = _dig(_load(text), "dispatch_readiness")
    if not isinstance(entries, list):
        raise VerdictError("the health answer has no dispatch_readiness list")
    rows = [_readiness_row(entry) for entry in cast("list[object]", entries)]
    return (1 if any(row.startswith("blocked ") for row in rows) else 0), rows


_COMMANDS: dict[str, Callable[[list[str], str], _Verdict]] = {
    "engine-slug": engine_slug,
    "has-secret": has_secret,
    "visibility": visibility,
    "harden-plan": harden_plan,
    "protection-probe": protection_probe,
    "protect-outcome": protect_outcome,
    "confirm": confirm,
    "run-newest": run_newest,
    "run-status": run_status,
    "run-progress": run_progress,
    "sonar-evidence": sonar_evidence,
    "reviewer-distinct": reviewer_distinct,
    "readiness": readiness,
}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    command = _COMMANDS.get(args[0]) if args else None
    if command is None:
        print(f"setup_verdicts: expected one of {', '.join(sorted(_COMMANDS))}", file=sys.stderr)
        return 2
    try:
        code, lines = command(args[1:], sys.stdin.read())
    except VerdictError as exc:
        print(f"setup_verdicts: {exc}", file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
