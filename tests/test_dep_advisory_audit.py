from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from alan.dep_sweep import Vulnerability
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.forge import FileRead
from alan.teams._dep_advisory_audit import (
    AuditResult,
    audit_locked_dependencies,
    parse_audit_json,
)
from tests.dep_advisory_support import (
    LOCK,
    PYPROJECT,
    SpawnStub,
    audit_row,
    audit_stdout,
    contents_payload,
    install_spawns,
    serve_repo,
    vuln_entry,
)
from tests.fakes.forge import FakeForge
from tests.forge_stubs import always, file_read

_ONE_VULN = audit_stdout(audit_row("urllib3", "2.7.0", vuln_entry("GHSA-aaaa", "2.7.5", "2.8.0")))


def _expected_vuln() -> Vulnerability:
    return Vulnerability("GHSA-aaaa", "urllib3", "2.7.0", ("2.7.5", "2.8.0"))


def test_parse_accepts_the_dependencies_envelope() -> None:
    assert parse_audit_json(_ONE_VULN) == (_expected_vuln(),)


def test_parse_accepts_the_legacy_bare_list() -> None:
    legacy = json.dumps([audit_row("urllib3", "2.7.0", vuln_entry("GHSA-aaaa", "2.7.5", "2.8.0"))])

    assert parse_audit_json(legacy) == (_expected_vuln(),)


def test_parse_of_a_clean_audit_is_an_empty_tuple() -> None:
    assert parse_audit_json(audit_stdout(audit_row("urllib3", "2.7.0"))) == ()
    assert parse_audit_json(audit_stdout()) == ()


def test_parse_skips_entries_that_carry_a_skip_reason() -> None:
    skipped = {"name": "private-pkg", "skip_reason": "not found on PyPI", "vulns": [{"id": "X"}]}

    assert parse_audit_json(json.dumps({"dependencies": [skipped]})) == ()


def test_parse_skips_a_skipped_entry_that_carries_only_a_name() -> None:
    skipped = {"name": "private-pkg", "skip_reason": "not found on PyPI"}

    assert parse_audit_json(json.dumps({"dependencies": [skipped]})) == ()


def test_parse_keeps_only_string_fix_versions() -> None:
    row = audit_row("pkg", "1.0", {"id": "A-1", "fix_versions": ["1.1", 2, None]})

    (vuln,) = parse_audit_json(audit_stdout(row)) or ()

    assert vuln.fix_versions == ("1.1",)


def test_parse_treats_a_missing_fix_versions_key_as_no_fix() -> None:
    row = audit_row("pkg", "1.0", {"id": "A-1"})

    (vuln,) = parse_audit_json(audit_stdout(row)) or ()

    assert vuln.fix_versions == ()


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        "not json",
        "3",
        '"text"',
        json.dumps({"dependencies": "nope"}),
        json.dumps({"other": []}),
        json.dumps({"dependencies": ["not-a-dict"]}),
        json.dumps({"dependencies": [{}]}),
        json.dumps({"dependencies": [{"vulns": []}]}),
        json.dumps({"dependencies": [{"name": "p"}]}),
        json.dumps({"dependencies": [{"version": "1", "vulns": []}]}),
        json.dumps({"dependencies": [{"name": "p", "version": "1"}]}),
        json.dumps({"dependencies": [{"name": "p", "version": "1", "vulns": None}]}),
        json.dumps({"dependencies": [{"name": 7, "version": "1", "vulns": []}]}),
        json.dumps({"dependencies": [{"name": "p", "version": 1, "vulns": []}]}),
        json.dumps({"dependencies": [{"name": "p", "version": "1", "vulns": "x"}]}),
        json.dumps({"dependencies": [{"name": 1, "version": "1", "vulns": [{"id": "A"}]}]}),
        json.dumps({"dependencies": [{"name": "p", "version": 1, "vulns": [{"id": "A"}]}]}),
        json.dumps({"dependencies": [{"name": "p", "version": "1", "vulns": ["bad"]}]}),
        json.dumps({"dependencies": [{"name": "p", "version": "1", "vulns": [{"id": ""}]}]}),
        json.dumps({"dependencies": [{"name": "p", "version": "1", "vulns": [{"id": 5}]}]}),
        json.dumps(
            {
                "dependencies": [
                    {"name": "p", "version": "1", "vulns": [{"id": "A", "fix_versions": "x"}]}
                ]
            }
        ),
    ],
)
def test_parse_returns_none_for_garbage(stdout: str) -> None:
    assert parse_audit_json(stdout) is None


def test_a_clean_audit_of_both_fetched_files_is_ok(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    serve_repo(_active_fake_forge)
    install_spawns(monkeypatch, SpawnStub(stdout=audit_stdout()))

    result = audit_locked_dependencies(DEFAULT_FORGE_TARGET)

    assert result == AuditResult("ok", (), ("PYSEC-2025-183",), PYPROJECT, "")


def test_findings_come_back_with_the_suppression_list_and_pyproject_text(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    serve_repo(_active_fake_forge)
    install_spawns(monkeypatch, SpawnStub(stdout=_ONE_VULN, audit_rc=1))

    result = audit_locked_dependencies(DEFAULT_FORGE_TARGET)

    assert result.outcome == "ok"
    assert result.vulns == (_expected_vuln(),)
    assert result.suppressed == ("PYSEC-2025-183",)
    assert result.pyproject_text == PYPROJECT


def test_both_files_are_read_from_the_default_branch(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    reads = serve_repo(_active_fake_forge, branch="trunk")
    install_spawns(monkeypatch, SpawnStub(stdout=audit_stdout()))

    audit_locked_dependencies(DEFAULT_FORGE_TARGET)

    assert reads == [("pyproject.toml", "trunk"), ("uv.lock", "trunk")]


def test_the_audit_runs_in_a_scratch_directory_that_held_both_files_and_is_removed(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    serve_repo(_active_fake_forge)
    stub = SpawnStub(stdout=audit_stdout(), peek=("pyproject.toml", "uv.lock"))
    install_spawns(monkeypatch, stub)

    audit_locked_dependencies(DEFAULT_FORGE_TARGET)

    assert stub.peeked[0] == {"pyproject.toml": PYPROJECT, "uv.lock": LOCK}
    scratch = stub.calls[0][1]
    assert scratch is not None
    assert not Path(scratch).exists()
    assert scratch != str(Path.cwd())


def test_the_audit_goes_through_the_service_interpreter_with_the_json_flag(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    serve_repo(_active_fake_forge)
    stub = install_spawns(monkeypatch, SpawnStub(stdout=audit_stdout()))

    audit_locked_dependencies(DEFAULT_FORGE_TARGET)

    (argv,) = stub.audit_calls()
    assert argv[:3] == [sys.executable, "-m", "pip_audit"]
    assert argv[-2:] == ["--format", "json"]
    assert argv[argv.index("--ignore-vuln") + 1] == "PYSEC-2025-183"


def test_an_unreadable_default_branch_fails_the_audit(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    serve_repo(_active_fake_forge, branch=None)
    stub = install_spawns(monkeypatch, SpawnStub())

    result = audit_locked_dependencies(DEFAULT_FORGE_TARGET)

    assert result.outcome == "failed"
    assert "default branch" in result.detail
    assert stub.calls == []


@pytest.mark.parametrize("error", [OSError("gh missing"), subprocess.TimeoutExpired("gh", 1)])
def test_a_raising_default_branch_read_fails_the_audit(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge, error: BaseException
) -> None:
    always(_active_fake_forge, "default_branch", error)
    install_spawns(monkeypatch, SpawnStub())

    assert audit_locked_dependencies(DEFAULT_FORGE_TARGET).outcome == "failed"


@pytest.mark.parametrize("error", [OSError("gh missing"), subprocess.TimeoutExpired("gh", 1)])
def test_a_raising_file_read_fails_the_audit(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge, error: BaseException
) -> None:
    serve_repo(_active_fake_forge)
    always(_active_fake_forge, "repo_file", error)
    stub = install_spawns(monkeypatch, SpawnStub())

    result = audit_locked_dependencies(DEFAULT_FORGE_TARGET)

    assert result.outcome == "failed"
    assert stub.calls == []


def test_a_missing_lock_file_is_reported_as_no_lock(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    serve_repo(_active_fake_forge, lock=None)
    stub = install_spawns(monkeypatch, SpawnStub())

    result = audit_locked_dependencies(DEFAULT_FORGE_TARGET)

    assert result.outcome == "no_lock"
    assert stub.calls == []


def test_a_missing_pyproject_fails_even_when_the_lock_is_also_missing(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    serve_repo(_active_fake_forge, pyproject=None, lock=None)
    install_spawns(monkeypatch, SpawnStub())

    assert audit_locked_dependencies(DEFAULT_FORGE_TARGET).outcome == "failed"


def test_a_missing_pyproject_with_a_lock_fails(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    serve_repo(_active_fake_forge, pyproject=None)
    install_spawns(monkeypatch, SpawnStub())

    assert audit_locked_dependencies(DEFAULT_FORGE_TARGET).outcome == "failed"


def test_a_failed_read_of_the_lock_fails_instead_of_reporting_no_lock(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    serve_repo(_active_fake_forge)
    paths: list[str] = []

    def _repo_file(path: str, **_kw: object) -> FileRead:
        paths.append(path)
        if path == "uv.lock":
            return file_read(None, outcome="failed", returncode=1, stderr="HTTP 502")
        return file_read(contents_payload(PYPROJECT))

    _active_fake_forge.stub("repo_file", _repo_file)
    install_spawns(monkeypatch, SpawnStub())

    assert audit_locked_dependencies(DEFAULT_FORGE_TARGET).outcome == "failed"
    assert paths == ["pyproject.toml", "uv.lock"]


def test_an_undecodable_file_body_fails_the_audit(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    serve_repo(_active_fake_forge)
    always(_active_fake_forge, "repo_file", file_read('{"content": "", "encoding": "none"}'))
    install_spawns(monkeypatch, SpawnStub())

    assert audit_locked_dependencies(DEFAULT_FORGE_TARGET).outcome == "failed"


def test_a_file_read_without_text_fails_the_audit(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    serve_repo(_active_fake_forge)
    always(_active_fake_forge, "repo_file", file_read(None))
    install_spawns(monkeypatch, SpawnStub())

    assert audit_locked_dependencies(DEFAULT_FORGE_TARGET).outcome == "failed"


def test_a_malformed_suppression_table_fails_the_audit_without_spawning_it(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    serve_repo(_active_fake_forge, pyproject="[tool.pip-audit]\nignore-vuln = 7\n")
    stub = install_spawns(monkeypatch, SpawnStub())

    result = audit_locked_dependencies(DEFAULT_FORGE_TARGET)

    assert result.outcome == "failed"
    assert "suppression list unreadable" in result.detail
    assert stub.calls == []


def test_a_failing_export_fails_the_audit_with_its_stderr(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    serve_repo(_active_fake_forge)
    install_spawns(monkeypatch, SpawnStub(export_rc=2))

    result = audit_locked_dependencies(DEFAULT_FORGE_TARGET)

    assert result.outcome == "failed"
    assert "uv export exited 2" in result.detail
    assert "export-err" in result.detail


def test_a_timeout_fails_the_audit(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    serve_repo(_active_fake_forge)
    install_spawns(monkeypatch, SpawnStub(raises=subprocess.TimeoutExpired("uv", 180)))

    result = audit_locked_dependencies(DEFAULT_FORGE_TARGET)

    assert result.outcome == "failed"
    assert "timed out" in result.detail


def test_a_missing_binary_fails_the_audit(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    serve_repo(_active_fake_forge)
    install_spawns(monkeypatch, SpawnStub(raises=FileNotFoundError("uv")))

    result = audit_locked_dependencies(DEFAULT_FORGE_TARGET)

    assert result.outcome == "failed"
    assert "could not start" in result.detail


def test_an_import_failure_of_pip_audit_is_named_in_the_detail(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    serve_repo(_active_fake_forge)
    install_spawns(
        monkeypatch,
        SpawnStub(audit_rc=1, stderr="/usr/bin/python: No module named pip_audit"),
    )

    result = audit_locked_dependencies(DEFAULT_FORGE_TARGET)

    assert result.outcome == "failed"
    assert "pip_audit is not installed" in result.detail


def test_a_nonzero_audit_with_unparsable_stdout_fails_with_the_stderr_tail(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    serve_repo(_active_fake_forge)
    install_spawns(monkeypatch, SpawnStub(audit_rc=3, stdout="oops", stderr="network down"))

    result = audit_locked_dependencies(DEFAULT_FORGE_TARGET)

    assert result.outcome == "failed"
    assert "exited 3" in result.detail
    assert "network down" in result.detail


def test_a_zero_exit_with_unparsable_stdout_still_fails(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    serve_repo(_active_fake_forge)
    install_spawns(monkeypatch, SpawnStub(audit_rc=0, stdout="not json"))

    assert audit_locked_dependencies(DEFAULT_FORGE_TARGET).outcome == "failed"
