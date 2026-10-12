from __future__ import annotations

from pathlib import Path

from alan.project_doc_context import get_active_project_doc_path

HEADLESS_EXECUTION_BLOCK = (
    "## Headless execution — nothing is watching for a wakeup\n"
    "You are running as a one-shot headless subprocess (`claude -p`). There is "
    "no scheduler, monitor, background job runner, or notification channel "
    "that can ever signal this process — anything you launch in the "
    "background and then wait to be notified about will wait forever.\n"
    "- Never background a long-running gate, test, lint, or verification "
    "command and then poll or wait for a completion notification. Run it to "
    "completion synchronously in the foreground and read its actual exit "
    "code and output directly before deciding what to do next.\n"
    "- Never sleep, poll, or otherwise idle waiting for an external event, "
    "wakeup, or scheduled callback to arrive — none can.\n"
    "- Never spend turns on no-op filler (repeated status checks, `sleep`, "
    "busy-waiting) while idling for a mechanism that cannot fire. Use every "
    "remaining turn to make concrete progress, or finish cleanly (commit "
    "your work and report status) with the turns you have left.\n"
    "- When a tool moves a command to the background and says it will "
    "notify you when it finishes, or refuses a command and tells you to run "
    "it in the background instead, both assume an interactive session that "
    "does not exist here — no notification and no wakeup can ever reach a "
    "one-shot headless run: do not comply, and do not wait. Re-run the "
    "command in the foreground and read its actual exit code before "
    "deciding what to do next.\n"
)


PROJECT_DOC_BLOCK_TEMPLATE = (
    "\n\nPROJECT DOC — this workspace belongs to a project with a durable "
    "planning doc at @{doc_path}. It records the project's Vision, "
    "Scope/Anti-scope, Decision Log, and Discovered Facts. Read it for "
    "context before you start. It is READ-ONLY reference material — never "
    "edit or write to this file.\n"
)


def project_doc_block() -> str:
    doc_path = get_active_project_doc_path()
    if doc_path is None or not Path(doc_path).is_file():
        return ""
    return PROJECT_DOC_BLOCK_TEMPLATE.format(doc_path=doc_path)
