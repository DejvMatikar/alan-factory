from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

RUNS_STATE = "ci_runs.json"
DISPATCH_LOG = "ci_dispatches.jsonl"
CLAUDE_LABEL = "ai.alan.claude-check"
RUN_CREATED = "2026-09-30T10:00:00Z"
EVIDENCE_AFTER = "2026-09-30T10:20:00Z"
EVIDENCE_BEFORE = "2026-09-30T09:00:00Z"
HEAD_SHA = "a" * 40
FIRST_RUN_ID = 1001
STILL_RUNNING = 99
DEFAULT_SLUG = "example-owner/example-repo"
PLANTED_EMAIL = "planted.person@example.invalid"
PLANTED_KEY = "sk" + "-ant-" + "planted000000"
JOB_NAMES = ("test-backend", "SonarCloud analysis")
SONAR_CHECK = "SonarCloud Code Analysis"
_PROFILE_BLOCK = re.compile(r"^code_quality:\n  organization: (\S+)\n  project_key: (\S+)$", re.M)
_QUALITY_REQUIRED = re.compile(r"^- code_quality$", re.M)


def run_url(slug: str, run_id: int) -> str:
    return f"https://github.com/{slug}/actions/runs/{run_id}"


def runs_path(stub_root: Path) -> Path:
    return stub_root / "state" / RUNS_STATE


def read_runs(stub_root: Path) -> list[dict[str, Any]]:
    path = runs_path(stub_root)
    if not path.exists():
        return []
    held: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    return held


def write_runs(stub_root: Path, runs: list[dict[str, Any]]) -> None:
    path = runs_path(stub_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(runs), encoding="utf-8")


def new_run(
    run_id: int, *, result: str, done_after: int, hidden: int = 0, failed_job: str = "test-backend"
) -> dict[str, Any]:
    return {
        "id": run_id,
        "created": RUN_CREATED,
        "result": result,
        "views": 0,
        "done_after": done_after,
        "hidden": hidden,
        "failed_job": failed_job,
    }


def seed_run(
    stub_root: Path,
    record: Path,
    run_id: int = FIRST_RUN_ID,
    *,
    result: str = "success",
    done_after: int = 0,
    failed_job: str = "test-backend",
    slug: str = DEFAULT_SLUG,
) -> None:
    fresh = new_run(run_id, result=result, done_after=done_after, failed_job=failed_job)
    write_runs(stub_root, [*read_runs(stub_root), fresh])
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(f"{run_id} {RUN_CREATED} {run_url(slug, run_id)}\n", encoding="utf-8")


def dispatches(stub_root: Path) -> list[dict[str, Any]]:
    path = stub_root / "state" / DISPATCH_LOG
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _append(ctx: Any, log: str, row: dict[str, Any]) -> None:
    ctx.state.mkdir(parents=True, exist_ok=True)
    with (ctx.state / log).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def _slug(ctx: Any) -> str:
    return str(ctx.flag("secret_engine", DEFAULT_SLUG))


def _fail(text: str) -> int:
    print(text, file=sys.stderr)
    return 1


def workflow_run(ctx: Any, args: list[str]) -> int:
    if ctx.flag("ci_dispatch_fails", False):
        return _fail("gh: HTTP 422")
    runs = read_runs(ctx.root)
    run_id = max([FIRST_RUN_ID - 1, *(run["id"] for run in runs)]) + 1
    runs.append(
        new_run(
            run_id,
            result=str(ctx.flag("ci_run_result", "success")),
            done_after=int(ctx.flag("ci_polls_before_done", 1)),
            hidden=int(ctx.flag("ci_run_visible_after", 0)),
            failed_job=str(ctx.flag("ci_failed_job", "test-backend")),
        )
    )
    write_runs(ctx.root, runs)
    _append(ctx, DISPATCH_LOG, {"workflow": args[2], "ref": args[args.index("--ref") + 1]})
    return 0


def run_list(ctx: Any, args: list[str]) -> int:
    if ctx.flag("ci_list_fails", False):
        return _fail("gh: HTTP 500")
    runs = read_runs(ctx.root)
    shown = [run for run in runs if run["hidden"] <= 0]
    for run in runs:
        run["hidden"] = max(0, run["hidden"] - 1)
    write_runs(ctx.root, runs)
    rows = [
        {
            "databaseId": run["id"],
            "createdAt": run["created"],
            "url": run_url(_slug(ctx), run["id"]),
            "headSha": HEAD_SHA,
        }
        for run in shown
    ]
    print(json.dumps(rows))
    return 0


def _job(name: str, done: bool, conclusion: str) -> dict[str, str]:
    return {
        "name": name,
        "status": "completed" if done else "in_progress",
        "conclusion": conclusion if done else "",
    }


def _jobs(run: dict[str, Any], done: bool) -> list[dict[str, str]]:
    red = run["result"] != "success"
    names = [*JOB_NAMES, *([run["failed_job"]] if run["failed_job"] not in JOB_NAMES else [])]
    jobs = [
        _job(name, done, "failure" if red and name == run["failed_job"] else "success")
        for name in names
    ]
    return [*jobs, _job("gate", done, run["result"])]


def run_view(ctx: Any, args: list[str]) -> int:
    if ctx.flag("ci_view_fails", False):
        return _fail("gh: HTTP 500")
    runs = read_runs(ctx.root)
    run = next((row for row in runs if row["id"] == int(args[2])), None)
    if run is None:
        return _fail("gh: HTTP 404")
    run["views"] += 1
    write_runs(ctx.root, runs)
    done = run["views"] > run["done_after"]
    body = {
        "status": "completed" if done else "in_progress",
        "conclusion": run["result"] if done else "",
        "url": run_url(_slug(ctx), run["id"]),
        "headSha": HEAD_SHA,
        "createdAt": run["created"],
        "jobs": _jobs(run, done),
    }
    print(json.dumps(body))
    return 0


def _check_run(name: str, slug: str, started: str) -> dict[str, Any]:
    return {
        "name": name,
        "app": {"slug": slug},
        "status": "completed",
        "conclusion": "success",
        "started_at": started,
    }


def _status(login: str, created: str) -> dict[str, Any]:
    return {
        "context": SONAR_CHECK,
        "state": "success",
        "created_at": created,
        "creator": {"login": login},
    }


def _evidence_rows(mode: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    runs = [_check_run("gate", "github-actions", EVIDENCE_AFTER)]
    statuses: list[dict[str, Any]] = []
    if mode == "app-check":
        runs.append(_check_run(SONAR_CHECK, "sonarqubecloud", EVIDENCE_AFTER))
    if mode == "renamed-app-check":
        runs.append(_check_run("SonarQube Cloud", "sonarqubecloud", EVIDENCE_AFTER))
    if mode == "actions-check":
        runs.append(_check_run(SONAR_CHECK, "github-actions", EVIDENCE_AFTER))
    if mode == "before-trigger":
        runs.append(_check_run(SONAR_CHECK, "sonarqubecloud", EVIDENCE_BEFORE))
    if mode == "app-status":
        statuses.append(_status("sonarqubecloud[bot]", EVIDENCE_AFTER))
    if mode == "actions-status":
        statuses.append(_status("github-actions[bot]", EVIDENCE_AFTER))
    if mode == "status-before-trigger":
        statuses.append(_status("sonarqubecloud[bot]", EVIDENCE_BEFORE))
    return runs, statuses


def commit_api(ctx: Any, endpoint: str) -> int:
    if ctx.flag("commit_reads_fail", False):
        return _fail("gh: HTTP 500")
    reading_runs = "/check-runs" in endpoint
    reads = ctx.bump("evidence_reads") if reading_runs else ctx.count("evidence_reads")
    visible = reads > int(ctx.flag("sonar_evidence_after_reads", 0))
    runs, statuses = _evidence_rows(ctx.flag("sonar_evidence", "app-check") if visible else "none")
    print(json.dumps({"check_runs": runs} if reading_runs else statuses))
    return 0


def service_view() -> dict[str, Any]:
    profile = Path(os.environ["HOME"]) / ".alan" / "engines" / "default_profile.yaml"
    text = profile.read_text(encoding="utf-8") if profile.exists() else ""
    found = _PROFILE_BLOCK.search(text)
    return {
        "profile": bool(text),
        "code_quality": list(found.groups()) if found else None,
        "quality_required": _QUALITY_REQUIRED.search(text) is not None,
    }


def _latch() -> tuple[str, str] | None:
    path = Path(os.environ["HOME"]) / ".alan" / "engines" / "code_quality.latch"
    if not path.exists():
        return None
    held = json.loads(path.read_text(encoding="utf-8"))
    return held["organization"], held["project_key"]


def _entry(slug: str, requirement: str, state: str, message: str) -> dict[str, str]:
    return {
        "engine": slug,
        "requirement": requirement,
        "state": state,
        "message": message,
        "fix": "./setup.sh",
    }


def _reviewer_entries(slug: str, stored: bool, distinct: str) -> list[dict[str, str]]:
    if not stored:
        return [_entry(slug, "reviewer_credential", "not_ready", "no reviewer credential")]
    if distinct == "fail":
        return [_entry(slug, "reviewer_credential", "not_ready", "same account as the author")]
    if distinct != "pass":
        message = "no preflight confirms distinctness"
        return [_entry(slug, "reviewer_credential", "unknown", message)]
    return []


def _quality_entries(slug: str, view: dict[str, Any]) -> list[dict[str, str]]:
    if not view.get("quality_required", True):
        return []
    wanted = view["code_quality"]
    if wanted is None:
        return [_entry(slug, "code_quality", "not_ready", "the profile names no project")]
    latch = _latch()
    if latch is None:
        return [_entry(slug, "code_quality", "not_ready", "SonarCloud has not reported")]
    if list(latch) != wanted:
        return [_entry(slug, "code_quality", "not_ready", "the latch is for another project")]
    return []


def readiness(ctx: Any, rows: list[dict[str, str]], distinct: str) -> list[dict[str, str]]:
    slug = _slug(ctx)
    agent = ctx.load_agent("ai.alan") or {}
    view = agent.get("view") or service_view()
    if not view["profile"]:
        return [_entry(slug, "default_engine", "not_ready", "no default profile is installed")]
    stored = any(row["name"] == f"{slug}:reviewer" for row in rows)
    entries = [*_reviewer_entries(slug, stored, distinct), *_quality_entries(slug, view)]
    if ctx.flag("readiness_warning", False):
        entries.append(_entry(slug, "reviewer_credential", "warning", "expires soon"))
    return [*entries, *ctx.flag("readiness_extra", [])]


def claude_auth_status(ctx: Any) -> int:
    mode = str(ctx.flag("claude_status", "logged-in"))
    if mode == "no-json":
        print(f"signed in as {PLANTED_EMAIL} with {PLANTED_KEY}")
        return 0
    answer = {
        "loggedIn": mode != "logged-out",
        "authMethod": ctx.flag("claude_method", "claude.ai"),
        "email": PLANTED_EMAIL,
        "token": PLANTED_KEY,
    }
    print(json.dumps(answer))
    return {"logged-out": 1, "crashes": 2}.get(mode, 0)


def bootstrap_claude(ctx: Any, manifest: dict[str, Any]) -> int:
    ctx.save_agent(
        {
            "label": CLAUDE_LABEL,
            "checkout": "",
            "runner": "claude-check",
            "pid": 4244,
            "served_sha": "",
            "up": True,
            "raw": None,
        }
    )
    if ctx.flag("claude_status", "logged-in") == "hangs":
        return 0
    done = subprocess.run(
        manifest["ProgramArguments"],
        capture_output=True,
        text=True,
        check=False,
        env=dict(os.environ),
    )
    Path(manifest["StandardOutPath"]).write_text(done.stdout, encoding="utf-8")
    return 0
