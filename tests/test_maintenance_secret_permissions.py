from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from alan.claude_cli import AgentStreamingResult, StreamEvent
from alan.teams import _base as base_mod
from alan.teams import _maintenance_secrets as secrets_mod
from alan.teams._maintenance_secrets import (
    HOST_READ_MODES_HEADING,
    SecretFileMode,
    secret_file_modes,
    secret_permission_block,
)
from alan.teams.maintenance import _SECURITY_PROMPT, ROLES, TEAM, MaintenanceTeam
from alan.workspace import create
from tests._helpers import seed_and_run

_SECURITY_ROLE = "security_scanner"
_OTHER_ROLES = [role.name for role in ROLES if role.name != _SECURITY_ROLE]


@pytest.fixture
def alan_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "synthetic-alan"
    monkeypatch.setenv("ALAN_STATE_ROOT", str(home / "state"))
    monkeypatch.setenv("ALAN_SECRETS_STORE_ROOT", str(home / "secrets"))
    return home


def write_secret(path: Path, mode: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("synthetic-canary-value", encoding="utf-8")
    path.chmod(mode)
    return path


def test_maintenance_reports_group_or_world_readable_secret_file(alan_home: Path) -> None:
    write_secret(alan_home / "secrets.env", 0o644)
    prefix = TEAM._role_user_prefix(_SECURITY_ROLE)  # noqa: SLF001
    assert HOST_READ_MODES_HEADING in prefix
    assert "- secrets.env: 644 GROUP/WORLD-ACCESSIBLE" in prefix


def test_maintenance_reports_a_world_readable_file_in_the_secrets_store(alan_home: Path) -> None:
    write_secret(alan_home / "secrets" / "nested" / "vault.json", 0o604)
    prefix = TEAM._role_user_prefix(_SECURITY_ROLE)  # noqa: SLF001
    assert "- secrets/nested/vault.json: 604 GROUP/WORLD-ACCESSIBLE" in prefix


@pytest.mark.parametrize("mode", [0o640, 0o620, 0o604, 0o602, 0o660, 0o666, 0o644, 0o710])
def test_any_group_or_world_bit_flags_the_file(alan_home: Path, mode: int) -> None:
    write_secret(alan_home / "state" / "control_token", mode)
    (entry,) = secret_file_modes()
    assert entry == SecretFileMode("control_token", mode)
    assert entry.exposed


@pytest.mark.parametrize("mode", [0o600, 0o400, 0o700])
def test_an_owner_only_file_is_not_flagged(alan_home: Path, mode: int) -> None:
    write_secret(alan_home / "secrets.env", mode)
    entries = [line for line in secret_permission_block().splitlines() if line.startswith("- ")]
    assert entries == [f"- secrets.env: {mode:03o}"]


def test_control_token_files_and_store_entries_are_listed_by_label_in_order(
    alan_home: Path,
) -> None:
    write_secret(alan_home / "secrets" / "b.env", 0o600)
    write_secret(alan_home / "secrets" / "a.env", 0o600)
    write_secret(alan_home / "state" / "control_token", 0o600)
    write_secret(alan_home / "state" / "control_token_meta.json", 0o600)
    write_secret(alan_home / "secrets.env", 0o600)
    labels = [entry.label for entry in secret_file_modes()]
    assert labels == [
        "secrets.env",
        "control_token",
        "control_token_meta.json",
        "secrets/a.env",
        "secrets/b.env",
    ]


def test_the_block_names_labels_only_and_never_an_absolute_path(
    alan_home: Path, tmp_path: Path
) -> None:
    write_secret(alan_home / "secrets.env", 0o644)
    write_secret(alan_home / "secrets" / "x.env", 0o644)
    block = secret_permission_block()
    assert str(tmp_path) not in block
    assert str(alan_home) not in block
    assert "- secrets/x.env: 644 GROUP/WORLD-ACCESSIBLE" in block


def test_absent_secret_files_are_not_listed(alan_home: Path) -> None:
    assert secret_file_modes() == []
    assert "- (no secret files present)" in secret_permission_block()


def test_a_directory_entry_in_the_secrets_store_is_not_listed_as_a_file(alan_home: Path) -> None:
    (alan_home / "secrets" / "empty-dir").mkdir(parents=True)
    assert secret_file_modes() == []


def test_a_secrets_store_that_is_a_plain_file_lists_nothing(
    alan_home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    not_a_dir = write_secret(tmp_path / "flat-store", 0o600)
    monkeypatch.setenv("ALAN_SECRETS_STORE_ROOT", str(not_a_dir))
    assert secret_file_modes() == []


def test_a_file_that_cannot_be_stat_ed_is_shown_as_unreadable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        secrets_mod, "_labelled_secret_paths", lambda: [("ghost", tmp_path / "missing")]
    )
    (entry,) = secret_file_modes()
    assert entry == SecretFileMode("ghost", None)
    assert not entry.exposed
    assert "- ghost: unreadable" in secret_permission_block()


def test_exposed_is_false_for_an_owner_only_mode() -> None:
    assert not SecretFileMode("x", 0o600).exposed
    assert SecretFileMode("x", 0o640).exposed


def test_only_the_security_scanner_receives_the_permission_block(alan_home: Path) -> None:
    write_secret(alan_home / "secrets.env", 0o644)
    assert TEAM._role_user_prefix(_SECURITY_ROLE).startswith(HOST_READ_MODES_HEADING)  # noqa: SLF001
    for role_name in _OTHER_ROLES:
        assert TEAM._role_user_prefix(role_name) == ""  # noqa: SLF001


def test_scanner_instructions_no_longer_list_or_read_the_secret_paths() -> None:
    assert "secrets.env" not in _SECURITY_PROMPT
    assert "~/.alan/secrets" not in _SECURITY_PROMPT
    assert "ls -la" not in _SECURITY_PROMPT
    assert HOST_READ_MODES_HEADING in _SECURITY_PROMPT
    assert "GROUP/WORLD-ACCESSIBLE" in _SECURITY_PROMPT
    assert "never try to list or read them" in _SECURITY_PROMPT


def test_other_maintenance_prompts_never_mention_the_secret_paths() -> None:
    for role in ROLES:
        assert "secrets.env" not in role.system_prompt
        assert "~/.alan/secrets" not in role.system_prompt


def _record_user_texts(
    monkeypatch: pytest.MonkeyPatch, sink: dict[str, str]
) -> Callable[..., AgentStreamingResult]:
    def fake(
        system_prompt: str,
        user_text: str,
        *,
        on_event: Callable[[StreamEvent], None] | None = None,
        stream_path: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        role = next(r.name for r in ROLES if f"You are the {r.name} " in system_prompt)
        sink[role] = user_text
        text = f"{role} clean\n\nROLE_SEVERITY: NONE"
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(json.dumps({"type": "result", "result": text}) + "\n")
        if on_event is not None:
            on_event({"type": "result", "result": text})
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    return fake


def test_a_maintenance_run_hands_the_scanner_the_host_read_modes(
    alan_home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    write_secret(alan_home / "secrets.env", 0o644)
    seen: dict[str, str] = {}
    _record_user_texts(monkeypatch, seen)
    ws = create("maintenance secrets", "d", root=tmp_path / "ws")
    seed_and_run(MaintenanceTeam(), ws, None, lambda _e: None)
    assert "- secrets.env: 644 GROUP/WORLD-ACCESSIBLE" in seen[_SECURITY_ROLE]
    for role_name in _OTHER_ROLES:
        assert HOST_READ_MODES_HEADING not in seen[role_name]
