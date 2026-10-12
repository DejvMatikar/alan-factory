from __future__ import annotations

import shlex

from alan.drift_gate import LOAD_BEARING_FILES, doc_target_hint
from alan.engine_config import EngineConfig, default_engine_config
from alan.handover import TestScenario
from alan.teams._scenario_block import unscoped_suite_when
from alan.teams._scenario_runner import (
    _allowed_prefixes_for,  # pyright: ignore[reportPrivateUsage]
    _check_command_prefix,  # pyright: ignore[reportPrivateUsage]
    _check_forbidden_ops,  # pyright: ignore[reportPrivateUsage]
    _check_no_path_traversal,  # pyright: ignore[reportPrivateUsage]
    resolve_aggregate_budget,
    scenario_timeout,
)


def _drift_gate_rule() -> str:
    lb = ", ".join(f"`{p}`" for p in sorted(LOAD_BEARING_FILES))
    return (
        f"DRIFT GATE RULE: if `files_to_edit` contains any load-bearing path "
        f"({lb}), then `files_to_edit` MUST also include a "
        f"documentation target ({doc_target_hint()}), and `## Implementation "
        "steps` MUST include an explicit ordered step to update that "
        "file with a substantive description of the behaviour change — "
        "not a token edit. The host backstop will attempt a same-pass "
        "session-resume to add the doc update before pushing."
    )


_SYNTHESIZER_PREFIX = (
    "You are the synthesizer on a planning team. Combine the prior agents' "
    "outputs into a single implementation plan in Markdown. Required "
    "sections: ## Context, ## Recommended approach, ## Implementation "
    "steps (ordered, each with exact file paths), ## Risks and "
    "mitigations, ## Test plan, ## Open questions. Reconcile any "
    "contradictions in the prior outputs. The ## Risks and mitigations "
    "section must explicitly address devil_advocate's "
    "## Adjacent code surface, ## Invariant risk, ## Ripple effects, and "
    "## UX consistency findings. Output the full plan in "
    "Markdown — no preamble.\n\n"
    + _drift_gate_rule()
    + "\n\nREQUIRED: end the response with a fenced YAML block labelled "
    "`decisions.yaml` containing three top-level keys. All three — "
    "`decisions`, `files_to_edit`, and `test_scenarios` — are MANDATORY; "
    "omitting any one of them fails the block:\n"
    "- `decisions:` 3-10 load-bearing decisions the downstream "
    "implementation team must respect. Each entry needs id, statement, "
    "rationale (one sentence), confidence (0.0-1.0), and reversibility "
    "(high|medium|low).\n"
    "- `files_to_edit:` the COMPLETE list of repo-relative file paths "
    "the implementation team is expected to create or modify (source + "
    "tests). This is what the dispatcher uses to size the worker's "
    "turn budget; under-counting starves the worker, over-counting "
    "wastes turns. Be exhaustive but tight.\n"
)

_SYNTHESIZER_SUFFIX = (
    "Decisions feed Handover.decisions; the file list feeds "
    "Handover.files_to_edit; the scenarios feed Handover.test_scenarios "
    "and are injected into the verification team's user prompt. "
    "The implementation team will fail loudly if the block is missing, "
    "the decisions list is empty, or test_scenarios is empty."
)


def _admissible_prefixes(cfg: EngineConfig) -> list[tuple[str, ...]]:
    derived = (tuple(cmd[:2]) for cmd in cfg.gate_commands if cmd)
    configured = (tuple(prefix) for prefix in cfg.scenario.command_prefixes if prefix)
    return list(dict.fromkeys((*derived, *configured)))


def _shape_string(prefix: tuple[str, ...], cfg: EngineConfig) -> str:
    full = next(
        (list(cmd) for cmd in cfg.gate_commands if tuple(cmd[: len(prefix)]) == prefix),
        list(prefix),
    )
    try:
        run_idx = full.index("run", 2)
    except ValueError:
        return " ".join([*full, "<target>"])
    return " ".join([*full[: run_idx + 1], "<script>"])


def _shape_list(cfg: EngineConfig) -> list[str]:
    return [_shape_string(prefix, cfg) for prefix in _admissible_prefixes(cfg)]


def _example_prefix(cfg: EngineConfig) -> tuple[str, ...] | None:
    if cfg.scenario.command_prefixes:
        return tuple(cfg.scenario.command_prefixes[0])
    prefixes = _admissible_prefixes(cfg)
    return prefixes[0] if prefixes else None


def _scenario_shape_block(cfg: EngineConfig) -> str:
    shapes_prose = "; ".join(f"`{s}`" for s in _shape_list(cfg))
    return (
        "- `test_scenarios:` 2-8 declarative Given/When/Then scenarios "
        "covering the user-visible Definition of Done from the task "
        "description. Each entry needs id (snake_case), description (one "
        "line), given, when, then. The `when` field IS the command — there "
        "is no separate bound-reference field that makes a scenario "
        "executable. Any scenario meant to gate the verdict must place the "
        "exact executable command directly in `when`. On this engine, the "
        f"admissible command shapes are: {shapes_prose}. A read-only "
        "inspection command (e.g. `git log`, `git diff`, `ls`, `cat`) is "
        "admissible everywhere, on any engine, but only the shapes above "
        "are known to run here. The runner "
        "executes `when` verbatim and works from the repository root (cwd "
        "= repo root), so paths in `when` are repo-relative. A `when` "
        "field that is prose (e.g. \"The user clicks…\") is DOCUMENTED: "
        "documentation-only, and it can never gate the verdict no matter "
        "what other fields the entry carries. Only FAIL blocks APPROVE; "
        "DOCUMENTED, SKIPPED, and ADVISORY are natural non-blockers — including a "
        "`when` command whose pytest run hits a collection error (rc=2, "
        "e.g. a broken import) or a missing/renamed node (rc=4, the "
        "command no longer resolves to any file or test), both SKIPPED "
        "and non-blocking as checkout artifacts, distinct from a command "
        "that resolves to an existing file or module but pytest collects "
        "zero tests from it (rc=5 — e.g. an author-written filter matches "
        "nothing, or the module/function no longer contains any tests), "
        "which still blocks as FAIL. Absent or empty `test_scenarios` "
        "still forces a REQUEST_CHANGES downgrade.\n\n"
    )


def _scenario_then_block() -> str:
    return (
        "The `then` field is a human-readable note describing the expected "
        "outcome. It is checked as an exact substring of the `when` "
        "command's combined stdout+stderr, but a mismatch is ADVISORY, not "
        "a gate: when the command exits 0 but `then` is not found in the "
        "output, the scenario is reported ADVISORY with the mismatch shown "
        "as diagnostic detail, and this never blocks APPROVE. `then` is "
        "advisory only — only the `when` command's exit status gates the "
        "verdict.\n\n"
    )


def _scenario_timeout_block(cfg: EngineConfig) -> str:
    timeout = scenario_timeout(cfg)
    budget = resolve_aggregate_budget(cfg)
    return (
        "Runner time limit: every `when` command runs under a "
        f"per-scenario timeout of {timeout:.0f}s, but the true ceiling is "
        f"min(timeout, time remaining in the run's aggregate budget of "
        f"{budget:.0f}s) — it SHRINKS as earlier scenarios in the same run "
        "consume that budget. A `when` command that cannot finish inside "
        "that shrinking window produces a blocking TIMEOUT (not a benign "
        "non-blocker) on every run, deterministically, no matter what the "
        "diff does. Scope every `when` command to the smallest test/module/"
        "file that proves the behavior — never an unscoped whole-suite "
        "invocation, and never a bare invocation of one of this engine's "
        "own gate commands with no further arguments — either shape is "
        "rejected at this planning stage instead of becoming a permanent "
        "failure downstream.\n\n"
    )


def _scenario_yaml_rule_block() -> str:
    return (
        "YAML safety rule: wrap EVERY string value (statement, rationale, "
        "description, given, when, then) in double quotes; escape inner "
        '`"` as `\\"`. Unquoted prose containing a colon-space (e.g. '
        '`then: shows "Bash: ls"`) parses as a nested mapping and fails the '
        "block. When in doubt, quote.\n\n"
    )


def _scenario_example_block(cfg: EngineConfig) -> str:
    prefix = _example_prefix(cfg)
    example_when = (
        _shape_string(prefix, cfg)
        if prefix is not None
        else "uv run --no-active pytest <target>"
    )
    return (
        "Use this exact format (the orchestrator parses the block by its "
        "`decisions.yaml` info string):\n\n"
        "```yaml decisions.yaml\n"
        "decisions:\n"
        "  - id: d1\n"
        '    statement: "use sqlite for the queue"\n'
        '    rationale: "single-file, atomic writes already covered"\n'
        "    confidence: 0.85\n"
        "    reversibility: medium\n"
        "files_to_edit:\n"
        "  - alan/queue.py\n"
        "  - tests/test_queue.py\n"
        "test_scenarios:\n"
        "  - id: enqueue_persists_across_restart\n"
        '    description: "Jobs enqueued before restart are claimable '
        'after restart."\n'
        '    given: "A job is enqueued and the alan service is restarted."\n'
        f'    when: "{example_when}"\n'
        '    then: "1 passed"\n'
        "```\n\n"
    )


def scenario_section(cfg: EngineConfig | None) -> str:
    resolved = cfg if cfg is not None else default_engine_config()
    return (
        _scenario_shape_block(resolved)
        + _scenario_then_block()
        + _scenario_timeout_block(resolved)
        + _scenario_yaml_rule_block()
        + _scenario_example_block(resolved)
    )


def synthesizer_prompt(cfg: EngineConfig | None) -> str:
    return _SYNTHESIZER_PREFIX + scenario_section(cfg) + _SYNTHESIZER_SUFFIX


def _gate_tokens(when: str) -> list[str]:
    try:
        return shlex.split(when)
    except ValueError:
        return when.split()


def _passes_full_prefix_gate(when: str, cfg: EngineConfig | None) -> bool:
    tokens = _gate_tokens(when)
    if not tokens:
        return False
    return _check_command_prefix(tokens, _allowed_prefixes_for(cfg)) is None


def shape_rejection(when: str, cfg: EngineConfig | None) -> str | None:
    if not _passes_full_prefix_gate(when, cfg):
        return None
    op_diag = _check_forbidden_ops(when)
    if op_diag is not None:
        return op_diag
    try:
        clean_tokens = shlex.split(when)
    except ValueError as exc:
        return f"when field has unbalanced quotes: {exc}"
    return _check_no_path_traversal(clean_tokens)


def whole_gate_when(when: str, cfg: EngineConfig | None) -> bool:
    try:
        tokens = shlex.split(when)
    except ValueError:
        return False
    if not tokens:
        return False
    resolved = cfg if cfg is not None else default_engine_config()
    return any(tokens == list(cmd) for cmd in resolved.gate_commands if cmd)


def _unscoped_message(idx: int, when: str) -> str:
    return (
        f"test_scenarios[{idx}].when is an unscoped whole-suite command "
        f"({when!r}) and cannot serve as a gating check within the "
        "scenario runner's shrinking per-scenario time limit — scope it to "
        "the smallest test/module/file that proves the behavior"
    )


def _entry_rejection(
    idx: int, scenario: TestScenario, cfg: EngineConfig | None
) -> str | None:
    when = scenario.when
    if unscoped_suite_when(when) or whole_gate_when(when, cfg):
        return _unscoped_message(idx, when)
    shape_diag = shape_rejection(when, cfg)
    if shape_diag is not None:
        return f"test_scenarios[{idx}].when {shape_diag}"
    return None


def collect_rejections(
    entries: list[tuple[int, TestScenario]], cfg: EngineConfig | None
) -> str | None:
    messages: list[str] = []
    for idx, scenario in entries:
        message = _entry_rejection(idx, scenario, cfg)
        if message is not None:
            messages.append(message)
    if not messages:
        return None
    lines = "\n".join(f"- {m}" for m in messages)
    return f"invalid test_scenarios entries:\n{lines}"


__all__ = [
    "collect_rejections",
    "scenario_section",
    "shape_rejection",
    "synthesizer_prompt",
    "whole_gate_when",
]
