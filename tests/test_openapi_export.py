from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI

from alan import server
from alan.routes.infra import mount_ui


def test_openapi_json_parses_as_json() -> None:
    parsed = json.loads(server.openapi_json())
    assert isinstance(parsed, dict)


def test_openapi_json_byte_identical_across_calls() -> None:
    assert server.openapi_json() == server.openapi_json()


def test_openapi_json_ends_with_newline() -> None:
    assert server.openapi_json().endswith("\n")


def test_openapi_json_matches_sorted_indented_format() -> None:
    out = server.openapi_json()
    expected = json.dumps(json.loads(out), sort_keys=True, indent=2) + "\n"
    assert out == expected


def test_openapi_paths_exclude_spa_root() -> None:
    schema = json.loads(server.openapi_json())
    assert "/" not in schema["paths"]


def test_mount_ui_does_not_change_openapi_schema(tmp_path: Path) -> None:
    probe = FastAPI()
    probe.openapi_schema = None
    before = probe.openapi()
    probe.openapi_schema = None

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<html></html>")
    assert mount_ui(probe, dist_dir) is True

    probe.openapi_schema = None
    after = probe.openapi()
    assert before == after


def test_dump_openapi_main_writes_exactly_openapi_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    import dump_openapi  # type: ignore[import-not-found]

    out_path = tmp_path / "openapi.json"
    monkeypatch.setattr(sys, "argv", ["dump_openapi", "--out", str(out_path)])
    exit_code = dump_openapi.main(["--out", str(out_path)])
    assert exit_code == 0
    assert out_path.read_text(encoding="utf-8") == server.openapi_json()


def test_dump_openapi_main_writes_to_stdout_when_out_omitted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    import dump_openapi

    exit_code = dump_openapi.main([])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.out == server.openapi_json()
    assert "wrote OpenAPI schema" not in captured.out
    assert "wrote OpenAPI schema" in captured.err


def test_openapi_health_and_repo_health_paths_use_declared_schema_refs() -> None:
    schema = json.loads(server.openapi_json())
    paths = schema["paths"]
    assert paths["/api/health"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/HealthResponse"}
    assert paths["/api/health/repos"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/RepoHealthResponse"}


def test_openapi_chat_message_routes_use_declared_chat_ack_schema_ref() -> None:
    schema = json.loads(server.openapi_json())
    paths = schema["paths"]
    for path in (
        "/api/build-chat/sessions/{session_id}/messages",
        "/api/explore-chat/sessions/{session_id}/messages",
    ):
        assert paths[path]["post"]["responses"]["202"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ChatAck"
        }
