from __future__ import annotations

from pathlib import Path

from alan.workspace import Requirement, create, read, update_requirements


def test_requirement_withdrawn_by_question_id_defaults_to_none() -> None:
    assert Requirement(text="x").withdrawn_by_question_id is None


def test_requirement_withdrawn_by_question_id_is_settable() -> None:
    req = Requirement(text="x", withdrawn_by_question_id="clause-stall-abc")
    assert req.withdrawn_by_question_id == "clause-stall-abc"


def test_create_drops_client_supplied_withdrawn_marker(tmp_path: Path) -> None:
    injected = Requirement(text="x", withdrawn_by_question_id="clause-stall-injected")
    ws = create("Withdrawn Injection", "d", root=tmp_path / "ws", requirements=[injected])
    assert ws.meta.requirements is not None
    assert ws.meta.requirements[0].withdrawn_by_question_id is None


def test_update_requirements_carries_forward_a_marker_missing_from_a_stale_snapshot(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ws"
    req = Requirement(text="the endpoint must be idempotent")
    ws = create("marker-race", "d", root=root, requirements=[req])

    withdrawn = req.model_copy(update={"withdrawn_by_question_id": "clause-stall-req"})
    update_requirements(ws, [withdrawn])

    stale_snapshot = [req.model_copy(update={"status": "FAIL"})]
    reconciled = update_requirements(ws, stale_snapshot)

    assert reconciled.meta.requirements is not None
    updated = reconciled.meta.requirements[0]
    assert updated.withdrawn_by_question_id == "clause-stall-req"
    assert updated.status == "FAIL"

    reloaded = read("marker-race", root=root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].withdrawn_by_question_id == "clause-stall-req"


def test_update_requirements_leaves_an_already_marked_or_unmarked_clause_alone(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ws"
    req = Requirement(text="clause a")
    untouched = Requirement(text="clause b")
    ws = create("marker-merge-branches", "d", root=root, requirements=[req, untouched])

    marked = req.model_copy(update={"withdrawn_by_question_id": "clause-stall-a"})
    update_requirements(ws, [marked, untouched])

    reconciled = update_requirements(ws, [marked, untouched])

    assert reconciled.meta.requirements is not None
    by_id = {r.id: r for r in reconciled.meta.requirements}
    assert by_id[marked.id].withdrawn_by_question_id == "clause-stall-a"
    assert by_id[untouched.id].withdrawn_by_question_id is None


def test_update_requirements_without_clear_withdrawn_carries_marker_forward(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ws"
    req = Requirement(text="clause a")
    ws = create("clear-withdrawn-omitted", "d", root=root, requirements=[req])
    marked = req.model_copy(update={"withdrawn_by_question_id": "operator:withdrawal"})
    update_requirements(ws, [marked])

    reconciled = update_requirements(ws, [req])

    assert reconciled.meta.requirements is not None
    assert reconciled.meta.requirements[0].withdrawn_by_question_id == "operator:withdrawal"


def test_update_requirements_clear_withdrawn_removes_a_matching_on_disk_marker(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ws"
    req = Requirement(text="clause a")
    ws = create("clear-withdrawn-matching", "d", root=root, requirements=[req])
    marked = req.model_copy(update={"withdrawn_by_question_id": "operator:withdrawal"})
    update_requirements(ws, [marked])

    reconciled = update_requirements(
        ws, [req], clear_withdrawn=(frozenset({req.id}), "operator:withdrawal")
    )

    assert reconciled.meta.requirements is not None
    assert reconciled.meta.requirements[0].withdrawn_by_question_id is None


def test_update_requirements_clear_withdrawn_with_a_wrong_expected_value_clears_nothing(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ws"
    req = Requirement(text="clause a")
    ws = create("clear-withdrawn-wrong-value", "d", root=root, requirements=[req])
    marked = req.model_copy(update={"withdrawn_by_question_id": "clause-stall-x"})
    update_requirements(ws, [marked])

    reconciled = update_requirements(
        ws, [req], clear_withdrawn=(frozenset({req.id}), "operator:withdrawal")
    )

    assert reconciled.meta.requirements is not None
    assert reconciled.meta.requirements[0].withdrawn_by_question_id == "clause-stall-x"


def test_update_requirements_clear_withdrawn_ignores_ids_outside_the_set(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ws"
    req_a = Requirement(text="clause a")
    req_b = Requirement(text="clause b")
    ws = create("clear-withdrawn-outside-set", "d", root=root, requirements=[req_a, req_b])
    marked_a = req_a.model_copy(update={"withdrawn_by_question_id": "operator:withdrawal"})
    marked_b = req_b.model_copy(update={"withdrawn_by_question_id": "operator:withdrawal"})
    update_requirements(ws, [marked_a, marked_b])

    reconciled = update_requirements(
        ws, [req_a, req_b], clear_withdrawn=(frozenset({req_a.id}), "operator:withdrawal")
    )

    assert reconciled.meta.requirements is not None
    by_id = {r.id: r for r in reconciled.meta.requirements}
    assert by_id[req_a.id].withdrawn_by_question_id is None
    assert by_id[req_b.id].withdrawn_by_question_id == "operator:withdrawal"


def test_update_requirements_clear_withdrawn_never_clears_a_question_provenance_marker(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ws"
    req_operator = Requirement(text="clause a")
    req_question = Requirement(text="clause b")
    ws = create(
        "clear-withdrawn-mixed-provenance",
        "d",
        root=root,
        requirements=[req_operator, req_question],
    )
    marked_operator = req_operator.model_copy(
        update={"withdrawn_by_question_id": "operator:withdrawal"}
    )
    marked_question = req_question.model_copy(
        update={"withdrawn_by_question_id": "clause-stall-x"}
    )
    update_requirements(ws, [marked_operator, marked_question])

    reconciled = update_requirements(
        ws,
        [req_operator, req_question],
        clear_withdrawn=(frozenset({req_operator.id, req_question.id}), "operator:withdrawal"),
    )

    assert reconciled.meta.requirements is not None
    by_id = {r.id: r for r in reconciled.meta.requirements}
    assert by_id[req_operator.id].withdrawn_by_question_id is None
    assert by_id[req_question.id].withdrawn_by_question_id == "clause-stall-x"


def test_update_requirements_clear_withdrawn_is_a_noop_when_the_incoming_marker_is_still_set(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ws"
    req = Requirement(text="clause a")
    ws = create("clear-withdrawn-incoming-still-set", "d", root=root, requirements=[req])
    marked = req.model_copy(update={"withdrawn_by_question_id": "operator:withdrawal"})
    update_requirements(ws, [marked])

    reconciled = update_requirements(
        ws, [marked], clear_withdrawn=(frozenset({req.id}), "operator:withdrawal")
    )

    assert reconciled.meta.requirements is not None
    assert reconciled.meta.requirements[0].withdrawn_by_question_id == "operator:withdrawal"
