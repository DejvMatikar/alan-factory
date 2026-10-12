from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from alan import process_runner

EXPORT_ARGV: tuple[str, ...] = (
    "uv",
    "export",
    "--frozen",
    "--all-extras",
    "--no-emit-project",
    "--format",
    "requirements-txt",
)

PYPROJECT_NAME = "pyproject.toml"
CI_TIMEOUT_S = 300.0
_USAGE_EXIT = 2


class GateConfigError(ValueError):
    pass


@dataclass(frozen=True)
class AuditRun:
    stage: Literal["export", "audit"]
    returncode: int
    stdout: str
    stderr: str
    suppressed: tuple[str, ...]


def _pip_audit_table(data: dict[str, Any]) -> dict[str, Any] | None:
    tool = data.get("tool")
    if tool is None:
        return None
    if not isinstance(tool, dict):
        raise GateConfigError("[tool] in pyproject.toml is not a table")
    table = cast(dict[str, Any], tool).get("pip-audit")
    if table is None:
        return None
    if not isinstance(table, dict):
        raise GateConfigError("[tool.pip-audit] in pyproject.toml is not a table")
    return cast(dict[str, Any], table)


def _ignore_vuln_ids(table: dict[str, Any]) -> tuple[str, ...]:
    raw = table.get("ignore-vuln", [])
    if not isinstance(raw, list):
        raise GateConfigError("[tool.pip-audit] ignore-vuln must be a list of strings")
    ids = cast(list[object], raw)
    if not all(isinstance(item, str) and item for item in ids):
        raise GateConfigError("[tool.pip-audit] ignore-vuln must be a list of strings")
    return tuple(cast(list[str], ids))


def read_suppressed_ids(pyproject_text: str) -> tuple[str, ...]:
    try:
        data = tomllib.loads(pyproject_text)
    except tomllib.TOMLDecodeError as exc:
        raise GateConfigError(f"pyproject.toml is not valid TOML: {exc}") from exc
    table = _pip_audit_table(data)
    return () if table is None else _ignore_vuln_ids(table)


def export_requirements(
    project_dir: Path, *, timeout_s: float
) -> subprocess.CompletedProcess[str]:
    return process_runner.run_subprocess(EXPORT_ARGV, cwd=str(project_dir), timeout=timeout_s)


def audit_argv(
    requirements: Path | str, suppressed: Sequence[str], *, json_output: bool
) -> list[str]:
    argv = [
        sys.executable,
        "-m",
        "pip_audit",
        "--strict",
        "--disable-pip",
        "-r",
        str(requirements),
    ]
    for vuln_id in suppressed:
        argv.extend(["--ignore-vuln", vuln_id])
    if json_output:
        argv.extend(["--format", "json"])
    return argv


def run_audit(project_dir: Path, *, json_output: bool, timeout_s: float) -> AuditRun:
    pyproject_text = (project_dir / PYPROJECT_NAME).read_text(encoding="utf-8")
    suppressed = read_suppressed_ids(pyproject_text)
    exported = export_requirements(project_dir, timeout_s=timeout_s)
    if exported.returncode != 0:
        return AuditRun(
            "export", exported.returncode, exported.stdout, exported.stderr, suppressed
        )
    with tempfile.TemporaryDirectory(prefix="alan-pip-audit-") as scratch:
        requirements = Path(scratch) / "requirements.txt"
        requirements.write_text(exported.stdout, encoding="utf-8")
        audited = process_runner.run_subprocess(
            audit_argv(requirements, suppressed, json_output=json_output),
            cwd=str(project_dir),
            timeout=timeout_s,
        )
    return AuditRun("audit", audited.returncode, audited.stdout, audited.stderr, suppressed)


def _run_check() -> int:
    try:
        run = run_audit(Path.cwd(), json_output=False, timeout_s=CI_TIMEOUT_S)
    except (GateConfigError, OSError, subprocess.TimeoutExpired) as exc:
        sys.stderr.write(f"pip_audit_gate: {exc}\n")
        return _USAGE_EXIT
    sys.stdout.write(run.stdout)
    sys.stderr.write(run.stderr)
    return run.returncode


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="alan.pip_audit_gate")
    parser.add_argument("--check", action="store_true", required=True)
    parser.parse_args(argv)
    return _run_check()


if __name__ == "__main__":
    sys.exit(main())
