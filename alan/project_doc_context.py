from __future__ import annotations

from contextvars import ContextVar

_active_project_doc_path_var: ContextVar[str | None] = ContextVar(
    "_active_project_doc_path_var", default=None
)


def get_active_project_doc_path() -> str | None:
    return _active_project_doc_path_var.get()


def set_active_project_doc_path(path: str | None) -> None:
    _active_project_doc_path_var.set(path)


__all__ = [
    "get_active_project_doc_path",
    "set_active_project_doc_path",
]
