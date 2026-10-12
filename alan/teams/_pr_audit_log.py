from __future__ import annotations

from pathlib import Path

from alan.roots import logs_root
from alan.teams._audit import append_jsonl_audit

AUDIT_FILE = "impl_audit.jsonl"
COPILOT_AUDIT_FILE = "copilot_review_audit.jsonl"


def audit_dir() -> Path:
    return logs_root()


def append_audit(entry: dict[str, object]) -> None:
    append_jsonl_audit(audit_dir() / AUDIT_FILE, entry, log_label="impl")


def append_copilot_audit(entry: dict[str, object]) -> None:
    append_jsonl_audit(audit_dir() / COPILOT_AUDIT_FILE, entry, log_label="copilot")
