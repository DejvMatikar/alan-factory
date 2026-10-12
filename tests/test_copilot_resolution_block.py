from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan import workspace as workspace_mod
from alan.block_correction import BlockCorrectionError
from alan.engine_config import EngineConfig, ForgeConfig, Posture, RepoIdentity
from alan.teams import _copilot_resolution_block as block_mod
from alan.teams import _pr_audit_log
from alan.workspace import EngineSnapshot, Workspace, create


def _fence(body: str) -> str:
    return f"## Summary\ndone\n\n```yaml copilot_resolution.yaml\n{body}\n```\n"


def test_parse_fix_row() -> None:
    rows = block_mod.parse_copilot_resolution_yaml(
        _fence("- thread_id: PRTI_1\n  mode: fix")
    )
    assert rows == (block_mod.CopilotResolutionRow(thread_id="PRTI_1", mode="fix"),)


def test_parse_acknowledge_row_requires_body() -> None:
    with pytest.raises(BlockCorrectionError):
        block_mod.parse_copilot_resolution_yaml(
            _fence("- thread_id: PRTI_1\n  mode: acknowledge")
        )


def test_parse_acknowledge_row_with_body() -> None:
    rows = block_mod.parse_copilot_resolution_yaml(
        _fence("- thread_id: PRTI_1\n  mode: acknowledge\n  body: nit, skipping")
    )
    assert rows[0].mode == "acknowledge"
    assert rows[0].body == "nit, skipping"


def test_parse_fix_or_acknowledge_requires_thread_id() -> None:
    with pytest.raises(BlockCorrectionError):
        block_mod.parse_copilot_resolution_yaml(_fence("- mode: fix"))


def test_parse_comment_ack_row_requires_database_id_and_body() -> None:
    with pytest.raises(BlockCorrectionError):
        block_mod.parse_copilot_resolution_yaml(_fence("- mode: comment_ack\n  body: ack"))
    with pytest.raises(BlockCorrectionError):
        block_mod.parse_copilot_resolution_yaml(
            _fence("- mode: comment_ack\n  database_id: 5")
        )


def test_parse_comment_ack_row_valid() -> None:
    rows = block_mod.parse_copilot_resolution_yaml(
        _fence("- database_id: 99\n  mode: comment_ack\n  body: acked")
    )
    assert rows[0].database_id == 99
    assert rows[0].mode == "comment_ack"


def test_parse_multiple_rows() -> None:
    text = _fence(
        "- thread_id: PRTI_1\n  mode: fix\n"
        "- database_id: 7\n  mode: comment_ack\n  body: ack"
    )
    rows = block_mod.parse_copilot_resolution_yaml(text)
    assert len(rows) == 2
    assert rows[0].mode == "fix"
    assert rows[1].mode == "comment_ack"


def test_parse_rejects_nested_value_wholesale() -> None:
    text = _fence("- thread_id: PRTI_1\n  mode: fix\n  extra:\n    nested: true")
    with pytest.raises(BlockCorrectionError):
        block_mod.parse_copilot_resolution_yaml(text)


def test_parse_rejects_non_mapping_row() -> None:
    text = _fence("- just a string")
    with pytest.raises(BlockCorrectionError):
        block_mod.parse_copilot_resolution_yaml(text)


def test_parse_rejects_non_string_key() -> None:
    text = _fence("- {1: fix}")
    with pytest.raises(BlockCorrectionError):
        block_mod.parse_copilot_resolution_yaml(text)


def test_parse_rejects_empty_list() -> None:
    text = _fence("[]")
    with pytest.raises(BlockCorrectionError):
        block_mod.parse_copilot_resolution_yaml(text)


def test_parse_rejects_missing_block() -> None:
    with pytest.raises(BlockCorrectionError):
        block_mod.parse_copilot_resolution_yaml("## Summary\nnothing here")


def test_parse_rejects_malformed_yaml() -> None:
    text = "```yaml copilot_resolution.yaml\n- thread_id: [unterminated\n```\n"
    with pytest.raises(BlockCorrectionError):
        block_mod.parse_copilot_resolution_yaml(text)


def test_parse_rejects_unknown_field() -> None:
    text = _fence("- thread_id: PRTI_1\n  mode: fix\n  bogus: value")
    with pytest.raises(BlockCorrectionError):
        block_mod.parse_copilot_resolution_yaml(text)


def test_resolution_block_attempted_true_when_fence_present() -> None:
    assert block_mod.resolution_block_attempted(_fence("- thread_id: t\n  mode: fix"))


def test_resolution_block_attempted_false_when_absent() -> None:
    assert not block_mod.resolution_block_attempted("## Summary\nnothing here")


def test_resolution_block_guidance_names_the_fence_and_modes() -> None:
    guidance = block_mod.RESOLUTION_BLOCK_GUIDANCE
    assert "copilot_resolution.yaml" in guidance
    assert "mode: fix" in guidance
    assert "mode: acknowledge" in guidance
    assert "mode: comment_ack" in guidance


def test_resolution_block_guidance_states_the_listed_set_rule_exactly_once() -> None:
    guidance = block_mod.RESOLUTION_BLOCK_GUIDANCE
    assert guidance.count("host listed on this PR") == 1


@pytest.mark.parametrize(
    "reason",
    ["thread_not_listed", "already_handled", "ack_cap_exceeded", "forge_call_failed"],
)
def test_refusal_messages_cover_every_reason_with_prose(reason: str) -> None:
    message = block_mod._REFUSAL_MESSAGES[reason]  # noqa: SLF001
    assert message
    assert message != reason


def test_refused_resolutions_block_renders_prose_for_thread_not_listed(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    log = tmp_path / "logs" / _pr_audit_log.COPILOT_AUDIT_FILE
    log.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "action": "copilot_resolution_refused",
        "pr_number": 42,
        "thread_id": "PRTI_1",
        "database_id": None,
        "mode": "fix",
        "repo": "acme-corp/widget-app",
        "reason": "thread_not_listed",
    }
    log.write_text(json.dumps(row) + "\n")
    block = block_mod.refused_resolutions_block(workspace, 42)
    assert block_mod._REFUSAL_MESSAGES["thread_not_listed"] in block  # noqa: SLF001
    assert "thread_not_listed" not in block


def test_refused_resolutions_block_falls_back_to_raw_slug_for_unmapped_reason(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    log = tmp_path / "logs" / _pr_audit_log.COPILOT_AUDIT_FILE
    log.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "action": "copilot_resolution_refused",
        "pr_number": 42,
        "thread_id": "PRTI_1",
        "database_id": None,
        "mode": "fix",
        "repo": "acme-corp/widget-app",
        "reason": "some_future_reason",
    }
    log.write_text(json.dumps(row) + "\n")
    block = block_mod.refused_resolutions_block(workspace, 42)
    assert "some_future_reason" in block


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    ws = create("copilot-block-task", "resolve threads", root=tmp_path / "ws")
    snapshot = EngineSnapshot(
        posture=Posture.propose,
        transition_graph={},
        config=EngineConfig(
            repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
            forge=ForgeConfig(
                repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
                credential_env_name="ACME_WIDGET_GH_TOKEN",
            ),
        ),
    )
    return workspace_mod.stamp_engine_snapshot(ws, snapshot)


def test_refused_resolutions_block_empty_when_no_pr_number(
    workspace: Workspace,
) -> None:
    assert block_mod.refused_resolutions_block(workspace, None) == ""


def test_refused_resolutions_block_empty_when_no_matching_rows(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    assert block_mod.refused_resolutions_block(workspace, 42) == ""


def test_refused_resolutions_block_renders_matching_rows(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    log = tmp_path / "logs" / _pr_audit_log.COPILOT_AUDIT_FILE
    log.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "action": "copilot_resolution_refused",
        "pr_number": 42,
        "thread_id": "PRTI_1",
        "database_id": None,
        "mode": "acknowledge",
        "repo": "acme-corp/widget-app",
        "reason": "ack_cap_exceeded",
    }
    log.write_text(json.dumps(row) + "\n")
    block = block_mod.refused_resolutions_block(workspace, 42)
    assert "PRTI_1" in block
    assert block_mod._REFUSAL_MESSAGES["ack_cap_exceeded"] in block  # noqa: SLF001


def test_refused_resolutions_block_filters_by_repo(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    log = tmp_path / "logs" / _pr_audit_log.COPILOT_AUDIT_FILE
    log.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "action": "copilot_resolution_refused",
        "pr_number": 42,
        "thread_id": "PRTI_1",
        "database_id": None,
        "mode": "acknowledge",
        "repo": "DejvMatikar/alan",
        "reason": "ack_cap_exceeded",
    }
    log.write_text(json.dumps(row) + "\n")
    assert block_mod.refused_resolutions_block(workspace, 42) == ""


def test_refused_resolutions_block_skips_blank_and_malformed_lines(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    log = tmp_path / "logs" / _pr_audit_log.COPILOT_AUDIT_FILE
    log.parent.mkdir(parents=True, exist_ok=True)
    good_row = {
        "action": "copilot_resolution_refused",
        "pr_number": 42,
        "thread_id": "PRTI_2",
        "database_id": None,
        "mode": "fix",
        "repo": "acme-corp/widget-app",
        "reason": "forge_call_failed",
    }
    log.write_text("\nnot json{\n" + json.dumps(good_row) + "\n")
    block = block_mod.refused_resolutions_block(workspace, 42)
    assert "PRTI_2" in block


def test_refused_resolutions_block_filters_by_pr_number(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    log = tmp_path / "logs" / _pr_audit_log.COPILOT_AUDIT_FILE
    log.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "action": "copilot_resolution_refused",
        "pr_number": 7,
        "thread_id": "PRTI_1",
        "database_id": None,
        "mode": "acknowledge",
        "repo": "acme-corp/widget-app",
        "reason": "ack_cap_exceeded",
    }
    log.write_text(json.dumps(row) + "\n")
    assert block_mod.refused_resolutions_block(workspace, 42) == ""
