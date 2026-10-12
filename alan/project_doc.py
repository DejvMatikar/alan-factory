from __future__ import annotations

import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import alan.project as project_mod
from alan import store
from alan.project import Project

DOC_FILENAME = "project.md"
_DISALLOWED_BARE_REFS = (".", "..")
_SAFE_REF_RE = re.compile(r"^[\w.-]+$")

_VISION = "Vision"
_SCOPE = "Scope/Anti-scope"
_DECISION_LOG = "Decision Log"
_DISCOVERED_FACTS = "Discovered Facts"

ANCHORS: tuple[str, ...] = (_VISION, _SCOPE, _DECISION_LOG, _DISCOVERED_FACTS)

_HEADER_PREFIX = "## "
_ANCHOR_ALTERNATION = "|".join(re.escape(anchor) for anchor in ANCHORS)
_HEADER_RE = re.compile(rf"(?m)^## (?P<name>{_ANCHOR_ALTERNATION})$")

_lock = threading.Lock()


class ProjectDocError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProjectDocSections:
    vision: str
    scope: str
    decision_log: str
    discovered_facts: str


@dataclass(frozen=True)
class LedgerOutcome:
    task_id: str
    title: str
    status: str
    pr_number: int | None = None


def _scaffold_text() -> str:
    return "".join(f"{_HEADER_PREFIX}{anchor}\n\n" for anchor in ANCHORS)


def _body_start(text: str, match: re.Match[str]) -> int:
    end = match.end()
    return end + 1 if text[end : end + 1] == "\n" else end


def _require_all_anchors_present(spans: dict[str, tuple[int, int]]) -> None:
    missing = [anchor for anchor in ANCHORS if anchor not in spans]
    if missing:
        raise ValueError(f"missing anchor header(s) in project doc: {missing!r}")


def _anchor_spans(text: str) -> dict[str, tuple[int, int]]:
    matches = list(_HEADER_RE.finditer(text))
    spans: dict[str, tuple[int, int]] = {}
    for i, match in enumerate(matches):
        name = match.group("name")
        if name in spans:
            raise ValueError(f"duplicate anchor header in project doc: {name!r}")
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        spans[name] = (_body_start(text, match), body_end)
    _require_all_anchors_present(spans)
    return spans


def _read_text(path: Path, *, root: Path) -> str:
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ProjectDocError(f"doc path escapes project root: {path!r}")
    return resolved.read_text(encoding="utf-8")


def _write_text(path: Path, text: str, *, root: Path) -> None:
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ProjectDocError(f"doc path escapes project root: {path!r}")
    store.write_text_atomic(resolved, text)


def _project_root(project: Project, *, root: Path | None) -> Path:
    return project_mod.project_dir(project.id, root=root).resolve()


def doc_path(project: Project, *, root: Path | None = None) -> Path:
    pdir = _project_root(project, root=root)
    ref = project.doc_ref or DOC_FILENAME
    if ref in _DISALLOWED_BARE_REFS or not _SAFE_REF_RE.fullmatch(ref):
        raise ProjectDocError(f"doc_ref must be a bare filename with no path separators: {ref!r}")
    candidate = (pdir / ref).resolve()
    if not candidate.is_relative_to(pdir):
        raise ProjectDocError(f"doc_ref must be a bare filename with no path separators: {ref!r}")
    return candidate


def ensure_doc(project: Project, *, root: Path | None = None) -> tuple[Project, Path]:
    with _lock:
        current = project_mod.read(project.id, root=root)
        pdir = _project_root(current, root=root)
        path = doc_path(current, root=root)
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_text(path, _scaffold_text(), root=pdir)
        if current.doc_ref is None:
            current = project_mod.update(current, {"doc_ref": DOC_FILENAME}, root=root)
    return current, path


def parse_anchors(text: str) -> ProjectDocSections:
    spans = _anchor_spans(text)

    def _section(anchor: str) -> str:
        start, end = spans[anchor]
        return text[start:end].strip("\n")

    return ProjectDocSections(
        vision=_section(_VISION),
        scope=_section(_SCOPE),
        decision_log=_section(_DECISION_LOG),
        discovered_facts=_section(_DISCOVERED_FACTS),
    )


def append_to_section(text: str, anchor: str, content: str) -> str:
    if anchor not in ANCHORS:
        raise ValueError(f"unknown anchor: {anchor!r}")
    start, end = _anchor_spans(text)[anchor]
    trimmed = text[start:end].rstrip("\n")
    stitched = f"{trimmed}\n\n{content.strip()}\n\n" if trimmed else f"{content.strip()}\n\n"
    return text[:start] + stitched + text[end:]


def replace_section(text: str, anchor: str, content: str) -> str:
    if anchor not in ANCHORS:
        raise ValueError(f"unknown anchor: {anchor!r}")
    start, end = _anchor_spans(text)[anchor]
    stitched = f"{content.strip()}\n\n" if content.strip() else "\n"
    return text[:start] + stitched + text[end:]


def read_scope_section(project_id: str, *, root: Path | None = None) -> str | None:
    try:
        project = project_mod.read(project_id, root=root)
        pdir = _project_root(project, root=root)
        path = doc_path(project, root=root)
        if not path.is_file():
            return None
        text = _read_text(path, root=pdir)
        sections = parse_anchors(text)
    except (project_mod.ProjectError, ProjectDocError, ValueError, OSError):
        return None
    scope = sections.scope.strip()
    return scope or None


def append_discovered_fact(project: Project, fact: str, *, root: Path | None = None) -> Path:
    project, path = ensure_doc(project, root=root)
    pdir = _project_root(project, root=root)
    with _lock:
        text = _read_text(path, root=pdir)
        _write_text(path, append_to_section(text, _DISCOVERED_FACTS, fact), root=pdir)
    return path


def append_decision(project: Project, entry: str, *, root: Path | None = None) -> Path:
    project, path = ensure_doc(project, root=root)
    pdir = _project_root(project, root=root)
    with _lock:
        text = _read_text(path, root=pdir)
        _write_text(path, append_to_section(text, _DECISION_LOG, entry), root=pdir)
    return path


def apply_vision_scope_amendment(
    project: Project,
    *,
    vision: str,
    scope: str,
    decision_entry: str,
    root: Path | None = None,
) -> Path:
    project, path = ensure_doc(project, root=root)
    pdir = _project_root(project, root=root)
    with _lock:
        text = _read_text(path, root=pdir)
        text = replace_section(text, _VISION, vision)
        text = replace_section(text, _SCOPE, scope)
        text = append_to_section(text, _DECISION_LOG, decision_entry)
        _write_text(path, text, root=pdir)
    return path


def _format_ledger_entry(entry: LedgerOutcome) -> str:
    pr_suffix = f", PR #{entry.pr_number}" if entry.pr_number is not None else ""
    return f"- {entry.title} [{entry.task_id}]: {entry.status}{pr_suffix}"


def _completion_summary_text(
    project: Project,
    *,
    completed_at: datetime,
    ledger: Sequence[LedgerOutcome],
    force: bool,
    reason: str,
) -> str:
    lines = [f'Project "{project.title}" marked complete at {completed_at.isoformat()}.']
    if force:
        lines.append(f"Forced complete: {reason.strip()}")
    if ledger:
        lines.append(f"Final workspace outcomes ({len(ledger)}):")
        lines.extend(_format_ledger_entry(entry) for entry in ledger)
    else:
        lines.append("No workspaces were associated with this project.")
    return "\n".join(lines)


def append_completion_summary(
    project: Project,
    *,
    ledger: Sequence[LedgerOutcome],
    force: bool,
    reason: str,
    completed_at: datetime,
    root: Path | None = None,
) -> Path:
    project, path = ensure_doc(project, root=root)
    pdir = _project_root(project, root=root)
    summary = _completion_summary_text(
        project, completed_at=completed_at, ledger=ledger, force=force, reason=reason
    )
    with _lock:
        text = _read_text(path, root=pdir)
        _write_text(path, append_to_section(text, _DECISION_LOG, summary), root=pdir)
    return path


__all__ = [
    "ANCHORS",
    "DOC_FILENAME",
    "LedgerOutcome",
    "ProjectDocError",
    "ProjectDocSections",
    "append_completion_summary",
    "append_decision",
    "append_discovered_fact",
    "append_to_section",
    "apply_vision_scope_amendment",
    "doc_path",
    "ensure_doc",
    "parse_anchors",
    "read_scope_section",
    "replace_section",
]
