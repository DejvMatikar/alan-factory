from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import alan.workspace as workspace_mod
from alan import agent_key, engine_config
from alan.claude_cli import run_agent
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.forge import active_forge
from alan.loop_guard import jaccard_trigrams
from alan.run_summary import RunSummary, derive_run_summary
from alan.teams._pr_state_paths import state_dir
from alan.workspace import Status

CollisionState = Literal["green", "yellow", "red"]

_THRESHOLD = 0.12
_CANDIDATE_CAP = 5
_LIVE_WS_STATUSES: frozenset[Status] = frozenset(
    {Status.ACTIVE, Status.AWAITING_INPUT, Status.PAUSED}
)
_STATE_ORDER: dict[str, int] = {"green": 0, "yellow": 1, "red": 2}
_GH_TIMEOUT_S = 60.0
_PR_LIST_LIMIT = 100

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CollisionMatch:
    label: str
    kind: Literal["pr", "workspace"]
    deep_link: str


@dataclass(frozen=True)
class CollisionResult:
    state: CollisionState
    sentence: str
    match: CollisionMatch | None


SAFE_RESULT = CollisionResult(state="green", sentence="No overlapping work found.", match=None)
_SAFE = SAFE_RESULT

_SYS = (
    "You are a collision-risk assessor for a software delivery system. "
    "Given a task brief and candidate work items, judge which genuinely overlap. "
    "Collapse duplicates: if two candidates describe the same work, keep only one verdict. "
    "Respond with valid JSON only (no markdown, no prose outside the JSON object):\n"
    '{"verdicts":[{"id":"<candidate id>","overlapping":true|false,'
    '"label":"<short descriptive name>"}],'
    '"sentence":"<one sentence for the user about the highest-risk overlap, '
    'or empty string if none overlap>"}'
)

_STREAM_DIR_NAME = "collision"
_STREAM_FILE_NAME = "stream.jsonl"


def _stream_path() -> Path:
    return state_dir() / _STREAM_DIR_NAME / _STREAM_FILE_NAME


def read_collision_usage(*, stream_path: Path | None = None) -> RunSummary | None:
    return derive_run_summary(stream_path or _stream_path(), system_prompt=_SYS, model="haiku")


@dataclass(frozen=True)
class _PrCandidate:
    pr_number: int
    title: str
    url: str
    diff_files: frozenset[str] | None


@dataclass(frozen=True)
class _WsCandidate:
    task_id: str
    title: str
    live: bool


def _parse_pr_item(raw: object) -> _PrCandidate | None:
    if not isinstance(raw, dict):
        return None
    obj = cast(dict[str, Any], raw)
    n, t, u = obj.get("number"), obj.get("title"), obj.get("url")
    if isinstance(n, int) and isinstance(t, str):
        url = u if isinstance(u, str) else ""
        return _PrCandidate(pr_number=n, title=t, url=url, diff_files=None)
    return None


def _fetch_prs() -> list[_PrCandidate]:
    result = active_forge().open_prs(
        forge_target=DEFAULT_FORGE_TARGET, limit=_PR_LIST_LIMIT, timeout=_GH_TIMEOUT_S,
    )
    if result.outcome != "ok" or result.rows is None:
        logger.warning(
            "collision pr list unavailable outcome=%s rc=%s stderr=%r",
            result.outcome, result.returncode, result.stderr,
        )
        return []
    out: list[_PrCandidate] = []
    for raw in result.rows:
        item = _parse_pr_item(raw)
        if item is not None:
            out.append(item)
    return out


def _pr_candidates(brief: str) -> list[_PrCandidate]:
    out: list[_PrCandidate] = []
    for pr in _fetch_prs():
        if jaccard_trigrams(brief.lower(), pr.title.lower()) >= _THRESHOLD:
            files = active_forge().changed_files(pr.pr_number, forge_target=DEFAULT_FORGE_TARGET)
            out.append(_PrCandidate(pr.pr_number, pr.title, pr.url, files))
            if len(out) >= _CANDIDATE_CAP:
                break
    return out


def _ws_candidate_match(
    ws: workspace_mod.Workspace,
    brief: str,
    scope: engine_config.RepoIdentity,
    exclude_task_id: str | None,
) -> _WsCandidate | None:
    if ws.task_id == exclude_task_id:
        return None
    if engine_config.canonical_scope_key(ws.meta.engine_key) != scope:
        return None
    text = f"{ws.meta.title}\n{ws.meta.description}"
    if jaccard_trigrams(brief.lower(), text.lower()) < _THRESHOLD:
        return None
    live = ws.meta.status in _LIVE_WS_STATUSES
    return _WsCandidate(ws.task_id, ws.meta.title, live)


def _ws_candidates(
    brief: str,
    *,
    workspaces_root: Path | None,
    exclude_task_id: str | None = None,
    scope_engine_key: str | None = None,
) -> list[_WsCandidate]:
    scope = engine_config.canonical_scope_key(scope_engine_key)
    out: list[_WsCandidate] = []
    for ws in workspace_mod.list_workspaces(root=workspaces_root):
        match = _ws_candidate_match(ws, brief, scope, exclude_task_id)
        if match is None:
            continue
        out.append(match)
        if len(out) >= _CANDIDATE_CAP:
            break
    return out


def _files_note(files: frozenset[str] | None) -> str:
    if not files:
        return ""
    return f"; files: {', '.join(sorted(files)[:10])}"


def _build_prompt(
    brief: str, pr_cands: list[_PrCandidate], ws_cands: list[_WsCandidate]
) -> str:
    lines = [f"Brief:\n{brief[:5000]}", "", "Candidates:"]
    for pr in pr_cands:
        lines.append(
            f"- id: pr_{pr.pr_number}, live: true, title: {json.dumps(pr.title)}"
            f"{_files_note(pr.diff_files)}"
        )
    for ws in ws_cands:
        live_str = "true" if ws.live else "false"
        lines.append(f"- id: ws_{ws.task_id}, live: {live_str}, title: {json.dumps(ws.title)}")
    return "\n".join(lines)


def _call_model(prompt: str, *, stream_path: Path | None = None) -> dict[str, Any]:
    try:
        raw = run_agent(
            _SYS,
            prompt,
            model="haiku",
            deny_all_tools=True,
            stream_path=stream_path or _stream_path(),
            agent=agent_key.COLLISION,
        )
        result = json.loads(raw.strip())
        if isinstance(result, dict):
            return cast(dict[str, Any], result)
    except Exception:  # noqa: BLE001
        logger.warning("_call_model: haiku call failed", exc_info=True)
    return {"verdicts": [], "sentence": ""}


def _classify(live: bool, overlapping: bool) -> CollisionState:
    if live and overlapping:
        return "red"
    return "yellow"


def _match_pr(vid: str, pr_cands: list[_PrCandidate]) -> tuple[CollisionMatch, bool] | None:
    for pr in pr_cands:
        if vid == f"pr_{pr.pr_number}":
            return CollisionMatch(label=pr.title, kind="pr", deep_link=pr.url), True
    return None


def _match_ws(task_id: str, ws_cands: list[_WsCandidate]) -> tuple[CollisionMatch, bool] | None:
    for ws in ws_cands:
        if ws.task_id == task_id:
            link = f"/workspaces/{ws.task_id}"
            return CollisionMatch(label=ws.title, kind="workspace", deep_link=link), ws.live
    return None


def _match_for_verdict(
    vid: str, pr_cands: list[_PrCandidate], ws_cands: list[_WsCandidate]
) -> tuple[CollisionMatch, bool] | None:
    if vid.startswith("pr_"):
        return _match_pr(vid, pr_cands)
    if vid.startswith("ws_"):
        return _match_ws(vid[3:], ws_cands)
    return None


def _verdict_hit(
    v: Any, pr_cands: list[_PrCandidate], ws_cands: list[_WsCandidate]
) -> tuple[CollisionState, CollisionMatch] | None:
    if not isinstance(v, dict):
        return None
    obj = cast(dict[str, Any], v)
    vid = obj.get("id")
    raw_ov = obj.get("overlapping")
    overlapping = raw_ov is True
    if not isinstance(vid, str):
        return None
    found = _match_for_verdict(vid, pr_cands, ws_cands)
    if found is None:
        return None
    match, live = found
    return _classify(live, overlapping), match


def _order(h: tuple[CollisionState, CollisionMatch]) -> int:
    return _STATE_ORDER[h[0]]


def _apply_verdicts(
    model_out: dict[str, Any],
    pr_cands: list[_PrCandidate],
    ws_cands: list[_WsCandidate],
) -> CollisionResult:
    verdicts: Any = model_out.get("verdicts", [])
    sentence = str(model_out.get("sentence") or "")
    if not isinstance(verdicts, list):
        return _SAFE
    hits: list[tuple[CollisionState, CollisionMatch]] = []
    for v in cast(list[object], verdicts):
        hit = _verdict_hit(v, pr_cands, ws_cands)
        if hit is not None:
            hits.append(hit)
    if not hits:
        return _SAFE
    state, match = max(hits, key=_order)
    default_sentence = f"Possible overlap with {match.label[:80]}."
    return CollisionResult(state=state, sentence=sentence or default_sentence, match=match)


def check_collision(
    brief: str,
    *,
    workspaces_root: Path | None = None,
    stream_path: Path | None = None,
    exclude_task_id: str | None = None,
    scope_engine_key: str | None = None,
) -> CollisionResult:
    try:
        if stream_path is not None:
            stream_path.unlink(missing_ok=True)
        is_default_scope = (
            engine_config.canonical_scope_key(scope_engine_key)
            == engine_config.canonical_scope_key(None)
        )
        pr_cands = _pr_candidates(brief) if is_default_scope else []
        ws_cands = _ws_candidates(
            brief,
            workspaces_root=workspaces_root,
            exclude_task_id=exclude_task_id,
            scope_engine_key=scope_engine_key,
        )
        if not pr_cands and not ws_cands:
            return _SAFE
        model_out = _call_model(_build_prompt(brief, pr_cands, ws_cands), stream_path=stream_path)
        return _apply_verdicts(model_out, pr_cands, ws_cands)
    except Exception:  # noqa: BLE001
        logger.warning("check_collision failed", exc_info=True)
        return _SAFE
