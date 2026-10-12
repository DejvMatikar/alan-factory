from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml

from alan import pip_audit_gate, process_runner

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS = _REPO_ROOT / ".github" / "workflows"
_STANDING_IDS = ("PYSEC-2025-183", "MAL-2026-4750")
_GATE_COMMAND = "uv run python -m alan.pip_audit_gate --check"


class _Spawns:
    def __init__(
        self,
        *,
        export_rc: int = 0,
        audit_rc: int = 0,
        audit_stdout: str = "",
        audit_stderr: str = "",
    ) -> None:
        self.export_rc = export_rc
        self.audit_rc = audit_rc
        self.audit_stdout = audit_stdout
        self.audit_stderr = audit_stderr
        self.calls: list[dict[str, Any]] = []
        self.requirements_seen: list[str] = []

    def __call__(
        self, cmd: Sequence[str], *, cwd: str | None, timeout: float, env: object = None
    ) -> subprocess.CompletedProcess[str]:
        argv = list(cmd)
        self.calls.append({"cmd": argv, "cwd": cwd, "timeout": timeout})
        if tuple(argv) == pip_audit_gate.EXPORT_ARGV:
            return subprocess.CompletedProcess(
                argv, self.export_rc, "requests==2.0.0\n", "export-err"
            )
        self.requirements_seen.append(Path(argv[argv.index("-r") + 1]).read_text(encoding="utf-8"))
        return subprocess.CompletedProcess(
            argv, self.audit_rc, self.audit_stdout, self.audit_stderr
        )


def _without_requirements_path(argv: list[str]) -> list[str]:
    if "-r" not in argv:
        return argv
    at = argv.index("-r") + 1
    return [*argv[:at], "<requirements>", *argv[at + 1 :]]


def _project(tmp_path: Path, pyproject: str) -> Path:
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    return tmp_path


def _install(monkeypatch: pytest.MonkeyPatch, spawns: _Spawns) -> _Spawns:
    monkeypatch.setattr(process_runner, "run_subprocess", spawns)
    return spawns


def test_real_repo_pyproject_yields_both_standing_ids() -> None:
    text = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert pip_audit_gate.read_suppressed_ids(text) == _STANDING_IDS


@pytest.mark.parametrize(
    "text",
    [
        "",
        "[project]\nname = 'x'\n",
        "[tool.ruff]\nline-length = 100\n",
        "[tool.pip-audit]\n",
    ],
)
def test_absent_table_or_key_returns_no_suppressions(text: str) -> None:
    assert pip_audit_gate.read_suppressed_ids(text) == ()


@pytest.mark.parametrize(
    "text",
    [
        "tool = 3\n",
        "[tool]\npip-audit = 3\n",
        "[tool.pip-audit]\nignore-vuln = 'PYSEC-1'\n",
        "[tool.pip-audit]\nignore-vuln = ['PYSEC-1', 7]\n",
        "[tool.pip-audit]\nignore-vuln = ['']\n",
        "[tool.pip-audit\nignore-vuln = []\n",
    ],
)
def test_malformed_table_raises(text: str) -> None:
    with pytest.raises(pip_audit_gate.GateConfigError):
        pip_audit_gate.read_suppressed_ids(text)


def test_export_argv_is_exact() -> None:
    assert pip_audit_gate.EXPORT_ARGV == (
        "uv",
        "export",
        "--frozen",
        "--all-extras",
        "--no-emit-project",
        "--format",
        "requirements-txt",
    )


def test_export_requirements_runs_in_the_project_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spawns = _install(monkeypatch, _Spawns())

    result = pip_audit_gate.export_requirements(tmp_path, timeout_s=9.0)

    assert result.stdout == "requests==2.0.0\n"
    assert spawns.calls == [
        {"cmd": list(pip_audit_gate.EXPORT_ARGV), "cwd": str(tmp_path), "timeout": 9.0}
    ]


def test_audit_argv_orders_suppressions_and_json_flag() -> None:
    argv = pip_audit_gate.audit_argv("req.txt", ("A-1", "B-2"), json_output=True)

    assert argv == [
        sys.executable,
        "-m",
        "pip_audit",
        "--strict",
        "--disable-pip",
        "-r",
        "req.txt",
        "--ignore-vuln",
        "A-1",
        "--ignore-vuln",
        "B-2",
        "--format",
        "json",
    ]


def test_audit_argv_omits_format_and_suppressions_when_unused() -> None:
    argv = pip_audit_gate.audit_argv(Path("req.txt"), (), json_output=False)

    assert "--format" not in argv
    assert "--ignore-vuln" not in argv


def test_run_audit_exports_then_audits_the_exported_requirements(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = _project(tmp_path, "[tool.pip-audit]\nignore-vuln = ['X-1']\n")
    spawns = _install(monkeypatch, _Spawns(audit_rc=1, audit_stdout="out", audit_stderr="err"))

    run = pip_audit_gate.run_audit(project, json_output=True, timeout_s=7.0)

    assert run == pip_audit_gate.AuditRun("audit", 1, "out", "err", ("X-1",))
    assert spawns.requirements_seen == ["requests==2.0.0\n"]
    export_call, audit_call = spawns.calls
    assert export_call["cwd"] == str(project)
    assert audit_call["cwd"] == str(project)
    assert audit_call["timeout"] == 7.0
    assert audit_call["cmd"][:3] == [sys.executable, "-m", "pip_audit"]
    assert "--ignore-vuln" in audit_call["cmd"]


def test_run_audit_stops_when_export_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = _project(tmp_path, "")
    spawns = _install(monkeypatch, _Spawns(export_rc=3))

    run = pip_audit_gate.run_audit(project, json_output=False, timeout_s=5.0)

    assert run == pip_audit_gate.AuditRun("export", 3, "requests==2.0.0\n", "export-err", ())
    assert len(spawns.calls) == 1


def test_main_check_builds_the_same_argv_as_run_audit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = _project(tmp_path, "[tool.pip-audit]\nignore-vuln = ['X-1', 'Y-2']\n")
    monkeypatch.chdir(project)
    via_main = _install(monkeypatch, _Spawns())
    assert pip_audit_gate.main(["--check"]) == 0
    via_run = _install(monkeypatch, _Spawns())
    pip_audit_gate.run_audit(project, json_output=False, timeout_s=1.0)

    assert [_without_requirements_path(c["cmd"]) for c in via_main.calls] == [
        _without_requirements_path(c["cmd"]) for c in via_run.calls
    ]


def test_main_check_returns_the_audit_exit_code_and_forwards_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(_project(tmp_path, ""))
    _install(monkeypatch, _Spawns(audit_rc=1, audit_stdout="found it\n", audit_stderr="warn\n"))

    code = pip_audit_gate.main(["--check"])

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == "found it\n"
    assert captured.err == "warn\n"


def test_main_check_fails_closed_on_malformed_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(_project(tmp_path, "[tool.pip-audit]\nignore-vuln = 1\n"))
    spawns = _install(monkeypatch, _Spawns())

    assert pip_audit_gate.main(["--check"]) == 2
    assert "ignore-vuln" in capsys.readouterr().err
    assert spawns.calls == []


def test_main_check_fails_closed_when_pyproject_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    _install(monkeypatch, _Spawns())

    assert pip_audit_gate.main(["--check"]) == 2


def test_main_check_fails_closed_on_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(_project(tmp_path, ""))

    def _timeout(
        cmd: Sequence[str], *, cwd: str | None, timeout: float, env: object = None
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(list(cmd), timeout)

    monkeypatch.setattr(process_runner, "run_subprocess", _timeout)

    assert pip_audit_gate.main(["--check"]) == 2


def test_main_requires_the_check_flag() -> None:
    with pytest.raises(SystemExit) as raised:
        pip_audit_gate.main([])

    assert raised.value.code == 2


def _security_audit_steps() -> list[dict[str, object]]:
    workflow = yaml.safe_load((_WORKFLOWS / "tests.yml").read_text(encoding="utf-8"))
    return list(workflow["jobs"]["security-audit"]["steps"])


def test_ci_security_step_runs_exactly_the_shared_gate() -> None:
    audit_steps = [
        step for step in _security_audit_steps() if "pip-audit" in str(step.get("name", ""))
    ]

    assert len(audit_steps) == 1
    assert str(audit_steps[0]["run"]).strip() == _GATE_COMMAND


def test_no_workflow_carries_its_own_ignore_vuln_flag() -> None:
    offenders = [
        path.name
        for path in sorted(_WORKFLOWS.glob("*.yml"))
        if "--ignore-vuln" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []
