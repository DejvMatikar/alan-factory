from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from alan import default_profile, engine_registry_store
from alan.engine_config import (
    _POSTURE_RANK,  # pyright: ignore[reportPrivateUsage]
    _TEAM_MIN_POSTURE,  # pyright: ignore[reportPrivateUsage]
    EngineConfig,
    EngineNotFoundError,
    Posture,
    PostureCeilingError,
    RepoIdentity,
    admitted_teams,
    ceiling_source,
    default_engine_config,
    file_ceiling,
    live_floored_posture,
    live_hidden,
    load_yaml,
    lookup_by_repo,
    posture_rank,
    register,
    set_default_ceiling,
    set_file_ceilings,
    set_posture_and_persist,
    unhide_and_persist,
)
from alan.engine_config_validation import (
    FlowGraphPolicy,
    PostureOrthogonalityError,
    check_posture_orthogonality,
)

_FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _engines_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "engines"
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(root))
    return root


def test_posture_has_four_levels() -> None:
    assert len(Posture) == 4


def test_posture_values() -> None:
    assert Posture.observe == "observe"
    assert Posture.propose == "propose"
    assert Posture.self_merge == "self_merge"
    assert Posture.autonomous == "autonomous"


def test_posture_rank_observe_is_zero() -> None:
    assert posture_rank(Posture.observe) == 0


def test_posture_rank_propose() -> None:
    assert posture_rank(Posture.propose) == 1


def test_posture_rank_self_merge() -> None:
    assert posture_rank(Posture.self_merge) == 2


def test_posture_rank_autonomous_is_highest() -> None:
    assert posture_rank(Posture.autonomous) == 3


def test_posture_rank_strictly_ordered() -> None:
    assert (
        posture_rank(Posture.observe)
        < posture_rank(Posture.propose)
        < posture_rank(Posture.self_merge)
        < posture_rank(Posture.autonomous)
    )


def test_posture_rank_uses_posture_rank_dict() -> None:
    for p in Posture:
        assert posture_rank(p) == _POSTURE_RANK[p]


def test_team_min_posture_contains_all_consequential_teams() -> None:
    assert "pr_merge" in _TEAM_MIN_POSTURE
    assert "verification" in _TEAM_MIN_POSTURE
    assert "triage" in _TEAM_MIN_POSTURE
    assert "dependency_sweep" in _TEAM_MIN_POSTURE


def test_team_min_posture_pr_merge_is_self_merge() -> None:
    assert _TEAM_MIN_POSTURE["pr_merge"] == Posture.self_merge


def test_team_min_posture_verification_is_self_merge() -> None:
    assert _TEAM_MIN_POSTURE["verification"] == Posture.self_merge


def test_team_min_posture_triage_is_autonomous() -> None:
    assert _TEAM_MIN_POSTURE["triage"] == Posture.autonomous


def test_team_min_posture_implementation_is_propose() -> None:
    assert _TEAM_MIN_POSTURE["implementation"] == Posture.propose


def test_team_min_posture_ui_review_is_propose() -> None:
    assert _TEAM_MIN_POSTURE["ui_review"] == Posture.propose


def test_admitted_teams_observe_empty_when_no_observe_teams() -> None:
    all_teams = set(_TEAM_MIN_POSTURE.keys())
    assert admitted_teams(all_teams, Posture.observe) == set()


def test_admitted_teams_observe_withholds_implementation() -> None:
    assert "implementation" not in admitted_teams({"implementation"}, Posture.observe)


def test_admitted_teams_observe_withholds_pr_merge() -> None:
    assert "pr_merge" not in admitted_teams({"pr_merge"}, Posture.observe)


def test_admitted_teams_observe_withholds_triage() -> None:
    assert "triage" not in admitted_teams({"triage"}, Posture.observe)


def test_admitted_teams_observe_withholds_ui_review() -> None:
    assert "ui_review" not in admitted_teams({"ui_review"}, Posture.observe)


def test_admitted_teams_propose_admits_implementation() -> None:
    assert "implementation" in admitted_teams({"implementation"}, Posture.propose)


def test_admitted_teams_propose_admits_ui_review() -> None:
    assert "ui_review" in admitted_teams({"ui_review"}, Posture.propose)


def test_admitted_teams_propose_admits_research() -> None:
    assert "research" in admitted_teams({"research"}, Posture.propose)


def test_admitted_teams_propose_withholds_verification() -> None:
    assert "verification" not in admitted_teams({"verification"}, Posture.propose)


def test_admitted_teams_propose_withholds_pr_merge() -> None:
    assert "pr_merge" not in admitted_teams({"pr_merge"}, Posture.propose)


def test_admitted_teams_propose_withholds_triage() -> None:
    assert "triage" not in admitted_teams({"triage"}, Posture.propose)


def test_admitted_teams_self_merge_admits_verification() -> None:
    assert "verification" in admitted_teams({"verification"}, Posture.self_merge)


def test_admitted_teams_self_merge_admits_pr_merge() -> None:
    assert "pr_merge" in admitted_teams({"pr_merge"}, Posture.self_merge)


def test_admitted_teams_self_merge_admits_propose_teams() -> None:
    assert "implementation" in admitted_teams({"implementation"}, Posture.self_merge)


def test_admitted_teams_self_merge_admits_ui_review() -> None:
    assert "ui_review" in admitted_teams({"ui_review"}, Posture.self_merge)


def test_admitted_teams_self_merge_withholds_triage() -> None:
    assert "triage" not in admitted_teams({"triage"}, Posture.self_merge)


def test_admitted_teams_self_merge_withholds_dependency_sweep() -> None:
    assert "dependency_sweep" not in admitted_teams(
        {"dependency_sweep"}, Posture.self_merge
    )


def test_admitted_teams_autonomous_admits_all_mapped_teams() -> None:
    all_teams = set(_TEAM_MIN_POSTURE.keys())
    assert admitted_teams(all_teams, Posture.autonomous) == all_teams


def test_admitted_teams_autonomous_admits_triage() -> None:
    assert "triage" in admitted_teams({"triage"}, Posture.autonomous)


def test_admitted_teams_autonomous_admits_dependency_sweep() -> None:
    assert "dependency_sweep" in admitted_teams(
        {"dependency_sweep"}, Posture.autonomous
    )


def test_admitted_teams_autonomous_admits_ui_review() -> None:
    assert "ui_review" in admitted_teams({"ui_review"}, Posture.autonomous)


def test_admitted_teams_unmapped_withheld_at_observe() -> None:
    assert "unknown_future_team" not in admitted_teams(
        {"unknown_future_team"}, Posture.observe
    )


def test_admitted_teams_unmapped_withheld_at_propose() -> None:
    assert "unknown_future_team" not in admitted_teams(
        {"unknown_future_team"}, Posture.propose
    )


def test_admitted_teams_unmapped_withheld_at_self_merge() -> None:
    assert "unknown_future_team" not in admitted_teams(
        {"unknown_future_team"}, Posture.self_merge
    )


def test_admitted_teams_unmapped_withheld_at_autonomous() -> None:
    assert "unknown_future_team" not in admitted_teams(
        {"unknown_future_team"}, Posture.autonomous
    )


def test_admitted_teams_empty_input_returns_empty() -> None:
    assert admitted_teams(set(), Posture.autonomous) == set()


def test_admitted_teams_monotonic_strict_supersets() -> None:
    all_teams = set(_TEAM_MIN_POSTURE.keys())
    obs = admitted_teams(all_teams, Posture.observe)
    prop = admitted_teams(all_teams, Posture.propose)
    merge = admitted_teams(all_teams, Posture.self_merge)
    auto = admitted_teams(all_teams, Posture.autonomous)
    assert obs < prop < merge < auto


def test_admitted_teams_monotonic_propose_superset_of_observe() -> None:
    all_teams = set(_TEAM_MIN_POSTURE.keys())
    obs = admitted_teams(all_teams, Posture.observe)
    prop = admitted_teams(all_teams, Posture.propose)
    assert obs.issubset(prop)
    assert prop - obs


def test_admitted_teams_monotonic_autonomous_superset_of_self_merge() -> None:
    all_teams = set(_TEAM_MIN_POSTURE.keys())
    merge = admitted_teams(all_teams, Posture.self_merge)
    auto = admitted_teams(all_teams, Posture.autonomous)
    assert merge.issubset(auto)
    assert auto - merge


def test_engine_config_posture_defaults_to_observe() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    assert cfg.posture == Posture.observe


def test_engine_config_posture_can_be_set_to_autonomous() -> None:
    cfg = EngineConfig(
        repo=RepoIdentity(owner="a", repo="b"),
        posture=Posture.autonomous,
    )
    assert cfg.posture == Posture.autonomous


def test_engine_config_posture_can_be_set_to_self_merge() -> None:
    cfg = EngineConfig(
        repo=RepoIdentity(owner="a", repo="b"),
        posture=Posture.self_merge,
    )
    assert cfg.posture == Posture.self_merge


def test_engine_config_rejects_unknown_posture_string() -> None:
    with pytest.raises(ValidationError):
        EngineConfig.model_validate(
            {"repo": {"owner": "a", "repo": "b"}, "posture": "god_mode"}
        )


def test_engine_config_coerces_posture_from_string() -> None:
    cfg = EngineConfig.model_validate(
        {"repo": {"owner": "a", "repo": "b"}, "posture": "propose"}
    )
    assert cfg.posture == Posture.propose


def test_default_engine_admitted_teams_is_full_set() -> None:
    cfg = default_engine_config()
    all_teams = set(_TEAM_MIN_POSTURE.keys())
    assert admitted_teams(all_teams, cfg.posture) == all_teams


def test_observe_fixture_loads_with_observe_posture() -> None:
    cfg = load_yaml(_FIXTURES / "engine_config_observe.yaml")
    assert cfg.posture == Posture.observe
    assert cfg.repo.owner == "example-corp"


def test_observe_fixture_withholds_consequential_teams() -> None:
    cfg = load_yaml(_FIXTURES / "engine_config_observe.yaml")
    consequential = {
        "pr_merge", "verification",
        "triage", "dependency_sweep",
        "deploy_verify", "maintenance",
        "implementation", "implementation_fix", "research",
    }
    assert admitted_teams(consequential, cfg.posture) == set()


def test_observe_fixture_no_flow_policy() -> None:
    cfg = load_yaml(_FIXTURES / "engine_config_observe.yaml")
    assert cfg.flow_policy is None


def test_nonpython_fixture_posture_defaults_to_observe() -> None:
    cfg = load_yaml(_FIXTURES / "engine_config_nonpython.yaml")
    assert cfg.posture == Posture.observe


def _make_policy(
    *,
    verifier_teams: frozenset[str] = frozenset({"verification"}),
    merge_teams: frozenset[str] = frozenset({"pr_merge"}),
) -> FlowGraphPolicy:
    return FlowGraphPolicy(
        verifier_teams=verifier_teams,
        merge_teams=merge_teams,
        terminal_teams=frozenset({"done"}),
        internal_only_teams=frozenset(),
        restricted_predecessors={},
        revisit_cap=3,
    )


def test_check_posture_orthogonality_accepts_valid_table() -> None:
    policy = _make_policy(
        verifier_teams=frozenset({"verification"}),
        merge_teams=frozenset({"pr_merge"}),
    )
    check_posture_orthogonality(_TEAM_MIN_POSTURE, policy)


def test_check_posture_orthogonality_rejects_merge_below_verifier() -> None:
    bad_table: dict[str, Posture] = {
        "my_merger": Posture.propose,
        "my_verifier": Posture.self_merge,
    }
    policy = _make_policy(
        verifier_teams=frozenset({"my_verifier"}),
        merge_teams=frozenset({"my_merger"}),
    )
    with pytest.raises(PostureOrthogonalityError, match="my_merger"):
        check_posture_orthogonality(bad_table, policy)


def test_check_posture_orthogonality_accepts_merge_equal_to_verifier() -> None:
    equal_table: dict[str, Posture] = {
        "my_merger": Posture.self_merge,
        "my_verifier": Posture.self_merge,
    }
    policy = _make_policy(
        verifier_teams=frozenset({"my_verifier"}),
        merge_teams=frozenset({"my_merger"}),
    )
    check_posture_orthogonality(equal_table, policy)


def test_check_posture_orthogonality_accepts_merge_above_verifier() -> None:
    table: dict[str, Posture] = {
        "my_merger": Posture.autonomous,
        "my_verifier": Posture.self_merge,
    }
    policy = _make_policy(
        verifier_teams=frozenset({"my_verifier"}),
        merge_teams=frozenset({"my_merger"}),
    )
    check_posture_orthogonality(table, policy)


def test_check_posture_orthogonality_accepts_merge_team_not_in_table() -> None:
    policy = _make_policy(
        merge_teams=frozenset({"unlisted_merger"}),
        verifier_teams=frozenset({"verification"}),
    )
    check_posture_orthogonality(_TEAM_MIN_POSTURE, policy)


def test_check_posture_orthogonality_accepts_verifier_not_in_table() -> None:
    policy = _make_policy(
        merge_teams=frozenset({"pr_merge"}),
        verifier_teams=frozenset({"unlisted_verifier"}),
    )
    check_posture_orthogonality(_TEAM_MIN_POSTURE, policy)


def test_check_posture_orthogonality_accepts_empty_merge_teams() -> None:
    policy = _make_policy(merge_teams=frozenset())
    check_posture_orthogonality(_TEAM_MIN_POSTURE, policy)


def test_check_posture_orthogonality_accepts_empty_verifier_teams() -> None:
    policy = _make_policy(
        merge_teams=frozenset({"pr_merge"}),
        verifier_teams=frozenset(),
    )
    check_posture_orthogonality(_TEAM_MIN_POSTURE, policy)


def test_check_posture_orthogonality_default_alan_policy_passes() -> None:
    cfg = default_engine_config()
    assert cfg.flow_policy is not None
    check_posture_orthogonality(_TEAM_MIN_POSTURE, cfg.flow_policy)


def test_live_floored_posture_none_engine_key_returns_snapshot() -> None:
    assert live_floored_posture(Posture.self_merge, None) == Posture.self_merge


def test_live_floored_posture_unregistered_engine_key_returns_snapshot() -> None:
    assert live_floored_posture(Posture.self_merge, "no/such-engine") == Posture.self_merge


def test_live_floored_posture_downgrade_floors_to_live() -> None:
    register(EngineConfig(repo=RepoIdentity(owner="acme", repo="widget"), posture=Posture.observe))
    assert live_floored_posture(Posture.self_merge, "acme/widget") == Posture.observe


def test_live_floored_posture_upgrade_holds_at_snapshot() -> None:
    register(
        EngineConfig(repo=RepoIdentity(owner="acme", repo="widget"), posture=Posture.autonomous)
    )
    assert live_floored_posture(Posture.observe, "acme/widget") == Posture.observe


def test_live_floored_posture_equal_returns_snapshot_value() -> None:
    register(
        EngineConfig(repo=RepoIdentity(owner="acme", repo="widget"), posture=Posture.propose)
    )
    assert live_floored_posture(Posture.propose, "acme/widget") == Posture.propose


def test_live_hidden_none_engine_key_returns_false() -> None:
    assert live_hidden(None) is False


def test_live_hidden_unregistered_engine_key_returns_false() -> None:
    assert live_hidden("no/such-engine") is False


def test_live_hidden_reflects_live_registry_value() -> None:
    register(
        EngineConfig(repo=RepoIdentity(owner="acme", repo="widget"), hidden=True),
        validate_env=False,
    )
    assert live_hidden("acme/widget") is True


def _register_widget(posture: Posture = Posture.observe) -> RepoIdentity:
    identity = RepoIdentity(owner="acme", repo="widget")
    register(EngineConfig(repo=identity, posture=posture), validate_env=False)
    return identity


def test_set_posture_and_persist_updates_in_memory_registry() -> None:
    identity = _register_widget(Posture.observe)
    updated = set_posture_and_persist(identity, Posture.propose, source="api")
    assert updated.posture == Posture.propose
    stored = lookup_by_repo(identity)
    assert stored is not None
    assert stored.posture == Posture.propose


def test_set_posture_and_persist_persists_to_store() -> None:
    identity = _register_widget(Posture.observe)
    set_posture_and_persist(identity, Posture.autonomous, source="api")
    payloads = engine_registry_store.list_engines()
    assert len(payloads) == 1
    assert payloads[0]["posture"] == "autonomous"


def test_set_posture_and_persist_unknown_repo_raises() -> None:
    with pytest.raises(EngineNotFoundError):
        set_posture_and_persist(
            RepoIdentity(owner="no", repo="such"), Posture.propose, source="api"
        )


def test_set_posture_and_persist_writes_audit_entry() -> None:
    identity = _register_widget(Posture.observe)
    set_posture_and_persist(identity, Posture.self_merge, source="api")
    audit_path = engine_registry_store.engines_root() / "posture_audit.jsonl"
    lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["repo"] == "acme/widget"
    assert entry["from"] == "observe"
    assert entry["to"] == "self_merge"
    assert entry["source"] == "api"
    assert "timestamp_utc" in entry


def test_set_posture_and_persist_audit_appends_not_truncates() -> None:
    identity = _register_widget(Posture.observe)
    set_posture_and_persist(identity, Posture.propose, source="api")
    set_posture_and_persist(identity, Posture.self_merge, source="api")
    audit_path = engine_registry_store.engines_root() / "posture_audit.jsonl"
    lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_set_posture_and_persist_no_audit_write_on_unknown_repo() -> None:
    with pytest.raises(EngineNotFoundError):
        set_posture_and_persist(
            RepoIdentity(owner="no", repo="such"), Posture.propose, source="api"
        )
    audit_path = engine_registry_store.engines_root() / "posture_audit.jsonl"
    assert not audit_path.exists()


def test_set_posture_and_persist_same_posture_is_a_true_no_op() -> None:
    identity = _register_widget(Posture.propose)

    result = set_posture_and_persist(identity, Posture.propose, source="api")

    assert result.posture == Posture.propose
    audit_path = engine_registry_store.engines_root() / "posture_audit.jsonl"
    assert not audit_path.exists()


def test_default_ceiling_is_seeded_from_the_resolved_default_posture() -> None:
    repo = default_engine_config().repo
    assert file_ceiling(repo) == default_profile.resolved().profile.posture
    assert ceiling_source(repo) == "default"


def test_default_ceiling_entry_applies_only_to_its_own_repository() -> None:
    identity = RepoIdentity(owner="acme", repo="widget")
    set_default_ceiling({identity: Posture.observe})
    assert file_ceiling(identity) == Posture.observe
    assert file_ceiling(RepoIdentity(owner="acme", repo="gadget")) == Posture.autonomous


def test_setting_a_default_ceiling_replaces_the_prior_map() -> None:
    identity = RepoIdentity(owner="acme", repo="widget")
    set_default_ceiling({identity: Posture.observe})
    set_default_ceiling({})
    assert file_ceiling(identity) == Posture.autonomous


def test_a_file_backed_ceiling_wins_over_the_default_ceiling() -> None:
    identity = RepoIdentity(owner="acme", repo="widget")
    set_default_ceiling({identity: Posture.observe})
    set_file_ceilings({identity: Posture.propose})
    assert file_ceiling(identity) == Posture.propose
    assert ceiling_source(identity) == "file"


def test_a_lowered_global_fallback_does_not_override_a_default_ceiling_entry() -> None:
    identity = RepoIdentity(owner="acme", repo="widget")
    set_default_ceiling({identity: Posture.propose})
    set_file_ceilings({}, fallback=Posture.observe)
    assert file_ceiling(identity) == Posture.propose
    assert file_ceiling(RepoIdentity(owner="acme", repo="gadget")) == Posture.observe


def test_ceiling_source_reports_the_fallback_for_an_unlisted_repository() -> None:
    assert ceiling_source(RepoIdentity(owner="acme", repo="widget")) == "fallback"


def test_ceiling_source_reports_default_for_a_default_only_entry() -> None:
    identity = RepoIdentity(owner="acme", repo="widget")
    set_default_ceiling({identity: Posture.observe})
    assert ceiling_source(identity) == "default"


def test_the_posture_dial_refuses_to_raise_an_engine_above_its_default_ceiling() -> None:
    identity = _register_widget(Posture.observe)
    set_default_ceiling({identity: Posture.observe})
    with pytest.raises(PostureCeilingError):
        set_posture_and_persist(identity, Posture.propose, source="api")
    live = lookup_by_repo(identity)
    assert live is not None
    assert live.posture == Posture.observe


def test_the_posture_dial_allows_a_raise_up_to_the_default_ceiling() -> None:
    identity = _register_widget(Posture.observe)
    set_default_ceiling({identity: Posture.propose})
    updated = set_posture_and_persist(identity, Posture.propose, source="api")
    assert updated.posture == Posture.propose


def test_unhide_restores_no_higher_than_the_default_ceiling() -> None:
    identity = RepoIdentity(owner="acme", repo="widget")
    register(
        EngineConfig(
            repo=identity,
            posture=Posture.observe,
            hidden=True,
            hidden_restore_posture=Posture.autonomous,
        ),
        validate_env=False,
    )
    set_default_ceiling({identity: Posture.propose})
    assert unhide_and_persist(identity).posture == Posture.propose


def test_an_unconfigured_default_cannot_be_dialed_above_observe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved = default_profile.resolve(tmp_path / "no-profile", tmp_path / "no-owner.yaml")
    monkeypatch.setattr(default_profile, "resolved", lambda: resolved)
    placeholder = default_engine_config()
    register(placeholder, default=True, validate_env=False)
    set_default_ceiling({placeholder.repo: placeholder.posture})
    assert file_ceiling(placeholder.repo) == Posture.observe
    with pytest.raises(PostureCeilingError):
        set_posture_and_persist(placeholder.repo, Posture.propose, source="api")
