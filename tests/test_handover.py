from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from alan import handover as handover_mod
from alan.handover import (
    SCHEMA_VERSION,
    ArtifactRef,
    Decision,
    Handover,
    HandoverError,
    OpenQuestion,
    ScenarioOutcome,
    TestScenario,
    load_handover,
    prune_handover_files,
    save_handover,
)
from alan.workspace import create


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


_FIXTURE_DECISION = {
    "id": "fixture-d1",
    "statement": "fixture decision",
    "rationale": "fixture rationale",
    "confidence": 0.9,
    "reversibility": "medium",
}


def _make_handover(task_id: str = "t1", **overrides: object) -> Handover:
    base: dict[str, object] = {
        "handover_id": f"handover_{task_id}_001",
        "handover_type": "forward",
        "task_id": task_id,
        "from_team": "implementation_plan",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": ["implementation"],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC),
        "next_hop": None,
        "decisions": [dict(_FIXTURE_DECISION)],
        "open_questions": [],
        "assumptions": [],
        "constraints_inherited": [],
        "artifacts_produced": [],
        "full_synthesis": "plan ready; proceed to implementation",
    }
    base.update(overrides)
    return Handover.model_validate(base)


def test_handover_default_schema_version() -> None:
    h = _make_handover()
    assert h.schema_version == SCHEMA_VERSION
    assert SCHEMA_VERSION == "1.23"


def test_handover_unsupported_schema_version_rejected(ws_root: Path) -> None:
    ws = create("unsupported", "d", root=ws_root)
    payload = {
        "schema_version": "9.9",
        "handover_id": f"handover_{ws.task_id}_001",
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "research",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": [],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "summary_for_downstream": "future-version payload",
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(HandoverError, match="unsupported schema_version"):
        load_handover(target)


def test_handover_v10_documents_still_load(ws_root: Path) -> None:
    ws = create("compat", "d", root=ws_root)
    payload = {
        "schema_version": "1.0",
        "handover_id": f"handover_{ws.task_id}_001",
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "research",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": ["implementation_plan"],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "summary_for_downstream": "legacy v1.0 payload",
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.schema_version == "1.0"
    assert loaded.forbidden_actions == []
    assert loaded.prior_attempts_summary is None
    assert loaded.test_scenarios == []
    assert loaded.full_synthesis == "legacy v1.0 payload"


def test_handover_v13_documents_still_load_with_empty_scenarios(
    ws_root: Path,
) -> None:
    ws = create("v13", "d", root=ws_root)
    payload = {
        "schema_version": "1.3",
        "handover_id": f"handover_{ws.task_id}_001",
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "implementation_plan",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": ["implementation"],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "decisions": [dict(_FIXTURE_DECISION)],
        "files_to_edit": ["alan/queue.py"],
        "pr_number": 42,
        "summary_for_downstream": "legacy v1.3 payload",
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.schema_version == "1.3"
    assert loaded.test_scenarios == []
    assert loaded.files_to_edit == ["alan/queue.py"]
    assert loaded.pr_number == 42
    assert loaded.full_synthesis == "legacy v1.3 payload"


def test_handover_pre_1_6_summary_for_downstream_migrates_to_full_synthesis(
    ws_root: Path,
) -> None:
    ws = create("migrate", "d", root=ws_root)
    payload = {
        "schema_version": "1.5",
        "handover_id": f"handover_{ws.task_id}_001",
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "research",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": ["implementation_plan"],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "summary_for_downstream": "x" * 5000,
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.schema_version == "1.5"
    assert loaded.full_synthesis == "x" * 5000
    assert not hasattr(loaded, "summary_for_downstream")


def test_handover_pre_1_6_with_existing_full_synthesis_no_migration(
    ws_root: Path,
) -> None:
    ws = create("no-migrate", "d", root=ws_root)
    payload = {
        "schema_version": "1.5",
        "handover_id": f"handover_{ws.task_id}_001",
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "research",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": ["implementation_plan"],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "full_synthesis": "new field wins",
        "summary_for_downstream": "old field ignored",
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.full_synthesis == "new field wins"


def test_test_scenario_model_fields() -> None:
    s = TestScenario(
        id="s1",
        description="desc",
        given="g",
        when="w",
        then="t",
    )
    assert s.id == "s1"
    assert s.then == "t"


def test_handover_test_scenarios_round_trip(ws_root: Path) -> None:
    ws = create("scenarios-rt", "d", root=ws_root)
    h = _make_handover(
        task_id=ws.task_id,
        test_scenarios=[
            TestScenario(
                id="s1",
                description="a thing",
                given="precondition",
                when="action",
                then="outcome",
            ).model_dump(),
        ],
    )
    path = save_handover(ws, h)
    loaded = load_handover(path)
    assert loaded.schema_version == "1.23"
    assert [s.id for s in loaded.test_scenarios] == ["s1"]
    assert loaded.test_scenarios[0].when == "action"


def test_handover_forbidden_actions_round_trip(ws_root: Path) -> None:
    ws = create("forbid", "d", root=ws_root)
    h = _make_handover(
        task_id=ws.task_id,
        forbidden_actions=["do not push", "do not edit prod"],
        prior_attempts_summary="tried twice, both failed CI",
    )
    save_handover(ws, h)
    [path] = list((ws.path / "handovers").glob("*.yaml"))
    loaded = load_handover(path)
    assert loaded.forbidden_actions == ["do not push", "do not edit prod"]
    assert loaded.prior_attempts_summary == "tried twice, both failed CI"


def test_handover_rejects_iteration_below_1() -> None:
    with pytest.raises(ValidationError, match="iteration"):
        _make_handover(iteration=0)


def test_decision_confidence_bounds() -> None:
    Decision(id="d1", statement="x", rationale="y", confidence=0.0, reversibility="low")
    Decision(id="d2", statement="x", rationale="y", confidence=1.0, reversibility="high")
    with pytest.raises(ValidationError, match="confidence"):
        Decision(
            id="d3", statement="x", rationale="y", confidence=1.5, reversibility="medium"
        )
    with pytest.raises(ValidationError, match="confidence"):
        Decision(
            id="d4", statement="x", rationale="y", confidence=-0.1, reversibility="medium"
        )


def test_decision_rejects_unknown_reversibility() -> None:
    with pytest.raises(ValidationError, match="reversibility"):
        Decision.model_validate(
            {
                "id": "d",
                "statement": "x",
                "rationale": "y",
                "confidence": 0.5,
                "reversibility": "extreme",
            }
        )


def test_handover_rejects_unknown_handover_type() -> None:
    with pytest.raises(ValidationError, match="handover_type"):
        _make_handover(handover_type="gossip")


def test_handover_accepts_each_handover_type() -> None:
    for ht in (
        "forward",
        "clarification_request",
        "revisit_amend",
        "parallel_fanout_join",
        "escalation",
    ):
        h = _make_handover(handover_type=ht)
        assert h.handover_type == ht


def test_open_question_defaults_are_empty_lists() -> None:
    q = OpenQuestion(
        id="q1",
        question="Which storage?",
        resolver_role="brainstorming",
        blocking=True,
    )
    assert q.proposed_answer_space == []
    assert q.prior_attempts == []


def test_artifact_ref_fields() -> None:
    a = ArtifactRef(path="plan.md", description="implementation plan")
    assert a.path == "plan.md"
    assert a.description == "implementation plan"


def test_save_handover_writes_sequential_files(ws_root: Path) -> None:
    ws = create("save test", "d", root=ws_root)
    p1 = save_handover(ws, _make_handover(task_id=ws.task_id))
    p2 = save_handover(ws, _make_handover(task_id=ws.task_id))
    assert p1.name == f"handover_{ws.task_id}_001.yaml"
    assert p2.name == f"handover_{ws.task_id}_002.yaml"
    assert p1.parent == ws.path / "handovers"


def test_save_handover_task_id_mismatch_raises(ws_root: Path) -> None:
    ws = create("mismatch", "d", root=ws_root)
    with pytest.raises(HandoverError, match="does not match workspace"):
        save_handover(ws, _make_handover(task_id="other"))


def test_save_handover_creates_dir_if_missing(ws_root: Path) -> None:
    ws = create("nodir", "d", root=ws_root)
    (ws.path / "handovers").rmdir()
    p = save_handover(ws, _make_handover(task_id=ws.task_id))
    assert p.parent.is_dir()


def test_save_handover_exhausted_slots_raises(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handover_mod, "_MAX_SEQ", 2)
    ws = create("exh", "d", root=ws_root)
    save_handover(ws, _make_handover(task_id=ws.task_id))
    save_handover(ws, _make_handover(task_id=ws.task_id))
    with pytest.raises(HandoverError, match="no free handover slot"):
        save_handover(ws, _make_handover(task_id=ws.task_id))


def test_prune_handover_files_removes_all_and_returns_count(ws_root: Path) -> None:
    ws = create("prune-all", "d", root=ws_root)
    for _ in range(3):
        save_handover(ws, _make_handover(task_id=ws.task_id))
    assert prune_handover_files(ws) == 3
    assert (ws.path / "handovers").is_dir()
    assert list((ws.path / "handovers").iterdir()) == []


def test_prune_handover_files_empty_dir_returns_zero(ws_root: Path) -> None:
    ws = create("prune-empty", "d", root=ws_root)
    assert prune_handover_files(ws) == 0


def test_prune_handover_files_missing_dir_returns_zero(ws_root: Path) -> None:
    ws = create("prune-nodir", "d", root=ws_root)
    (ws.path / "handovers").rmdir()
    assert prune_handover_files(ws) == 0


def test_prune_handover_files_leaves_non_matching_files(ws_root: Path) -> None:
    ws = create("prune-stray", "d", root=ws_root)
    save_handover(ws, _make_handover(task_id=ws.task_id))
    stray = ws.path / "handovers" / "stray.txt"
    stray.write_text("keep", encoding="utf-8")
    other = ws.path / "handovers" / "handover_othertask_001.yaml"
    other.write_text("keep", encoding="utf-8")
    assert prune_handover_files(ws) == 1
    assert stray.exists()
    assert other.exists()


def test_prune_handover_files_frees_slots_after_exhaustion(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handover_mod, "_MAX_SEQ", 2)
    ws = create("prune-exh", "d", root=ws_root)
    save_handover(ws, _make_handover(task_id=ws.task_id))
    save_handover(ws, _make_handover(task_id=ws.task_id))
    with pytest.raises(HandoverError, match="no free handover slot"):
        save_handover(ws, _make_handover(task_id=ws.task_id))
    assert prune_handover_files(ws) == 2
    p = save_handover(ws, _make_handover(task_id=ws.task_id))
    assert p.name.endswith("_001.yaml")


def test_prune_handover_files_swallows_unlink_oserror(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("prune-err", "d", root=ws_root)
    save_handover(ws, _make_handover(task_id=ws.task_id))

    def boom(*_a: object, **_kw: object) -> None:
        raise OSError("locked")

    monkeypatch.setattr(Path, "unlink", boom)
    assert prune_handover_files(ws) == 0
    assert len(list((ws.path / "handovers").iterdir())) == 1


def test_save_handover_cleans_up_tmp_and_placeholder_on_dump_failure(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("dirty", "d", root=ws_root)

    def boom(*_a: object, **_kw: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("alan.handover.yaml.safe_dump", boom)
    with pytest.raises(OSError, match="disk full"):
        save_handover(ws, _make_handover(task_id=ws.task_id))
    contents = list((ws.path / "handovers").iterdir())
    assert contents == []


def test_save_handover_cleans_up_when_yaml_serialization_fails(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("yaml-err", "d", root=ws_root)

    def boom(*_a: object, **_kw: object) -> None:
        raise yaml.YAMLError("cannot serialize")

    monkeypatch.setattr("alan.handover.yaml.safe_dump", boom)
    with pytest.raises(yaml.YAMLError, match="cannot serialize"):
        save_handover(ws, _make_handover(task_id=ws.task_id))
    contents = list((ws.path / "handovers").iterdir())
    assert contents == []


def test_claim_handover_slot_skips_concurrently_created_file(ws_root: Path) -> None:
    ws = create("concurrent", "d", root=ws_root)
    handovers = ws.path / "handovers"
    (handovers / f"handover_{ws.task_id}_001.yaml").touch()
    p = save_handover(ws, _make_handover(task_id=ws.task_id))
    assert p.name == f"handover_{ws.task_id}_002.yaml"


def test_load_handover_roundtrips_save(ws_root: Path) -> None:
    ws = create("rt", "d", root=ws_root)
    original = _make_handover(
        task_id=ws.task_id,
        decisions=[
            Decision(
                id="d1",
                statement="use json",
                rationale="simple",
                confidence=0.9,
                reversibility="high",
            ).model_dump(),
        ],
        open_questions=[
            OpenQuestion(
                id="q1",
                question="which db?",
                proposed_answer_space=["sqlite", "duckdb"],
                resolver_role="brainstorming",
                blocking=True,
                prior_attempts=["handover_rt_001"],
            ).model_dump(),
        ],
        assumptions=["single-user"],
        constraints_inherited=["py3.11"],
        artifacts_produced=[ArtifactRef(path="plan.md", description="the plan").model_dump()],
        full_synthesis="ok",
    )
    path = save_handover(ws, original)
    loaded = load_handover(path)
    assert loaded.handover_id == original.handover_id
    assert loaded.decisions[0].confidence == 0.9
    assert loaded.open_questions[0].blocking is True
    assert loaded.artifacts_produced[0].path == "plan.md"
    assert loaded.assumptions == ["single-user"]


def test_load_handover_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(HandoverError, match="missing file"):
        load_handover(tmp_path / "nope.yaml")


def test_load_handover_invalid_yaml_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("foo: [unterminated", encoding="utf-8")
    with pytest.raises(HandoverError, match="invalid YAML"):
        load_handover(bad)


def test_load_handover_non_mapping_root_raises(tmp_path: Path) -> None:
    bad = tmp_path / "list.yaml"
    bad.write_text("- 1\n- 2\n", encoding="utf-8")
    with pytest.raises(HandoverError, match="expected mapping"):
        load_handover(bad)


def test_load_handover_schema_violation_raises(tmp_path: Path) -> None:
    bad = tmp_path / "shape.yaml"
    bad.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0",
                "handover_id": "h_x_001",
                "handover_type": "forward",
                "task_id": "x",
                "from_team": "research",
                "from_invocation_id": "inv_001",
                "iteration": 0,
                "timestamp_utc": "2026-05-11T20:00:00+00:00",
                "full_synthesis": "",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(HandoverError, match="schema mismatch"):
        load_handover(bad)


def test_handover_default_schema_version_serializes() -> None:
    h = _make_handover()
    dumped = h.model_dump(mode="json")
    assert dumped["schema_version"] == "1.23"
    assert dumped["iteration"] == 1


def test_handover_v12_documents_still_load(ws_root: Path) -> None:
    ws = create("compat12", "d", root=ws_root)
    payload = {
        "schema_version": "1.2",
        "handover_id": f"handover_{ws.task_id}_001",
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "implementation_plan",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": ["implementation"],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "full_synthesis": "legacy v1.2 payload — no files_to_edit field",
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.schema_version == "1.2"
    assert loaded.files_to_edit == []


def test_handover_files_to_edit_round_trip(ws_root: Path) -> None:
    ws = create("files-trip", "d", root=ws_root)
    h = _make_handover(
        task_id=ws.task_id,
        files_to_edit=["alan/router.py", "tests/test_router.py"],
        decisions=[
            Decision(
                id="d1",
                statement="x",
                rationale="y",
                confidence=0.9,
                reversibility="high",
            )
        ],
        to_team_suggestions=["implementation"],
    )
    save_handover(ws, h)
    [path] = list((ws.path / "handovers").glob("*.yaml"))
    loaded = load_handover(path)
    assert loaded.files_to_edit == ["alan/router.py", "tests/test_router.py"]


def test_handover_with_revisit_and_next_hop() -> None:
    h = _make_handover(
        handover_type="revisit_amend",
        iteration=2,
        revisit_of="handover_t1_001",
        next_hop="implementation_plan",
    )
    assert h.iteration == 2
    assert h.revisit_of == "handover_t1_001"
    assert h.next_hop == "implementation_plan"


def test_list_handovers_empty_when_dir_missing(ws_root: Path) -> None:
    from alan.handover import list_handovers
    ws = create("no-dir", "d", root=ws_root)
    (ws.path / "handovers").rmdir()
    assert list_handovers(ws) == []


def test_list_handovers_returns_handovers_in_save_order(ws_root: Path) -> None:
    from alan.handover import list_handovers

    ws = create("list-h", "d", root=ws_root)
    h1 = _make_handover(task_id=ws.task_id).model_copy(
        update={"handover_id": f"handover_{ws.task_id}_aaa"}
    )
    h2 = _make_handover(task_id=ws.task_id).model_copy(
        update={"handover_id": f"handover_{ws.task_id}_bbb"}
    )
    save_handover(ws, h1)
    save_handover(ws, h2)
    listed = list_handovers(ws)
    assert [h.handover_id for h in listed] == [h1.handover_id, h2.handover_id]


def test_find_handover_by_id_returns_match(ws_root: Path) -> None:
    from alan.handover import find_handover_by_id
    ws = create("find-h", "d", root=ws_root)
    h = _make_handover(task_id=ws.task_id)
    save_handover(ws, h)
    found = find_handover_by_id(ws, h.handover_id)
    assert found is not None
    assert found.handover_id == h.handover_id


def test_find_handover_by_id_returns_none_when_missing(ws_root: Path) -> None:
    from alan.handover import find_handover_by_id
    ws = create("missing-h", "d", root=ws_root)
    h = _make_handover(task_id=ws.task_id)
    save_handover(ws, h)
    assert find_handover_by_id(ws, "handover_no_such_id") is None


def test_save_handover_rejects_forward_to_implementation_without_decisions(
    ws_root: Path,
) -> None:
    ws = create("layer3-impl", "d", root=ws_root)
    h = _make_handover(
        task_id=ws.task_id,
        to_team_suggestions=["implementation"],
        decisions=[],
    )
    with pytest.raises(HandoverError, match="requires non-empty decisions"):
        save_handover(ws, h)


def test_save_handover_rejects_forward_to_verification_without_decisions(
    ws_root: Path,
) -> None:
    ws = create("layer3-verif", "d", root=ws_root)
    h = _make_handover(
        task_id=ws.task_id,
        to_team_suggestions=["verification"],
        decisions=[],
    )
    with pytest.raises(HandoverError, match="requires non-empty decisions"):
        save_handover(ws, h)


def test_save_handover_rejects_forward_to_implementation_fix_without_decisions(
    ws_root: Path,
) -> None:
    ws = create("layer3-fix", "d", root=ws_root)
    h = _make_handover(
        task_id=ws.task_id,
        to_team_suggestions=["implementation_fix"],
        decisions=[],
    )
    with pytest.raises(HandoverError, match="requires non-empty decisions"):
        save_handover(ws, h)


def test_save_handover_allows_other_targets_without_decisions(
    ws_root: Path,
) -> None:
    ws = create("layer3-ok", "d", root=ws_root)
    h = _make_handover(
        task_id=ws.task_id,
        to_team_suggestions=["implementation_plan"],
        decisions=[],
    )
    save_handover(ws, h)


def test_save_handover_allows_escalation_without_decisions(
    ws_root: Path,
) -> None:
    ws = create("layer3-esc", "d", root=ws_root)
    h = _make_handover(
        task_id=ws.task_id,
        handover_type="escalation",
        to_team_suggestions=["implementation"],
        decisions=[],
    )
    save_handover(ws, h)


def test_save_handover_rejects_forward_from_architecture_without_decisions(
    ws_root: Path,
) -> None:
    ws = create("layer3-arch", "d", root=ws_root)
    h = _make_handover(
        task_id=ws.task_id,
        from_team="architecture",
        to_team_suggestions=[],
        decisions=[],
    )
    with pytest.raises(HandoverError, match="requires non-empty decisions"):
        save_handover(ws, h)


def test_save_handover_allows_forward_from_architecture_with_decisions(
    ws_root: Path,
) -> None:
    ws = create("layer3-arch-ok", "d", root=ws_root)
    h = _make_handover(
        task_id=ws.task_id,
        from_team="architecture",
        to_team_suggestions=[],
        decisions=[dict(_FIXTURE_DECISION)],
    )
    save_handover(ws, h)


def test_save_handover_allows_forward_from_brainstorming_without_decisions(
    ws_root: Path,
) -> None:
    ws = create("layer3-brainstorm", "d", root=ws_root)
    h = _make_handover(
        task_id=ws.task_id,
        from_team="brainstorming",
        to_team_suggestions=[],
        decisions=[],
    )
    save_handover(ws, h)


def test_save_handover_allows_forward_from_opportunity_scout_without_decisions(
    ws_root: Path,
) -> None:
    ws = create("layer3-opp-scout", "d", root=ws_root)
    h = _make_handover(
        task_id=ws.task_id,
        from_team="opportunity_scout",
        to_team_suggestions=[],
        decisions=[],
    )
    save_handover(ws, h)


def test_handover_pr_number_round_trips(ws_root: Path) -> None:
    ws = create("prnum", "d", root=ws_root)
    h = _make_handover(task_id=ws.task_id, pr_number=99)
    path = save_handover(ws, h)
    loaded = load_handover(path)
    assert loaded.pr_number == 99


def test_handover_chain_id_defaults_to_handover_id_for_new_chain() -> None:
    h = _make_handover()
    assert h.handover_chain_id == h.handover_id


def test_handover_chain_id_explicit_value_preserved() -> None:
    root_id = "handover_t1_root001"
    h = _make_handover(
        handover_id="handover_t1_child001",
        handover_chain_id=root_id,
        revisit_of="handover_t1_root001",
    )
    assert h.handover_chain_id == root_id


def test_handover_chain_id_round_trips_on_save(ws_root: Path) -> None:
    ws = create("chain-rt", "d", root=ws_root)
    root_id = f"handover_{ws.task_id}_root"
    h = _make_handover(
        task_id=ws.task_id,
        handover_id=f"handover_{ws.task_id}_child",
        handover_chain_id=root_id,
        revisit_of=root_id,
    )
    path = save_handover(ws, h)
    loaded = load_handover(path)
    assert loaded.handover_chain_id == root_id


def test_handover_v14_documents_still_load(ws_root: Path) -> None:
    ws = create("v14", "d", root=ws_root)
    root_id = f"handover_{ws.task_id}_root"
    root_payload = {
        "schema_version": "1.4",
        "handover_id": root_id,
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "implementation_plan",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": ["implementation"],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "decisions": [dict(_FIXTURE_DECISION)],
        "full_synthesis": "v1.4 root",
    }
    child_id = f"handover_{ws.task_id}_child"
    child_payload = {
        "schema_version": "1.4",
        "handover_id": child_id,
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "implementation",
        "from_invocation_id": "inv_002",
        "to_team_suggestions": ["verification"],
        "iteration": 2,
        "revisit_of": root_id,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "decisions": [dict(_FIXTURE_DECISION)],
        "full_synthesis": "v1.4 child",
    }
    handover_dir = ws.path / "handovers"
    handover_dir.mkdir(parents=True, exist_ok=True)
    (handover_dir / f"handover_{ws.task_id}_001.yaml").write_text(
        yaml.safe_dump(root_payload), encoding="utf-8"
    )
    (handover_dir / f"handover_{ws.task_id}_002.yaml").write_text(
        yaml.safe_dump(child_payload), encoding="utf-8"
    )
    root = load_handover(handover_dir / f"handover_{ws.task_id}_001.yaml")
    child = load_handover(handover_dir / f"handover_{ws.task_id}_002.yaml")
    assert root.handover_chain_id == root_id
    assert child.handover_chain_id == root_id


def test_handover_v10_load_synthesizes_chain_id_as_self_for_root(ws_root: Path) -> None:
    ws = create("v10chain", "d", root=ws_root)
    hid = f"handover_{ws.task_id}_001"
    payload = {
        "schema_version": "1.0",
        "handover_id": hid,
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "research",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": ["implementation_plan"],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "full_synthesis": "v1.0 root",
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.handover_chain_id == hid


def test_handover_v11_load_synthesizes_chain_id_as_self_for_root(ws_root: Path) -> None:
    ws = create("v11chain", "d", root=ws_root)
    hid = f"handover_{ws.task_id}_001"
    payload = {
        "schema_version": "1.1",
        "handover_id": hid,
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "research",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": [],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "forbidden_actions": [],
        "full_synthesis": "v1.1 root",
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.handover_chain_id == hid


def test_handover_v12_load_synthesizes_chain_id(ws_root: Path) -> None:
    ws = create("v12chain", "d", root=ws_root)
    hid = f"handover_{ws.task_id}_001"
    payload = {
        "schema_version": "1.2",
        "handover_id": hid,
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "implementation_plan",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": ["implementation"],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "full_synthesis": "v1.2 root",
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.handover_chain_id == hid


def test_handover_v13_load_synthesizes_chain_id(ws_root: Path) -> None:
    ws = create("v13chain", "d", root=ws_root)
    hid = f"handover_{ws.task_id}_001"
    payload = {
        "schema_version": "1.3",
        "handover_id": hid,
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "implementation_plan",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": ["implementation"],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "decisions": [dict(_FIXTURE_DECISION)],
        "pr_number": 5,
        "files_to_edit": ["alan/router.py"],
        "full_synthesis": "v1.3 root",
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.handover_chain_id == hid


def test_load_handover_raises_when_handover_id_absent(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    payload = {
        "schema_version": "1.3",
        "handover_type": "forward",
        "task_id": "task-x",
        "from_team": "researcher",
        "from_invocation_id": "inv_001",
        "iteration": 1,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "full_synthesis": "x",
        "revisit_of": None,
    }
    bad.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(HandoverError, match="handover_id missing or empty"):
        load_handover(bad)


def test_synthesize_chain_id_raises_on_empty_handover_id(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    payload = {
        "schema_version": "1.0",
        "handover_id": "",
        "handover_type": "forward",
        "task_id": "x",
        "from_team": "r",
        "from_invocation_id": "i",
        "iteration": 1,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "full_synthesis": "x",
        "revisit_of": "some-parent",
    }
    bad.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(HandoverError, match="handover_id missing or empty"):
        handover_mod._synthesize_chain_id(bad, payload)  # noqa: SLF001


def test_build_chain_index_skips_invalid_yaml(tmp_path: Path) -> None:
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("{{invalid: :", encoding="utf-8")
    good = tmp_path / "good.yaml"
    good.write_text(
        yaml.safe_dump({"handover_id": "good-err", "revisit_of": None}),
        encoding="utf-8",
    )
    index = handover_mod._build_chain_index(tmp_path)  # noqa: SLF001
    assert "good-err" in index
    assert len(index) == 1


def test_build_chain_index_skips_non_dict_yaml(tmp_path: Path) -> None:
    list_yaml = tmp_path / "list.yaml"
    list_yaml.write_text(yaml.safe_dump(["a", "b"]), encoding="utf-8")
    good = tmp_path / "good.yaml"
    good.write_text(
        yaml.safe_dump({"handover_id": "good-list", "revisit_of": None}),
        encoding="utf-8",
    )
    index = handover_mod._build_chain_index(tmp_path)  # noqa: SLF001
    assert "good-list" in index
    assert len(index) == 1


def test_build_chain_index_skips_non_string_handover_id(tmp_path: Path) -> None:
    int_id = tmp_path / "intid.yaml"
    int_id.write_text(yaml.safe_dump({"handover_id": 123, "revisit_of": None}), encoding="utf-8")
    good = tmp_path / "good.yaml"
    good.write_text(
        yaml.safe_dump({"handover_id": "good-int", "revisit_of": None}),
        encoding="utf-8",
    )
    index = handover_mod._build_chain_index(tmp_path)  # noqa: SLF001
    assert "good-int" in index
    assert len(index) == 1


def test_build_chain_index_caches_repeated_calls(tmp_path: Path) -> None:
    handover_mod._CHAIN_INDEX_CACHE.pop(tmp_path, None)  # noqa: SLF001
    good = tmp_path / "h.yaml"
    good.write_text(
        yaml.safe_dump({"handover_id": "cached", "revisit_of": None}),
        encoding="utf-8",
    )
    first = handover_mod._build_chain_index(tmp_path)  # noqa: SLF001
    second = handover_mod._build_chain_index(tmp_path)  # noqa: SLF001
    assert first is second
    assert "cached" in first


def test_build_chain_index_falls_back_when_stat_fails(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    assert not missing.exists()
    index = handover_mod._build_chain_index(missing)  # noqa: SLF001
    assert index == {}


def test_walk_to_root_cycle_detection(tmp_path: Path) -> None:
    index: dict[str, str | None] = {"a": "b", "b": "a"}
    result = handover_mod._walk_to_root("a", index)  # noqa: SLF001
    assert result == "a"


def test_synthesize_chain_id_non_string_revisit_of_treated_as_root(tmp_path: Path) -> None:
    payload: dict[str, object] = {
        "schema_version": "1.0",
        "handover_id": "handover_x_001",
        "revisit_of": 42,
    }
    result = handover_mod._synthesize_chain_id(tmp_path / "dummy.yaml", payload)  # noqa: SLF001
    assert result == "handover_x_001"


def test_handover_workspace_id_defaults_to_task_id() -> None:
    h = _make_handover(task_id="t1")
    assert h.workspace_id == "t1"


def test_handover_workspace_id_explicit_value_preserved() -> None:
    h = _make_handover(task_id="sub-task-001", workspace_id="root-workspace")
    assert h.workspace_id == "root-workspace"


def test_handover_v16_load_fills_workspace_id_from_task_id(ws_root: Path) -> None:
    ws = create("v16ws", "d", root=ws_root)
    payload = {
        "schema_version": "1.6",
        "handover_id": f"handover_{ws.task_id}_001",
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "implementation_plan",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": ["implementation"],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "decisions": [dict(_FIXTURE_DECISION)],
        "full_synthesis": "legacy v1.6 payload without workspace_id",
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.schema_version == "1.6"
    assert loaded.workspace_id == ws.task_id


def test_handover_v17_workspace_id_round_trip(ws_root: Path) -> None:
    ws = create("v17ws", "d", root=ws_root)
    h = _make_handover(task_id=ws.task_id, workspace_id="explicit-ws")
    path = save_handover(ws, h)
    loaded = load_handover(path)
    assert loaded.schema_version == "1.23"
    assert loaded.workspace_id == "explicit-ws"


def test_handover_default_schema_version_is_122() -> None:
    h = _make_handover()
    assert h.schema_version == "1.23"
    assert SCHEMA_VERSION == "1.23"


def test_injected_task_model_fields() -> None:
    from alan.handover import InjectedTask

    it = InjectedTask(id="t1", team="research", instructions="do X")
    assert it.id == "t1"
    assert it.team == "research"
    assert it.instructions == "do X"
    assert it.depends_on == []


def test_injected_task_instructions_defaults_none() -> None:
    from alan.handover import InjectedTask

    it = InjectedTask(id="t1", team="research")
    assert it.instructions is None


def test_injected_task_depends_on_defaults_empty() -> None:
    from alan.handover import InjectedTask

    it = InjectedTask(id="t1", team="research", depends_on=["t0"])
    assert it.depends_on == ["t0"]


def test_handover_injected_tasks_round_trip(ws_root: Path) -> None:
    from alan.handover import InjectedTask

    ws = create("inj-rt", "d", root=ws_root)
    h = _make_handover(
        task_id=ws.task_id,
        injected_tasks=[
            InjectedTask(id="sub-a", team="research", instructions="work").model_dump(),
        ],
    )
    path = save_handover(ws, h)
    loaded = load_handover(path)
    assert len(loaded.injected_tasks) == 1
    assert loaded.injected_tasks[0].id == "sub-a"
    assert loaded.injected_tasks[0].team == "research"
    assert loaded.injected_tasks[0].instructions == "work"


def test_handover_injected_tasks_defaults_empty() -> None:
    h = _make_handover()
    assert h.injected_tasks == []


def test_injected_task_dod_clauses_round_trip(ws_root: Path) -> None:
    from alan.handover import InjectedTask
    from alan.workspace import TaskDoDClause

    ws = create("inj-dod-rt", "d", root=ws_root)
    h = _make_handover(
        task_id=ws.task_id,
        injected_tasks=[
            InjectedTask(
                id="sub",
                team="implementation",
                dod_clauses=[TaskDoDClause(text="x", covers=["req-1"])],
            ).model_dump(),
        ],
    )
    loaded = load_handover(save_handover(ws, h))
    assert loaded.injected_tasks[0].dod_clauses[0].covers == ["req-1"]


def test_injected_task_dod_clauses_default_empty() -> None:
    from alan.handover import InjectedTask

    assert InjectedTask(id="t1", team="research").dod_clauses == []



def test_handover_split_rationale_round_trip(ws_root: Path) -> None:
    ws = create("split-rat-rt", "d", root=ws_root)
    h = _make_handover(task_id=ws.task_id, split_rationale="kept whole")
    assert load_handover(save_handover(ws, h)).split_rationale == "kept whole"


def test_handover_split_rationale_defaults_none() -> None:
    assert _make_handover().split_rationale is None


def test_v17_payload_loads_with_empty_injected_tasks(ws_root: Path) -> None:
    ws = create("v17-inj-compat", "d", root=ws_root)
    payload = {
        "schema_version": "1.7",
        "handover_id": f"handover_{ws.task_id}_001",
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "implementation_plan",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": ["implementation"],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "decisions": [dict(_FIXTURE_DECISION)],
        "full_synthesis": "v1.7 payload without injected_tasks",
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.schema_version == "1.7"
    assert loaded.injected_tasks == []


def test_handover_110_round_trip(ws_root: Path) -> None:
    ws = create("v110-rt", "d", root=ws_root)
    from alan.handover import InjectedTask

    h = _make_handover(
        task_id=ws.task_id,
        injected_tasks=[
            InjectedTask(id="x", team="research").model_dump(),
        ],
    )
    assert h.schema_version == "1.23"
    path = save_handover(ws, h)
    loaded = load_handover(path)
    assert loaded.schema_version == "1.23"
    assert len(loaded.injected_tasks) == 1
    assert loaded.injected_tasks[0].team == "research"


def test_injected_task_title_defaults_none() -> None:
    from alan.handover import InjectedTask

    it = InjectedTask(id="t1", team="research")
    assert it.title is None


def test_injected_task_title_accepts_string() -> None:
    from alan.handover import InjectedTask

    it = InjectedTask(id="t1", team="research", title="Backend API layer")
    assert it.title == "Backend API layer"


def test_injected_task_title_round_trips(ws_root: Path) -> None:
    from alan.handover import InjectedTask

    ws = create("inj-title-rt", "d", root=ws_root)
    h = _make_handover(
        task_id=ws.task_id,
        injected_tasks=[
            InjectedTask(id="sub", team="implementation", title="Add user model").model_dump(),
        ],
    )
    loaded = load_handover(save_handover(ws, h))
    assert loaded.injected_tasks[0].title == "Add user model"


def test_handover_v19_payload_still_loads(ws_root: Path) -> None:
    ws = create("v19-compat", "d", root=ws_root)
    payload = {
        "schema_version": "1.9",
        "handover_id": f"handover_{ws.task_id}_001",
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "implementation_plan",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": ["implementation"],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "decisions": [dict(_FIXTURE_DECISION)],
        "full_synthesis": "v1.9 payload without title on injected_task",
        "injected_tasks": [
            {"id": "sub", "team": "implementation", "instructions": None,
             "depends_on": [], "dod_clauses": []},
        ],
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.schema_version == "1.9"
    assert loaded.injected_tasks[0].title is None


def test_test_scenario_test_ref_defaults_none() -> None:
    s = TestScenario(id="s1", description="d", given="g", when="w", then="t")
    assert s.test_ref is None


def test_test_scenario_test_ref_stores_value() -> None:
    s = TestScenario(
        id="s1", description="d", given="g", when="", then="", test_ref="tests/test_x.py"
    )
    assert s.test_ref == "tests/test_x.py"


def test_injected_task_scenarios_defaults_empty() -> None:
    from alan.handover import InjectedTask as _InjectedTask

    it = _InjectedTask(id="t1", team="implementation")
    assert it.scenarios == []
    assert it.scenarios_waived is False


def test_injected_task_scenarios_stores_ids() -> None:
    from alan.handover import InjectedTask as _InjectedTask

    it = _InjectedTask(id="t1", team="implementation", scenarios=["s1", "s2"])
    assert it.scenarios == ["s1", "s2"]


def test_injected_task_scenarios_waived_flag() -> None:
    from alan.handover import InjectedTask as _InjectedTask

    it = _InjectedTask(id="t1", team="implementation", scenarios_waived=True)
    assert it.scenarios_waived is True


def test_handover_scenarios_waived_defaults_false() -> None:
    h = _make_handover()
    assert h.scenarios_waived is False


def test_handover_scenarios_waived_round_trips(ws_root: Path) -> None:
    ws = create("sw-rt", "d", root=ws_root)
    h = _make_handover(task_id=ws.task_id)
    h = h.model_copy(update={"scenarios_waived": True})
    path = save_handover(ws, h)
    loaded = load_handover(path)
    assert loaded.scenarios_waived is True


def test_schema_111_payload_loads(ws_root: Path) -> None:
    ws = create("v111-compat", "d", root=ws_root)
    payload = {
        "schema_version": "1.11",
        "handover_id": f"handover_{ws.task_id}_001",
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "implementation",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": ["verification"],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "decisions": [dict(_FIXTURE_DECISION)],
        "full_synthesis": "schema 1.11 payload",
        "scenarios_waived": True,
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.schema_version == "1.11"
    assert loaded.scenarios_waived is True


def test_list_handovers_skips_corrupt_and_returns_valid(ws_root: Path) -> None:
    from alan.handover import list_handovers

    ws = create("list-corrupt", "d", root=ws_root)
    h = _make_handover(task_id=ws.task_id)
    save_handover(ws, h)
    corrupt_path = ws.path / "handovers" / f"handover_{ws.task_id}_002.yaml"
    corrupt_path.write_text("not: valid: yaml: [", encoding="utf-8")
    listed = list_handovers(ws)
    assert len(listed) == 1
    assert listed[0].handover_id == h.handover_id


def test_handover_dependency_bot_authorized_defaults_false() -> None:
    h = _make_handover()
    assert h.dependency_bot_authorized is False


def test_handover_dependency_bot_authorized_round_trips(ws_root: Path) -> None:
    ws = create("dep-bot-rt", "d", root=ws_root)
    h = _make_handover(task_id=ws.task_id, dependency_bot_authorized=True)
    path = save_handover(ws, h)
    loaded = load_handover(path)
    assert loaded.dependency_bot_authorized is True


def test_handover_v111_loads_with_dependency_bot_authorized_false(ws_root: Path) -> None:
    ws = create("v111-dep-bot", "d", root=ws_root)
    payload = {
        "schema_version": "1.11",
        "handover_id": f"handover_{ws.task_id}_001",
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "implementation_plan",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": ["implementation"],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "decisions": [dict(_FIXTURE_DECISION)],
        "full_synthesis": "v1.11 payload without dependency_bot_authorized",
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.schema_version == "1.11"
    assert loaded.dependency_bot_authorized is False


def test_injected_task_files_defaults_empty() -> None:
    from alan.handover import InjectedTask

    it = InjectedTask(id="t1", team="implementation")
    assert it.files == []


def test_injected_task_files_stores_value() -> None:
    from alan.handover import InjectedTask

    it = InjectedTask(id="t1", team="implementation", files=["alan/foo.py"])
    assert it.files == ["alan/foo.py"]


def test_schema_113_payload_loads(ws_root: Path) -> None:
    ws = create("v113-compat", "d", root=ws_root)
    payload = {
        "schema_version": "1.13",
        "handover_id": f"handover_{ws.task_id}_001",
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "implementation",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": ["verification"],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "decisions": [dict(_FIXTURE_DECISION)],
        "full_synthesis": "schema 1.13 payload",
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.schema_version == "1.13"


def test_handover_v112_injected_task_loads_without_files(ws_root: Path) -> None:
    from alan.handover import InjectedTask

    ws = create("v112-no-files", "d", root=ws_root)
    payload = {
        "schema_version": "1.12",
        "handover_id": f"handover_{ws.task_id}_001",
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "implementation_plan",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": ["implementation"],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "decisions": [dict(_FIXTURE_DECISION)],
        "full_synthesis": "v1.12 payload without files",
        "injected_tasks": [
            InjectedTask(id="x", team="research").model_dump(exclude={"files"})
        ],
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.schema_version == "1.12"
    assert loaded.injected_tasks[0].files == []


def test_list_handovers_corrupt_is_logged(
    ws_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from alan.handover import list_handovers

    ws = create("list-log", "d", root=ws_root)
    handovers_dir = ws.path / "handovers"
    handovers_dir.mkdir(parents=True, exist_ok=True)
    (handovers_dir / f"handover_{ws.task_id}_001.yaml").write_text(
        "!@#$%^&*()", encoding="utf-8"
    )
    with caplog.at_level("WARNING", logger="alan.handover"):
        result = list_handovers(ws)
    assert result == []
    assert any("skipping unreadable" in r.message for r in caplog.records)


def test_open_question_answer_defaults_none() -> None:
    q = OpenQuestion(id="q1", question="which?", resolver_role="human", blocking=True)
    assert q.answer is None


def test_open_question_answer_round_trips(ws_root: Path) -> None:
    ws = create("answer-rt", "d", root=ws_root)
    h = _make_handover(
        task_id=ws.task_id,
        open_questions=[
            OpenQuestion(
                id="q1",
                question="which db?",
                resolver_role="human",
                blocking=True,
                answer="use sqlite",
            ).model_dump(),
        ],
    )
    path = save_handover(ws, h)
    loaded = load_handover(path)
    assert loaded.open_questions[0].answer == "use sqlite"


def test_schema_114_payload_loads(ws_root: Path) -> None:
    ws = create("v114-compat", "d", root=ws_root)
    payload = {
        "schema_version": "1.14",
        "handover_id": f"handover_{ws.task_id}_001",
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "implementation",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": ["verification"],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "decisions": [dict(_FIXTURE_DECISION)],
        "full_synthesis": "schema 1.14 payload",
        "open_questions": [
            {
                "id": "q1",
                "question": "which?",
                "resolver_role": "human",
                "blocking": True,
                "prior_attempts": [],
                "answer": "resolved",
            },
        ],
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.schema_version == "1.14"
    assert loaded.open_questions[0].answer == "resolved"


def test_handover_v113_payload_loads_with_unanswered_open_question(ws_root: Path) -> None:
    ws = create("v113-oq-compat", "d", root=ws_root)
    payload = {
        "schema_version": "1.13",
        "handover_id": f"handover_{ws.task_id}_001",
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "research",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": ["implementation_plan"],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "full_synthesis": "v1.13 payload without answer field on open_questions",
        "open_questions": [
            {
                "id": "q1",
                "question": "which?",
                "resolver_role": "human",
                "blocking": True,
                "prior_attempts": [],
            },
        ],
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.schema_version == "1.13"
    assert loaded.open_questions[0].answer is None


def test_handover_plan_synthesis_defaults_none() -> None:
    assert _make_handover().plan_synthesis is None


def test_handover_plan_synthesis_round_trip(ws_root: Path) -> None:
    ws = create("plan-synth-rt", "d", root=ws_root)
    h = _make_handover(task_id=ws.task_id, plan_synthesis="## Implementation steps\n1. do x")
    path = save_handover(ws, h)
    loaded = load_handover(path)
    assert loaded.plan_synthesis == "## Implementation steps\n1. do x"


def test_schema_115_payload_loads_with_plan_synthesis(ws_root: Path) -> None:
    ws = create("v115-compat", "d", root=ws_root)
    payload = {
        "schema_version": "1.15",
        "handover_id": f"handover_{ws.task_id}_001",
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "decomposer",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": ["implementation"],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "decisions": [dict(_FIXTURE_DECISION)],
        "full_synthesis": "schema 1.15 payload",
        "plan_synthesis": "## Implementation steps\n1. do the thing",
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.schema_version == "1.15"
    assert loaded.plan_synthesis == "## Implementation steps\n1. do the thing"


def test_handover_v114_payload_loads_with_plan_synthesis_none(ws_root: Path) -> None:
    ws = create("v114-plan-synth-compat", "d", root=ws_root)
    payload = {
        "schema_version": "1.14",
        "handover_id": f"handover_{ws.task_id}_001",
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "implementation_plan",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": ["decomposer"],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "decisions": [dict(_FIXTURE_DECISION)],
        "full_synthesis": "v1.14 payload without plan_synthesis",
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.schema_version == "1.14"
    assert loaded.plan_synthesis is None


@pytest.fixture(autouse=True)
def _clear_handover_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handover_mod, "_HANDOVER_CACHE", {})


def test_load_handover_cached_hit_calls_load_once(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("cache-hit", "d", root=ws_root)
    path = save_handover(ws, _make_handover(task_id=ws.task_id))
    call_count = 0
    original = handover_mod.load_handover

    def spy(p: Path) -> Handover:
        nonlocal call_count
        call_count += 1
        return original(p)

    monkeypatch.setattr(handover_mod, "load_handover", spy)
    h1 = handover_mod.load_handover_cached(path)
    h2 = handover_mod.load_handover_cached(path)
    assert call_count == 1
    assert h1.handover_id == h2.handover_id


def test_load_handover_cached_fallthrough_on_stat_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_count = 0
    original = handover_mod.load_handover

    def spy(p: Path) -> Handover:
        nonlocal call_count
        call_count += 1
        return original(p)

    monkeypatch.setattr(handover_mod, "load_handover", spy)
    path = tmp_path / "ghost.yaml"
    with pytest.raises(HandoverError, match="missing file"):
        handover_mod.load_handover_cached(path)
    assert call_count == 1
    assert path not in handover_mod._HANDOVER_CACHE


def test_list_handovers_reuses_cache_across_calls(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("cache-list", "d", root=ws_root)
    save_handover(ws, _make_handover(task_id=ws.task_id))
    save_handover(ws, _make_handover(task_id=ws.task_id))
    call_count = 0
    original = handover_mod.load_handover

    def spy(p: Path) -> Handover:
        nonlocal call_count
        call_count += 1
        return original(p)

    monkeypatch.setattr(handover_mod, "load_handover", spy)
    first = handover_mod.list_handovers(ws)
    second = handover_mod.list_handovers(ws)
    assert call_count == 2
    assert [h.handover_id for h in first] == [h.handover_id for h in second]


def test_verifier_metrics_default_none_and_empty() -> None:
    h = _make_handover()
    assert h.verifier_completeness is None
    assert h.verifier_confidence is None
    assert h.verifier_gaps == []


def test_verifier_metrics_round_trip(ws_root: Path) -> None:
    ws = create("verifier-metrics-rt", "d", root=ws_root)
    h = _make_handover(
        task_id=ws.task_id,
        verifier_completeness=0.45,
        verifier_confidence=0.55,
        verifier_gaps=["gap one", "gap two"],
    )
    path = save_handover(ws, h)
    loaded = load_handover(path)
    assert loaded.schema_version == "1.23"
    assert loaded.verifier_completeness == 0.45
    assert loaded.verifier_confidence == 0.55
    assert loaded.verifier_gaps == ["gap one", "gap two"]


def test_verifier_completeness_rejects_out_of_bounds() -> None:
    with pytest.raises(ValidationError, match="verifier_completeness"):
        _make_handover(verifier_completeness=1.5)
    with pytest.raises(ValidationError, match="verifier_completeness"):
        _make_handover(verifier_completeness=-0.1)


def test_verifier_confidence_rejects_out_of_bounds() -> None:
    with pytest.raises(ValidationError, match="verifier_confidence"):
        _make_handover(verifier_confidence=1.5)
    with pytest.raises(ValidationError, match="verifier_confidence"):
        _make_handover(verifier_confidence=-0.1)


def test_verifier_gaps_rejects_more_than_twenty_items() -> None:
    with pytest.raises(ValidationError, match="verifier_gaps"):
        _make_handover(verifier_gaps=[f"gap {i}" for i in range(21)])


def test_verifier_gaps_accepts_exactly_twenty_items(ws_root: Path) -> None:
    ws = create("verifier-gaps-20", "d", root=ws_root)
    h = _make_handover(task_id=ws.task_id, verifier_gaps=[f"gap {i}" for i in range(20)])
    assert len(h.verifier_gaps) == 20


def test_schema_115_payload_loads_with_verifier_metrics_defaulted(ws_root: Path) -> None:
    ws = create("v115-verifier-compat", "d", root=ws_root)
    payload = {
        "schema_version": "1.15",
        "handover_id": f"handover_{ws.task_id}_001",
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "research",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": [],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "full_synthesis": "v1.15 payload without verifier metrics",
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.schema_version == "1.15"
    assert loaded.verifier_completeness is None
    assert loaded.verifier_confidence is None
    assert loaded.verifier_gaps == []


def test_schema_116_payload_loads(ws_root: Path) -> None:
    ws = create("v116-compat", "d", root=ws_root)
    payload = {
        "schema_version": "1.16",
        "handover_id": f"handover_{ws.task_id}_001",
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "research",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": [],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "full_synthesis": "schema 1.16 payload",
        "verifier_completeness": 0.3,
        "verifier_confidence": 0.4,
        "verifier_gaps": ["missed x"],
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.schema_version == "1.16"
    assert loaded.verifier_completeness == 0.3
    assert loaded.verifier_confidence == 0.4
    assert loaded.verifier_gaps == ["missed x"]


def test_open_question_question_class_and_recommended_default_none() -> None:
    q = OpenQuestion(id="q1", question="which?", resolver_role="human", blocking=True)
    assert q.question_class is None
    assert q.recommended is None


def test_open_question_question_class_and_recommended_round_trip(
    ws_root: Path,
) -> None:
    ws = create("oq-class-rt", "d", root=ws_root)
    h = _make_handover(
        task_id=ws.task_id,
        open_questions=[
            OpenQuestion(
                id="q1",
                question="which db?",
                proposed_answer_space=["sqlite", "duckdb"],
                recommended="sqlite",
                question_class="fix_escalation",
                resolver_role="human",
                blocking=True,
            ).model_dump(),
        ],
    )
    path = save_handover(ws, h)
    loaded = load_handover(path)
    assert loaded.schema_version == "1.23"
    [q] = loaded.open_questions
    assert q.question_class == "fix_escalation"
    assert q.recommended == "sqlite"


def test_open_question_rejects_unknown_question_class() -> None:
    with pytest.raises(ValidationError, match="question_class"):
        OpenQuestion(
            id="q1",
            question="x",
            proposed_answer_space=["a", "b"],
            question_class="not_a_real_class",  # type: ignore[arg-type]
            resolver_role="human",
            blocking=True,
        )


def test_open_question_recommended_must_be_in_options() -> None:
    with pytest.raises(ValidationError, match="recommended must be one of"):
        OpenQuestion(
            id="q1",
            question="x",
            proposed_answer_space=["a", "b"],
            recommended="c",
            resolver_role="human",
            blocking=True,
        )


def test_open_question_options_must_have_at_least_two_when_non_empty() -> None:
    with pytest.raises(ValidationError, match="2-4 options"):
        OpenQuestion(
            id="q1",
            question="x",
            proposed_answer_space=["only one"],
            resolver_role="human",
            blocking=True,
        )


def test_open_question_options_reject_more_than_four() -> None:
    with pytest.raises(ValidationError, match="2-4 options"):
        OpenQuestion(
            id="q1",
            question="x",
            proposed_answer_space=["a", "b", "c", "d", "e"],
            resolver_role="human",
            blocking=True,
        )


def test_open_question_empty_options_bypass_count_validation() -> None:
    q = OpenQuestion(id="q1", question="x", resolver_role="human", blocking=True)
    assert q.proposed_answer_space == []
    assert q.recommended is None


def test_schema_117_payload_loads_with_question_class_and_recommended(
    ws_root: Path,
) -> None:
    ws = create("v117-compat", "d", root=ws_root)
    payload = {
        "schema_version": "1.17",
        "handover_id": f"handover_{ws.task_id}_001",
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "verification",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": [],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "full_synthesis": "schema 1.17 payload",
        "open_questions": [
            {
                "id": "verif-judge-parse",
                "question": "judge failed to parse",
                "proposed_answer_space": ["retry", "review manually"],
                "recommended": "retry",
                "question_class": "verification_escalation",
                "resolver_role": "human",
                "blocking": True,
            }
        ],
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.schema_version == "1.17"
    [q] = loaded.open_questions
    assert q.question_class == "verification_escalation"
    assert q.recommended == "retry"


def test_schema_116_payload_loads_with_open_question_defaults(
    ws_root: Path,
) -> None:
    ws = create("v116-oq-compat", "d", root=ws_root)
    payload = {
        "schema_version": "1.16",
        "handover_id": f"handover_{ws.task_id}_001",
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "pr_merge",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": [],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "full_synthesis": "schema 1.16 payload",
        "open_questions": [
            {
                "id": "pr-merge-failed",
                "question": "checks failed",
                "resolver_role": "human",
                "blocking": True,
            }
        ],
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.schema_version == "1.16"
    [q] = loaded.open_questions
    assert q.question_class is None
    assert q.recommended is None


def test_credential_failure_defaults_none() -> None:
    h = _make_handover()
    assert h.credential_failure is None


def test_credential_failure_round_trip(ws_root: Path) -> None:
    ws = create("cred-failure-rt", "d", root=ws_root)
    h = _make_handover(task_id=ws.task_id, credential_failure="expired_token")
    path = save_handover(ws, h)
    loaded = load_handover(path)
    assert loaded.schema_version == "1.23"
    assert loaded.credential_failure == "expired_token"


def test_credential_failure_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError, match="credential_failure"):
        _make_handover(credential_failure="revoked")


def test_schema_117_payload_loads_with_credential_failure_defaulted(
    ws_root: Path,
) -> None:
    ws = create("v117-cred-compat", "d", root=ws_root)
    payload = {
        "schema_version": "1.17",
        "handover_id": f"handover_{ws.task_id}_001",
        "handover_type": "escalation",
        "task_id": ws.task_id,
        "from_team": "implementation",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": [],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "full_synthesis": "schema 1.17 payload predating credential_failure",
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.schema_version == "1.17"
    assert loaded.credential_failure is None


def test_scenario_outcome_valid_status_literals() -> None:
    for status in ("PASS", "FAIL", "SKIPPED", "DOCUMENTED"):
        outcome = ScenarioOutcome(scenario_id="s1", status=status)
        assert outcome.status == status
        assert outcome.diagnostic == ""


def test_scenario_outcome_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError, match="status"):
        ScenarioOutcome.model_validate({"scenario_id": "s1", "status": "MAYBE"})


def test_scenario_outcome_diagnostic_max_length_boundary() -> None:
    ScenarioOutcome(scenario_id="s1", status="FAIL", diagnostic="x" * 2000)
    with pytest.raises(ValidationError, match="diagnostic"):
        ScenarioOutcome(scenario_id="s1", status="FAIL", diagnostic="x" * 2001)


def test_handover_scenario_results_defaults_empty() -> None:
    assert _make_handover().scenario_results == []


def test_handover_scenario_results_round_trip(ws_root: Path) -> None:
    ws = create("scenario-results-rt", "d", root=ws_root)
    h = _make_handover(
        task_id=ws.task_id,
        scenario_results=[
            ScenarioOutcome(scenario_id="s1", status="PASS").model_dump(),
            ScenarioOutcome(
                scenario_id="s2", status="SKIPPED", diagnostic="pytest scenario not found (rc=4)"
            ).model_dump(),
        ],
    )
    path = save_handover(ws, h)
    loaded = load_handover(path)
    assert loaded.schema_version == "1.23"
    assert [r.scenario_id for r in loaded.scenario_results] == ["s1", "s2"]
    assert loaded.scenario_results[0].status == "PASS"
    assert loaded.scenario_results[1].diagnostic == "pytest scenario not found (rc=4)"


def test_schema_119_payload_loads() -> None:
    assert "1.19" in handover_mod._SUPPORTED_SCHEMA_VERSIONS  # noqa: SLF001


def test_schema_118_payload_loads_with_scenario_results_defaulted(ws_root: Path) -> None:
    ws = create("v118-scenario-results-compat", "d", root=ws_root)
    payload = {
        "schema_version": "1.18",
        "handover_id": f"handover_{ws.task_id}_001",
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "verification",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": [],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "full_synthesis": "schema 1.18 payload predating scenario_results",
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.schema_version == "1.18"
    assert loaded.scenario_results == []


def _blocking_handover(
    task_id: str, *, questions: list[OpenQuestion], **overrides: object
) -> Handover:
    return _make_handover(
        task_id=task_id,
        handover_type="escalation",
        open_questions=[q.model_dump() for q in questions],
        **overrides,
    )


def test_record_answer_appends_revisit_amend_without_mutating_original(ws_root: Path) -> None:
    from alan.handover import list_handovers, record_answer

    ws = create("record-answer", "d", root=ws_root)
    q = OpenQuestion(
        id="q1",
        question="which db?",
        proposed_answer_space=["sqlite", "duckdb"],
        resolver_role="human",
        blocking=True,
    )
    blocking = _blocking_handover(ws.task_id, questions=[q], pr_number=882)
    save_handover(ws, blocking)

    recorded = record_answer(ws, blocking, "q1", "sqlite", from_team=blocking.from_team)

    assert recorded.handover_type == "revisit_amend"
    assert recorded.revisit_of == blocking.handover_id
    assert recorded.pr_number == 882
    [answered_q] = recorded.open_questions
    assert answered_q.id == "q1"
    assert answered_q.answer == "sqlite"

    all_handovers = list_handovers(ws)
    assert len(all_handovers) == 2
    original_reloaded = next(h for h in all_handovers if h.handover_id == blocking.handover_id)
    assert original_reloaded.open_questions[0].answer is None


def test_record_answer_carries_forward_blocking_handover_payload(ws_root: Path) -> None:
    from alan.handover import record_answer

    ws = create("record-answer-carry", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="waiver?", proposed_answer_space=["yes", "no"],
        resolver_role="human", blocking=True, question_class="fix_escalation",
    )
    scenario = TestScenario(
        id="s1", description="parity", given="a composed prompt", when="judge_case runs",
        then="the composed prompt reaches the model",
    )
    blocking = _blocking_handover(
        ws.task_id,
        questions=[q],
        pr_number=882,
        test_scenarios=[scenario.model_dump()],
        files_to_edit=["alan/judge_eval.py"],
        forbidden_actions=["do not amend prior commits"],
        constraints_inherited=["keep deny_all_tools=True"],
        verifier_completeness=0.8,
        verifier_confidence=0.75,
        verifier_gaps=["scenario 4 unrun"],
        prior_attempts_summary="one fix pass",
    )
    save_handover(ws, blocking)

    recorded = record_answer(ws, blocking, "q1", "yes", from_team="implementation_fix")

    assert recorded.pr_number == 882
    assert [s.id for s in recorded.test_scenarios] == ["s1"]
    assert recorded.files_to_edit == ["alan/judge_eval.py"]
    assert recorded.forbidden_actions == ["do not amend prior commits"]
    assert recorded.constraints_inherited == ["keep deny_all_tools=True"]
    assert [d.id for d in recorded.decisions] == [_FIXTURE_DECISION["id"]]
    assert recorded.verifier_completeness == 0.8
    assert recorded.verifier_confidence == 0.75
    assert recorded.verifier_gaps == ["scenario 4 unrun"]
    assert recorded.prior_attempts_summary == "one fix pass"

    assert recorded.schema_version == SCHEMA_VERSION
    assert recorded.handover_id != blocking.handover_id
    assert recorded.handover_type == "revisit_amend"
    assert recorded.handover_chain_id == blocking.handover_chain_id
    assert recorded.task_id == ws.task_id
    assert recorded.from_team == "implementation_fix"
    assert recorded.from_invocation_id == blocking.from_invocation_id
    assert recorded.to_team_suggestions == []
    assert recorded.next_hop is None
    assert recorded.iteration == blocking.iteration + 1
    assert recorded.full_synthesis == (
        "Recorded answer for question 'q1': yes\n\n" + blocking.full_synthesis
    )


def test_record_answer_keeps_pr_number_reachable_for_the_next_dispatch(ws_root: Path) -> None:
    from alan.dispatch import prior_handover_for_task
    from alan.handover import record_answer

    ws = create("record-answer-prior", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="waiver?", proposed_answer_space=["yes", "no"],
        resolver_role="human", blocking=True, question_class="fix_escalation",
    )
    blocking = _blocking_handover(ws.task_id, questions=[q], pr_number=882)
    save_handover(ws, blocking)

    record_answer(ws, blocking, "q1", "yes", from_team="implementation_fix")

    prior = prior_handover_for_task(ws, ws.task_id)
    assert prior is not None
    assert prior.handover_type == "revisit_amend"
    assert prior.pr_number == 882


def test_record_answer_monotonic_iteration_across_multiple_answers(ws_root: Path) -> None:
    from alan.handover import record_answer

    ws = create("record-answer-iteration", "d", root=ws_root)
    q1 = OpenQuestion(
        id="q1", question="a?", proposed_answer_space=["x", "y"],
        resolver_role="human", blocking=True,
    )
    q2 = OpenQuestion(
        id="q2", question="b?", proposed_answer_space=["x", "y"],
        resolver_role="human", blocking=True,
    )
    blocking = _blocking_handover(ws.task_id, questions=[q1, q2])
    save_handover(ws, blocking)

    first = record_answer(ws, blocking, "q1", "x", from_team=blocking.from_team)
    second = record_answer(ws, blocking, "q2", "y", from_team=blocking.from_team)

    assert first.iteration == blocking.iteration + 1
    assert second.iteration == first.iteration + 1


def test_record_answer_unknown_question_id_raises(ws_root: Path) -> None:
    from alan.handover import record_answer

    ws = create("record-answer-404", "d", root=ws_root)
    blocking = _blocking_handover(ws.task_id, questions=[])
    save_handover(ws, blocking)

    with pytest.raises(HandoverError, match="not found"):
        record_answer(ws, blocking, "missing-q", "answer", from_team=blocking.from_team)


def test_record_answer_revalidates_answer_max_length(ws_root: Path) -> None:
    from alan.handover import record_answer

    ws = create("record-answer-too-long", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="which db?", proposed_answer_space=["sqlite", "duckdb"],
        resolver_role="human", blocking=True,
    )
    blocking = _blocking_handover(ws.task_id, questions=[q])
    save_handover(ws, blocking)

    with pytest.raises(ValidationError):
        record_answer(ws, blocking, "q1", "x" * 20001, from_team=blocking.from_team)


def test_find_recorded_answer_none_when_nothing_recorded(ws_root: Path) -> None:
    from alan.handover import find_recorded_answer

    ws = create("find-none", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="which?", proposed_answer_space=["a", "b"],
        resolver_role="human", blocking=True,
    )
    blocking = _blocking_handover(ws.task_id, questions=[q])
    save_handover(ws, blocking)

    assert find_recorded_answer(ws, blocking.handover_id, "q1") is None


def test_find_recorded_answer_returns_recorded_value(ws_root: Path) -> None:
    from alan.handover import find_recorded_answer, record_answer

    ws = create("find-hit", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="which?", proposed_answer_space=["a", "b"],
        resolver_role="human", blocking=True,
    )
    blocking = _blocking_handover(ws.task_id, questions=[q])
    save_handover(ws, blocking)
    record_answer(ws, blocking, "q1", "a", from_team=blocking.from_team)

    assert find_recorded_answer(ws, blocking.handover_id, "q1") == "a"


def test_find_recorded_answer_ignores_unrelated_revisit_of(ws_root: Path) -> None:
    from alan.handover import find_recorded_answer, record_answer

    ws = create("find-unrelated", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="which?", proposed_answer_space=["a", "b"],
        resolver_role="human", blocking=True,
    )
    blocking_a = _blocking_handover(ws.task_id, questions=[q]).model_copy(
        update={
            "handover_id": f"handover_{ws.task_id}_aaa",
            "handover_chain_id": f"handover_{ws.task_id}_aaa",
        }
    )
    save_handover(ws, blocking_a)
    blocking_b = _blocking_handover(ws.task_id, questions=[q]).model_copy(
        update={
            "handover_id": f"handover_{ws.task_id}_bbb",
            "handover_chain_id": f"handover_{ws.task_id}_bbb",
        }
    )
    save_handover(ws, blocking_b)
    record_answer(ws, blocking_a, "q1", "a", from_team=blocking_a.from_team)

    assert find_recorded_answer(ws, blocking_b.handover_chain_id, "q1") is None
    assert find_recorded_answer(ws, blocking_a.handover_chain_id, "q1") == "a"


def test_find_recorded_answer_matches_across_chain_not_just_immediate_parent(
    ws_root: Path,
) -> None:
    from alan.handover import find_recorded_answer, record_answer

    ws = create("find-cross-chain", "d", root=ws_root)
    q1 = OpenQuestion(
        id="q1", question="a?", proposed_answer_space=["x", "y"],
        resolver_role="human", blocking=True,
    )
    q2 = OpenQuestion(
        id="q2", question="b?", proposed_answer_space=["x", "y"],
        resolver_role="human", blocking=True,
    )
    root = _blocking_handover(ws.task_id, questions=[q1, q2])
    save_handover(ws, root)

    intermediate = record_answer(ws, root, "q1", "x", from_team=root.from_team)
    record_answer(ws, intermediate, "q2", "y", from_team=intermediate.from_team)

    assert find_recorded_answer(ws, root.handover_chain_id, "q2") == "y"


def test_find_recorded_answer_ignores_non_revisit_amend_handover_type(ws_root: Path) -> None:
    from alan.handover import find_recorded_answer

    ws = create("find-wrong-type", "d", root=ws_root)
    q = OpenQuestion(
        id="q1",
        question="which?",
        proposed_answer_space=["a", "b"],
        resolver_role="human",
        blocking=True,
        answer="a",
    )
    blocking = _blocking_handover(ws.task_id, questions=[q])
    save_handover(ws, blocking)
    decoy = _make_handover(
        task_id=ws.task_id,
        handover_type="forward",
        to_team_suggestions=["implementation"],
        open_questions=[q.model_dump()],
        revisit_of=blocking.handover_id,
    )
    save_handover(ws, decoy)

    assert find_recorded_answer(ws, blocking.handover_id, "q1") is None


def test_unanswered_blocking_question_ids_returns_all_when_none_answered(ws_root: Path) -> None:
    from alan.handover import unanswered_blocking_question_ids

    ws = create("unanswered-all", "d", root=ws_root)
    q1 = OpenQuestion(id="q1", question="a?", resolver_role="human", blocking=True)
    q2 = OpenQuestion(id="q2", question="b?", resolver_role="human", blocking=True)
    blocking = _blocking_handover(ws.task_id, questions=[q1, q2])
    save_handover(ws, blocking)

    assert unanswered_blocking_question_ids(blocking, ws) == ["q1", "q2"]


def test_unanswered_blocking_question_ids_ignores_non_blocking(ws_root: Path) -> None:
    from alan.handover import unanswered_blocking_question_ids

    ws = create("unanswered-nonblocking", "d", root=ws_root)
    q1 = OpenQuestion(id="q1", question="a?", resolver_role="human", blocking=True)
    q2 = OpenQuestion(id="q2", question="b?", resolver_role="human", blocking=False)
    blocking = _blocking_handover(ws.task_id, questions=[q1, q2])
    save_handover(ws, blocking)

    assert unanswered_blocking_question_ids(blocking, ws) == ["q1"]


def test_unanswered_blocking_question_ids_excludes_inline_answered(ws_root: Path) -> None:
    from alan.handover import unanswered_blocking_question_ids

    ws = create("unanswered-inline", "d", root=ws_root)
    q1 = OpenQuestion(
        id="q1", question="a?", resolver_role="human", blocking=True, answer="yes"
    )
    q2 = OpenQuestion(id="q2", question="b?", resolver_role="human", blocking=True)
    blocking = _blocking_handover(ws.task_id, questions=[q1, q2])
    save_handover(ws, blocking)

    assert unanswered_blocking_question_ids(blocking, ws) == ["q2"]


def test_unanswered_blocking_question_ids_excludes_recorded_answer(ws_root: Path) -> None:
    from alan.handover import record_answer, unanswered_blocking_question_ids

    ws = create("unanswered-recorded", "d", root=ws_root)
    q1 = OpenQuestion(id="q1", question="a?", resolver_role="human", blocking=True)
    q2 = OpenQuestion(id="q2", question="b?", resolver_role="human", blocking=True)
    blocking = _blocking_handover(ws.task_id, questions=[q1, q2])
    save_handover(ws, blocking)
    record_answer(ws, blocking, "q1", "yes", from_team=blocking.from_team)

    assert unanswered_blocking_question_ids(blocking, ws) == ["q2"]


def test_unanswered_blocking_question_ids_empty_when_all_answered(ws_root: Path) -> None:
    from alan.handover import record_answer, unanswered_blocking_question_ids

    ws = create("unanswered-none-left", "d", root=ws_root)
    q1 = OpenQuestion(id="q1", question="a?", resolver_role="human", blocking=True)
    blocking = _blocking_handover(ws.task_id, questions=[q1])
    save_handover(ws, blocking)
    record_answer(ws, blocking, "q1", "yes", from_team=blocking.from_team)

    assert unanswered_blocking_question_ids(blocking, ws) == []


def test_open_question_option_resolvers_defaults_empty() -> None:
    q = OpenQuestion(
        id="q1", question="which?", proposed_answer_space=["a", "b"],
        resolver_role="human", blocking=True,
    )
    assert q.option_resolvers == {}


def test_open_question_option_resolvers_round_trips(ws_root: Path) -> None:
    ws = create("option-resolvers-rt", "d", root=ws_root)
    h = _make_handover(
        task_id=ws.task_id,
        open_questions=[
            OpenQuestion(
                id="q1",
                question="re-dispatch or hand-fix?",
                proposed_answer_space=["retry", "fix by hand"],
                recommended="retry",
                question_class="fix_escalation",
                resolver_role="human",
                blocking=True,
                option_resolvers={"fix by hand": "verification"},
            ).model_dump(),
        ],
    )
    path = save_handover(ws, h)
    loaded = load_handover(path)
    assert loaded.schema_version == "1.23"
    [q] = loaded.open_questions
    assert q.option_resolvers == {"fix by hand": "verification"}


def test_open_question_option_resolvers_rejects_key_outside_answer_space() -> None:
    with pytest.raises(ValidationError, match="option_resolvers keys must be members"):
        OpenQuestion(
            id="q1",
            question="which?",
            proposed_answer_space=["a", "b"],
            resolver_role="human",
            blocking=True,
            option_resolvers={"c": "verification"},
        )


def test_schema_119_payload_loads_with_option_resolvers_defaulted(ws_root: Path) -> None:
    ws = create("v119-option-resolvers-compat", "d", root=ws_root)
    payload = {
        "schema_version": "1.19",
        "handover_id": f"handover_{ws.task_id}_001",
        "handover_type": "escalation",
        "task_id": ws.task_id,
        "from_team": "implementation_fix",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": [],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "full_synthesis": "schema 1.19 payload predating option_resolvers",
        "open_questions": [
            {
                "id": "non_actionable_feedback_abc",
                "question": "not actionable",
                "proposed_answer_space": ["retry", "fix by hand"],
                "recommended": "retry",
                "question_class": "fix_escalation",
                "resolver_role": "human",
                "blocking": True,
            }
        ],
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.schema_version == "1.19"
    [q] = loaded.open_questions
    assert q.option_resolvers == {}


def test_recorded_answers_for_chain_empty_when_nothing_recorded(ws_root: Path) -> None:
    from alan.handover import recorded_answers_for_chain

    ws = create("chain-answers-empty", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="which?", proposed_answer_space=["a", "b"],
        resolver_role="human", blocking=True,
    )
    blocking = _blocking_handover(ws.task_id, questions=[q])
    save_handover(ws, blocking)

    assert recorded_answers_for_chain(ws, blocking.handover_chain_id) == {}


def test_recorded_answers_for_chain_collects_multiple_questions(ws_root: Path) -> None:
    from alan.handover import record_answer, recorded_answers_for_chain

    ws = create("chain-answers-multi", "d", root=ws_root)
    q1 = OpenQuestion(
        id="q1", question="a?", proposed_answer_space=["x", "y"],
        resolver_role="human", blocking=True,
    )
    q2 = OpenQuestion(
        id="q2", question="b?", proposed_answer_space=["x", "y"],
        resolver_role="human", blocking=True,
    )
    root = _blocking_handover(ws.task_id, questions=[q1, q2])
    save_handover(ws, root)

    record_answer(ws, root, "q1", "x", from_team=root.from_team)
    intermediate = record_answer(ws, root, "q2", "y", from_team=root.from_team)
    del intermediate

    answers = recorded_answers_for_chain(ws, root.handover_chain_id)
    assert answers == {"q1": "x", "q2": "y"}


def test_recorded_answers_for_chain_ignores_unrelated_chain(ws_root: Path) -> None:
    from alan.handover import record_answer, recorded_answers_for_chain

    ws = create("chain-answers-unrelated", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="which?", proposed_answer_space=["a", "b"],
        resolver_role="human", blocking=True,
    )
    blocking_a = _blocking_handover(ws.task_id, questions=[q]).model_copy(
        update={
            "handover_id": f"handover_{ws.task_id}_aaa",
            "handover_chain_id": f"handover_{ws.task_id}_aaa",
        }
    )
    save_handover(ws, blocking_a)
    blocking_b = _blocking_handover(ws.task_id, questions=[q]).model_copy(
        update={
            "handover_id": f"handover_{ws.task_id}_bbb",
            "handover_chain_id": f"handover_{ws.task_id}_bbb",
        }
    )
    save_handover(ws, blocking_b)
    record_answer(ws, blocking_a, "q1", "a", from_team=blocking_a.from_team)

    assert recorded_answers_for_chain(ws, blocking_b.handover_chain_id) == {}
    assert recorded_answers_for_chain(ws, blocking_a.handover_chain_id) == {"q1": "a"}


def test_record_answer_full_synthesis_preserves_original_context(ws_root: Path) -> None:
    from alan.handover import record_answer

    ws = create("record-answer-preserves-context", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="which?", proposed_answer_space=["a", "b"],
        resolver_role="human", blocking=True,
    )
    blocking = _blocking_handover(
        ws.task_id, questions=[q], full_synthesis="the situation that prompted the question"
    )
    save_handover(ws, blocking)

    recorded = record_answer(ws, blocking, "q1", "a", from_team=blocking.from_team)

    assert recorded.full_synthesis.startswith("Recorded answer for question 'q1': a")
    assert "the situation that prompted the question" in recorded.full_synthesis
