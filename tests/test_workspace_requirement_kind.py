from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from alan import workspace as workspace_mod
from alan.app.workspace_create_models import CreateWorkspaceBody, reject_uncovered_clauses
from alan.workspace import ClauseKind, Requirement, Task, TaskDoDClause, TaskStatus


def _legacy_id(text: str) -> str:
    return f"req-{hashlib.sha256(text.strip().encode()).hexdigest()[:12]}"


class TestRequirementKindField:
    def test_kind_defaults_to_none(self) -> None:
        assert Requirement(text="deliver X").kind is None

    def test_legacy_payload_without_kind_loads_as_none(self) -> None:
        legacy = {"id": "req-abc", "text": "deliver X", "status": "PASS"}
        assert Requirement.model_validate(legacy).kind is None

    def test_legacy_payload_is_never_back_filled_on_dump(self) -> None:
        dumped = Requirement.model_validate({"text": "deliver X"}).model_dump()
        assert dumped["kind"] is None

    @pytest.mark.parametrize("kind", ["behaviour", "record"])
    def test_supported_kinds_round_trip_through_dump(self, kind: ClauseKind) -> None:
        req = Requirement(text="deliver X", kind=kind)
        assert Requirement.model_validate(json.loads(req.model_dump_json())) == req

    def test_unknown_kind_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Requirement.model_validate({"text": "deliver X", "kind": "bogus"})

    def test_explicit_null_kind_is_accepted(self) -> None:
        assert Requirement.model_validate({"text": "deliver X", "kind": None}).kind is None


class TestRequirementKindIdentity:
    @pytest.mark.parametrize("kind", [None, "behaviour", "record"])
    def test_id_ignores_kind_and_equals_the_text_only_hash(self, kind: ClauseKind | None) -> None:
        req = Requirement(text="deliver X", kind=kind)
        assert req.id == _legacy_id("deliver X")

    def test_id_is_identical_across_kinds_for_the_same_text(self) -> None:
        kinds: tuple[ClauseKind | None, ...] = (None, "behaviour", "record")
        assert len({Requirement(text="deliver X", kind=k).id for k in kinds}) == 1


class TestRecordClauseTestRefValidator:
    def test_record_clause_naming_a_test_ref_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Requirement(text="the doc says X", kind="record", test_ref="scn-1")

    def test_record_clause_naming_a_test_ref_is_rejected_from_a_dict(self) -> None:
        with pytest.raises(ValidationError):
            Requirement.model_validate(
                {"text": "the doc says X", "kind": "record", "test_ref": "scn-1"}
            )

    def test_behaviour_clause_may_name_a_test_ref(self) -> None:
        assert Requirement(text="X works", kind="behaviour", test_ref="scn-1").test_ref == "scn-1"

    def test_kindless_clause_may_name_a_test_ref(self) -> None:
        assert Requirement(text="X works", test_ref="scn-1").test_ref == "scn-1"

    def test_record_clause_without_a_test_ref_is_accepted(self) -> None:
        assert Requirement(text="the doc says X", kind="record").test_ref is None

    def test_record_clause_status_stays_pending_without_a_test_ref(self) -> None:
        req = Requirement(text="the doc says X", kind="record")
        assert req.status == "PENDING"


class TestKindSurvivesCreate:
    def test_single_task_create_persists_kind(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        ws = workspace_mod.create(
            "kind-ws", "d", root=root,
            requirements=[
                Requirement(text="the doc says X", kind="record"),
                Requirement(text="X works", kind="behaviour"),
                Requirement(text="Y works"),
            ],
        )
        persisted = workspace_mod.read(ws.task_id, root=root)
        assert persisted.meta.requirements is not None
        assert [r.kind for r in persisted.meta.requirements] == ["record", "behaviour", None]

    def test_create_keeps_test_ref_alongside_kind(self, tmp_path: Path) -> None:
        ws = workspace_mod.create(
            "kind-ws", "d", root=tmp_path / "ws",
            requirements=[Requirement(text="X works", kind="behaviour", test_ref="scn-1")],
        )
        assert ws.meta.requirements is not None
        assert ws.meta.requirements[0].kind == "behaviour"
        assert ws.meta.requirements[0].test_ref == "scn-1"

    def test_create_without_requirements_leaves_the_gate_off(self, tmp_path: Path) -> None:
        ws = workspace_mod.create("kind-ws", "d", root=tmp_path / "ws")
        assert ws.meta.requirements is None

    def test_uncovered_check_does_not_touch_the_kind_that_create_persists(
        self, tmp_path: Path
    ) -> None:
        record = Requirement(text="the doc says X", kind="record")
        body = CreateWorkspaceBody(title="t", requirements=[record])
        tasks = [
            Task(
                id="a", status=TaskStatus.PENDING,
                dod_clauses=[TaskDoDClause(text="done", covers=[record.id])],
            ),
            Task(id="b", status=TaskStatus.PENDING),
        ]
        reject_uncovered_clauses(body, tasks)
        assert body.requirements is not None
        ws = workspace_mod.create(
            "kind-ws", "d", root=tmp_path / "ws", requirements=body.requirements,
        )
        assert ws.meta.requirements is not None
        assert ws.meta.requirements[0].kind == "record"


class TestKindSurvivesRequirementUpdates:
    def test_update_requirements_preserves_kind(self, tmp_path: Path) -> None:
        ws = workspace_mod.create("kind-ws", "d", root=tmp_path / "ws", requirements=[])
        updated = workspace_mod.update_requirements(
            ws, [Requirement(text="the doc says X", kind="record")]
        )
        reread = workspace_mod.read(ws.task_id, root=tmp_path / "ws")
        assert updated.meta.requirements is not None
        assert updated.meta.requirements[0].kind == "record"
        assert reread.meta.requirements is not None
        assert reread.meta.requirements[0].kind == "record"

    def test_carrying_a_withdrawal_marker_forward_preserves_kind(self) -> None:
        req = Requirement(text="the doc says X", kind="record")
        carried = workspace_mod._carry_forward_marker(  # noqa: SLF001
            req, {req.id: "clause-stall-a"}
        )
        assert carried.withdrawn_by_question_id == "clause-stall-a"
        assert carried.kind == "record"


def _created(tmp_path: Path, requirements: list[Requirement] | None) -> list[Requirement] | None:
    return workspace_mod.create(
        "kind-ws", "d", root=tmp_path / "ws", requirements=requirements
    ).meta.requirements


class TestCreateCollapsesDuplicateClauses:
    def test_identical_blocking_texts_collapse_to_one(self, tmp_path: Path) -> None:
        created = _created(tmp_path, [Requirement(text="X works"), Requirement(text="X works")])
        assert created is not None
        assert [(r.text, r.kind) for r in created] == [("X works", None)]

    @pytest.mark.parametrize("record_first", [True, False])
    def test_record_and_blocking_of_the_same_text_collapse_to_one_blocking(
        self, tmp_path: Path, record_first: bool
    ) -> None:
        record = Requirement(text="X works", kind="record")
        blocking = Requirement(text="X works")
        created = _created(tmp_path, [record, blocking] if record_first else [blocking, record])
        assert created is not None
        assert [(r.text, r.kind) for r in created] == [("X works", None)]

    @pytest.mark.parametrize("record_first", [True, False])
    def test_behaviour_and_record_collapse_keeping_the_behaviour_literal(
        self, tmp_path: Path, record_first: bool
    ) -> None:
        record = Requirement(text="X works", kind="record")
        behaviour = Requirement(text="X works", kind="behaviour")
        created = _created(
            tmp_path, [record, behaviour] if record_first else [behaviour, record]
        )
        assert created is not None
        assert [(r.text, r.kind) for r in created] == [("X works", "behaviour")]

    @pytest.mark.parametrize(("first", "second"), [(None, "behaviour"), ("behaviour", None)])
    def test_first_blocking_copy_is_the_survivor_whatever_its_kind_literal(
        self, tmp_path: Path, first: ClauseKind | None, second: ClauseKind | None
    ) -> None:
        created = _created(
            tmp_path,
            [
                Requirement(text="X works", kind=first),
                Requirement(text="X works", kind=second),
            ],
        )
        assert created is not None
        assert [(r.text, r.kind) for r in created] == [("X works", first)]

    def test_identical_record_texts_collapse_to_one_record(self, tmp_path: Path) -> None:
        created = _created(
            tmp_path,
            [
                Requirement(text="doc says X", kind="record"),
                Requirement(text="doc says X", kind="record"),
            ],
        )
        assert created is not None
        assert [(r.text, r.kind) for r in created] == [("doc says X", "record")]

    def test_texts_differing_only_by_surrounding_whitespace_collapse(
        self, tmp_path: Path
    ) -> None:
        created = _created(
            tmp_path, [Requirement(text="X works"), Requirement(text="  X works\n")]
        )
        assert created is not None
        assert len(created) == 1

    def test_three_copies_record_behaviour_record_yield_one_behaviour(
        self, tmp_path: Path
    ) -> None:
        created = _created(
            tmp_path,
            [
                Requirement(text="X works", kind="record"),
                Requirement(text="X works", kind="behaviour"),
                Requirement(text="X works", kind="record"),
            ],
        )
        assert created is not None
        assert [(r.text, r.kind) for r in created] == [("X works", "behaviour")]

    def test_interleaved_duplicates_keep_first_occurrence_order(self, tmp_path: Path) -> None:
        created = _created(
            tmp_path,
            [
                Requirement(text="B", kind="record"),
                Requirement(text="A"),
                Requirement(text="B"),
                Requirement(text="C", kind="record"),
                Requirement(text="A", kind="record"),
            ],
        )
        assert created is not None
        assert [(r.text, r.kind) for r in created] == [("B", None), ("A", None), ("C", "record")]

    @pytest.mark.parametrize("record_first", [True, False])
    def test_blocking_copy_test_ref_survives_collapse_with_a_record_copy(
        self, tmp_path: Path, record_first: bool
    ) -> None:
        record = Requirement(text="X works", kind="record")
        blocking = Requirement(text="X works", test_ref="scn-1")
        created = _created(tmp_path, [record, blocking] if record_first else [blocking, record])
        assert created is not None
        assert [(r.kind, r.test_ref) for r in created] == [(None, "scn-1")]

    def test_two_blocking_copies_with_different_test_refs_keep_the_first(
        self, tmp_path: Path
    ) -> None:
        created = _created(
            tmp_path,
            [
                Requirement(text="X works", test_ref="scn-1"),
                Requirement(text="X works", test_ref="scn-2"),
            ],
        )
        assert created is not None
        assert [r.test_ref for r in created] == ["scn-1"]

    def test_caller_supplied_ids_are_discarded_and_identical_texts_still_collapse(
        self, tmp_path: Path
    ) -> None:
        created = _created(
            tmp_path,
            [Requirement(id="req-a", text="X works"), Requirement(id="req-b", text="X works")],
        )
        assert created is not None
        assert [r.id for r in created] == [_legacy_id("X works")]

    def test_none_stays_none(self, tmp_path: Path) -> None:
        assert _created(tmp_path, None) is None

    def test_unique_clauses_pass_through_unchanged_with_kind_absent(self, tmp_path: Path) -> None:
        created = _created(
            tmp_path, [Requirement(text="A"), Requirement(text="B", kind="record")]
        )
        assert created is not None
        assert [(r.text, r.kind) for r in created] == [("A", None), ("B", "record")]

    def test_update_requirements_does_not_collapse(self, tmp_path: Path) -> None:
        ws = workspace_mod.create("kind-ws", "d", root=tmp_path / "ws", requirements=[])
        updated = workspace_mod.update_requirements(
            ws, [Requirement(text="X works"), Requirement(text="X works")]
        )
        assert updated.meta.requirements is not None
        assert len(updated.meta.requirements) == 2
