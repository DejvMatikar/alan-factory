from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from alan import claude_cli, engine_presets, explore_chat_repo
from alan import engine_config as engine_config_mod
from alan import engine_config_proposal as ecp
from alan.engine_config import (
    EngineConfig,
    ForgeConfig,
    RepoIdentity,
    default_engine_config,
)
from alan.explore_chat_repo import RepoProvisionError

_OWNER, _REPO = "acme", "widget"
_SLUG = f"{_OWNER}/{_REPO}"
_CREDENTIAL_ENV = "ACME_WIDGET_GH_TOKEN"  # noqa: S105


def _target_cfg() -> EngineConfig:
    repo = RepoIdentity(owner=_OWNER, repo=_REPO)
    return EngineConfig(
        repo=repo,
        forge=ForgeConfig(repo=repo, credential_env_name=_CREDENTIAL_ENV),
    )


def _register_target() -> None:
    engine_config_mod.register(_target_cfg(), validate_env=False)


@pytest.fixture(autouse=True)
def _state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "state"
    monkeypatch.setenv("ALAN_STATE_ROOT", str(root))
    return root


def _fail_if_called(label: str) -> Any:
    def _boom(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError(f"{label} must not be called on a fail-closed abort")

    return _boom


def _writing_provision(*filenames: str) -> Any:
    calls: list[tuple[str, Path]] = []

    def _provision(engine_key: str, dest: Path) -> None:
        calls.append((engine_key, dest))
        dest.mkdir(parents=True, exist_ok=True)
        for name in filenames:
            (dest / name).write_text("x")

    _provision.calls = calls  # type: ignore[attr-defined]
    return _provision


def _fake_chat_turn(result_text: str, captured: dict[str, Any]) -> Any:
    def _run(**kwargs: Any) -> claude_cli.ChatTurnResult:
        captured.update(kwargs)
        return claude_cli.ChatTurnResult(
            result_text=result_text, claude_session_id=kwargs["session_id"]
        )

    return _run


def test_propose_config_aborts_when_engine_not_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(explore_chat_repo, "provision", _fail_if_called("provision"))
    monkeypatch.setattr(ecp, "run_chat_turn", _fail_if_called("run_chat_turn"))
    with pytest.raises(ecp.ProposalAbortedError):
        ecp.propose_config(_SLUG)


def test_propose_config_aborts_when_engine_has_no_forge(monkeypatch: pytest.MonkeyPatch) -> None:
    engine_config_mod.register(
        EngineConfig(repo=RepoIdentity(owner=_OWNER, repo=_REPO)), validate_env=False
    )
    monkeypatch.setattr(explore_chat_repo, "provision", _fail_if_called("provision"))
    with pytest.raises(ecp.ProposalAbortedError):
        ecp.propose_config(_SLUG)


def test_propose_config_never_falls_back_to_alan_default_forge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default_cfg = default_engine_config()
    engine_config_mod.register(default_cfg, default=True, validate_env=False)
    _register_target()

    provision = _writing_provision("package.json")
    monkeypatch.setattr(explore_chat_repo, "provision", provision)
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        ecp, "run_chat_turn", _fake_chat_turn(json.dumps({}), captured)
    )

    result = ecp.propose_config(_SLUG)

    assert provision.calls == [(_SLUG, provision.calls[0][1])]
    engine_key, dest = provision.calls[0]
    assert engine_key == _SLUG
    assert "DejvMatikar" not in str(dest)
    assert "acme" in str(dest) and "widget" in str(dest)
    assert captured["cwd"] == dest
    assert result.slug == _SLUG


def test_propose_config_busy_slug_aborts_without_provisioning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_target()
    monkeypatch.setattr(explore_chat_repo, "provision", _fail_if_called("provision"))
    with ecp._slug_lock(_SLUG):  # noqa: SLF001
        with pytest.raises(ecp.ProposalAbortedError):
            ecp.propose_config(_SLUG)


def test_propose_config_lock_released_after_failure_allows_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_target()
    monkeypatch.setattr(
        explore_chat_repo, "provision", lambda *_a, **_kw: (_ for _ in ()).throw(
            RepoProvisionError("no auth")
        )
    )
    with pytest.raises(ecp.ProposalAbortedError):
        ecp.propose_config(_SLUG)

    monkeypatch.setattr(explore_chat_repo, "provision", _writing_provision("package.json"))
    monkeypatch.setattr(ecp, "run_chat_turn", _fake_chat_turn(json.dumps({}), {}))
    result = ecp.propose_config(_SLUG)
    assert result.slug == _SLUG


def test_propose_config_aborts_on_provision_failure_and_cleans_up_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_target()
    seen_dest: dict[str, Path] = {}

    def _provision(engine_key: str, dest: Path) -> None:
        del engine_key
        dest.mkdir(parents=True, exist_ok=True)
        seen_dest["path"] = dest
        raise RepoProvisionError("authentication failed")

    monkeypatch.setattr(explore_chat_repo, "provision", _provision)
    monkeypatch.setattr(ecp, "run_chat_turn", _fail_if_called("run_chat_turn"))

    with pytest.raises(ecp.ProposalAbortedError, match="authentication failed"):
        ecp.propose_config(_SLUG)

    assert not seen_dest["path"].exists()


def test_propose_config_aborts_on_inference_failure_and_cleans_up_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_target()
    provision = _writing_provision("package.json")
    monkeypatch.setattr(explore_chat_repo, "provision", provision)

    def _raise(**_kw: Any) -> claude_cli.ChatTurnResult:
        raise claude_cli.ClaudeCliError("timed out")

    monkeypatch.setattr(ecp, "run_chat_turn", _raise)

    with pytest.raises(ecp.ProposalAbortedError, match="timed out"):
        ecp.propose_config(_SLUG)

    _engine_key, dest = provision.calls[0]
    assert not dest.exists()


def test_propose_config_rejects_non_json_model_output(monkeypatch: pytest.MonkeyPatch) -> None:
    _register_target()
    monkeypatch.setattr(explore_chat_repo, "provision", _writing_provision("package.json"))
    monkeypatch.setattr(ecp, "run_chat_turn", _fake_chat_turn("not json at all, sorry", {}))

    with pytest.raises(ecp.ProposalUnrepairableError):
        ecp.propose_config(_SLUG)


def test_propose_config_rejects_json_array_top_level(monkeypatch: pytest.MonkeyPatch) -> None:
    _register_target()
    monkeypatch.setattr(explore_chat_repo, "provision", _writing_provision("package.json"))
    monkeypatch.setattr(ecp, "run_chat_turn", _fake_chat_turn("[1, 2, 3]", {}))

    with pytest.raises(ecp.ProposalUnrepairableError, match="not a JSON object"):
        ecp.propose_config(_SLUG)


def test_propose_config_rejects_wrong_shape_model_output(monkeypatch: pytest.MonkeyPatch) -> None:
    _register_target()
    monkeypatch.setattr(explore_chat_repo, "provision", _writing_provision("package.json"))
    monkeypatch.setattr(
        ecp, "run_chat_turn", _fake_chat_turn(json.dumps({"gate_commands": "not-a-list"}), {})
    )

    with pytest.raises(ecp.ProposalUnrepairableError, match="does not fit"):
        ecp.propose_config(_SLUG)


def test_propose_config_extracts_json_from_fenced_code_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_target()
    monkeypatch.setattr(explore_chat_repo, "provision", _writing_provision("package.json"))
    fenced = (
        "Here is my proposal:\n```json\n"
        + json.dumps({"gate_commands": [["make", "test"]]})
        + "\n```\nLet me know if you have questions."
    )
    monkeypatch.setattr(ecp, "run_chat_turn", _fake_chat_turn(fenced, {}))

    result = ecp.propose_config(_SLUG)
    assert result.proposed_config.gate_commands == [["make", "test"]]


def test_propose_config_extracts_bare_json_substring_without_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_target()
    monkeypatch.setattr(explore_chat_repo, "provision", _writing_provision("package.json"))
    prose = (
        "Sure, here is my answer: "
        + json.dumps({"warmup_commands": [["npm", "ci"]]})
        + " Thanks!"
    )
    monkeypatch.setattr(ecp, "run_chat_turn", _fake_chat_turn(prose, {}))

    result = ecp.propose_config(_SLUG)
    assert result.proposed_config.warmup_commands == [["npm", "ci"]]


def test_propose_config_drops_conventions_and_posture_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_target()
    monkeypatch.setattr(explore_chat_repo, "provision", _writing_provision("package.json"))
    raw = json.dumps({
        "posture": "autonomous",
        "conventions": "# Some markdown a model invented",
        "gate_commands": [["echo", "hi"]],
    })
    monkeypatch.setattr(ecp, "run_chat_turn", _fake_chat_turn(raw, {}))

    result = ecp.propose_config(_SLUG)

    assert result.proposed_config.posture.value == "observe"
    assert result.proposed_config.gate_commands == [["echo", "hi"]]


def test_propose_config_preserves_registered_forge_authoritative_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = RepoIdentity(owner=_OWNER, repo=_REPO)
    forge = ForgeConfig(
        repo=repo,
        credential_env_name=_CREDENTIAL_ENV,
        authoritative_checks=frozenset({"ci/tests"}),
    )
    engine_config_mod.register(EngineConfig(repo=repo, forge=forge), validate_env=False)
    monkeypatch.setattr(explore_chat_repo, "provision", _writing_provision("package.json"))
    monkeypatch.setattr(ecp, "run_chat_turn", _fake_chat_turn(json.dumps({}), {}))

    result = ecp.propose_config(_SLUG)

    assert result.proposed_config.forge is not None
    assert result.proposed_config.forge.authoritative_checks == frozenset({"ci/tests"})
    assert result.proposed_config.forge.credential_env_name == _CREDENTIAL_ENV


def test_propose_config_happy_path_recommends_preset_and_produces_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_target()
    monkeypatch.setattr(explore_chat_repo, "provision", _writing_provision("package.json"))
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        ecp,
        "run_chat_turn",
        _fake_chat_turn(json.dumps({"gate_commands": [["npm", "test"]]}), captured),
    )

    result = ecp.propose_config(_SLUG)

    assert result.base_preset == "node"
    assert result.detected_stacks == ("node",)
    assert result.additional_stacks == ()
    assert result.proposed_config.gate_commands == [["npm", "test"]]
    assert result.validation.status == "pass"

    by_field = {d.field: d for d in result.diff}
    assert by_field["gate_commands"].changed is True
    assert by_field["warmup_commands"].changed is False
    assert set(by_field) == ecp._PROPOSABLE_FIELDS  # noqa: SLF001

    assert "'node'" in captured["system_prompt"]
    assert "none" in captured["system_prompt"]
    assert captured["allowed_tools"] is None
    assert captured["resume"] is False


def test_propose_config_cache_env_comes_from_preset_baseline_not_model_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_target()
    monkeypatch.setattr(explore_chat_repo, "provision", _writing_provision("package.json"))
    monkeypatch.setattr(
        ecp,
        "run_chat_turn",
        _fake_chat_turn(json.dumps({"cache_env": {"NUGET_PACKAGES": "evil"}}), {}),
    )

    result = ecp.propose_config(_SLUG)

    assert result.base_preset == "node"
    assert result.proposed_config.cache_env == engine_presets.node_preset().cache_env
    assert "cache_env" not in ecp._PROPOSABLE_FIELDS  # noqa: SLF001


def test_propose_config_mixed_stack_flags_additional_stacks_in_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_target()
    monkeypatch.setattr(
        explore_chat_repo, "provision", _writing_provision("package.json", "pyproject.toml")
    )
    captured: dict[str, Any] = {}
    monkeypatch.setattr(ecp, "run_chat_turn", _fake_chat_turn(json.dumps({}), captured))

    result = ecp.propose_config(_SLUG)

    assert result.base_preset == "node"
    assert result.additional_stacks == ("python",)
    assert "python" in captured["system_prompt"]


def test_propose_config_base_preset_override_takes_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_target()
    monkeypatch.setattr(explore_chat_repo, "provision", _writing_provision("package.json"))
    monkeypatch.setattr(ecp, "run_chat_turn", _fake_chat_turn(json.dumps({}), {}))

    result = ecp.propose_config(_SLUG, base_preset="dotnet")

    assert result.base_preset == "dotnet"
    assert "Bash(dotnet:*)" in result.proposed_config.base_tools
    assert result.proposed_config.warmup_commands == engine_presets.dotnet_preset().warmup_commands


def test_propose_config_unknown_base_preset_override_aborts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_target()
    provision = _writing_provision("package.json")
    monkeypatch.setattr(explore_chat_repo, "provision", provision)
    monkeypatch.setattr(ecp, "run_chat_turn", _fail_if_called("run_chat_turn"))

    with pytest.raises(ecp.ProposalAbortedError, match="unknown base preset"):
        ecp.propose_config(_SLUG, base_preset="ruby")

    _engine_key, dest = provision.calls[0]
    assert not dest.exists()


def test_propose_config_removes_checkout_directory_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_target()
    provision = _writing_provision("package.json")
    monkeypatch.setattr(explore_chat_repo, "provision", provision)
    monkeypatch.setattr(ecp, "run_chat_turn", _fake_chat_turn(json.dumps({}), {}))

    ecp.propose_config(_SLUG)

    _engine_key, dest = provision.calls[0]
    assert not dest.exists()


def test_validate_draft_pass_for_default_engine_config() -> None:
    finding = ecp.validate_draft(default_engine_config())
    assert finding.status == "pass"
    assert finding.check == "flow_graph_and_posture_valid"


def test_validate_draft_fail_on_invalid_graph() -> None:
    from alan.engine_config_validation import FlowGraphPolicy

    bad_policy = FlowGraphPolicy(
        verifier_teams=frozenset({"verification"}),
        merge_teams=frozenset({"pr_merge"}),
        terminal_teams=frozenset({"deploy_verify"}),
        internal_only_teams=frozenset(),
        restricted_predecessors={},
        revisit_cap=3,
    )
    cfg = _target_cfg().model_copy(update={
        "transition_graph": {"implementation": ["pr_merge"], "pr_merge": []},
        "flow_policy": bad_policy,
    })
    finding = ecp.validate_draft(cfg)
    assert finding.status == "fail"


def test_extract_json_object_raises_when_nothing_parses() -> None:
    with pytest.raises(ecp.ProposalUnrepairableError, match="parseable JSON"):
        ecp._extract_json_object("no braces here at all")  # noqa: SLF001


def test_extract_json_object_fence_regex_does_not_over_capture_multiple_blocks() -> None:
    text = (
        "```json\n"
        + json.dumps({"note": "example"})
        + "\n```\n\nAnd the real answer:\n```json\n"
        + json.dumps({"gate_commands": [["make", "test"]]})
        + "\n```\n"
    )
    parsed = ecp._extract_json_object(text)  # noqa: SLF001
    assert parsed == {"note": "example"}


def test_extract_json_object_fence_regex_handles_nested_braces() -> None:
    payload = {
        "team_tools": {"implementation": ["Bash(npm:*)"], "verification": ["Bash(pytest:*)"]},
        "gate_commands": [["npm", "test"]],
    }
    text = "Here:\n```json\n" + json.dumps(payload, indent=2) + "\n```\nend"
    parsed = ecp._extract_json_object(text)  # noqa: SLF001
    assert parsed == payload
