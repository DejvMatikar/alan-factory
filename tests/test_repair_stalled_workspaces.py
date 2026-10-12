from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import repair_stalled_multitask_workspaces as repair_mod  # type: ignore[import-not-found]  # noqa: E402


def _fake_response(payload: object) -> MagicMock:
    resp = MagicMock()
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    return resp


def test_build_url_joins_base_workspace_subtask() -> None:
    url = repair_mod._build_url("http://localhost:8585", "ws1", "sub1")  # noqa: SLF001
    assert url == "http://localhost:8585/api/workspaces/ws1/tasks/sub1/complete"


def test_build_url_strips_trailing_slash_on_base() -> None:
    url = repair_mod._build_url("http://localhost:8585/", "ws1", "sub1")  # noqa: SLF001
    assert url == "http://localhost:8585/api/workspaces/ws1/tasks/sub1/complete"


def test_wrapper_posts_to_endpoint() -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> MagicMock:  # noqa: ARG001
        captured["url"] = request.full_url
        assert isinstance(request.data, bytes)
        captured["data"] = json.loads(request.data.decode("utf-8"))
        captured["method"] = request.get_method()
        return _fake_response({"task_id": "ws1", "status": "active"})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = repair_mod.complete_out_of_band(
            "http://localhost:8585", "ws1", "sub1", 42, force=False
        )

    assert captured["url"] == "http://localhost:8585/api/workspaces/ws1/tasks/sub1/complete"
    assert captured["data"] == {"pr_number": 42, "force": False}
    assert captured["method"] == "POST"
    assert result == {"task_id": "ws1", "status": "active"}


def test_complete_out_of_band_non_dict_response_returns_empty_dict() -> None:
    with patch("urllib.request.urlopen", return_value=_fake_response([1, 2])):
        result = repair_mod.complete_out_of_band(
            "http://localhost:8585", "ws1", "sub1", 42, force=False
        )
    assert result == {}


def test_main_dry_run_prints_without_network_call(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("urllib.request.urlopen") as mock_urlopen:
        rc = repair_mod.main(
            ["--workspace", "ws1", "--subtask", "sub1", "--pr", "42", "--dry-run"]
        )
    assert rc == 0
    mock_urlopen.assert_not_called()
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "ws1" in out


def test_main_success_prints_json_result(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("urllib.request.urlopen", return_value=_fake_response({"status": "active"})):
        rc = repair_mod.main(["--workspace", "ws1", "--subtask", "sub1", "--pr", "42"])
    assert rc == 0
    out = capsys.readouterr().out
    assert json.loads(out)["status"] == "active"


def test_main_force_flag_sets_force_true() -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> MagicMock:  # noqa: ARG001
        assert isinstance(request.data, bytes)
        captured["data"] = json.loads(request.data.decode("utf-8"))
        return _fake_response({"status": "active"})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        rc = repair_mod.main(
            ["--workspace", "ws1", "--subtask", "sub1", "--pr", "42", "--force"]
        )
    assert rc == 0
    assert captured["data"] == {"pr_number": 42, "force": True}


def test_main_http_error_returns_1(capsys: pytest.CaptureFixture[str]) -> None:
    def raise_http_error(*_a: object, **_kw: object) -> MagicMock:
        raise urllib.error.HTTPError(
            "http://x", 422, "Unprocessable", Message(), io.BytesIO(b'{"detail":"nope"}')
        )

    with patch("urllib.request.urlopen", side_effect=raise_http_error):
        rc = repair_mod.main(["--workspace", "ws1", "--subtask", "sub1", "--pr", "42"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "422" in err
    assert "nope" in err


def test_main_url_error_returns_1(capsys: pytest.CaptureFixture[str]) -> None:
    def raise_url_error(*_a: object, **_kw: object) -> MagicMock:
        raise urllib.error.URLError("connection refused")

    with patch("urllib.request.urlopen", side_effect=raise_url_error):
        rc = repair_mod.main(["--workspace", "ws1", "--subtask", "sub1", "--pr", "42"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "could not reach" in err


def test_main_requires_workspace_subtask_pr() -> None:
    with pytest.raises(SystemExit):
        repair_mod.main([])


def test_main_uses_default_base_url_when_omitted() -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> MagicMock:  # noqa: ARG001
        captured["url"] = request.full_url
        return _fake_response({"status": "active"})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        rc = repair_mod.main(["--workspace", "ws1", "--subtask", "sub1", "--pr", "42"])
    assert rc == 0
    assert captured["url"] == (
        f"{repair_mod._DEFAULT_BASE_URL}/api/workspaces/ws1/tasks/sub1/complete"  # noqa: SLF001
    )
