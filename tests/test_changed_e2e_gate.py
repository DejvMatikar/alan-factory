from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from alan.teams import _changed_e2e


def _mk_checkout(tmp_path: Path, specs: list[str]) -> Path:
    for rel in specs:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("// spec\n", encoding="utf-8")
    return tmp_path


def test_changed_e2e_specs_filters_to_existing_runnable_specs(tmp_path: Path) -> None:
    checkout = _mk_checkout(
        tmp_path,
        ["alan/web/e2e/a.spec.ts", "alan/web/e2e/mobile-audit/m.spec.ts"],
    )
    diff = frozenset({
        "alan/web/e2e/a.spec.ts",
        "alan/web/e2e/mobile-audit/m.spec.ts",
        "alan/web/e2e/deleted.spec.ts",
        "alan/web/e2e/helpers.ts",
        "alan/web/src/App.tsx",
    })
    assert _changed_e2e.changed_e2e_specs(diff, checkout) == (
        "alan/web/e2e/a.spec.ts",
    )


def test_changed_e2e_specs_excludes_capture_prefix(tmp_path: Path) -> None:
    checkout = _mk_checkout(tmp_path, ["alan/web/e2e/capture/c.spec.ts"])
    diff = frozenset({"alan/web/e2e/capture/c.spec.ts"})
    assert _changed_e2e.changed_e2e_specs(diff, checkout) == ()


def test_changed_e2e_specs_excludes_desktop_audit_prefix(tmp_path: Path) -> None:
    checkout = _mk_checkout(tmp_path, ["alan/web/e2e/desktop-audit/desktop-audit.spec.ts"])
    diff = frozenset({"alan/web/e2e/desktop-audit/desktop-audit.spec.ts"})
    assert _changed_e2e.changed_e2e_specs(diff, checkout) == ()


def _fake_git(stdout: str, rc: int = 0) -> object:
    def run(cmd: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, rc, stdout=stdout, stderr="")

    return run


def test_local_diff_files_parses_names(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "alan.teams._changed_e2e.subprocess.run",
        _fake_git("a.py\nalan/web/e2e/x.spec.ts\n\n"),
    )
    assert _changed_e2e.local_diff_files(tmp_path, "origin/main") == frozenset(
        {"a.py", "alan/web/e2e/x.spec.ts"}
    )


def test_local_diff_files_nonzero_rc_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("alan.teams._changed_e2e.subprocess.run", _fake_git("", rc=1))
    assert _changed_e2e.local_diff_files(tmp_path, "origin/main") == frozenset()


def test_local_diff_files_exception_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _raise(*_a: object, **_kw: object) -> object:
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr("alan.teams._changed_e2e.subprocess.run", _raise)
    assert _changed_e2e.local_diff_files(tmp_path, "origin/main") == frozenset()


def test_removed_testids_extracts_and_skips_readded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch = (
        '-      <span data-testid={`repo-spend-${key}`}>\n'
        '-      <div data-testid="gone-token">\n'
        '+      <div data-testid="gone-token">\n'
    )
    monkeypatch.setattr("alan.teams._changed_e2e.subprocess.run", _fake_git(patch))
    assert _changed_e2e._removed_testids(tmp_path, "origin/main") == frozenset(
        {"repo-spend-"}
    )


def test_removed_testids_git_failure_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("alan.teams._changed_e2e.subprocess.run", _fake_git("", rc=128))
    assert _changed_e2e._removed_testids(tmp_path, "origin/main") == frozenset()


def test_removed_testids_git_exception_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _raise(*_a: object, **_kw: object) -> object:
        raise FileNotFoundError("git")

    monkeypatch.setattr("alan.teams._changed_e2e.subprocess.run", _raise)
    assert _changed_e2e._removed_testids(tmp_path, "origin/main") == frozenset()


def test_testid_referencing_specs_finds_asserting_spec(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    checkout = _mk_checkout(
        tmp_path, ["alan/web/e2e/hit.spec.ts", "alan/web/e2e/miss.spec.ts"]
    )
    (checkout / "alan/web/e2e/hit.spec.ts").write_text(
        "getByTestId(`repo-spend-acme`)\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        _changed_e2e, "_removed_testids", lambda _c, _b: frozenset({"repo-spend-acme"})
    )
    assert _changed_e2e.testid_referencing_specs(checkout, "origin/main") == (
        "alan/web/e2e/hit.spec.ts",
    )


def test_testid_referencing_specs_no_tokens_short_circuits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(_changed_e2e, "_removed_testids", lambda _c, _b: frozenset())
    assert _changed_e2e.testid_referencing_specs(tmp_path, "origin/main") == ()


def test_testid_referencing_specs_missing_e2e_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        _changed_e2e, "_removed_testids", lambda _c, _b: frozenset({"tok-x"})
    )
    assert _changed_e2e.testid_referencing_specs(tmp_path, "origin/main") == ()


def test_changed_e2e_scenario_none_on_empty() -> None:
    assert _changed_e2e.changed_e2e_scenario(()) is None


def test_changed_e2e_scenario_command_uses_web_relative_paths() -> None:
    scenario = _changed_e2e.changed_e2e_scenario(("alan/web/e2e/a.spec.ts",))
    assert scenario is not None
    assert scenario.id == "changed-e2e-specs"
    assert scenario.when == "npm --prefix alan/web run e2e -- e2e/a.spec.ts"


def test_changed_e2e_scenario_then_is_empty_not_unmatchable_prose() -> None:
    scenario = _changed_e2e.changed_e2e_scenario(("alan/web/e2e/a.spec.ts",))
    assert scenario is not None
    assert scenario.then == ""


def test_changed_e2e_scenario_caps_and_notes_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_CHANGED_E2E_MAX_SPECS", "2")
    specs = tuple(f"alan/web/e2e/s{i}.spec.ts" for i in range(4))
    scenario = _changed_e2e.changed_e2e_scenario(specs)
    assert scenario is not None
    assert scenario.when.count(".spec.ts") == 2
    assert "2 more spec file(s) over the cap" in scenario.description


def test_changed_e2e_timeout_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_CHANGED_E2E_TIMEOUT_S", raising=False)
    assert _changed_e2e.changed_e2e_timeout_s() == 900.0
    monkeypatch.setenv("ALAN_CHANGED_E2E_TIMEOUT_S", "60")
    assert _changed_e2e.changed_e2e_timeout_s() == 60.0


def test_build_changed_e2e_scenario_combines_and_dedupes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "alan.teams._changed_e2e.base_ref.resolve", lambda *_a, **_kw: "origin/main"
    )
    monkeypatch.setattr(
        _changed_e2e,
        "local_diff_files",
        lambda _c, _b: frozenset({"alan/web/e2e/a.spec.ts"}),
    )
    checkout = _mk_checkout(tmp_path, ["alan/web/e2e/a.spec.ts"])
    monkeypatch.setattr(
        _changed_e2e,
        "testid_referencing_specs",
        lambda _c, _b: ("alan/web/e2e/a.spec.ts", "alan/web/e2e/b.spec.ts"),
    )
    selection = _changed_e2e.build_changed_e2e_scenario(checkout)
    assert selection.base_unresolved is False
    assert selection.scenario is not None
    assert selection.scenario.when == (
        "npm --prefix alan/web run e2e -- e2e/a.spec.ts e2e/b.spec.ts"
    )


def test_build_changed_e2e_scenario_none_when_nothing_selected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "alan.teams._changed_e2e.base_ref.resolve", lambda *_a, **_kw: "origin/main"
    )
    monkeypatch.setattr(_changed_e2e, "local_diff_files", lambda _c, _b: frozenset())
    monkeypatch.setattr(_changed_e2e, "testid_referencing_specs", lambda _c, _b: ())
    selection = _changed_e2e.build_changed_e2e_scenario(tmp_path)
    assert selection.base_unresolved is False
    assert selection.scenario is None
