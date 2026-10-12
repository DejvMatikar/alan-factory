from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from alan.drift_gate import (
    _DOC_PATTERNS,
    LOAD_BEARING_FILES,
    BaseUnresolvedError,
    _is_doc_target,
    _main,
    changed_paths,
    doc_target_hint,
    drift_gate_violated,
)


def test_no_load_bearing_file_passes() -> None:
    assert drift_gate_violated(["alan/foo.py", "README.md"]) is False


def test_empty_paths_passes() -> None:
    assert drift_gate_violated([]) is False


def test_load_bearing_without_doc_fails() -> None:
    assert drift_gate_violated(["alan/server.py"]) is True


def test_load_bearing_with_claude_md_passes() -> None:
    assert drift_gate_violated(["alan/server.py", "CLAUDE.md"]) is False


def test_load_bearing_with_agentic_guide_passes() -> None:
    assert drift_gate_violated(["pyproject.toml", "docs/AGENTIC_CODING_GUIDE.md"]) is False


def test_load_bearing_with_architecture_doc_passes() -> None:
    assert drift_gate_violated(["alan/claude_cli.py", "docs/architecture/foo.md"]) is False


def test_load_bearing_with_nested_architecture_doc_passes() -> None:
    assert drift_gate_violated(["alan/__main__.py", "docs/architecture/sub/bar.md"]) is False


def test_all_load_bearing_files_individually_fail_without_doc() -> None:
    for f in LOAD_BEARING_FILES:
        assert drift_gate_violated([f]) is True, f"Expected violation for {f}"


def test_routes_infra_is_load_bearing() -> None:
    assert drift_gate_violated(["alan/routes/infra.py"]) is True


def test_routes_deps_is_load_bearing() -> None:
    assert drift_gate_violated(["alan/routes/_deps.py"]) is True


def test_is_doc_target_claude_md() -> None:
    assert _is_doc_target("CLAUDE.md") is True


def test_is_doc_target_agentic_guide() -> None:
    assert _is_doc_target("docs/AGENTIC_CODING_GUIDE.md") is True


def test_is_doc_target_architecture_file() -> None:
    assert _is_doc_target("docs/architecture/foo.md") is True


def test_is_doc_target_non_doc() -> None:
    assert _is_doc_target("alan/server.py") is False


def test_is_doc_target_partial_path_fails() -> None:
    assert _is_doc_target("other/CLAUDE.md") is False


def test_is_doc_target_matches_nested_architecture_file() -> None:
    assert _is_doc_target("docs/architecture/sub/bar.md") is True


def test_doc_target_hint_lists_architecture_docs_first() -> None:
    hint = doc_target_hint()
    assert hint.startswith("a path under `docs/architecture/`")


def test_changed_paths_uses_three_dot_semantics() -> None:
    mock_result = MagicMock(returncode=0, stdout="alan/server.py\nCLAUDE.md\n")
    with patch("alan.drift_gate.subprocess.run", return_value=mock_result) as mock_run:
        result = changed_paths("origin/main")
    assert result == ["alan/server.py", "CLAUDE.md"]
    cmd = mock_run.call_args[0][0]
    assert cmd[3] == "origin/main...HEAD"


def test_changed_paths_raises_on_nonzero_returncode() -> None:
    mock_result = MagicMock(returncode=128, stdout="", stderr="fatal: not a git repo")
    with patch("alan.drift_gate.subprocess.run", return_value=mock_result):
        with pytest.raises(RuntimeError, match="128"):
            changed_paths("origin/main")


def test_changed_paths_filters_empty_lines() -> None:
    mock_result = MagicMock(returncode=0, stdout="alan/server.py\n\nCLAUDE.md\n")
    with patch("alan.drift_gate.subprocess.run", return_value=mock_result):
        result = changed_paths("origin/main")
    assert result == ["alan/server.py", "CLAUDE.md"]


def test_changed_paths_uses_provided_base_ref() -> None:
    mock_result = MagicMock(returncode=0, stdout="")
    with patch("alan.drift_gate.subprocess.run", return_value=mock_result) as mock_run:
        changed_paths("main")
    cmd = mock_run.call_args[0][0]
    assert cmd[3] == "main...HEAD"


def test_changed_paths_rejects_unsafe_base_ref() -> None:
    with patch("alan.drift_gate.subprocess.run") as mock_run:
        with pytest.raises(ValueError, match="unsafe base ref"):
            changed_paths("--output=/tmp/x")
    mock_run.assert_not_called()


def test_changed_paths_accepts_sha() -> None:
    mock_result = MagicMock(returncode=0, stdout="")
    with patch("alan.drift_gate.subprocess.run", return_value=mock_result) as mock_run:
        changed_paths("a" * 40)
    cmd = mock_run.call_args[0][0]
    assert cmd[3] == "a" * 40 + "...HEAD"


def test_changed_paths_resolves_base_when_none() -> None:
    mock_result = MagicMock(returncode=0, stdout="")
    with patch("alan.drift_gate._base_ref.resolve", return_value="origin/main"):
        with patch("alan.drift_gate.subprocess.run", return_value=mock_result) as mock_run:
            changed_paths(None)
    cmd = mock_run.call_args[0][0]
    assert cmd[3] == "origin/main...HEAD"


def test_changed_paths_raises_base_unresolved_when_resolve_fails() -> None:
    with patch("alan.drift_gate._base_ref.resolve", return_value=None):
        with pytest.raises(BaseUnresolvedError):
            changed_paths(None)


def test_changed_paths_defaults_cwd_to_process_cwd() -> None:
    mock_result = MagicMock(returncode=0, stdout="")
    with patch("alan.drift_gate.subprocess.run", return_value=mock_result) as mock_run:
        changed_paths("origin/main")
    assert mock_run.call_args.kwargs["cwd"] == str(Path.cwd())


def test_changed_paths_uses_provided_cwd_for_diff(tmp_path: Path) -> None:
    mock_result = MagicMock(returncode=0, stdout="")
    worktree = tmp_path / "some-worktree"
    with patch("alan.drift_gate.subprocess.run", return_value=mock_result) as mock_run:
        changed_paths("origin/main", cwd=worktree)
    assert mock_run.call_args.kwargs["cwd"] == str(worktree)


def test_changed_paths_resolves_base_using_provided_cwd(tmp_path: Path) -> None:
    mock_result = MagicMock(returncode=0, stdout="")
    worktree = tmp_path / "some-worktree"
    with patch(
        "alan.drift_gate._base_ref.resolve", return_value="origin/main"
    ) as mock_resolve:
        with patch("alan.drift_gate.subprocess.run", return_value=mock_result):
            changed_paths(None, cwd=worktree)
    mock_resolve.assert_called_once_with(worktree)


def test_main_exits_nonzero_when_drift_gate_violated() -> None:
    with patch("alan.drift_gate.changed_paths", return_value=["alan/server.py"]):
        with pytest.raises(SystemExit) as exc_info:
            _main(["--check"])
    assert exc_info.value.code == 1


def test_main_succeeds_when_not_violated() -> None:
    with patch("alan.drift_gate.changed_paths", return_value=["alan/foo.py"]):
        _main(["--check"])


def test_main_uses_base_ref_argument() -> None:
    with patch("alan.drift_gate.changed_paths", return_value=[]) as mock_cp:
        _main(["--check", "--base-ref", "origin/develop"])
    mock_cp.assert_called_once_with("origin/develop")


def test_main_default_base_ref_is_none_resolve_at_use() -> None:
    with patch("alan.drift_gate.changed_paths", return_value=[]) as mock_cp:
        _main([])
    mock_cp.assert_called_once_with(None)


def test_main_exits_nonzero_when_git_fails() -> None:
    mock_result = MagicMock(returncode=128, stdout="", stderr="fatal: not a git repo")
    with patch("alan.drift_gate.subprocess.run", return_value=mock_result):
        with pytest.raises(SystemExit) as exc_info:
            _main(["--base-ref", "origin/main"])
    assert exc_info.value.code == 1


def test_main_exits_zero_when_base_unresolved(capsys: pytest.CaptureFixture[str]) -> None:
    with patch(
        "alan.drift_gate.changed_paths",
        side_effect=BaseUnresolvedError("could not resolve origin/main"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            _main(["--check"])
    assert exc_info.value.code == 0
    assert "base unresolved" in capsys.readouterr().err


def test_main_exits_nonzero_on_unsafe_base_ref() -> None:
    with pytest.raises(SystemExit) as exc_info:
        _main(["--check", "--base-ref=--output=x"])
    assert exc_info.value.code == 1


def test_main_does_not_exit_without_check_flag_even_if_violated() -> None:
    with patch("alan.drift_gate.changed_paths", return_value=["alan/server.py"]):
        _main([])


_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEPLOY_WORKFLOW = ".github/workflows/ci-deploy.yml"


def test_doc_target_hint_covers_all_patterns() -> None:
    hint = doc_target_hint()
    assert "CLAUDE.md" in hint
    assert "docs/AGENTIC_CODING_GUIDE.md" in hint
    assert "docs/architecture/" in hint
    assert "or" in hint


def _expected_loadbearing(root: Path) -> frozenset[str]:
    if (root / _DEPLOY_WORKFLOW).is_file():
        return LOAD_BEARING_FILES
    return LOAD_BEARING_FILES - {_DEPLOY_WORKFLOW}


def _mirror_mismatch(root: Path) -> str | None:
    workflow = yaml.safe_load((root / ".github" / "workflows" / "tests.yml").read_text())
    drift_job = workflow["jobs"]["drift-gate"]
    filter_step = next(s for s in drift_job["steps"] if s.get("id") == "filter")
    filters = yaml.safe_load(filter_step["with"]["filters"])
    expected = _expected_loadbearing(root)
    if frozenset(filters["loadbearing"]) != expected:
        return f"loadbearing filter {sorted(filters['loadbearing'])} != {sorted(expected)}"
    if frozenset(filters["docs"]) != frozenset(_DOC_PATTERNS):
        return f"docs filter {sorted(filters['docs'])} != {sorted(_DOC_PATTERNS)}"
    return None


def _write_workflow(root: Path, loadbearing: Iterable[str], docs: Iterable[str]) -> None:
    filters = yaml.safe_dump({"loadbearing": sorted(loadbearing), "docs": sorted(docs)})
    workflow = {"jobs": {"drift-gate": {"steps": [{"id": "filter", "with": {"filters": filters}}]}}}
    target = root / ".github" / "workflows" / "tests.yml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(workflow), encoding="utf-8")


def _add_deploy_workflow(root: Path) -> None:
    target = root / _DEPLOY_WORKFLOW
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("name: deploy\n", encoding="utf-8")


def test_drift_gate_mirrors_tests_yml() -> None:
    assert _mirror_mismatch(_REPO_ROOT) is None


def test_mirror_expects_deploy_workflow_when_present(tmp_path: Path) -> None:
    _add_deploy_workflow(tmp_path)
    _write_workflow(tmp_path, LOAD_BEARING_FILES, _DOC_PATTERNS)
    assert _expected_loadbearing(tmp_path) == LOAD_BEARING_FILES
    assert _mirror_mismatch(tmp_path) is None


def test_mirror_omits_deploy_workflow_when_absent(tmp_path: Path) -> None:
    _write_workflow(tmp_path, LOAD_BEARING_FILES - {_DEPLOY_WORKFLOW}, _DOC_PATTERNS)
    assert _expected_loadbearing(tmp_path) == LOAD_BEARING_FILES - {_DEPLOY_WORKFLOW}
    assert _mirror_mismatch(tmp_path) is None


def test_mirror_flags_deploy_workflow_listed_when_absent(tmp_path: Path) -> None:
    _write_workflow(tmp_path, LOAD_BEARING_FILES, _DOC_PATTERNS)
    mismatch = _mirror_mismatch(tmp_path)
    assert mismatch is not None
    assert "loadbearing" in mismatch


def test_mirror_flags_deploy_workflow_missing_when_present(tmp_path: Path) -> None:
    _add_deploy_workflow(tmp_path)
    _write_workflow(tmp_path, LOAD_BEARING_FILES - {_DEPLOY_WORKFLOW}, _DOC_PATTERNS)
    mismatch = _mirror_mismatch(tmp_path)
    assert mismatch is not None
    assert "loadbearing" in mismatch


@pytest.mark.parametrize("deploy_present", [True, False])
def test_mirror_flags_other_path_missing(tmp_path: Path, deploy_present: bool) -> None:
    if deploy_present:
        _add_deploy_workflow(tmp_path)
    listed = _expected_loadbearing(tmp_path) - {"pyproject.toml"}
    _write_workflow(tmp_path, listed, _DOC_PATTERNS)
    mismatch = _mirror_mismatch(tmp_path)
    assert mismatch is not None
    assert "loadbearing" in mismatch


def test_mirror_flags_docs_divergence(tmp_path: Path) -> None:
    _write_workflow(tmp_path, LOAD_BEARING_FILES - {_DEPLOY_WORKFLOW}, _DOC_PATTERNS[:-1])
    mismatch = _mirror_mismatch(tmp_path)
    assert mismatch is not None
    assert mismatch.startswith("docs filter")
