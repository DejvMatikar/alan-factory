from __future__ import annotations

import re
from pathlib import Path

import pytest

from alan import default_profile
from alan.default_profile import DefaultProfile, Resolved, Source
from alan.engine_config import EngineConfig, Posture, PromptOverride, default_engine_config
from alan.prompt_templates import (
    PLACEHOLDER_ENVIRONMENT,
    PLACEHOLDER_REPO_GUIDANCE,
    PLACEHOLDER_SUCCESS,
)
from alan.teams import _native_capture, _pr_body
from alan.teams import brainstorming as _brainstorming
from alan.teams import implementation as _impl
from alan.teams import implementation_fix as _impl_fix
from alan.teams import opportunity_scout as _opportunity_scout
from alan.teams import ui_review as _ui_review
from alan.teams._orchestrated import assemble_worker_prompt

_BUNDLE_ID = "com.example.app"


def _ios_cfg() -> EngineConfig:
    return default_engine_config().model_copy(update={"preset": "ios"})


def _resolve_profile_with(monkeypatch: pytest.MonkeyPatch, bundle_id: str | None) -> None:
    profile = DefaultProfile(
        owner="acme", repo="widget", posture=Posture.observe, ios_bundle_id=bundle_id
    )
    resolved = Resolved(profile, Source.profile)
    monkeypatch.setattr(default_profile, "resolved", lambda: resolved)


def _launch_step(recipe: str) -> str:
    return recipe.split("7. INSTALL + LAUNCH")[1].split("8. CAPTURE")[0]


def test_native_capture_block_returns_recipe_for_ios_implementation() -> None:
    assert (
        _native_capture.native_capture_block(_ios_cfg(), "implementation")
        == _native_capture.NATIVE_CAPTURE_RECIPE
    )


def test_native_capture_block_returns_recipe_for_ios_implementation_fix() -> None:
    assert (
        _native_capture.native_capture_block(_ios_cfg(), "implementation_fix")
        == _native_capture.NATIVE_CAPTURE_RECIPE
    )


def test_native_capture_block_returns_unavailable_for_ios_ui_review() -> None:
    assert (
        _native_capture.native_capture_block(_ios_cfg(), "ui_review")
        == _native_capture.NATIVE_CAPTURE_UNAVAILABLE
    )


def test_native_capture_block_empty_for_default_engine_every_team() -> None:
    cfg = default_engine_config()
    for team in ("implementation", "implementation_fix", "ui_review", "brainstorming"):
        assert _native_capture.native_capture_block(cfg, team) == ""


def test_native_capture_block_empty_for_non_ios_preset() -> None:
    cfg = default_engine_config().model_copy(update={"preset": "web"})
    assert _native_capture.native_capture_block(cfg, "implementation") == ""
    assert _native_capture.native_capture_block(cfg, "ui_review") == ""


def test_native_capture_block_empty_for_ios_unrelated_teams() -> None:
    cfg = _ios_cfg()
    assert _native_capture.native_capture_block(cfg, "brainstorming") == ""
    assert _native_capture.native_capture_block(cfg, "opportunity_scout") == ""
    assert _native_capture.native_capture_block(cfg, "verification") == ""


def test_default_engine_implementation_prompt_is_byte_for_byte_unchanged(
    tmp_path: Path,
) -> None:
    cfg = default_engine_config()
    rendered = _impl.render_system_prompt(cfg, tmp_path)
    expected = _impl._SYSTEM_PROMPT + "\n\n" + _pr_body.PR_BODY_BLOCK_INSTRUCTIONS  # noqa: SLF001
    assert rendered == expected


def test_default_engine_implementation_fix_prompt_is_byte_for_byte_unchanged(
    tmp_path: Path,
) -> None:
    cfg = default_engine_config()
    rendered = _impl_fix.render_system_prompt(cfg, tmp_path)
    expected = (
        _impl_fix._SYSTEM_PROMPT + "\n\n" + _pr_body.PR_BODY_BLOCK_INSTRUCTIONS  # noqa: SLF001
    )
    assert rendered == expected


def test_recipe_survives_composed_prompt_override_for_ios_implementation(
    tmp_path: Path,
) -> None:
    override_text = (
        f"Custom implementer preamble.\n\n{PLACEHOLDER_ENVIRONMENT}\n\n"
        f"{PLACEHOLDER_SUCCESS}\n\n{PLACEHOLDER_REPO_GUIDANCE}"
    )
    cfg = _ios_cfg().model_copy(
        update={
            "prompt_overrides": {
                "implementation/implementer": PromptOverride(
                    text=override_text, base_fingerprint="deadbeef"
                )
            }
        }
    )
    rendered = _impl.render_system_prompt(cfg, tmp_path)
    assert _native_capture.NATIVE_CAPTURE_RECIPE in rendered
    assert "Custom implementer preamble." in rendered


def test_brainstorming_worker_prompt_unchanged_on_ios_preset() -> None:
    cfg = _ios_cfg()
    extra = _native_capture.native_capture_block(cfg, _brainstorming.TEAM.name)
    assert extra == ""
    with_extra = assemble_worker_prompt(
        _brainstorming.TEAM.worker_system_template, "deep_diver", extra_block=extra
    )
    without_extra = assemble_worker_prompt(
        _brainstorming.TEAM.worker_system_template, "deep_diver"
    )
    assert with_extra == without_extra


def test_opportunity_scout_worker_prompt_unchanged_on_ios_preset() -> None:
    cfg = _ios_cfg()
    extra = _native_capture.native_capture_block(cfg, _opportunity_scout.TEAM.name)
    assert extra == ""
    with_extra = assemble_worker_prompt(
        _opportunity_scout.TEAM.worker_system_template, "scout", extra_block=extra
    )
    without_extra = assemble_worker_prompt(
        _opportunity_scout.TEAM.worker_system_template, "scout"
    )
    assert with_extra == without_extra


def test_ui_review_worker_prompt_gains_unavailable_block_on_ios_preset() -> None:
    cfg = _ios_cfg()
    extra = _native_capture.native_capture_block(cfg, _ui_review.TEAM.name)
    assembled = assemble_worker_prompt(
        _ui_review.TEAM.worker_system_template, "ux_reviewer", extra_block=extra
    )
    assert _native_capture.NATIVE_CAPTURE_UNAVAILABLE in assembled


def test_ui_review_block_contains_constraint_not_the_executable_recipe() -> None:
    block = _native_capture.NATIVE_CAPTURE_UNAVAILABLE
    assert "unavailable" in block.lower()
    assert "xcrun simctl" not in block
    assert "DERIVED_DATA_PATH" not in block


def test_implementation_block_contains_recipe_and_no_web_capture_text() -> None:
    block = _native_capture.NATIVE_CAPTURE_RECIPE
    assert "xcrun simctl" in block
    assert "capture:screens" not in block
    assert "audit:mobile" not in block


def test_recipe_has_no_bare_currently_booted_shorthand() -> None:
    assert "currently booted" not in _native_capture.NATIVE_CAPTURE_RECIPE.lower()


def test_recipe_has_no_build_invocation() -> None:
    recipe = _native_capture.NATIVE_CAPTURE_RECIPE
    assert "xcodebuild build" not in recipe
    assert "tuist build" not in recipe
    assert "npm run build" not in recipe


def test_recipe_names_the_configured_bundle_identifier_in_the_launch_step() -> None:
    recipe = _native_capture.native_capture_recipe(_BUNDLE_ID)
    assert f'launch "$UDID" {_BUNDLE_ID}`' in recipe
    assert f"bundle identifier is `{_BUNDLE_ID}`" in recipe
    assert "PlistBuddy" not in recipe


def test_recipe_without_a_bundle_identifier_names_none() -> None:
    recipe = _native_capture.native_capture_recipe(None)
    assert re.search(r"\bcom\.[A-Za-z0-9-]+\.", recipe) is None
    assert _BUNDLE_ID not in recipe
    assert recipe == _native_capture.NATIVE_CAPTURE_RECIPE


def test_recipe_without_a_bundle_identifier_reads_it_from_the_built_app() -> None:
    launch = _launch_step(_native_capture.native_capture_recipe(None))
    assert "/usr/libexec/PlistBuddy" in launch
    assert "CFBundleIdentifier" in launch
    assert '"$APP/Info.plist"' in launch
    assert 'launch "$UDID" "$BUNDLE_ID"' in launch


def test_recipe_without_a_bundle_identifier_degrades_on_denial_or_an_empty_result() -> None:
    launch = _launch_step(_native_capture.native_capture_recipe(None))
    assert "empty" in launch
    assert "denied by your tool sandbox" in launch
    assert f"`{_native_capture._DEGRADE_LINE}`" in launch  # noqa: SLF001
    assert launch.index("TEARDOWN") < launch.index(_native_capture._DEGRADE_LINE)  # noqa: SLF001


def test_recipes_differ_only_in_the_launch_step() -> None:
    with_id = _native_capture.native_capture_recipe(_BUNDLE_ID)
    without_id = _native_capture.native_capture_recipe(None)
    assert with_id != without_id
    assert with_id.split("7. INSTALL + LAUNCH")[0] == without_id.split("7. INSTALL + LAUNCH")[0]
    assert with_id.split("8. CAPTURE")[1] == without_id.split("8. CAPTURE")[1]


@pytest.mark.parametrize("team", ["implementation", "implementation_fix"])
def test_native_capture_block_renders_the_profile_bundle_identifier(
    team: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _resolve_profile_with(monkeypatch, _BUNDLE_ID)
    block = _native_capture.native_capture_block(_ios_cfg(), team)
    assert block == _native_capture.native_capture_recipe(_BUNDLE_ID)
    assert _BUNDLE_ID in block


@pytest.mark.parametrize("team", ["implementation", "implementation_fix"])
def test_native_capture_block_without_a_profile_bundle_identifier_renders_the_discovery_step(
    team: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _resolve_profile_with(monkeypatch, None)
    block = _native_capture.native_capture_block(_ios_cfg(), team)
    assert block == _native_capture.native_capture_recipe(None)
    assert "CFBundleIdentifier" in block


def test_ui_review_block_ignores_the_profile_bundle_identifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _resolve_profile_with(monkeypatch, _BUNDLE_ID)
    block = _native_capture.native_capture_block(_ios_cfg(), "ui_review")
    assert block == _native_capture.NATIVE_CAPTURE_UNAVAILABLE
    assert _BUNDLE_ID not in block


def test_recipe_states_freshness_refusal_and_teardown() -> None:
    recipe = _native_capture.NATIVE_CAPTURE_RECIPE
    assert "FRESHNESS REFUSAL" in recipe
    assert "TEARDOWN" in recipe
    assert "stat -f %m" in recipe


def test_degrade_line_and_unavailable_statement_are_textually_distinct() -> None:
    degrade = _native_capture._DEGRADE_LINE  # noqa: SLF001
    unavailable = _native_capture.NATIVE_CAPTURE_UNAVAILABLE
    assert degrade not in unavailable
    assert unavailable not in degrade


def test_team_name_literals_match_the_real_team_names() -> None:
    assert _native_capture._RECIPE_TEAMS == {  # noqa: SLF001
        _impl._TEAM_NAME,  # noqa: SLF001
        _impl_fix._TEAM_NAME,  # noqa: SLF001
    }
    assert _native_capture._UI_REVIEW_TEAM == _ui_review.TEAM.name  # noqa: SLF001
