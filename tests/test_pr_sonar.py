from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from alan import process_runner
from alan.engine_config import DEFAULT_FORGE_TARGET, RepoIdentity
from alan.teams import _pr_branch, _pr_sonar
from tests import _pr_branch_support as support
from tests._pr_branch_support import (
    _OWNER_REPO_STDOUT,
    _SONAR_CHECK_RUNS_BOTH,
    _audit_entries,
    _sonar_fake,
)

_CHECK_RUNS_WITH_SUITE = json.dumps({
    "check_runs": [{
        "id": 1001,
        "name": "SonarCloud Code Analysis",
        "conclusion": "failure",
        "output": {"annotations_count": 2},
        "check_suite": {"id": 5555},
    }]
})

_HOTSPOT_SEARCH_EMPTY = json.dumps({"hotspots": [], "paging": {"total": 0}})

_HOTSPOT_SEARCH_OK = json.dumps({
    "hotspots": [
        {"key": "AXn5b_KEY1", "status": "TO_REVIEW"},
        {"key": "AXn5b_KEY2", "status": "TO_REVIEW"},
    ],
    "paging": {"total": 2},
})

@pytest.fixture(autouse=True)
def _default_transport_token(monkeypatch: pytest.MonkeyPatch) -> None:
    support.set_verifier_transport_token(monkeypatch)


def test_list_sonarcloud_annotations_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(process_runner, "run_subprocess", _sonar_fake())
    result = _pr_sonar.list_sonarcloud_annotations(188, forge_target=DEFAULT_FORGE_TARGET)
    assert len(result) == 2
    assert result[0].path == "alan/web/src/useQueuePauseStatus.ts"
    assert result[0].line == 27
    assert result[0].title == 'Remove this use of the "void" operator.'
    assert result[0].rule_url == "https://sonarcloud.io/organizations/x/rules?open=S3735"
    assert result[1].line == 33
    assert result[1].rule_url == ""


def test_list_sonarcloud_annotations_no_failing_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    only_blocking = json.dumps({
        "check_runs": [{
            "id": 999,
            "name": "SonarCloud (blocking quality gate)",
            "conclusion": "failure",
            "output": {"annotations_count": 2},
        }]
    })
    monkeypatch.setattr(
        process_runner, "run_subprocess", _sonar_fake(check_runs_stdout=only_blocking)
    )
    assert _pr_sonar.list_sonarcloud_annotations(188, forge_target=DEFAULT_FORGE_TARGET) == ()


def test_list_sonarcloud_annotations_subprocess_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if cmd[1] == "repo":
            return subprocess.CompletedProcess(cmd, 0, stdout=_OWNER_REPO_STDOUT, stderr="")
        raise FileNotFoundError("gh")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_sonar.list_sonarcloud_annotations(42, forge_target=DEFAULT_FORGE_TARGET) == ()
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "list_sonarcloud_annotations_failed"
        and "gh subprocess error" in str(e.get("reason"))
        for e in entries
    )


def test_list_sonarcloud_annotations_subprocess_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if cmd[1] == "repo":
            return subprocess.CompletedProcess(cmd, 0, stdout=_OWNER_REPO_STDOUT, stderr="")
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=1.0)

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_sonar.list_sonarcloud_annotations(42, forge_target=DEFAULT_FORGE_TARGET) == ()
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "list_sonarcloud_annotations_failed"
        and "gh subprocess error" in str(e.get("reason"))
        for e in entries
    )


def test_list_sonarcloud_annotations_head_sha_nonzero_rc(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if cmd[1] == "repo":
            return subprocess.CompletedProcess(cmd, 0, stdout=_OWNER_REPO_STDOUT, stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not found")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_sonar.list_sonarcloud_annotations(42, forge_target=DEFAULT_FORGE_TARGET) == ()
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "list_sonarcloud_annotations_failed"
        and "returncode=1" in str(e.get("reason"))
        for e in entries
    )


def test_list_sonarcloud_annotations_head_sha_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if cmd[1] == "repo":
            return subprocess.CompletedProcess(cmd, 0, stdout=_OWNER_REPO_STDOUT, stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="   \n", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_sonar.list_sonarcloud_annotations(42, forge_target=DEFAULT_FORGE_TARGET) == ()
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "list_sonarcloud_annotations_failed"
        and e.get("reason") == "head_sha_empty"
        for e in entries
    )


def test_list_sonarcloud_annotations_check_runs_subprocess_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    call_n: list[int] = [0]

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        call_n[0] += 1
        if call_n[0] == 1:
            return subprocess.CompletedProcess(cmd, 0, stdout="sha123\n", stderr="")
        raise FileNotFoundError("gh")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_sonar.list_sonarcloud_annotations(42, forge_target=DEFAULT_FORGE_TARGET) == ()
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "list_sonarcloud_annotations_failed"
        and "gh subprocess error" in str(e.get("reason"))
        for e in entries
    )


def test_list_sonarcloud_annotations_check_runs_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    call_n: list[int] = [0]

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        call_n[0] += 1
        if call_n[0] == 1:
            return subprocess.CompletedProcess(cmd, 0, stdout="sha123\n", stderr="")
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=1.0)

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_sonar.list_sonarcloud_annotations(42, forge_target=DEFAULT_FORGE_TARGET) == ()
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "list_sonarcloud_annotations_failed"
        and "gh subprocess error" in str(e.get("reason"))
        for e in entries
    )


def test_list_sonarcloud_annotations_rest_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    call_n: list[int] = [0]

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        call_n[0] += 1
        if call_n[0] == 1:
            return subprocess.CompletedProcess(cmd, 0, stdout="sha123\n", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="api error")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_sonar.list_sonarcloud_annotations(42, forge_target=DEFAULT_FORGE_TARGET) == ()
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "list_sonarcloud_annotations_failed"
        and "returncode=1" in str(e.get("reason"))
        for e in entries
    )


def test_list_sonarcloud_annotations_check_runs_json_malformed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(
        process_runner, "run_subprocess",
        _sonar_fake(check_runs_stdout="not json {{{"),
    )
    assert _pr_sonar.list_sonarcloud_annotations(42, forge_target=DEFAULT_FORGE_TARGET) == ()
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "list_sonarcloud_annotations_failed"
        and e.get("reason") == "check_runs_json_malformed"
        for e in entries
    )


def test_list_sonarcloud_annotations_annotations_fetch_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    call_n: list[int] = [0]

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        call_n[0] += 1
        if call_n[0] == 1:
            return subprocess.CompletedProcess(cmd, 0, stdout="sha123\n", stderr="")
        if call_n[0] == 2:
            return subprocess.CompletedProcess(cmd, 0, stdout=_SONAR_CHECK_RUNS_BOTH, stderr="")
        raise FileNotFoundError("gh")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_sonar.list_sonarcloud_annotations(42, forge_target=DEFAULT_FORGE_TARGET) == ()
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "list_sonarcloud_annotations_failed"
        and "gh subprocess error" in str(e.get("reason"))
        for e in entries
    )


def test_list_sonarcloud_annotations_annotations_fetch_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    call_n: list[int] = [0]

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        call_n[0] += 1
        if call_n[0] == 1:
            return subprocess.CompletedProcess(cmd, 0, stdout="sha123\n", stderr="")
        if call_n[0] == 2:
            return subprocess.CompletedProcess(cmd, 0, stdout=_SONAR_CHECK_RUNS_BOTH, stderr="")
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=1.0)

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_sonar.list_sonarcloud_annotations(42, forge_target=DEFAULT_FORGE_TARGET) == ()
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "list_sonarcloud_annotations_failed"
        and "gh subprocess error" in str(e.get("reason"))
        for e in entries
    )


def test_list_sonarcloud_annotations_annotations_fetch_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    call_n: list[int] = [0]

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        call_n[0] += 1
        if call_n[0] == 1:
            return subprocess.CompletedProcess(cmd, 0, stdout="sha123\n", stderr="")
        if call_n[0] == 2:
            return subprocess.CompletedProcess(cmd, 0, stdout=_SONAR_CHECK_RUNS_BOTH, stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="forbidden")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_sonar.list_sonarcloud_annotations(42, forge_target=DEFAULT_FORGE_TARGET) == ()
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "list_sonarcloud_annotations_failed"
        and "returncode=1" in str(e.get("reason"))
        for e in entries
    )


def test_list_sonarcloud_annotations_annotations_json_malformed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(
        process_runner, "run_subprocess",
        _sonar_fake(annotations_stdout='{"not": "a list"}'),
    )
    assert _pr_sonar.list_sonarcloud_annotations(42, forge_target=DEFAULT_FORGE_TARGET) == ()
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "list_sonarcloud_annotations_failed"
        and e.get("reason") == "annotations_json_malformed"
        for e in entries
    )


def test_list_sonarcloud_annotations_empty_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        process_runner, "run_subprocess",
        _sonar_fake(annotations_stdout="[]"),
    )
    assert _pr_sonar.list_sonarcloud_annotations(42, forge_target=DEFAULT_FORGE_TARGET) == ()


def test_list_sonarcloud_annotations_handles_malformed_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    malformed = json.dumps([
        "not-a-dict",
        {"path": "", "start_line": 1, "title": "t", "message": "m"},
        {"path": "f.ts", "start_line": "not-int", "title": "t", "message": "m"},
        {"path": "f.ts", "start_line": 5, "title": "ok", "message": "msg"},
    ])
    monkeypatch.setattr(
        process_runner, "run_subprocess",
        _sonar_fake(annotations_stdout=malformed),
    )
    result = _pr_sonar.list_sonarcloud_annotations(42, forge_target=DEFAULT_FORGE_TARGET)
    assert len(result) == 1
    assert result[0].path == "f.ts"
    assert result[0].line == 5


def test_list_sonarcloud_annotations_passing_analysis_no_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    passing_no_output = json.dumps({
        "check_runs": [{
            "id": 1001,
            "name": "SonarCloud Code Analysis",
            "conclusion": "success",
        }]
    })
    monkeypatch.setattr(
        process_runner, "run_subprocess",
        _sonar_fake(check_runs_stdout=passing_no_output),
    )
    assert _pr_sonar.list_sonarcloud_annotations(42, forge_target=DEFAULT_FORGE_TARGET) == ()


def test_list_sonarcloud_annotations_annotations_count_zero_no_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    count_zero = json.dumps({
        "check_runs": [{
            "id": 1001,
            "name": "SonarCloud Code Analysis",
            "conclusion": "success",
            "output": {"annotations_count": 0},
        }]
    })
    monkeypatch.setattr(
        process_runner, "run_subprocess",
        _sonar_fake(check_runs_stdout=count_zero),
    )
    assert _pr_sonar.list_sonarcloud_annotations(42, forge_target=DEFAULT_FORGE_TARGET) == ()


def test_list_sonarcloud_annotations_annotations_count_nonzero_triggers_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    count_nonzero = json.dumps({
        "check_runs": [{
            "id": 2002,
            "name": "SonarCloud Code Analysis",
            "conclusion": "success",
            "output": {"annotations_count": 1},
        }]
    })
    single_ann = json.dumps([{
        "path": "src/x.ts",
        "start_line": 10,
        "title": "issue",
        "message": "details",
    }])
    monkeypatch.setattr(
        process_runner, "run_subprocess",
        _sonar_fake(check_runs_stdout=count_nonzero, annotations_stdout=single_ann),
    )
    result = _pr_sonar.list_sonarcloud_annotations(42, forge_target=DEFAULT_FORGE_TARGET)
    assert len(result) == 1
    assert result[0].line == 10


def test_list_sonarcloud_annotations_id_not_int_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    non_int_id = json.dumps({
        "check_runs": [{
            "id": "not-an-int",
            "name": "SonarCloud Code Analysis",
            "conclusion": "failure",
            "output": {"annotations_count": 1},
        }]
    })
    monkeypatch.setattr(
        process_runner, "run_subprocess",
        _sonar_fake(check_runs_stdout=non_int_id),
    )
    assert _pr_sonar.list_sonarcloud_annotations(42, forge_target=DEFAULT_FORGE_TARGET) == ()


def test_list_sonarcloud_annotations_check_runs_non_dict_items_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mixed = json.dumps({
        "check_runs": [
            "not-a-dict",
            42,
            {
                "id": 1001,
                "name": "SonarCloud Code Analysis",
                "conclusion": "failure",
                "output": {"annotations_count": 1},
            },
        ]
    })
    single_ann = json.dumps([{
        "path": "src/x.ts",
        "start_line": 5,
        "title": "t",
        "message": "m",
    }])
    monkeypatch.setattr(
        process_runner, "run_subprocess",
        _sonar_fake(check_runs_stdout=mixed, annotations_stdout=single_ann),
    )
    result = _pr_sonar.list_sonarcloud_annotations(42, forge_target=DEFAULT_FORGE_TARGET)
    assert len(result) == 1
    assert result[0].line == 5


def test_extract_rule_url_strips_trailing_punctuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trailing_punct = json.dumps([{
        "path": "src/x.ts",
        "start_line": 5,
        "title": "Remove void",
        "message": "See rule at https://sonarcloud.io/rules?open=S3735).",
    }])
    monkeypatch.setattr(
        process_runner, "run_subprocess",
        _sonar_fake(annotations_stdout=trailing_punct),
    )
    result = _pr_sonar.list_sonarcloud_annotations(42, forge_target=DEFAULT_FORGE_TARGET)
    assert len(result) == 1
    assert result[0].rule_url == "https://sonarcloud.io/rules?open=S3735"


def test_list_sonarcloud_annotations_null_conclusion_uses_annotations_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    null_conclusion = json.dumps({
        "check_runs": [{
            "id": 1001,
            "name": "SonarCloud Code Analysis",
            "conclusion": None,
            "output": {"annotations_count": 2},
        }]
    })
    monkeypatch.setattr(
        process_runner, "run_subprocess",
        _sonar_fake(check_runs_stdout=null_conclusion),
    )
    result = _pr_sonar.list_sonarcloud_annotations(188, forge_target=DEFAULT_FORGE_TARGET)
    assert len(result) == 2


def test_sonar_ack_hotspots_token_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.delenv("SONAR_TOKEN", raising=False)
    assert _pr_sonar.sonar_ack_hotspots(42, "task-x") is False
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "sonar_ack_hotspots_failed"
        and e.get("reason") == "token_missing"
        for e in entries
    )


def test_sonar_ack_hotspots_fetch_subprocess_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("SONAR_TOKEN", "tok")

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("curl")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_sonar.sonar_ack_hotspots(42, "task-x") is False
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "sonar_ack_hotspots_failed"
        and "curl subprocess error" in str(e.get("reason"))
        for e in entries
    )


def test_sonar_ack_hotspots_fetch_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("SONAR_TOKEN", "tok")

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=30.0)

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_sonar.sonar_ack_hotspots(42, "task-x") is False
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "sonar_ack_hotspots_failed"
        and "curl subprocess error" in str(e.get("reason"))
        for e in entries
    )


def test_sonar_ack_hotspots_fetch_nonzero_rc(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("SONAR_TOKEN", "tok")

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="unauthorized")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_sonar.sonar_ack_hotspots(42, "task-x") is False
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "sonar_ack_hotspots_failed"
        and "returncode=1" in str(e.get("reason"))
        for e in entries
    )


def test_sonar_ack_hotspots_fetch_json_malformed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("SONAR_TOKEN", "tok")

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout="[not a dict]", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_sonar.sonar_ack_hotspots(42, "task-x") is False
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "sonar_ack_hotspots_failed"
        and e.get("reason") == "hotspot_search_json_malformed"
        for e in entries
    )


def test_sonar_ack_hotspots_no_hotspots_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("SONAR_TOKEN", "tok")

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout=_HOTSPOT_SEARCH_EMPTY, stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_sonar.sonar_ack_hotspots(42, "task-x") is True
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "sonar_ack_hotspots_ok"
        and e.get("note") == "no_hotspots_found"
        for e in entries
    )


def test_sonar_ack_hotspots_fetch_filters_bad_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("SONAR_TOKEN", "tok")
    mixed = json.dumps({
        "hotspots": [
            "not-a-dict",
            {"no_key_field": "x"},
            {"key": "VALID1"},
        ],
        "paging": {"total": 1},
    })
    call_n: list[int] = [0]

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        call_n[0] += 1
        if call_n[0] == 1:
            return subprocess.CompletedProcess(cmd, 0, stdout=mixed, stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_sonar.sonar_ack_hotspots(42, "task-x") is True
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "sonar_ack_hotspots_ok" and e.get("count") == 1
        for e in entries
    )


def test_sonar_ack_hotspots_bulk_ack_subprocess_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("SONAR_TOKEN", "tok")
    call_n: list[int] = [0]

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        call_n[0] += 1
        if call_n[0] == 1:
            return subprocess.CompletedProcess(cmd, 0, stdout=_HOTSPOT_SEARCH_OK, stderr="")
        raise FileNotFoundError("curl")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_sonar.sonar_ack_hotspots(42, "task-x") is False
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "sonar_ack_hotspots_failed"
        and "curl subprocess error" in str(e.get("reason"))
        for e in entries
    )


def test_sonar_ack_hotspots_bulk_ack_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("SONAR_TOKEN", "tok")
    call_n: list[int] = [0]

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        call_n[0] += 1
        if call_n[0] == 1:
            return subprocess.CompletedProcess(cmd, 0, stdout=_HOTSPOT_SEARCH_OK, stderr="")
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=30.0)

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_sonar.sonar_ack_hotspots(42, "task-x") is False
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "sonar_ack_hotspots_failed"
        and "curl subprocess error" in str(e.get("reason"))
        for e in entries
    )


def test_sonar_ack_hotspots_bulk_ack_nonzero_rc(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("SONAR_TOKEN", "tok")
    call_n: list[int] = [0]

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        call_n[0] += 1
        if call_n[0] == 1:
            return subprocess.CompletedProcess(cmd, 0, stdout=_HOTSPOT_SEARCH_OK, stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="forbidden")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_sonar.sonar_ack_hotspots(42, "task-x") is False
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "sonar_ack_hotspots_failed"
        and "returncode=1" in str(e.get("reason"))
        for e in entries
    )


def test_sonar_ack_hotspots_full_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("SONAR_TOKEN", "tok")
    call_n: list[int] = [0]

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        call_n[0] += 1
        if call_n[0] == 1:
            return subprocess.CompletedProcess(cmd, 0, stdout=_HOTSPOT_SEARCH_OK, stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_sonar.sonar_ack_hotspots(42, "task-x") is True
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "sonar_ack_hotspots_ok" and e.get("count") == 2
        for e in entries
    )


def test_sonar_ack_hotspots_search_url_has_status_to_review(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("SONAR_TOKEN", "tok")
    captured: list[list[str]] = []

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        captured.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout=_HOTSPOT_SEARCH_EMPTY, stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    _pr_sonar.sonar_ack_hotspots(42, "task-x")
    search_url = captured[0][-1]
    assert "status=TO_REVIEW" in search_url


def test_find_sonar_check_suite_id_no_match() -> None:
    runs: list[dict[str, object]] = [
        {"name": "Other Check", "check_suite": {"id": 99}},
    ]
    assert _pr_sonar._find_sonar_check_suite_id(runs) is None


def test_find_sonar_check_suite_id_suite_not_dict() -> None:
    runs: list[dict[str, object]] = [
        {"name": "SonarCloud Code Analysis", "check_suite": "not-a-dict"},
    ]
    assert _pr_sonar._find_sonar_check_suite_id(runs) is None


def test_find_sonar_check_suite_id_suite_id_not_int() -> None:
    runs: list[dict[str, object]] = [
        {"name": "SonarCloud Code Analysis", "check_suite": {"id": "str-id"}},
    ]
    assert _pr_sonar._find_sonar_check_suite_id(runs) is None


def test_find_sonar_check_suite_id_success() -> None:
    runs: list[dict[str, object]] = [
        {"name": "Other Check", "check_suite": {"id": 99}},
        {"name": "SonarCloud Code Analysis", "check_suite": {"id": 777}},
    ]
    assert _pr_sonar._find_sonar_check_suite_id(runs) == 777


def test_retrigger_sonar_sha_lookup_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not found")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_sonar.retrigger_sonar_analysis(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) is False
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "sonar_retrigger_failed"
        and e.get("reason") == "sha_lookup_failed"
        for e in entries
    )


def test_retrigger_sonar_check_runs_lookup_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    call_n: list[int] = [0]

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        call_n[0] += 1
        if call_n[0] == 1:
            return subprocess.CompletedProcess(cmd, 0, stdout="sha123\n", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="api error")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_sonar.retrigger_sonar_analysis(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) is False
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "sonar_retrigger_failed"
        and e.get("reason") == "check_runs_lookup_failed"
        for e in entries
    )


def test_retrigger_sonar_check_suite_not_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    no_sonar = json.dumps({"check_runs": [{"name": "Other", "check_suite": {"id": 9}}]})
    call_n: list[int] = [0]

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        call_n[0] += 1
        if call_n[0] == 1:
            return subprocess.CompletedProcess(cmd, 0, stdout="sha123\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=no_sonar, stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_sonar.retrigger_sonar_analysis(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) is False
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "sonar_retrigger_failed"
        and e.get("reason") == "check_suite_not_found"
        for e in entries
    )


def test_retrigger_sonar_rerequest_subprocess_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    call_n: list[int] = [0]

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        call_n[0] += 1
        if call_n[0] == 1:
            return subprocess.CompletedProcess(cmd, 0, stdout="sha123\n", stderr="")
        if call_n[0] == 2:
            return subprocess.CompletedProcess(cmd, 0, stdout=_CHECK_RUNS_WITH_SUITE, stderr="")
        raise FileNotFoundError("gh")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_sonar.retrigger_sonar_analysis(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) is False
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "sonar_retrigger_failed" for e in entries
    )


def test_retrigger_sonar_rerequest_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    call_n: list[int] = [0]

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        call_n[0] += 1
        if call_n[0] == 1:
            return subprocess.CompletedProcess(cmd, 0, stdout="sha123\n", stderr="")
        if call_n[0] == 2:
            return subprocess.CompletedProcess(cmd, 0, stdout=_CHECK_RUNS_WITH_SUITE, stderr="")
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=60.0)

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_sonar.retrigger_sonar_analysis(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) is False
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "sonar_retrigger_failed" for e in entries
    )


def test_retrigger_sonar_rerequest_nonzero_rc(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    call_n: list[int] = [0]

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        call_n[0] += 1
        if call_n[0] == 1:
            return subprocess.CompletedProcess(cmd, 0, stdout="sha123\n", stderr="")
        if call_n[0] == 2:
            return subprocess.CompletedProcess(cmd, 0, stdout=_CHECK_RUNS_WITH_SUITE, stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="forbidden")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_sonar.retrigger_sonar_analysis(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) is False
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "sonar_retrigger_failed" for e in entries
    )


def test_retrigger_sonar_full_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    call_n: list[int] = [0]

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        call_n[0] += 1
        if call_n[0] == 1:
            return subprocess.CompletedProcess(cmd, 0, stdout="sha123\n", stderr="")
        if call_n[0] == 2:
            return subprocess.CompletedProcess(cmd, 0, stdout=_CHECK_RUNS_WITH_SUITE, stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_sonar.retrigger_sonar_analysis(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) is True
    entries = _audit_entries(tmp_path)
    assert any(e.get("action") == "sonar_retrigger_ok" for e in entries)


def test_init_sonar_constants_without_code_quality(monkeypatch: pytest.MonkeyPatch) -> None:
    from alan.engine_config import EngineConfig

    no_cq = EngineConfig(repo=RepoIdentity(owner="test", repo="test"))
    monkeypatch.setattr(_pr_sonar, "_ENGINE_DEFAULT", no_cq)
    assert _pr_sonar._init_sonar_constants() == ("", "", "")  # pyright: ignore[reportPrivateUsage]


def test_sonar_hotspots_pr_url_returns_url_when_configured() -> None:
    url = _pr_sonar.sonar_hotspots_pr_url(123)
    assert url == (
        f"{_pr_sonar._SONAR_HOST}/project/security_hotspots"  # noqa: SLF001
        f"?id={_pr_sonar._SONAR_PROJECT_KEY}&pullRequest=123"  # noqa: SLF001
    )


def test_sonar_hotspots_pr_url_none_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_sonar, "_SONAR_HOST", "")
    monkeypatch.setattr(_pr_sonar, "_SONAR_PROJECT_KEY", "")
    assert _pr_sonar.sonar_hotspots_pr_url(123) is None


_MOVED_PUBLIC_NAMES = (
    "SonarAnnotation",
    "sonar_hotspots_pr_url",
    "list_sonarcloud_annotations",
    "sonar_ack_hotspots",
    "retrigger_sonar_analysis",
)


@pytest.mark.parametrize("name", _MOVED_PUBLIC_NAMES)
def test_moved_name_lives_only_on_pr_sonar(name: str) -> None:
    assert hasattr(_pr_sonar, name)
    assert not hasattr(_pr_branch, name)
