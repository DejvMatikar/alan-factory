from __future__ import annotations

from pathlib import Path

import pytest

from alan import self_update
from alan.self_update import _cli, _run


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    recorded: list[dict[str, object]] = []

    def fake(repo: Path, **kwargs: object) -> int:
        recorded.append({"repo": repo, **kwargs})
        return 0

    monkeypatch.setattr(_run, "main_apply", fake)
    return recorded


def test_dry_run_calls_check_and_never_reaches_main_apply(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, calls: list[dict[str, object]]
) -> None:
    checked: list[Path] = []
    monkeypatch.setattr(self_update, "check", lambda repo: checked.append(repo))
    plist = tmp_path / "ai.alan.plist"
    result = _cli.main([
        "apply", "--dry-run", "--kit-repo", str(tmp_path), "--plist-path", str(plist),
    ])
    assert result == 0
    assert checked == [tmp_path]
    assert calls == []


def test_dry_run_requires_the_plist_path_flag_like_any_other_apply() -> None:
    assert _cli.main(["apply", "--dry-run"]) == 2


def test_sha_and_force_flags_are_forwarded_to_main_apply(
    tmp_path: Path, calls: list[dict[str, object]]
) -> None:
    plist = tmp_path / "ai.alan.plist"
    sha = "a" * 40
    result = _cli.main(["apply", "--plist-path", str(plist), "--sha", sha, "--force"])
    assert result == 0
    assert calls[0]["pin"] == sha
    assert calls[0]["force"] is True


def test_queue_db_path_and_scheduled_flags_are_forwarded_to_main_apply(
    tmp_path: Path, calls: list[dict[str, object]]
) -> None:
    plist = tmp_path / "ai.alan.plist"
    queue_db = tmp_path / "queue.db"
    result = _cli.main([
        "apply", "--plist-path", str(plist), "--scheduled", "--queue-db-path", str(queue_db),
    ])
    assert result == 0
    assert calls[0]["scheduled"] is True
    assert calls[0]["queue_db_path"] == queue_db


def test_absent_sha_force_and_queue_db_path_default_to_none_and_false(
    tmp_path: Path, calls: list[dict[str, object]]
) -> None:
    plist = tmp_path / "ai.alan.plist"
    result = _cli.main(["apply", "--plist-path", str(plist)])
    assert result == 0
    assert calls[0]["pin"] is None
    assert calls[0]["force"] is False
    assert calls[0]["queue_db_path"] is None


def test_sha_as_the_last_token_with_no_value_is_rejected(
    calls: list[dict[str, object]],
) -> None:
    assert _cli.main(["apply", "--plist-path", "x", "--sha"]) == 2
    assert calls == []


def test_queue_db_path_as_the_last_token_with_no_value_is_rejected(
    calls: list[dict[str, object]],
) -> None:
    assert _cli.main(["apply", "--plist-path", "x", "--queue-db-path"]) == 2
    assert calls == []


def test_scheduled_with_force_is_rejected_instead_of_silently_dropping_force(
    calls: list[dict[str, object]],
) -> None:
    assert _cli.main(["apply", "--plist-path", "x", "--scheduled", "--force"]) == 2
    assert calls == []


def test_scheduled_with_sha_is_rejected_instead_of_silently_dropping_the_pin(
    calls: list[dict[str, object]],
) -> None:
    sha = "a" * 40
    assert _cli.main(["apply", "--plist-path", "x", "--scheduled", "--sha", sha]) == 2
    assert calls == []
