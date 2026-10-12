from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from alan import pip_audit_gate
from alan.dep_sweep import Vulnerability
from alan.engine_config import ForgeTarget
from alan.forge import active_forge, decode_contents_payload
from alan.teams._pr_forge import GH_TIMEOUT_S

AuditOutcome = Literal["ok", "failed", "no_lock"]

_AUDIT_TIMEOUT_S = 180.0
_PYPROJECT_PATH = "pyproject.toml"
_LOCK_PATH = "uv.lock"
_STDERR_TAIL = 300
_MISSING_MODULE_MARKER = "No module named"


@dataclass(frozen=True)
class AuditResult:
    outcome: AuditOutcome
    vulns: tuple[Vulnerability, ...] = ()
    suppressed: tuple[str, ...] = ()
    pyproject_text: str = ""
    detail: str = ""


@dataclass(frozen=True)
class _Fetched:
    kind: Literal["ok", "absent", "failed"]
    text: str = ""


def _failed(detail: str) -> AuditResult:
    return AuditResult(outcome="failed", detail=detail)


def _fetch(forge: ForgeTarget, path: str, branch: str) -> _Fetched:
    try:
        read = active_forge().repo_file(
            path, forge_target=forge, timeout=GH_TIMEOUT_S, ref=branch
        )
    except (OSError, subprocess.TimeoutExpired):
        return _Fetched("failed")
    if read.outcome == "absent":
        return _Fetched("absent")
    if read.outcome != "ok":
        return _Fetched("failed")
    text = decode_contents_payload(read.text or "")
    return _Fetched("failed") if text is None else _Fetched("ok", text)


def _default_branch(forge: ForgeTarget) -> str | None:
    try:
        return active_forge().default_branch(forge_target=forge, timeout=GH_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _parse_vuln(name: str, version: str, raw: object) -> Vulnerability | None:
    if not isinstance(raw, dict):
        return None
    entry = cast(dict[str, Any], raw)
    vuln_id = entry.get("id")
    fixes = entry.get("fix_versions", [])
    if not isinstance(vuln_id, str) or not vuln_id or not isinstance(fixes, list):
        return None
    fix_versions = tuple(v for v in cast(list[object], fixes) if isinstance(v, str))
    return Vulnerability(
        id=vuln_id, package=name, installed_version=version, fix_versions=fix_versions
    )


def _parse_vuln_rows(name: str, version: str, rows: object) -> tuple[Vulnerability, ...] | None:
    if not isinstance(rows, list):
        return None
    parsed = [_parse_vuln(name, version, row) for row in cast(list[object], rows)]
    if None in parsed:
        return None
    return tuple(cast("list[Vulnerability]", parsed))


def _parse_dependency(dep: object) -> tuple[Vulnerability, ...] | None:
    if not isinstance(dep, dict):
        return None
    entry = cast(dict[str, Any], dep)
    if "skip_reason" in entry:
        return ()
    name, version = entry.get("name"), entry.get("version")
    if not isinstance(name, str) or not isinstance(version, str):
        return None
    return _parse_vuln_rows(name, version, entry.get("vulns"))


def _dependency_rows(raw: object) -> list[object] | None:
    if isinstance(raw, dict):
        raw = cast(dict[str, Any], raw).get("dependencies")
    if not isinstance(raw, list):
        return None
    return cast(list[object], raw)


def parse_audit_json(stdout: str) -> tuple[Vulnerability, ...] | None:
    try:
        raw: object = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    rows = _dependency_rows(raw)
    if rows is None:
        return None
    found: list[Vulnerability] = []
    for dep in rows:
        parsed = _parse_dependency(dep)
        if parsed is None:
            return None
        found.extend(parsed)
    return tuple(found)


def _tail(text: str) -> str:
    return text.strip()[-_STDERR_TAIL:]


def _interpret(run: pip_audit_gate.AuditRun, pyproject_text: str) -> AuditResult:
    if run.stage == "export":
        return _failed(f"uv export exited {run.returncode}: {_tail(run.stderr)}")
    vulns = parse_audit_json(run.stdout)
    if vulns is None:
        missing = _MISSING_MODULE_MARKER in run.stderr
        reason = "pip_audit is not installed" if missing else _tail(run.stderr)
        return _failed(f"pip-audit exited {run.returncode} without parsable JSON: {reason}")
    return AuditResult(
        outcome="ok", vulns=vulns, suppressed=run.suppressed, pyproject_text=pyproject_text
    )


def _audit_project(project: Path, pyproject_text: str) -> AuditResult:
    try:
        run = pip_audit_gate.run_audit(project, json_output=True, timeout_s=_AUDIT_TIMEOUT_S)
    except pip_audit_gate.GateConfigError as exc:
        return _failed(f"suppression list unreadable: {exc}")
    except subprocess.TimeoutExpired:
        return _failed("pip-audit timed out")
    except OSError as exc:
        return _failed(f"audit could not start: {exc}")
    return _interpret(run, pyproject_text)


def _fetch_inputs(forge: ForgeTarget, branch: str) -> tuple[_Fetched, _Fetched]:
    return _fetch(forge, _PYPROJECT_PATH, branch), _fetch(forge, _LOCK_PATH, branch)


def audit_locked_dependencies(forge: ForgeTarget) -> AuditResult:
    branch = _default_branch(forge)
    if branch is None:
        return _failed("default branch unreadable")
    pyproject, lock = _fetch_inputs(forge, branch)
    if lock.kind == "absent" and pyproject.kind == "ok":
        return AuditResult(outcome="no_lock", detail="uv.lock is absent")
    if pyproject.kind != "ok" or lock.kind != "ok":
        return _failed("pyproject.toml or uv.lock could not be read")
    with tempfile.TemporaryDirectory(prefix="alan-dep-advisory-") as scratch:
        project = Path(scratch)
        (project / _PYPROJECT_PATH).write_text(pyproject.text, encoding="utf-8")
        (project / _LOCK_PATH).write_text(lock.text, encoding="utf-8")
        return _audit_project(project, pyproject.text)
