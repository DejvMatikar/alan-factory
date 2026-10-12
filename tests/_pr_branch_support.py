from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from alan import process_runner
from alan.engine_config import DEFAULT_FORGE_TARGET, ForgeTarget, RepoIdentity
from alan.teams import _pr_audit_log, _pr_push, _pr_ready

_FAKE_TOKEN = "test-transport-token"  # noqa: S105  # nosec B105


def set_verifier_transport_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_VERIFIER_GH_TOKEN", _FAKE_TOKEN)


_VALID_SHA = "a" * 40


def _call_push_fix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    responses: list[tuple[int, str]],
    base_sha: str = _VALID_SHA,
    stdout_by_index: dict[int, str] | None = None,
) -> _pr_push.PushResult:
    call_n: list[int] = [0]
    stdout_map = stdout_by_index or {}

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        idx = call_n[0]
        call_n[0] += 1
        rc, stderr = responses[idx] if idx < len(responses) else (0, "")
        return subprocess.CompletedProcess(cmd, rc, stdout=stdout_map.get(idx, ""), stderr=stderr)

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    return _pr_push.push_fix_branch_with_reconcile(
        tmp_path, "fix/foo", "task-x", base_sha, forge_target=DEFAULT_FORGE_TARGET
    )


def _fake_gh(
    stdout: str, returncode: int = 0
) -> "Callable[..., subprocess.CompletedProcess[str]]":
    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")
    return fake


def _audit_entries(tmp_path: Path) -> list[dict[str, object]]:
    log = tmp_path / "logs" / _pr_audit_log.AUDIT_FILE
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


_WIDGET_FORGE = ForgeTarget(
    repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
    credential_env_name="ACME_WIDGET_GH_TOKEN",  # noqa: S106  # nosec B106
)


_OWNER_REPO_STDOUT = json.dumps({"owner": {"login": "DejvMatikar"}, "name": "alan"})


def _threads_payload(*nodes: dict[str, object]) -> str:
    return json.dumps(
        {"data": {"repository": {"pullRequest": {
            "reviewThreads": {"nodes": list(nodes)}
        }}}}
    )


def _thread_node(
    *,
    thread_id: str = "T",
    resolved: bool = False,
    outdated: bool = False,
    path: str = "alan/x.py",
    line: int = 1,
    body: str = "issue",
    author: str | None = "copilot-pull-request-reviewer[bot]",
) -> dict[str, object]:
    comment: dict[str, object] = {"body": body}
    if author is not None:
        comment["author"] = {"login": author}
    return {
        "id": thread_id,
        "isResolved": resolved,
        "isOutdated": outdated,
        "path": path,
        "line": line,
        "comments": {"nodes": [comment]},
    }


_SONAR_CHECK_RUNS_BOTH = json.dumps({
    "check_runs": [
        {
            "id": 999,
            "name": "SonarCloud (blocking quality gate)",
            "conclusion": "failure",
            "output": {"annotations_count": 2},
        },
        {
            "id": 1001,
            "name": "SonarCloud Code Analysis",
            "conclusion": "failure",
            "output": {"annotations_count": 2},
        },
    ]
})


_SONAR_ANNOTATIONS_TWO = json.dumps([
    {
        "path": "alan/web/src/useQueuePauseStatus.ts",
        "start_line": 27,
        "title": 'Remove this use of the "void" operator.',
        "message": (
            'Confusing. See https://sonarcloud.io/organizations/x/rules?open=S3735'
        ),
        "annotation_level": "failure",
    },
    {
        "path": "alan/web/src/useQueuePauseStatus.ts",
        "start_line": 33,
        "title": 'Remove this use of the "void" operator.',
        "message": "Confusing. No URL here.",
        "annotation_level": "failure",
    },
])


def _sonar_fake(
    *,
    check_runs_stdout: str = _SONAR_CHECK_RUNS_BOTH,
    annotations_stdout: str = _SONAR_ANNOTATIONS_TWO,
) -> "Callable[..., subprocess.CompletedProcess[str]]":
    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if cmd[1] == "pr":
            return subprocess.CompletedProcess(cmd, 0, stdout="abc123sha\n", stderr="")
        if "check-runs" in cmd[2] and "annotations" not in cmd[2]:
            return subprocess.CompletedProcess(cmd, 0, stdout=check_runs_stdout, stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=annotations_stdout, stderr="")
    return fake


def _fake_gh_seq(
    responses: list[subprocess.CompletedProcess[str]],
) -> tuple[list[list[str]], object]:
    calls: list[list[str]] = []
    idx: dict[str, int] = {"n": 0}

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        result = responses[idx["n"]]
        idx["n"] += 1
        return result

    return calls, fake


def _ok(cmd: list[str], stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")


def _fail(cmd: list[str], stderr: str = "err") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=stderr)


_E2E_GATE = "Frontend E2E (Playwright, blocking)"


def _flake_pr_readiness(
    *,
    failing_checks: tuple[str, ...] = (),
    pending_checks: tuple[str, ...] = (),
) -> _pr_ready.PrReadiness:
    return _pr_ready.PrReadiness(
        ok=not failing_checks and not pending_checks,
        merge_state_status="CLEAN",
        review_decision="APPROVED",
        failing_checks=failing_checks,
        pending_checks=pending_checks,
    )


_FLAKY_GATE = _E2E_GATE


def _gate_readiness(
    *,
    failing_checks: tuple[str, ...] = (),
    quarantined_checks: tuple[str, ...] = (),
) -> _pr_ready.PrReadiness:
    return _pr_ready.PrReadiness(
        ok=False,
        merge_state_status="BLOCKED",
        review_decision="",
        failing_checks=failing_checks,
        quarantined_checks=quarantined_checks,
    )


_GATE_CHECK_RUNS = json.dumps({
    "check_runs": [
        {
            "id": 1,
            "name": "SonarCloud Code Analysis",
            "conclusion": "failure",
            "details_url": "https://sonarcloud.io/dashboard?pullRequest=855",
            "output": {
                "title": "Quality Gate failed",
                "summary": "6.0% Duplication on New Code (required <= 5%)",
            },
        },
        {
            "id": 2,
            "name": "Frontend E2E (Playwright, blocking)",
            "conclusion": "failure",
            "html_url": "https://github.com/o/r/actions/runs/1/job/2",
        },
        {
            "id": 3,
            "name": "Backend tests + coverage",
            "conclusion": "success",
        },
        "not-a-dict",
    ]
})


def _gate_fake(
    *, check_runs_stdout: str = _GATE_CHECK_RUNS, sha_rc: int = 0
) -> "Callable[..., subprocess.CompletedProcess[str]]":
    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if cmd[1] == "pr":
            return subprocess.CompletedProcess(cmd, sha_rc, stdout="deadbeef\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=check_runs_stdout, stderr="")
    return fake


def _flaky_e2e_readiness(**kw: object) -> _pr_ready.PrReadiness:
    return _gate_readiness(failing_checks=(_FLAKY_GATE,), **kw)  # type: ignore[arg-type]
