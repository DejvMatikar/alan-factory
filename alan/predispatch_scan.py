from __future__ import annotations

import logging
import re
from pathlib import Path

import alan.workspace as workspace_mod
from alan import engine_config
from alan.loop_guard import jaccard_trigrams
from alan.scope_audit import ScopeGapFinding
from alan.teams._pr_branch import list_open_prs

logger = logging.getLogger(__name__)

_SCAN_CAP = 10
_SIMILARITY_THRESHOLD = 0.12

_ANTI_SCOPE_OVERLAP_THRESHOLD = 0.6
_NEGATION_RE = re.compile(
    r"(must not|do not|don't|cannot|can't|never\b|avoid\b|out of scope|"
    r"out-of-scope|anti-scope|shall not|should not|not allowed)",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with",
    "is", "are", "be", "this", "that", "any", "all", "must", "not", "do",
    "does", "did", "cannot", "never", "avoid", "should", "shall", "may",
    "scope", "anti", "out", "allowed", "no", "work",
})


def scan_predispatch(
    title: str,
    description: str,
    *,
    workspaces_root: Path | None = None,
    exclude_task_id: str | None = None,
    anti_scope: str | None = None,
    scope_engine_key: str | None = None,
) -> list[ScopeGapFinding]:
    query = f"{title}\n{description}"
    findings: list[ScopeGapFinding] = []
    if anti_scope:
        try:
            findings.extend(_scan_anti_scope(query, anti_scope))
        except Exception:  # noqa: BLE001
            logger.warning("predispatch_scan: anti_scope check failed", exc_info=True)
    is_default_scope = engine_config.canonical_scope_key(
        scope_engine_key
    ) == engine_config.canonical_scope_key(None)
    if is_default_scope:
        try:
            findings.extend(_scan_in_flight_pr(query))
        except Exception:  # noqa: BLE001
            logger.warning("predispatch_scan: in_flight_pr check failed", exc_info=True)
    try:
        findings.extend(
            _scan_prior_workspace(
                query,
                workspaces_root=workspaces_root,
                exclude_task_id=exclude_task_id,
                scope_engine_key=scope_engine_key,
            )
        )
    except Exception:  # noqa: BLE001
        logger.warning("predispatch_scan: prior_workspace check failed", exc_info=True)
    return findings[:_SCAN_CAP]


def _content_tokens(text: str) -> set[str]:
    return {
        w for w in _WORD_RE.findall(text.lower())
        if len(w) >= 3 and w not in _STOPWORDS
    }


def _negative_constraints(anti_scope: str) -> list[str]:
    out: list[str] = []
    for raw_line in anti_scope.splitlines():
        line = raw_line.strip().lstrip("-*•").strip()
        if line and _NEGATION_RE.search(line):
            out.append(line)
    return out


def _scan_anti_scope(query: str, anti_scope: str) -> list[ScopeGapFinding]:
    query_tokens = _content_tokens(query)
    out: list[ScopeGapFinding] = []
    for constraint in _negative_constraints(anti_scope):
        tokens = _content_tokens(constraint)
        if not tokens:
            continue
        overlap = tokens & query_tokens
        if len(overlap) / len(tokens) >= _ANTI_SCOPE_OVERLAP_THRESHOLD:
            out.append(
                ScopeGapFinding(
                    kind="anti_scope",
                    summary=f"Dispatch may violate project anti-scope: {constraint[:200]!r}",
                    references=[],
                    suggested_action="out_of_scope",
                )
            )
    return out


def _scan_in_flight_pr(query: str) -> list[ScopeGapFinding]:
    prs = list_open_prs()
    out: list[ScopeGapFinding] = []
    for pr in prs:
        score = jaccard_trigrams(query.lower(), pr.title.lower())
        if score >= _SIMILARITY_THRESHOLD:
            out.append(
                ScopeGapFinding(
                    kind="in_flight_pr",
                    summary=(
                        f"Open PR #{pr.number} may overlap: "
                        f"{pr.title[:100]!r} (similarity {score:.2f})"
                    ),
                    references=[f"PR #{pr.number}"],
                    suggested_action="needs_user_call",
                )
            )
    return out


def _scan_prior_workspace(
    query: str,
    *,
    workspaces_root: Path | None,
    exclude_task_id: str | None,
    scope_engine_key: str | None = None,
) -> list[ScopeGapFinding]:
    scope = engine_config.canonical_scope_key(scope_engine_key)
    workspaces = workspace_mod.list_workspaces(root=workspaces_root)
    out: list[ScopeGapFinding] = []
    for ws in workspaces:
        if ws.task_id == exclude_task_id:
            continue
        if engine_config.canonical_scope_key(ws.meta.engine_key) != scope:
            continue
        candidate = f"{ws.meta.title}\n{ws.meta.description}"
        score = jaccard_trigrams(query.lower(), candidate.lower())
        if score >= _SIMILARITY_THRESHOLD:
            out.append(
                ScopeGapFinding(
                    kind="prior_workspace",
                    summary=(
                        f"Workspace '{ws.meta.title[:100]}' "
                        f"(status: {ws.meta.status.value}) "
                        f"may be a duplicate (similarity {score:.2f})"
                    ),
                    references=[ws.task_id],
                    suggested_action="needs_user_call",
                )
            )
    return out
