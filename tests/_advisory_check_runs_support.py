from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from alan.engine_config import ForgeTarget, RepoIdentity
from alan.forge import JsonObject, active_forge
from alan.teams import _pr_ready
from tests.fakes.forge import FakeForge
from tests.forge_stubs import api_result, pr_read, readiness_fields

SONAR = "SonarCloud Code Analysis"
BACKEND = "Backend tests + coverage"
DRIFT_GATE = "Docs drift gate"
E2E_GATE = "Frontend E2E (Playwright, blocking)"
SONAR_APP = "sonarqubecloud"
ACTIONS_APP = "github-actions"
HEAD_SHA = "sha-advisory"
TASK_ID = "advisory-task"

CheckRun = dict[str, Any]

FORGE = ForgeTarget(
    repo=RepoIdentity(owner="acme", repo="widget"),
    credential_env_name="ACME_GH_TOKEN",  # noqa: S106  # nosec B106
)

AUTHORITATIVE_SONAR_FORGE = ForgeTarget(
    repo=RepoIdentity(owner="acme", repo="widget"),
    credential_env_name="ACME_GH_TOKEN",  # noqa: S106  # nosec B106
    authoritative_checks=frozenset({SONAR}),
)


def _fake() -> FakeForge:
    return cast(FakeForge, active_forge())


def check_run(name: str, slug: str) -> CheckRun:
    return {"name": name, "status": "completed", "conclusion": "failure", "app": {"slug": slug}}


def app_less_run(name: str) -> CheckRun:
    return {"name": name, "status": "completed", "conclusion": "failure"}


def failing_run(name: str) -> dict[str, str]:
    return {"name": name, "conclusion": "FAILURE", "status": "COMPLETED"}


def pending_run(name: str) -> dict[str, str]:
    return {"name": name, "conclusion": "", "status": "IN_PROGRESS"}


def passing_run(name: str) -> dict[str, str]:
    return {"name": name, "conclusion": "SUCCESS", "status": "COMPLETED"}


def failing_context(context: str) -> dict[str, str]:
    return {"context": context, "state": "FAILURE"}


def serve_check_runs(runs: list[Any] | None) -> list[str]:
    shas: list[str] = []

    def page(sha: str, *, forge_target: ForgeTarget) -> Any:
        assert isinstance(forge_target, ForgeTarget)
        shas.append(sha)
        if runs is None:
            return api_result("", returncode=1, stderr="rate limited")
        return api_result(json.dumps({"check_runs": runs}))

    _fake().stub("commit_check_runs_page", page)
    return shas


def pr_payload(
    rollup: list[Any], *, merge_state: str = "UNSTABLE", review_decision: str = "",
    head_sha: str = HEAD_SHA,
) -> JsonObject:
    return readiness_fields(merge_state, review_decision, rollup=rollup, head_sha=head_sha)


def serve_pr_reads(
    *payloads: JsonObject, by_fields: dict[tuple[str, ...], JsonObject] | None = None,
) -> list[tuple[str, ...]]:
    queue = list(payloads)
    fixed = by_fields or {}
    reads: list[tuple[str, ...]] = []

    def read_pr(
        pr_number: int, fields: tuple[str, ...], *, forge_target: ForgeTarget, timeout: float,
    ) -> Any:
        assert pr_number > 0
        assert isinstance(forge_target, ForgeTarget)
        assert timeout > 0
        reads.append(fields)
        if fields in fixed:
            return pr_read(fixed[fields])
        if fields == ("baseRefOid",):
            return pr_read({})
        return pr_read(queue.pop(0) if len(queue) > 1 else queue[0])

    _fake().stub("read_pr", read_pr)
    return reads


def serve_unavailable_pr_reads() -> None:
    _fake().stub("read_pr", lambda *_a, **_kw: pr_read(returncode=1, stderr="HTTP 502 Bad Gateway"))


def serve_no_head_sha() -> None:
    _fake().stub("head_sha", lambda *_a, **_kw: api_result("", returncode=1, stderr="unavailable"))


def serve_no_failing_jobs() -> None:
    _fake().stub("status_check_rollup", lambda *_a, **_kw: api_result(""))


def forge_methods_called() -> set[str]:
    return {name for name, _args, _kwargs in _fake().calls}


def merge_readiness(
    rollup: list[Any], *, merge_state: str = "UNSTABLE", review_decision: str = "",
    head_sha: str = HEAD_SHA, **replacements: Any,
) -> _pr_ready.PrReadiness:
    parsed = _pr_ready._parse_readiness_payload(  # noqa: SLF001
        pr_payload(rollup, merge_state=merge_state, review_decision=review_decision,
                   head_sha=head_sha)
    )
    assert parsed is not None
    return replace(parsed, **replacements)


def verifier_readiness(
    rollup: list[Any], *, merge_state: str = "UNSTABLE", head_sha: str = HEAD_SHA,
    **replacements: Any,
) -> _pr_ready.PrReadiness:
    parsed = _pr_ready._parse_verifier_readiness_payload(  # noqa: SLF001
        pr_payload(rollup, merge_state=merge_state, head_sha=head_sha)
    )
    assert parsed is not None
    return replace(parsed, **replacements)


def impl_audit_rows(logs_root: Path, action: str) -> list[dict[str, Any]]:
    path = logs_root / "impl_audit.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [row for row in rows if row["action"] == action]
