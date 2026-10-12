from __future__ import annotations

from pathlib import Path

import pytest

from alan.engine_config import ForgeTarget, RepoIdentity
from alan.teams import _pr_body, _pr_flake_ledger
from alan.teams import _review_context as ctx_mod
from alan.teams import _verification_prompts as vp_mod
from alan.workspace import create

_FORGE = ForgeTarget(
    repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
    credential_env_name="ACME_WIDGET_GH_TOKEN",  # noqa: S106  # nosec B106
)


def test_pr_evidence_block_includes_description_and_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_body, "fetch_pr_body", lambda *_a, **_kw: "the description")
    monkeypatch.setattr(
        _pr_flake_ledger, "fetch_pr_diff_patch", lambda *_a, **_kw: "+added line"
    )
    block = ctx_mod.pr_evidence_block(1, forge_target=_FORGE)
    assert "## PR EVIDENCE" in block
    assert "the description" in block
    assert "+added line" in block
    assert "Do not attempt to fetch either yourself" in block


def test_pr_evidence_block_empty_description_renders_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_body, "fetch_pr_body", lambda *_a, **_kw: None)
    monkeypatch.setattr(_pr_flake_ledger, "fetch_pr_diff_patch", lambda *_a, **_kw: "+x")
    block = ctx_mod.pr_evidence_block(1, forge_target=_FORGE)
    assert "(empty)" in block


def test_pr_evidence_block_missing_diff_forbids_self_fetch_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_body, "fetch_pr_body", lambda *_a, **_kw: "desc")
    monkeypatch.setattr(_pr_flake_ledger, "fetch_pr_diff_patch", lambda *_a, **_kw: None)
    block = ctx_mod.pr_evidence_block(1, forge_target=_FORGE)
    assert "unavailable from the host fetch" in block
    assert "do not attempt to recover it yourself" in block
    assert "no `git fetch`" in block
    assert "never itself a finding" in block


def test_checkout_has_ui_surface_true_when_both_paths_present(tmp_path: Path) -> None:
    (tmp_path / "alan" / "web").mkdir(parents=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "DESIGN_SYSTEM.md").write_text("design")
    assert ctx_mod.checkout_has_ui_surface(tmp_path) is True


def test_checkout_has_ui_surface_false_when_web_dir_missing(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "DESIGN_SYSTEM.md").write_text("design")
    assert ctx_mod.checkout_has_ui_surface(tmp_path) is False


def test_checkout_has_ui_surface_false_when_design_doc_missing(tmp_path: Path) -> None:
    (tmp_path / "alan" / "web").mkdir(parents=True)
    assert ctx_mod.checkout_has_ui_surface(tmp_path) is False


def test_checkout_has_ui_surface_false_when_checkout_none() -> None:
    assert ctx_mod.checkout_has_ui_surface(None) is False


def test_ui_blocks_empty_without_ui_surface(tmp_path: Path) -> None:
    assert ctx_mod.ui_blocks(tmp_path) == ""


def test_ui_blocks_returns_both_constants_verbatim_with_ui_surface(tmp_path: Path) -> None:
    (tmp_path / "alan" / "web").mkdir(parents=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "DESIGN_SYSTEM.md").write_text("design")
    assert ctx_mod.ui_blocks(tmp_path) == (
        vp_mod.UI_CONSISTENCY_BLOCK + vp_mod.RENDERED_SCREEN_BLOCK
    )


def test_targeted_test_scope_block_neutral_without_ui_surface(tmp_path: Path) -> None:
    assert ctx_mod.targeted_test_scope_block(tmp_path) == vp_mod.TARGETED_TEST_SCOPE_NEUTRAL


def test_targeted_test_scope_block_full_with_ui_surface(tmp_path: Path) -> None:
    (tmp_path / "alan" / "web").mkdir(parents=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "DESIGN_SYSTEM.md").write_text("design")
    assert ctx_mod.targeted_test_scope_block(tmp_path) == vp_mod.TARGETED_TEST_SCOPE_BLOCK


def test_diff_reviewer_prefix_includes_evidence_before_ui_and_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from alan.teams import _ratchet_direction

    monkeypatch.setattr(_ratchet_direction, "detect_ratchet_loosening", lambda *_a, **_kw: ())
    monkeypatch.setattr(_pr_body, "fetch_pr_body", lambda *_a, **_kw: "desc")
    monkeypatch.setattr(_pr_flake_ledger, "fetch_pr_diff_patch", lambda *_a, **_kw: "+x")
    (tmp_path / "alan" / "web").mkdir(parents=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "DESIGN_SYSTEM.md").write_text("design")

    workspace = create("verify-task", "do verification", root=tmp_path / "ws")
    block = ctx_mod.diff_reviewer_prefix(1, workspace, "task-x", checkout=tmp_path)

    assert block is not None
    evidence_idx = block.index("## PR EVIDENCE")
    ui_idx = block.index("UI-CONSISTENCY PASS")
    scope_idx = block.index("## TEST SCOPE")
    assert evidence_idx < ui_idx < scope_idx
    assert "CRITICAL is reserved for a genuine WCAG-AA accessibility regression" in block


def test_diff_reviewer_prefix_omits_ui_blocks_for_non_ui_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from alan.teams import _ratchet_direction

    monkeypatch.setattr(_ratchet_direction, "detect_ratchet_loosening", lambda *_a, **_kw: ())
    monkeypatch.setattr(_pr_body, "fetch_pr_body", lambda *_a, **_kw: None)
    monkeypatch.setattr(_pr_flake_ledger, "fetch_pr_diff_patch", lambda *_a, **_kw: None)

    workspace = create("verify-task", "do verification", root=tmp_path / "ws")
    block = ctx_mod.diff_reviewer_prefix(1, workspace, "task-x", checkout=tmp_path)

    assert block is not None
    assert "UI-CONSISTENCY PASS" not in block
    assert "DESIGN_SYSTEM.md" not in block
    assert vp_mod.TARGETED_TEST_SCOPE_NEUTRAL in block
