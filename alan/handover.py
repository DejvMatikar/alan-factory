from __future__ import annotations

import contextlib
import copy
import logging
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, get_args

import yaml
from pydantic import BaseModel, Field, TypeAdapter, ValidationError, model_validator

from alan import store
from alan.workspace import TaskDoDClause, Workspace
from alan.workspace import TestScenario as TestScenario

SCHEMA_VERSION: Literal["1.23"] = "1.23"
_HANDOVERS_SUBDIR = "handovers"
_FILE_MODE = 0o600
_HANDOVER_FILE_TEMPLATE = "handover_{task_id}_{seq:03d}.yaml"
_MAX_SEQ = 999
_DECISION_REQUIRED_TARGETS: frozenset[str] = frozenset(
    {"implementation", "implementation_fix", "verification"}
)

HandoverType = Literal[
    "forward",
    "clarification_request",
    "revisit_amend",
    "parallel_fanout_join",
    "escalation",
    "terminal",
]
Reversibility = Literal["high", "medium", "low"]
QuestionClass = Literal[
    "merge_block_escalation",
    "verification_escalation",
    "fix_escalation",
    "orchestrated_completeness",
]
# Genuine credential-authentication outcomes on a push/merge/review-post call
# site. Defined here (not in alan/teams/_pr_branch.py) because Handover sits
# below the `teams` layer in pyproject.toml's importlinter contract — the
# classifier and its stderr markers live in _pr_branch.py, which imports this
# type back.
CredentialFailureClass = Literal["missing_token", "expired_token", "transient"]
OptionEffect = Literal["closes", "pending_external"]
PENDING_EXTERNAL_NOTICE = (
    "A human undertook to perform this step outside the fleet. It has not been "
    "observed to have landed. Check whether it has. If it has not, this remains "
    "blocked on that human — it is not accepted work, and the underlying finding "
    "is not to be re-derived as a fresh discovery."
)
_SchemaVersion = Literal[
    "1.0",
    "1.1",
    "1.2",
    "1.3",
    "1.4",
    "1.5",
    "1.6",
    "1.7",
    "1.8",
    "1.9",
    "1.10",
    "1.11",
    "1.12",
    "1.13",
    "1.14",
    "1.15",
    "1.16",
    "1.17",
    "1.18",
    "1.19",
    "1.20",
    "1.21",
    "1.22",
    "1.23",
]
_SUPPORTED_SCHEMA_VERSIONS: frozenset[str] = frozenset(get_args(_SchemaVersion))
_RootMappingAdapter: TypeAdapter[dict[Any, Any]] = TypeAdapter(dict[Any, Any])


class Decision(BaseModel):
    id: str
    statement: str
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)
    reversibility: Reversibility


class OpenQuestion(BaseModel):
    id: str
    question: str
    proposed_answer_space: list[str] = Field(default_factory=list)
    resolver_role: str
    blocking: bool
    prior_attempts: list[str] = Field(default_factory=list)
    answer: str | None = Field(default=None, max_length=20000)
    question_class: QuestionClass | None = None
    recommended: str | None = None
    option_resolvers: dict[str, str] = Field(default_factory=dict)
    option_effects: dict[str, OptionEffect] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_recommendation(self) -> "OpenQuestion":
        n = len(self.proposed_answer_space)
        if n and not 2 <= n <= 4:
            raise ValueError(
                f"proposed_answer_space must have 2-4 options when non-empty, got {n}"
            )
        if self.recommended is not None and self.recommended not in self.proposed_answer_space:
            raise ValueError("recommended must be one of proposed_answer_space")
        if not set(self.option_resolvers).issubset(self.proposed_answer_space):
            raise ValueError("option_resolvers keys must be members of proposed_answer_space")
        if not set(self.option_effects).issubset(self.proposed_answer_space):
            raise ValueError("option_effects keys must be members of proposed_answer_space")
        return self


class ArtifactRef(BaseModel):
    path: str
    description: str


class DeployWaitSignal(BaseModel):
    merge_sha: str | None
    deadline: float
    race_deadline: float | None
    reason: str
    merged_at: float | None = None
    repo: str | None = None


class ScenarioOutcome(BaseModel):
    scenario_id: str
    status: Literal[
        "PASS", "FAIL", "SKIPPED", "DOCUMENTED", "ADVISORY", "TIMEOUT", "BUDGET_EXHAUSTED"
    ]
    diagnostic: str = Field(default="", max_length=2000)


class ClauseOutcome(BaseModel):
    task_id: str
    actionable_ids: tuple[str, ...]
    clause_texts: dict[str, str]
    evidence: dict[str, str]
    judge_rationale: str = Field(default="", max_length=2000)
    operator_only_ids: tuple[str, ...] = ()
    unactionable_ids: tuple[str, ...] = ()


class InjectedTask(BaseModel):
    id: str = Field(min_length=1)
    team: str = Field(min_length=1)
    title: str | None = Field(default=None, max_length=500)
    instructions: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    dod_clauses: list[TaskDoDClause] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    scenarios: list[str] = Field(default_factory=list)
    scenarios_waived: bool = False
    files: list[str] = Field(default_factory=list)


class Handover(BaseModel):
    schema_version: _SchemaVersion = SCHEMA_VERSION
    handover_id: str
    handover_chain_id: str = ""
    handover_type: HandoverType
    task_id: str
    workspace_id: str = ""
    from_team: str
    from_invocation_id: str
    to_team_suggestions: list[str] = Field(default_factory=list)
    iteration: int = Field(ge=1)
    revisit_of: str | None = None
    timestamp_utc: datetime
    next_hop: str | None = None
    decisions: list[Decision] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    open_questions: list[OpenQuestion] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    assumptions: list[str] = Field(default_factory=list)
    constraints_inherited: list[str] = Field(default_factory=list)
    artifacts_produced: list[ArtifactRef] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    full_synthesis: str
    forbidden_actions: list[str] = Field(default_factory=list)
    prior_attempts_summary: str | None = None
    pr_number: int | None = None
    files_to_edit: list[str] = Field(default_factory=list)
    test_scenarios: list[TestScenario] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    injected_tasks: list[InjectedTask] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    split_rationale: str | None = None
    plan_synthesis: str | None = None
    scenarios_waived: bool = False
    dependency_bot_authorized: bool = False
    verifier_completeness: float | None = Field(default=None, ge=0.0, le=1.0)
    verifier_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    verifier_gaps: list[str] = Field(default_factory=list, max_length=20)
    scenario_results: list[ScenarioOutcome] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    credential_failure: CredentialFailureClass | None = None
    deploy_wait: DeployWaitSignal | None = None
    clause_outcome: ClauseOutcome | None = None

    @model_validator(mode="after")
    def _fill_chain_id(self) -> "Handover":
        if not self.handover_chain_id:
            self.handover_chain_id = self.handover_id
        return self

    @model_validator(mode="after")
    def _fill_workspace_id(self) -> "Handover":
        if not self.workspace_id:
            self.workspace_id = self.task_id
        return self


class HandoverError(RuntimeError):
    pass


_log = logging.getLogger(__name__)


def save_handover(ws: Workspace, handover: Handover) -> Path:
    if handover.task_id != ws.task_id:
        raise HandoverError(
            f"handover.task_id={handover.task_id!r} does not match workspace "
            f"{ws.task_id!r}"
        )
    _validate_layer3_decisions(handover)
    target_dir = ws.path / _HANDOVERS_SUBDIR
    target_dir.mkdir(parents=True, exist_ok=True)
    path = _claim_handover_slot(target_dir, handover.task_id)
    try:
        _write_yaml(path, handover.model_dump(mode="json"))
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def _validate_layer3_decisions(handover: Handover) -> None:
    """Reject persisting `forward` handovers to implementation/verification
    that don't carry at least one Decision. Layer 3 producer-side enforcement."""
    if handover.handover_type != "forward":
        return
    if handover.from_team == "architecture" and not handover.decisions:
        raise HandoverError(
            f"forward handover from {handover.from_team!r} "
            "requires non-empty decisions (Layer 3 enforcement)"
        )
    targets = set(handover.to_team_suggestions) & _DECISION_REQUIRED_TARGETS
    if not targets:
        return
    if handover.decisions:
        return
    raise HandoverError(
        f"forward handover from {handover.from_team!r} to "
        f"{sorted(targets)} requires non-empty decisions (Layer 3 enforcement)"
    )


def load_handover(path: Path) -> Handover:
    raw = _read_text(path)
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise HandoverError(f"{path.name}: invalid YAML") from exc
    try:
        payload = _RootMappingAdapter.validate_python(data)
    except ValidationError:
        raise HandoverError(f"{path.name}: expected mapping at root") from None
    version = payload.get("schema_version", SCHEMA_VERSION)
    if version not in _SUPPORTED_SCHEMA_VERSIONS:
        raise HandoverError(
            f"{path.name}: unsupported schema_version {version!r}; "
            f"supported: {sorted(_SUPPORTED_SCHEMA_VERSIONS)}"
        )
    _migrate_payload(path, payload)
    try:
        return Handover.model_validate(payload)
    except ValidationError as exc:
        raise HandoverError(f"{path.name}: schema mismatch") from exc


def _migrate_payload(path: Path, payload: dict[str, Any]) -> None:
    if not payload.get("handover_chain_id"):
        payload["handover_chain_id"] = _synthesize_chain_id(path, payload)
    if "full_synthesis" not in payload and "summary_for_downstream" in payload:
        payload["full_synthesis"] = payload.pop("summary_for_downstream")


_HANDOVER_CACHE: dict[Path, tuple[int, int, Handover]] = {}


def load_handover_cached(path: Path) -> Handover:
    try:
        st = path.stat()
    except OSError:
        return load_handover(path)
    hit = store.stat_cache_hit(_HANDOVER_CACHE, path, st)
    if hit is not None:
        return copy.deepcopy(hit[0])
    handover = load_handover(path)
    store.stat_cache_put(_HANDOVER_CACHE, path, st, handover)
    return copy.deepcopy(handover)


def list_handovers(ws: Workspace) -> list[Handover]:
    result: list[Handover] = []
    for p in handover_paths(ws):
        try:
            result.append(load_handover_cached(p))
        except (HandoverError, OSError):
            _log.warning("list_handovers: skipping unreadable %s", p.name, exc_info=True)
    return result


def find_handover_by_id(ws: Workspace, handover_id: str) -> Handover | None:
    for path in reversed(handover_paths(ws)):
        try:
            handover = load_handover(path)
        except (HandoverError, OSError):
            _log.warning(
                "find_handover_by_id: skipping unreadable %s", path.name, exc_info=True
            )
            continue
        if handover.handover_id == handover_id:
            return handover
    return None


def _replace_question_answer(
    open_questions: list[OpenQuestion],
    question_id: str,
    answer: str,
    *,
    effect_override: OptionEffect | None = None,
) -> list[OpenQuestion]:
    result: list[OpenQuestion] = []
    for q in open_questions:
        if q.id != question_id:
            result.append(q)
            continue
        update: dict[str, Any] = {"answer": answer}
        if effect_override is not None:
            update["option_effects"] = {**q.option_effects, answer: effect_override}
        result.append(OpenQuestion.model_validate({**q.model_dump(), **update}))
    return result


def _option_effect_for(
    open_questions: list[OpenQuestion], question_id: str, answer: str
) -> OptionEffect | None:
    for question in open_questions:
        if question.id == question_id:
            return question.option_effects.get(answer)
    return None


def _record_answer_full_synthesis(
    blocking_handover: Handover,
    question_id: str,
    answer: str,
    *,
    effect_override: OptionEffect | None = None,
) -> str:
    effect = (
        effect_override
        if effect_override is not None
        else _option_effect_for(blocking_handover.open_questions, question_id, answer)
    )
    if effect == "pending_external":
        return (
            f"{PENDING_EXTERNAL_NOTICE}\n\n(question {question_id!r}, answer: {answer!r})"
            f"\n\n{blocking_handover.full_synthesis}"
        )
    return (
        f"Recorded answer for question {question_id!r}: {answer}\n\n"
        f"{blocking_handover.full_synthesis}"
    )


def record_answer(
    ws: Workspace,
    blocking_handover: Handover,
    question_id: str,
    answer: str,
    *,
    from_team: str,
    effect_override: OptionEffect | None = None,
) -> Handover:
    if not any(q.id == question_id for q in blocking_handover.open_questions):
        raise HandoverError(
            f"question {question_id!r} not found on handover "
            f"{blocking_handover.handover_id!r}"
        )
    open_questions = _replace_question_answer(
        blocking_handover.open_questions, question_id, answer, effect_override=effect_override
    )
    new_id = f"handover_{ws.task_id}_{uuid.uuid4().hex[:8]}"
    latest = list_handovers(ws)
    handover = Handover.model_validate({
        **blocking_handover.model_dump(),
        "schema_version": SCHEMA_VERSION,
        "handover_id": new_id,
        "handover_type": "revisit_amend",
        "task_id": ws.task_id,
        "from_team": from_team,
        "to_team_suggestions": [],
        "iteration": latest[-1].iteration + 1,
        "revisit_of": blocking_handover.handover_id,
        "timestamp_utc": datetime.now(UTC),
        "next_hop": None,
        "full_synthesis": _record_answer_full_synthesis(
            blocking_handover, question_id, answer, effect_override=effect_override
        ),
        "open_questions": open_questions,
    })
    save_handover(ws, handover)
    return handover


def is_answer_recording(handover: Handover) -> bool:
    return handover.handover_type == "revisit_amend" and any(
        q.answer is not None for q in handover.open_questions
    )


def record_pr_attachment(ws: Workspace, prior: Handover, pr_number: int) -> Handover:
    new_id = f"handover_{ws.task_id}_{uuid.uuid4().hex[:8]}"
    handover = Handover.model_validate({
        **prior.model_dump(),
        "schema_version": SCHEMA_VERSION,
        "handover_id": new_id,
        "handover_type": "revisit_amend",
        "task_id": ws.task_id,
        "iteration": prior.iteration + 1,
        "revisit_of": prior.handover_id,
        "timestamp_utc": datetime.now(UTC),
        "next_hop": None,
        "pr_number": pr_number,
        "full_synthesis": f"Operator attached PR #{pr_number}.\n\n{prior.full_synthesis}",
    })
    save_handover(ws, handover)
    return handover


def _answer_for_question(handover: Handover, question_id: str) -> str | None:
    for question in handover.open_questions:
        if question.id == question_id and question.answer is not None:
            return question.answer
    return None


def find_recorded_answer(
    ws: Workspace, handover_chain_id: str, question_id: str
) -> str | None:
    for handover in list_handovers(ws):
        if handover.handover_type != "revisit_amend":
            continue
        if handover.handover_chain_id != handover_chain_id:
            continue
        answer = _answer_for_question(handover, question_id)
        if answer is not None:
            return answer
    return None


def _is_chain_revisit_amend(handover: Handover, handover_chain_id: str) -> bool:
    return (
        handover.handover_type == "revisit_amend"
        and handover.handover_chain_id == handover_chain_id
    )


def _answered_questions(handover: Handover) -> dict[str, str]:
    return {
        question.id: question.answer
        for question in handover.open_questions
        if question.answer is not None
    }


def recorded_answers_for_chain(ws: Workspace, handover_chain_id: str) -> dict[str, str]:
    answers: dict[str, str] = {}
    for handover in list_handovers(ws):
        if _is_chain_revisit_amend(handover, handover_chain_id):
            answers.update(_answered_questions(handover))
    return answers


def unanswered_blocking_question_ids(blocking_handover: Handover, ws: Workspace) -> list[str]:
    unanswered: list[str] = []
    for question in blocking_handover.open_questions:
        if not question.blocking:
            continue
        effective_answer = question.answer or find_recorded_answer(
            ws, blocking_handover.handover_chain_id, question.id
        )
        if effective_answer is None:
            unanswered.append(question.id)
    return unanswered


def handovers_dir(ws_path: Path) -> Path:
    return ws_path / _HANDOVERS_SUBDIR


def handover_paths_under(ws_path: Path, task_id: str) -> list[Path]:
    target_dir = handovers_dir(ws_path)
    if not target_dir.is_dir():
        return []
    return sorted(target_dir.glob(f"handover_{task_id}_*.yaml"))


def handover_paths(ws: Workspace, task_dir: Path | None = None) -> list[Path]:
    base = task_dir if task_dir is not None else ws.path
    return handover_paths_under(base, ws.task_id)


def prune_handover_files(ws: Workspace) -> int:
    removed = 0
    for path in handover_paths(ws):
        with contextlib.suppress(OSError):
            path.unlink()
            removed += 1
    return removed


def _claim_handover_slot(target_dir: Path, task_id: str) -> Path:
    for seq in range(1, _MAX_SEQ + 1):
        candidate = target_dir / _HANDOVER_FILE_TEMPLATE.format(
            task_id=task_id, seq=seq
        )
        try:
            fd = os.open(str(candidate), os.O_WRONLY | os.O_CREAT | os.O_EXCL, _FILE_MODE)
        except FileExistsError:
            continue
        os.close(fd)
        return candidate
    raise HandoverError(f"no free handover slot for {task_id}")


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    store.write_text_atomic(
        path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True), mode=_FILE_MODE
    )


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise HandoverError(f"missing file: {path.name}") from exc


_CHAIN_INDEX_CACHE: dict[Path, tuple[int, dict[str, str | None]]] = {}


def _synthesize_chain_id(path: Path, payload: dict[str, Any]) -> str:
    hid = payload.get("handover_id", "")
    if not isinstance(hid, str) or not hid:
        raise HandoverError(
            f"{path.name}: handover_id missing or empty — cannot synthesize chain_id"
        )
    revisit_of = payload.get("revisit_of")
    if revisit_of is None or not isinstance(revisit_of, str):
        return hid
    index = _build_chain_index(path.parent)
    return _walk_to_root(revisit_of, index)


def _build_chain_index(directory: Path) -> dict[str, str | None]:
    try:
        mtime_ns = directory.stat().st_mtime_ns
    except OSError:
        return _scan_chain_index(directory)
    cached = _CHAIN_INDEX_CACHE.get(directory)
    if cached is not None and cached[0] == mtime_ns:
        return cached[1]
    index = _scan_chain_index(directory)
    _CHAIN_INDEX_CACHE[directory] = (mtime_ns, index)
    return index


def _scan_chain_index(directory: Path) -> dict[str, str | None]:
    index: dict[str, str | None] = {}
    for p in sorted(directory.glob("*.yaml")):
        entry = _load_chain_entry(p)
        if entry is not None:
            hid, parent = entry
            index[hid] = parent
    return index


def _load_chain_entry(path: Path) -> tuple[str, str | None] | None:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    try:
        root = _RootMappingAdapter.validate_python(data)
    except ValidationError:
        return None
    hid = root.get("handover_id", "")
    if not isinstance(hid, str) or not hid:
        return None
    return hid, root.get("revisit_of") or None


def _walk_to_root(start: str, index: dict[str, str | None]) -> str:
    seen: set[str] = set()
    current = start
    while current not in seen:
        seen.add(current)
        parent = index.get(current)
        if parent is None:
            return current
        current = parent
    return current
