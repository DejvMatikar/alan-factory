from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest

from alan import default_profile
from alan import forge as forge_mod
from alan.default_profile import DefaultProfile, Resolved, Source
from alan.engine_config import DEFAULT_FORGE_TARGET, ForgeTarget, Posture, RepoIdentity
from alan.teams import _pr_merge_checks, _pr_ready
from alan.teams import pr_merge as pr_merge_mod
from alan.workspace import create
from tests.fakes.forge import FakeForge
from tests.forge_stubs import file_read, pr_read

_ENFORCED_SLUG = "example-org/example-repo"
_ENFORCED_FORGE = ForgeTarget(
    repo=RepoIdentity.from_slug(_ENFORCED_SLUG),
    credential_env_name="EXAMPLE_GH_TOKEN",  # noqa: S106  # nosec B106
)


def _codeowners_body(text: str) -> str:
    return json.dumps({"content": base64.b64encode(text.encode()).decode(), "encoding": "base64"})


def _profile_naming(*slugs: str) -> Resolved:
    profile = DefaultProfile(
        owner="acme", repo="widget", posture=Posture.observe, codeowners_repos=frozenset(slugs)
    )
    return Resolved(profile, Source.profile)


@pytest.fixture(autouse=True)
def _profile_enforcing_the_ownership_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    resolved = _profile_naming(_ENFORCED_SLUG)
    monkeypatch.setattr(default_profile, "resolved", lambda: resolved)


def test_classify_checks_branches() -> None:
    from alan.teams._pr_feedback import CommentInfo, CopilotThread, ThreadInfo
    pending = [{"name": "ci", "state": "PENDING"}]
    failure = [{"name": "ci", "state": "FAILURE"}]
    success = [{"name": "ci", "state": "SUCCESS"}]
    info_zero = ThreadInfo(copilot_threads=(), human_unresolved=0, other_bot_unresolved=0, total=0)
    info_three = ThreadInfo(
        copilot_threads=(), human_unresolved=3, other_bot_unresolved=0, total=3
    )
    info_ceiling = ThreadInfo(
        copilot_threads=(), human_unresolved=0, other_bot_unresolved=0, total=101
    )
    copilot_t = CopilotThread(thread_id="t1", body="fix this", database_id=9)
    info_copilot = ThreadInfo(
        copilot_threads=(copilot_t,), human_unresolved=0, other_bot_unresolved=0, total=1
    )
    cinfo_zero = CommentInfo(copilot_items=(), human_items=0, other_bot_items=0, total=0)
    assert _pr_merge_checks.classify_checks(pending, info_zero, cinfo_zero) == "pending"
    assert _pr_merge_checks.classify_checks(failure, info_zero, cinfo_zero) == "failed"
    assert _pr_merge_checks.classify_checks(success, info_three, cinfo_zero) == "unresolved"
    assert _pr_merge_checks.classify_checks(success, info_zero, cinfo_zero) == "ready"
    assert (
        _pr_merge_checks.classify_checks(success, info_ceiling, cinfo_zero)
        == "unresolved_ceiling"
    )
    assert (
        _pr_merge_checks.classify_checks(success, info_copilot, cinfo_zero)
        == "copilot_unresolved"
    )


def test_classify_checks_empty_list_is_pending_not_ready() -> None:
    from alan.teams._pr_feedback import CommentInfo, ThreadInfo
    info_zero = ThreadInfo(copilot_threads=(), human_unresolved=0, other_bot_unresolved=0, total=0)
    cinfo_zero = CommentInfo(copilot_items=(), human_items=0, other_bot_items=0, total=0)
    assert _pr_merge_checks.classify_checks([], info_zero, cinfo_zero) == "pending"


def test_classify_checks_human_precedence_over_copilot() -> None:
    from alan.teams._pr_feedback import CommentInfo, CopilotThread, ThreadInfo
    success = [{"name": "ci", "state": "SUCCESS"}]
    copilot_t = CopilotThread(thread_id="t1", body="fix this", database_id=9)
    info_mixed = ThreadInfo(
        copilot_threads=(copilot_t,), human_unresolved=1, other_bot_unresolved=0, total=2
    )
    cinfo = CommentInfo(copilot_items=(), human_items=0, other_bot_items=0, total=0)
    assert _pr_merge_checks.classify_checks(success, info_mixed, cinfo) == "unresolved"


def test_failed_check_names_robust() -> None:
    assert _pr_merge_checks.failed_check_names("not-a-list") == []
    assert _pr_merge_checks.failed_check_names([
        "not-a-dict",
        {"name": "a", "state": "FAILURE"},
        {"name": "b", "state": "SUCCESS"},
    ]) == ["a"]


def test_confirm_ready_kind_short_circuits_for_non_ready_kinds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    def _boom(*_a: object, **_kw: object) -> object:
        raise AssertionError("probe must not fetch readiness for non-ready kinds")

    monkeypatch.setattr(_pr_ready, "check_pr_merge_readiness", _boom)
    ws = create("probe-lazy", "d", root=tmp_path / "unused-probe-lazy")
    for kind in ("pending", "failed", "unresolved", "timeout", "error"):
        assert _pr_merge_checks.confirm_ready_kind(
            ws, 42, kind, forge_target=DEFAULT_FORGE_TARGET
        ) == (kind, "")


def test_confirm_ready_kind_demotes_on_absent_results(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    ws = create("confirm-absent", "d", root=tmp_path / "confirm-absent")
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness",
        lambda *_a, **_kw: _pr_ready.PrReadiness(
            ok=True, merge_state_status="CLEAN", review_decision="", check_count=0,
        ),
    )
    assert _pr_merge_checks.confirm_ready_kind(
        ws, 42, "ready", forge_target=DEFAULT_FORGE_TARGET
    ) == ("pending", "")


def test_confirm_ready_kind_stays_ready_on_populated_passing_rollup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    ws = create("confirm-ready", "d", root=tmp_path / "confirm-ready")
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness",
        lambda *_a, **_kw: _pr_ready.PrReadiness(
            ok=True, merge_state_status="CLEAN", review_decision="",
            check_count=1, passing_checks=("ci",), head_sha="deadbeef",
        ),
    )
    assert _pr_merge_checks.confirm_ready_kind(
        ws, 42, "ready", forge_target=DEFAULT_FORGE_TARGET
    ) == ("ready", "deadbeef")


def test_confirm_ready_kind_pending_when_head_sha_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    ws = create("confirm-no-head-sha", "d", root=tmp_path / "confirm-no-head-sha")
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness",
        lambda *_a, **_kw: _pr_ready.PrReadiness(
            ok=True, merge_state_status="CLEAN", review_decision="",
            check_count=1, passing_checks=("ci",), head_sha="",
        ),
    )
    assert _pr_merge_checks.confirm_ready_kind(
        ws, 42, "ready", forge_target=DEFAULT_FORGE_TARGET
    ) == ("pending", "")


def test_confirm_ready_kind_pending_on_gh_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    ws = create("confirm-ghfail", "d", root=tmp_path / "confirm-ghfail")
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness",
        lambda *_a, **_kw: _pr_ready.ReadinessUnavailable("gh unavailable"),
    )
    assert _pr_merge_checks.confirm_ready_kind(
        ws, 42, "ready", forge_target=DEFAULT_FORGE_TARGET
    ) == ("pending", "")


def test_confirm_ready_kind_pending_on_unmergeable_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    ws = create("confirm-blocked", "d", root=tmp_path / "confirm-blocked")
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness",
        lambda *_a, **_kw: _pr_ready.PrReadiness(
            ok=False, merge_state_status="BLOCKED", review_decision="",
            check_count=1, passing_checks=("ci",),
        ),
    )
    assert _pr_merge_checks.confirm_ready_kind(
        ws, 42, "ready", forge_target=DEFAULT_FORGE_TARGET
    ) == ("pending", "")


def test_resolve_checks_kind_refuses_owned_path_on_confirmed_route(
    tmp_path: Path, _active_fake_forge: FakeForge,
) -> None:
    from alan.teams._pr_feedback import CommentInfo, ThreadInfo

    readiness_fields: dict[str, object] = {
        "mergeStateStatus": "CLEAN",
        "reviewDecision": "",
        "statusCheckRollup": [{"name": "ci", "state": "SUCCESS"}],
        "headRefOid": "deadbeef",
        "headRefName": "fix/x",
        "baseRefName": "main",
    }
    _active_fake_forge.stub("read_pr", lambda *_a, **_kw: pr_read(readiness_fields))
    _active_fake_forge.stub(
        "repo_file", lambda *_a, **_kw: file_read(_codeowners_body("/scripts/ @owner\n")),
    )
    _active_fake_forge.stub("changed_files", lambda *_a, **_kw: frozenset({"scripts/deploy.py"}))
    ws = create("owned-confirmed", "d", root=tmp_path / "owned-confirmed")
    info_zero = ThreadInfo(copilot_threads=(), human_unresolved=0, other_bot_unresolved=0, total=0)
    cinfo_zero = CommentInfo(copilot_items=(), human_items=0, other_bot_items=0, total=0)
    success = [{"name": "ci", "state": "SUCCESS"}]
    result = _pr_merge_checks.resolve_checks_kind(
        ws, 42, success, info_zero, cinfo_zero, forge_target=_ENFORCED_FORGE,
    )
    assert result == ("codeowners_blocked", "", None)


def test_resolve_checks_kind_refuses_owned_path_on_tolerated_route(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _active_fake_forge: FakeForge,
) -> None:
    from alan.teams._pr_feedback import CommentInfo, ThreadInfo

    readiness = _pr_ready.PrReadiness(
        ok=True, merge_state_status="UNSTABLE", review_decision="",
        check_count=1, passing_checks=("ci",), head_sha="deadbeef",
    )
    monkeypatch.setattr(
        _pr_merge_checks, "_tolerated_kind", lambda *_a, **_kw: ("ready", "deadbeef", readiness),
    )
    _active_fake_forge.stub("read_pr", lambda *_a, **_kw: pr_read({"baseRefName": "main"}))
    _active_fake_forge.stub(
        "repo_file", lambda *_a, **_kw: file_read(_codeowners_body("/scripts/ @owner\n")),
    )
    _active_fake_forge.stub("changed_files", lambda *_a, **_kw: frozenset({"scripts/deploy.py"}))
    ws = create("owned-tolerated", "d", root=tmp_path / "owned-tolerated")
    info_zero = ThreadInfo(copilot_threads=(), human_unresolved=0, other_bot_unresolved=0, total=0)
    cinfo_zero = CommentInfo(copilot_items=(), human_items=0, other_bot_items=0, total=0)
    pending = [{"name": "ci", "state": "PENDING"}]
    result = _pr_merge_checks.resolve_checks_kind(
        ws, 42, pending, info_zero, cinfo_zero, forge_target=_ENFORCED_FORGE,
    )
    assert result == ("codeowners_blocked", "", None)


def test_resolve_checks_kind_unowned_tolerated_ready_keeps_readiness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _active_fake_forge: FakeForge,
) -> None:
    from alan.teams._pr_feedback import CommentInfo, ThreadInfo

    readiness = _pr_ready.PrReadiness(
        ok=True, merge_state_status="UNSTABLE", review_decision="",
        check_count=1, passing_checks=("ci",), head_sha="deadbeef",
    )
    tolerated = ("ready", "deadbeef", readiness)
    monkeypatch.setattr(_pr_merge_checks, "_tolerated_kind", lambda *_a, **_kw: tolerated)
    _active_fake_forge.stub("read_pr", lambda *_a, **_kw: pr_read({"baseRefName": "main"}))
    _active_fake_forge.stub(
        "repo_file", lambda *_a, **_kw: file_read(_codeowners_body("/scripts/ @owner\n")),
    )
    _active_fake_forge.stub("changed_files", lambda *_a, **_kw: frozenset({"docs/readme.md"}))
    ws = create("unowned-tolerated", "d", root=tmp_path / "unowned-tolerated")
    info_zero = ThreadInfo(copilot_threads=(), human_unresolved=0, other_bot_unresolved=0, total=0)
    cinfo_zero = CommentInfo(copilot_items=(), human_items=0, other_bot_items=0, total=0)
    pending = [{"name": "ci", "state": "PENDING"}]
    result = _pr_merge_checks.resolve_checks_kind(
        ws, 42, pending, info_zero, cinfo_zero, forge_target=_ENFORCED_FORGE,
    )
    assert result == tolerated
    assert result[2] is readiness


def test_ownership_gate_reads_codeowners_at_pr_base_ref(_active_fake_forge: FakeForge) -> None:
    _active_fake_forge.stub("read_pr", lambda *_a, **_kw: pr_read({"baseRefName": "release/1.0"}))
    _active_fake_forge.stub(
        "repo_file", lambda *_a, **_kw: file_read(_codeowners_body("README.md @owner\n")),
    )
    _active_fake_forge.stub("changed_files", lambda *_a, **_kw: frozenset({"README.md"}))
    _pr_merge_checks._apply_ownership_gate(  # noqa: SLF001
        ("ready", "sha", None), 7, forge_target=_ENFORCED_FORGE,
    )
    _name, _args, kwargs = next(c for c in _active_fake_forge.calls if c[0] == "repo_file")
    assert kwargs["ref"] == "release/1.0"


def test_ownership_gate_refuses_when_base_ref_unresolved(_active_fake_forge: FakeForge) -> None:
    _active_fake_forge.stub("read_pr", lambda *_a, **_kw: pr_read({}))
    result = _pr_merge_checks._apply_ownership_gate(  # noqa: SLF001
        ("ready", "sha", None), 7, forge_target=_ENFORCED_FORGE,
    )
    assert result == ("codeowners_blocked", "", None)
    assert not any(c[0] == "repo_file" for c in _active_fake_forge.calls)


def test_ownership_gate_refuses_when_codeowners_absent(_active_fake_forge: FakeForge) -> None:
    _active_fake_forge.stub("read_pr", lambda *_a, **_kw: pr_read({"baseRefName": "main"}))
    _active_fake_forge.stub(
        "repo_file", lambda *_a, **_kw: file_read(None, outcome="absent", returncode=1),
    )
    result = _pr_merge_checks._apply_ownership_gate(  # noqa: SLF001
        ("ready", "sha", None), 7, forge_target=_ENFORCED_FORGE,
    )
    assert result == ("codeowners_blocked", "", None)
    assert sum(1 for c in _active_fake_forge.calls if c[0] == "repo_file") == 1


def test_ownership_gate_retries_transient_read_then_refuses(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    _active_fake_forge.stub("read_pr", lambda *_a, **_kw: pr_read({"baseRefName": "main"}))
    _active_fake_forge.stub(
        "repo_file",
        lambda *_a, **_kw: file_read(
            None, outcome="failed", returncode=1, stderr="connection reset",
        ),
    )
    result = _pr_merge_checks._apply_ownership_gate(  # noqa: SLF001
        ("ready", "sha", None), 7, forge_target=_ENFORCED_FORGE,
    )
    assert result == ("codeowners_blocked", "", None)
    assert sum(1 for c in _active_fake_forge.calls if c[0] == "repo_file") == 3
    assert sleeps == [_pr_merge_checks._OWNERSHIP_RETRY_INTERVAL_S] * 2  # noqa: SLF001


def test_ownership_gate_transient_then_ok_still_decides(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    attempts: list[int] = []

    def _repo_file(*_a: object, **_kw: object) -> object:
        attempts.append(1)
        if len(attempts) < 2:
            return file_read(None, outcome="failed", returncode=1, stderr="connection reset")
        return file_read(_codeowners_body("README.md @owner\n"))

    _active_fake_forge.stub("read_pr", lambda *_a, **_kw: pr_read({"baseRefName": "main"}))
    _active_fake_forge.stub("repo_file", _repo_file)
    _active_fake_forge.stub("changed_files", lambda *_a, **_kw: frozenset({"README.md"}))
    result = _pr_merge_checks._apply_ownership_gate(  # noqa: SLF001
        ("ready", "sha", None), 7, forge_target=_ENFORCED_FORGE,
    )
    assert result == ("codeowners_blocked", "", None)
    assert len(attempts) == 2


def test_ownership_gate_refuses_hard_failure_without_retry(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    def _boom(*_a: object, **_kw: object) -> None:
        raise AssertionError("must not retry a hard failure")

    monkeypatch.setattr(time, "sleep", _boom)
    _active_fake_forge.stub("read_pr", lambda *_a, **_kw: pr_read({"baseRefName": "main"}))
    _active_fake_forge.stub(
        "repo_file",
        lambda *_a, **_kw: file_read(
            None, outcome="failed", returncode=1, stderr="bad credentials",
        ),
    )
    result = _pr_merge_checks._apply_ownership_gate(  # noqa: SLF001
        ("ready", "sha", None), 7, forge_target=_ENFORCED_FORGE,
    )
    assert result == ("codeowners_blocked", "", None)
    assert sum(1 for c in _active_fake_forge.calls if c[0] == "repo_file") == 1


def test_ownership_gate_refuses_undecodable_contents(_active_fake_forge: FakeForge) -> None:
    _active_fake_forge.stub("read_pr", lambda *_a, **_kw: pr_read({"baseRefName": "main"}))
    _active_fake_forge.stub("repo_file", lambda *_a, **_kw: file_read("not json"))
    result = _pr_merge_checks._apply_ownership_gate(  # noqa: SLF001
        ("ready", "sha", None), 7, forge_target=_ENFORCED_FORGE,
    )
    assert result == ("codeowners_blocked", "", None)


def test_ownership_gate_refuses_when_changed_files_unavailable(
    _active_fake_forge: FakeForge,
) -> None:
    _active_fake_forge.stub("read_pr", lambda *_a, **_kw: pr_read({"baseRefName": "main"}))
    _active_fake_forge.stub(
        "repo_file", lambda *_a, **_kw: file_read(_codeowners_body("README.md @owner\n")),
    )
    _active_fake_forge.stub("changed_files", lambda *_a, **_kw: None)
    result = _pr_merge_checks._apply_ownership_gate(  # noqa: SLF001
        ("ready", "sha", None), 7, forge_target=_ENFORCED_FORGE,
    )
    assert result == ("codeowners_blocked", "", None)


def test_ownership_gate_refuses_when_changed_files_raises_os_error(
    _active_fake_forge: FakeForge,
) -> None:
    _active_fake_forge.stub("read_pr", lambda *_a, **_kw: pr_read({"baseRefName": "main"}))
    _active_fake_forge.stub(
        "repo_file", lambda *_a, **_kw: file_read(_codeowners_body("README.md @owner\n")),
    )

    def _raise_permission_denied(*_a: object, **_kw: object) -> object:
        raise PermissionError("denied")

    _active_fake_forge.stub("changed_files", _raise_permission_denied)
    result = _pr_merge_checks._apply_ownership_gate(  # noqa: SLF001
        ("ready", "sha", None), 7, forge_target=_ENFORCED_FORGE,
    )
    assert result == ("codeowners_blocked", "", None)


def test_ownership_gate_refuses_unsupported_syntax_as_distinct_kind(
    _active_fake_forge: FakeForge,
) -> None:
    _active_fake_forge.stub("read_pr", lambda *_a, **_kw: pr_read({"baseRefName": "main"}))
    _active_fake_forge.stub(
        "repo_file", lambda *_a, **_kw: file_read(_codeowners_body("*.py @owner\n")),
    )
    _active_fake_forge.stub("changed_files", lambda *_a, **_kw: frozenset({"a.py"}))
    result = _pr_merge_checks._apply_ownership_gate(  # noqa: SLF001
        ("ready", "sha", None), 7, forge_target=_ENFORCED_FORGE,
    )
    assert result == ("codeowners_unsupported", "", None)


def test_ownership_gate_skips_non_opted_in_repo(_active_fake_forge: FakeForge) -> None:
    result = _pr_merge_checks._apply_ownership_gate(  # noqa: SLF001
        ("ready", "sha", None), 7, forge_target=DEFAULT_FORGE_TARGET,  # noqa: SLF001
    )
    assert result == ("ready", "sha", None)
    assert _active_fake_forge.calls == []


def test_ownership_gate_skips_non_ready_kind(_active_fake_forge: FakeForge) -> None:
    result = _pr_merge_checks._apply_ownership_gate(  # noqa: SLF001
        ("pending", "", None), 7, forge_target=_ENFORCED_FORGE,
    )
    assert result == ("pending", "", None)
    assert _active_fake_forge.calls == []


def test_ownership_gate_passes_every_repo_through_when_the_profile_names_none(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    resolved = _profile_naming()
    monkeypatch.setattr(default_profile, "resolved", lambda: resolved)
    for target in (_ENFORCED_FORGE, DEFAULT_FORGE_TARGET):
        result = _pr_merge_checks._apply_ownership_gate(  # noqa: SLF001
            ("ready", "sha", None), 7, forge_target=target,
        )
        assert result == ("ready", "sha", None)
    assert _active_fake_forge.calls == []


def test_ownership_gate_matches_the_profile_slug_case_insensitively(
    _active_fake_forge: FakeForge,
) -> None:
    _active_fake_forge.stub("read_pr", lambda *_a, **_kw: pr_read({"baseRefName": "main"}))
    _active_fake_forge.stub(
        "repo_file", lambda *_a, **_kw: file_read(_codeowners_body("/scripts/ @owner\n")),
    )
    _active_fake_forge.stub("changed_files", lambda *_a, **_kw: frozenset({"scripts/deploy.py"}))
    shouting = ForgeTarget(
        repo=RepoIdentity.from_slug("EXAMPLE-ORG/Example-Repo"),
        credential_env_name="EXAMPLE_GH_TOKEN",  # noqa: S106  # nosec B106
    )
    result = _pr_merge_checks._apply_ownership_gate(  # noqa: SLF001
        ("ready", "sha", None), 7, forge_target=shouting,
    )
    assert result == ("codeowners_blocked", "", None)


def test_ownership_gate_reads_the_profile_at_call_time(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    resolved = _profile_naming("other-org/other-repo")
    monkeypatch.setattr(default_profile, "resolved", lambda: resolved)
    result = _pr_merge_checks._apply_ownership_gate(  # noqa: SLF001
        ("ready", "sha", None), 7, forge_target=_ENFORCED_FORGE,
    )
    assert result == ("ready", "sha", None)
    assert _active_fake_forge.calls == []


def test_decode_contents_refuses_wrong_shaped_envelope() -> None:
    bad = forge_mod.decode_contents_payload(
        json.dumps({"content": 123, "encoding": "base64"}),
    )
    assert bad is None


def test_decode_contents_refuses_a_non_json_payload() -> None:
    assert forge_mod.decode_contents_payload("not json") is None
    assert forge_mod.decode_contents_payload("") is None


def test_decode_contents_refuses_a_non_object_payload() -> None:
    assert forge_mod.decode_contents_payload("[1, 2]") is None


def test_decode_contents_refuses_undecodable_base64_bytes() -> None:
    bad_utf8 = base64.b64encode(b"\xff\xfe").decode()
    result = forge_mod.decode_contents_payload(
        json.dumps({"content": bad_utf8, "encoding": "base64"}),
    )
    assert result is None


def test_decode_contents_refuses_malformed_but_permissively_decodable_base64() -> None:
    result = forge_mod.decode_contents_payload(
        json.dumps({"content": "!!!", "encoding": "base64"}),
    )
    assert result is None


def test_decode_contents_tolerates_line_wrapped_newlines() -> None:
    encoded = base64.b64encode(b"README.md @owner\n").decode()
    wrapped = "\n".join(encoded[i : i + 4] for i in range(0, len(encoded), 4))
    result = forge_mod.decode_contents_payload(
        json.dumps({"content": wrapped, "encoding": "base64"}),
    )
    assert result == "README.md @owner\n"


def test_read_base_ref_retries_transport_exception_then_refuses(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    def _raise_missing_binary(*_a: object, **_kw: object) -> object:
        raise FileNotFoundError("gh")

    _active_fake_forge.stub("read_pr", _raise_missing_binary)
    base_ref = _pr_merge_checks._read_base_ref(7, forge_target=_ENFORCED_FORGE)  # noqa: SLF001
    assert base_ref is None
    assert sum(1 for c in _active_fake_forge.calls if c[0] == "read_pr") == 3


def test_read_base_ref_retries_permission_error_then_refuses(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    def _raise_permission_denied(*_a: object, **_kw: object) -> object:
        raise PermissionError("denied")

    _active_fake_forge.stub("read_pr", _raise_permission_denied)
    base_ref = _pr_merge_checks._read_base_ref(7, forge_target=_ENFORCED_FORGE)  # noqa: SLF001
    assert base_ref is None
    assert sum(1 for c in _active_fake_forge.calls if c[0] == "read_pr") == 3


def test_read_base_ref_refuses_on_hard_read_pr_failure(_active_fake_forge: FakeForge) -> None:
    _active_fake_forge.stub(
        "read_pr", lambda *_a, **_kw: pr_read({}, returncode=1, stderr="bad credentials"),
    )
    base_ref = _pr_merge_checks._read_base_ref(7, forge_target=_ENFORCED_FORGE)  # noqa: SLF001
    assert base_ref is None
    assert sum(1 for c in _active_fake_forge.calls if c[0] == "read_pr") == 1


def test_read_codeowners_text_refuses_on_raised_repo_file_outcome(
    _active_fake_forge: FakeForge,
) -> None:
    _active_fake_forge.stub(
        "repo_file", lambda *_a, **_kw: file_read(None, outcome="raised", stderr="boom"),
    )
    result = _pr_merge_checks._read_codeowners_text(  # noqa: SLF001
        7, "main", forge_target=_ENFORCED_FORGE,
    )
    assert result is None
    assert sum(1 for c in _active_fake_forge.calls if c[0] == "repo_file") == 3


@pytest.mark.parametrize(
    "line",
    [
        "*.py @owner",
        "path/file?.py @owner",
        "path/[abc].py @owner",
        "path/file].py @owner",
        "!path/file.py @owner",
        "path\\file.py @owner",
        "path/file.py @owner # trailing comment",
        "path/file.py",
    ],
)
def test_parse_codeowners_refuses_unsupported_lines(line: str) -> None:
    with pytest.raises(_pr_merge_checks.UnsupportedCodeownersSyntax):
        _pr_merge_checks._parse_codeowners(line)  # noqa: SLF001


def test_parse_codeowners_skips_comment_and_blank_lines() -> None:
    text = "\n# a full-line comment\n\n/scripts/ @owner\n"
    rules = _pr_merge_checks._parse_codeowners(text)  # noqa: SLF001
    assert len(rules) == 1


def test_anchored_dir_with_trailing_slash_owns_descendants() -> None:
    rules = _pr_merge_checks._parse_codeowners("/scripts/ @owner\n")  # noqa: SLF001
    assert _pr_merge_checks._path_is_owned("scripts/deploy.py", rules[0])  # noqa: SLF001


def test_anchored_path_without_trailing_slash_owns_descendants() -> None:
    rules = _pr_merge_checks._parse_codeowners("/docs/handbook @owner\n")  # noqa: SLF001
    assert _pr_merge_checks._path_is_owned("docs/handbook", rules[0])  # noqa: SLF001
    assert _pr_merge_checks._path_is_owned("docs/handbook/sub.md", rules[0])  # noqa: SLF001


def test_unanchored_name_matches_at_any_depth() -> None:
    rules = _pr_merge_checks._parse_codeowners("OWNERS.md @owner\n")  # noqa: SLF001
    assert _pr_merge_checks._path_is_owned("OWNERS.md", rules[0])  # noqa: SLF001
    assert _pr_merge_checks._path_is_owned("a/b/OWNERS.md", rules[0])  # noqa: SLF001


def test_scripts_extra_does_not_match_the_anchored_scripts_boundary() -> None:
    rules = _pr_merge_checks._parse_codeowners("/scripts/ @owner\n")  # noqa: SLF001
    assert not _pr_merge_checks._path_is_owned("scripts-extra/x.py", rules[0])  # noqa: SLF001


def test_any_depth_doublestar_rule_matches_nested_and_root_path() -> None:
    rules = _pr_merge_checks._parse_codeowners(  # noqa: SLF001
        "**/__Snapshots__/manifest.json @owner\n"
    )
    assert _pr_merge_checks._path_is_owned(  # noqa: SLF001
        "__Snapshots__/manifest.json", rules[0]
    )
    assert _pr_merge_checks._path_is_owned(  # noqa: SLF001
        "app/ui/__Snapshots__/manifest.json", rules[0]
    )


def test_any_depth_doublestar_rule_does_not_overmatch_adjacent_segments() -> None:
    rules = _pr_merge_checks._parse_codeowners(  # noqa: SLF001
        "**/__Snapshots__/manifest.json @owner\n"
    )
    assert not _pr_merge_checks._path_is_owned(  # noqa: SLF001
        "__Snapshots__extra/manifest.json", rules[0]
    )
    assert not _pr_merge_checks._path_is_owned(  # noqa: SLF001
        "somewhere/__Snapshots__/other.json", rules[0]
    )


def test_any_depth_doublestar_rule_refuses_further_glyphs() -> None:
    with pytest.raises(_pr_merge_checks.UnsupportedCodeownersSyntax):
        _pr_merge_checks._parse_codeowners("**/*.py @owner\n")  # noqa: SLF001


def test_snapshot_manifest_owner_gate_blocks_owned_and_allows_unowned(
    _active_fake_forge: FakeForge,
) -> None:
    _active_fake_forge.stub("read_pr", lambda *_a, **_kw: pr_read({"baseRefName": "main"}))
    _active_fake_forge.stub(
        "repo_file",
        lambda *_a, **_kw: file_read(
            _codeowners_body("**/__Snapshots__/manifest.json @owner\n")
        ),
    )
    _active_fake_forge.stub(
        "changed_files",
        lambda *_a, **_kw: frozenset({"ios/App/__Snapshots__/manifest.json"}),
    )
    blocked = _pr_merge_checks._apply_ownership_gate(  # noqa: SLF001
        ("ready", "sha", None), 7, forge_target=_ENFORCED_FORGE,
    )
    assert blocked == ("codeowners_blocked", "", None)

    _active_fake_forge.stub("changed_files", lambda *_a, **_kw: frozenset({"ios/App/View.swift"}))
    allowed = _pr_merge_checks._apply_ownership_gate(  # noqa: SLF001
        ("ready", "sha", None), 7, forge_target=_ENFORCED_FORGE,
    )
    assert allowed == ("ready", "sha", None)


def test_pr_merge_routes_ownership_refusal_to_human_question(tmp_path: Path) -> None:
    ws = create("codeowners-refused", "d", root=tmp_path / "codeowners-refused")
    outcome = pr_merge_mod.PrMergeOutcome(
        kind="codeowners_blocked", pr_number=99,
        reason="codeowners-blocked: file ownership requires review.",
    )
    handover = pr_merge_mod._build_handover(ws, None, "inv-1", outcome)  # noqa: SLF001
    assert handover.to_team_suggestions == []
    (question,) = handover.open_questions
    assert question.id == "pr-merge-codeowners_blocked"
    assert question.option_resolvers == {}
