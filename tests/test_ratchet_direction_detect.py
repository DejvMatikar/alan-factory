from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from alan import base_ref
from alan.teams import _ratchet_direction


def _git(repo: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603,S607
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "README.md").write_text("hello\n")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "initial")
    return root


def _seed_base(repo: Path, rel: str, content: str) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", f"seed {rel}")


def _write_head(repo: Path, rel: str, content: str) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def test_detect_returns_none_when_base_unresolved(
    monkeypatch: pytest.MonkeyPatch, repo: Path,
) -> None:
    monkeypatch.setattr(base_ref, "resolve", lambda *_a, **_kw: None)
    assert _ratchet_direction.detect_ratchet_loosening(repo) is None


def test_detect_returns_empty_tuple_when_no_canonical_path_present(
    monkeypatch: pytest.MonkeyPatch, repo: Path,
) -> None:
    monkeypatch.setattr(base_ref, "resolve", lambda *_a, **_kw: "HEAD")
    assert _ratchet_direction.detect_ratchet_loosening(repo) == ()


def test_detect_skips_a_path_present_only_in_base(
    monkeypatch: pytest.MonkeyPatch, repo: Path,
) -> None:
    _seed_base(repo, "alan/web/.jscpd.json", json.dumps({"threshold": 1}))
    _git(repo, "rm", "-q", "alan/web/.jscpd.json")
    _git(repo, "commit", "-q", "-m", "drop jscpd config from head")
    monkeypatch.setattr(base_ref, "resolve", lambda *_a, **_kw: "HEAD~1")
    assert _ratchet_direction.detect_ratchet_loosening(repo) == ()


def test_detect_skips_a_path_present_only_in_head(
    monkeypatch: pytest.MonkeyPatch, repo: Path,
) -> None:
    monkeypatch.setattr(base_ref, "resolve", lambda *_a, **_kw: "HEAD")
    _write_head(repo, "alan/web/.jscpd.json", json.dumps({"threshold": 1}))
    assert _ratchet_direction.detect_ratchet_loosening(repo) == ()


def test_detect_flags_a_flaky_baseline_addition_end_to_end(
    monkeypatch: pytest.MonkeyPatch, repo: Path,
) -> None:
    _seed_base(repo, "alan/web/e2e/flaky-baseline.json", "[]")
    monkeypatch.setattr(base_ref, "resolve", lambda *_a, **_kw: "HEAD")
    _write_head(repo, "alan/web/e2e/flaky-baseline.json", json.dumps(["a.spec.ts"]))
    findings = _ratchet_direction.detect_ratchet_loosening(repo)
    assert findings is not None
    assert any(f.path == "alan/web/e2e/flaky-baseline.json" for f in findings)


def test_detect_ignores_a_flaky_baseline_shrink_end_to_end(
    monkeypatch: pytest.MonkeyPatch, repo: Path,
) -> None:
    _seed_base(repo, "alan/web/e2e/flaky-baseline.json", json.dumps(["a.spec.ts"]))
    monkeypatch.setattr(base_ref, "resolve", lambda *_a, **_kw: "HEAD")
    _write_head(repo, "alan/web/e2e/flaky-baseline.json", "[]")
    assert _ratchet_direction.detect_ratchet_loosening(repo) == ()


def test_detect_flags_a_jscpd_threshold_raise_end_to_end(
    monkeypatch: pytest.MonkeyPatch, repo: Path,
) -> None:
    _seed_base(repo, "alan/web/.jscpd.json", json.dumps({"threshold": 1}))
    monkeypatch.setattr(base_ref, "resolve", lambda *_a, **_kw: "HEAD")
    _write_head(repo, "alan/web/.jscpd.json", json.dumps({"threshold": 2}))
    findings = _ratchet_direction.detect_ratchet_loosening(repo)
    assert findings is not None
    assert any(f.path == "alan/web/.jscpd.json" for f in findings)


def test_detect_multiple_paths_can_each_produce_a_finding(
    monkeypatch: pytest.MonkeyPatch, repo: Path,
) -> None:
    _seed_base(repo, "alan/web/.jscpd.json", json.dumps({"threshold": 1}))
    _seed_base(
        repo, "alan/web/e2e/stress-floor.json",
        json.dumps({"minExecutedTests": 840, "minSpecFiles": 75}),
    )
    monkeypatch.setattr(base_ref, "resolve", lambda *_a, **_kw: "HEAD")
    _write_head(repo, "alan/web/.jscpd.json", json.dumps({"threshold": 3}))
    _write_head(
        repo, "alan/web/e2e/stress-floor.json",
        json.dumps({"minExecutedTests": 100, "minSpecFiles": 75}),
    )
    findings = _ratchet_direction.detect_ratchet_loosening(repo)
    assert findings is not None
    paths = {f.path for f in findings}
    assert paths == {"alan/web/.jscpd.json", "alan/web/e2e/stress-floor.json"}


def test_detect_flags_a_pin_raise_without_touching_the_baseline_json(
    monkeypatch: pytest.MonkeyPatch, repo: Path,
) -> None:
    pin_path = "alan/web/e2e/mobile-audit/baseline-high-water.ts"
    pin_content = (
        "export const MOBILE_AUDIT_BASELINE_HIGH_WATER: MobileAuditBaseline = {{\n"
        "  home: {{ overflow: {overflow}, tapTarget: 2, contrast: 0 }},\n"
        "}};\n"
    )
    _seed_base(repo, pin_path, pin_content.format(overflow=1))
    monkeypatch.setattr(base_ref, "resolve", lambda *_a, **_kw: "HEAD")
    _write_head(repo, pin_path, pin_content.format(overflow=2))
    findings = _ratchet_direction.detect_ratchet_loosening(repo)
    assert findings is not None
    assert any(f.path == pin_path for f in findings)


_CI_BUDGETS_PATH = ".github/ci-duration-budgets.json"


def _budgets(**jobs: int) -> str:
    return json.dumps({"tests.yml": jobs})


def test_detect_flags_a_raised_ci_duration_budget_end_to_end(
    monkeypatch: pytest.MonkeyPatch, repo: Path,
) -> None:
    _seed_base(repo, _CI_BUDGETS_PATH, _budgets(backend=301, lint=50))
    monkeypatch.setattr(base_ref, "resolve", lambda *_a, **_kw: "HEAD")
    _write_head(repo, _CI_BUDGETS_PATH, _budgets(backend=400, lint=50))
    assert _ratchet_direction.detect_ratchet_loosening(repo) == (
        _ratchet_direction.Loosening(
            _CI_BUDGETS_PATH, "tests.yml.backend raised from 301 to 400",
        ),
    )


def test_detect_ignores_a_lowered_ci_duration_budget_end_to_end(
    monkeypatch: pytest.MonkeyPatch, repo: Path,
) -> None:
    _seed_base(repo, _CI_BUDGETS_PATH, _budgets(backend=301))
    monkeypatch.setattr(base_ref, "resolve", lambda *_a, **_kw: "HEAD")
    _write_head(repo, _CI_BUDGETS_PATH, _budgets(backend=250))
    assert _ratchet_direction.detect_ratchet_loosening(repo) == ()


def test_detect_ignores_a_new_ci_duration_budget_job_end_to_end(
    monkeypatch: pytest.MonkeyPatch, repo: Path,
) -> None:
    _seed_base(repo, _CI_BUDGETS_PATH, _budgets(backend=301))
    monkeypatch.setattr(base_ref, "resolve", lambda *_a, **_kw: "HEAD")
    _write_head(repo, _CI_BUDGETS_PATH, _budgets(backend=301, brand_new=900))
    assert _ratchet_direction.detect_ratchet_loosening(repo) == ()


def test_base_content_returns_none_for_unresolvable_base(repo: Path) -> None:
    assert _ratchet_direction.base_content(repo, "README.md", "nonexistent-ref") is None


def test_base_content_returns_none_for_missing_git_binary(
    monkeypatch: pytest.MonkeyPatch, repo: Path,
) -> None:
    def _raise(*_a: object, **_kw: object) -> object:
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", _raise)
    assert _ratchet_direction.base_content(repo, "README.md", "HEAD") is None


def test_base_content_returns_none_on_timeout(
    monkeypatch: pytest.MonkeyPatch, repo: Path,
) -> None:
    def _raise(*_a: object, **_kw: object) -> object:
        raise subprocess.TimeoutExpired(cmd="git", timeout=30)

    monkeypatch.setattr(subprocess, "run", _raise)
    assert _ratchet_direction.base_content(repo, "README.md", "HEAD") is None


def test_base_content_reads_the_committed_blob(repo: Path) -> None:
    assert _ratchet_direction.base_content(repo, "README.md", "HEAD") == "hello\n"
