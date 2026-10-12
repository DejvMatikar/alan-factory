from __future__ import annotations

from pathlib import Path

from alan import merge_gate as merge_brief
from alan import merge_recovery
from alan.park import Blocker
from alan.teams import implementation_fix as fix_mod
from alan.workspace import create


def test_merge_gate_remediation_instructions_reach_composed_user_text(tmp_path: Path) -> None:
    ws = create("fix-remediation-brief", "d", root=tmp_path / "ws")
    blockers = (
        Blocker(kind="failing_check", name="ci/test", detail="check failing: ci/test"),
    )
    instructions = merge_brief.render_remediation_instructions(
        merge_recovery.MergeBlockClass.GENUINE, "failing checks: ci/test", blockers,
    )

    user_text = fix_mod._build_user_text(ws, None, instructions, None)  # noqa: SLF001

    assert "## User dispatch instructions" in user_text
    assert "genuine" in user_text
    assert "failing checks: ci/test" in user_text
    assert "check failing: ci/test" in user_text


def test_no_instructions_omits_dispatch_instructions_header(tmp_path: Path) -> None:
    ws = create("fix-remediation-none", "d", root=tmp_path / "ws")
    user_text = fix_mod._build_user_text(ws, None, None, None)  # noqa: SLF001
    assert "## User dispatch instructions" not in user_text
