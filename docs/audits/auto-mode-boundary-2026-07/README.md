# Auto-mode permission boundary for read-role fleet workers (2026-07) — PA6.3

> **2026-08-02 addendum — rows 4 and 10 have drifted.** A production maintenance run on a
> current CLI showed the analyzer no longer auto-approves every read-only command: path-bearing
> commands (`ls`, `stat`, `find`, `cat`) are sandboxed to the session's working directories
> ("may only list files in the allowed working directories"), and `gh` / `ps` require approval
> even for read-only invocations — all fail closed headless. The observed-in-2026-07 verdicts
> below are kept as the audit's record; the working-directory sandbox is now documented in
> `docs/architecture/claude_cli_streaming.md` (§`--add-dir`), and the maintenance team is the
> one team that extends its sandbox with the live state tree via `WorkerOverrides.add_dirs`.
> The `alan/claude_cli.py:NNN` citations below are frozen at the 2026-07-13 revision: the
> permission argv builders now live in `alan/claude_cli_grants.py` (`grant_args`,
> `disallowed_tools_args`, `WRITE_TOOLS`) — re-derive line numbers before citing them again.

> **2026-09-29 addendum — the deny surface changed (P1.12).** Every invocation that has tools
> now also carries credential deny rules (secret files and directories, the gh config
> directory, the Linux Claude credential file, and Bash denies for `security`, `git credential`,
> `gh auth`, `git diff --no-index`, `git blame --contents`), in `acceptEdits` too, and every
> role except `implementation`/`implementation_fix` has `Bash(uv run:*)`, `Bash(npx:*)` and
> `Bash(npm --prefix:*)` removed from its argument list and replaced by per-gate grants. Row 2
> and the rec#5 bullet below are corrected accordingly; the other rows are the observed-in-2026-07
> record and were not re-measured. The design, the per-role reach table, the canary and the
> remaining risks are in `docs/architecture/worker_credential_boundary.md`.

**Date:** 2026-07-13. **Mode:** read-only, prior sandboxed investigation over production
verification streams plus the code (no runtime mutation, no permission-mode/allowlist code
change — this document commits that investigation's output, it does not re-run or re-derive the
probe). **Scope:** the empirical permission boundary that governs a `permission_mode="auto"`
read-role (`verification`) fleet worker in headless (`claude -p`) execution.

## Executive finding

CLAUDE.md's prior wording — "fleet workers run through a subprocess tool allowlist" — describes
`BASE_ALLOWED_TOOLS` (`alan/teams/_base.py:47`, sourced from `_BASE_TOOLS`,
`alan/engine_config.py:273-290`) as if it were the security boundary. It is not. `--allowedTools`
is **advisory pre-approval** only: it lets a listed command skip an interactive prompt, but a
headless (`-p`) run has no interactive prompt to skip in the first place. The real boundary a
read-role worker runs inside is:

1. **Claude Code's built-in Bash safety analyzer** (observed CLI v2.1.207) — classifies every Bash
   invocation as read-only or mutating regardless of whether it appears in `--allowedTools`. A
   read-only command is auto-approved even when never pre-approved. A mutating command is denied
   in headless execution because there is no channel to grant the interactive approval it would
   otherwise prompt for.
2. **alan's own hard block on the file-writing tools** — `_disallowed_tools_args`
   (`alan/claude_cli.py:426-431`) appends `--disallowedTools Write Edit NotebookEdit`
   (`_WRITE_TOOLS`, `:423`) whenever `permission_mode != "acceptEdits"`. `verification.py` never
   sets `permission_mode` on its `WorkerOverrides` (`verification.py:1425` —
   `WorkerOverrides(cwd=cwd)`, no `permission_mode` field), so it runs at the `_build_cmd` default
   of `"auto"` (`claude_cli.py:489`) and gets this hard block unconditionally. Only
   `implementation`/`implementation_fix` opt into `permission_mode="acceptEdits"`
   (`alan/teams/implementation.py:433,622`; `alan/teams/implementation_fix.py:547`), which is the
   one thing that lifts the write-tool block.

`BASE_ALLOWED_TOOLS` still matters — it is what lets a **mutating** command skip the analyzer's
deny path — but it is pre-approval layered on top of that boundary, not the boundary itself.

## Boundary table

Observed against a `permission_mode="auto"` `verification` worker (`BASE_ALLOWED_TOOLS`; no
`Bash(gh ...)`/`Bash(npm ...)`/network-tool entries in that list) reviewing real production PRs:

| # | Command class | Verdict | Basis |
|---|---|---|---|
| 1 | `Read` / `Grep` / `Glob` | ALLOWED | In `BASE_ALLOWED_TOOLS`; pre-approved and read-only either way |
| 2 | `Write` / `Edit` / `NotebookEdit` | **HARD-DENIED by alan** | `--disallowedTools Write Edit NotebookEdit` (`claude_cli.py:423,426-431`) — alan's own block, not an analyzer decision; unconditional outside `acceptEdits`. As of P1.12 that flag also carries the credential deny rules as separate arguments, in every permission mode |
| 3 | Bash read-only command **in** `BASE_ALLOWED_TOOLS` (e.g. `git log`, `git diff`, `git status`) | ALLOWED | Matches the pre-approved entry directly |
| 4 | Bash read-only command **not** in `BASE_ALLOWED_TOOLS` (e.g. `gh pr view`, `npm list`) | ALLOWED | The Bash safety analyzer classifies it read-only and auto-approves it despite never being pre-approved — this is the core doc-vs-reality gap |
| 5 | Bash output redirect to `/tmp` (e.g. `... > /tmp/x`) | ALLOWED | Analyzer treats a write outside the repo working tree as non-mutating to the reviewed artifact |
| 6 | Bash output redirect to a repo file (e.g. `... > alan/foo.txt`) | DENIED | Analyzer classifies a repo-file write as mutating; headless has no approval channel, surfaces as `tool_result is_error: true` |
| 7 | `settings.json` / brace-JSON heredoc write | DENIED | Same mutating classification as #6 |
| 8 | Mutating compound command chained with a read-only one (e.g. `git add && git status`) | DENIED | Analyzer classifies the whole compound as mutating — a read-only component does not launder it |
| 9 | `git commit` / `git push` / `git add` in isolation | **NOT ATTEMPTED** in the observed streams (the verification system prompt never instructs a write) → **would-DENY** | Inferred, not directly observed, from the analyzer's consistent mutating-classification behavior on #6–#8; flagged as inference, not measurement |
| 10 | Network reads (`gh api` / `curl`-style reads embedded in Bash) | ALLOWED, no egress block | Analyzer does not distinguish network reach from local read-only for a read-class command |

Rows 4 and 6/8 are pinned by `tests/test_auto_mode_boundary_smoke.py`'s
`test_auto_mode_boundary_smoke`, run on demand against a real headless worker — advisory only,
gated behind `ALAN_BOUNDARY_SMOKE_ENABLE`, never a PR/CI gate. Run it by hand with that
variable set.

**A boundary smoke failure is not by itself evidence that this boundary changed.**
The smoke runs a real subprocess-spawning worker through the full streaming CLI plumbing
(`alan/claude_cli.py`); a failure there can come from something unrelated to the analyzer's
read/write verdicts — e.g. a resource leak tripping the suite's `filterwarnings=error` gate
(GitHub #748 filed exactly this failure mode and misdiagnosed it as a boundary regression;
it wasn't — the observed verdicts still matched this table and there were zero assertion
failures). `scripts/boundary_report.py` reports the actual pytest failure text rather than
asserting a regression, precisely so the next reader isn't misled the way #748 was — read the
actual failure text before concluding rows 4/6/8 above no longer hold.

## Definitions

- **Permitted** — the command runs and returns output to the worker without any approval prompt.
  Headless execution has no interactive prompt at all, so "permitted" here specifically means the
  analyzer (or the pre-approval list) auto-approved it before the no-prompt-available condition
  could bite.
- **Denied** — the analyzer determined the command needed interactive approval; headless cannot
  grant that approval, so the tool call fails closed and the stream event carries
  `tool_result` with `is_error: true`.

## Citations

- `alan/claude_cli.py:423` — `_WRITE_TOOLS = ("Write", "Edit", "NotebookEdit")`.
- `alan/claude_cli.py:426-431` — `_disallowed_tools_args`; appends the hard block whenever
  `permission_mode != "acceptEdits"`.
- `alan/claude_cli.py:511-512` — `--allowedTools` is appended only when `allowed_tools` is
  truthy and `deny_all_tools` is false; it is pre-approval, never a deny surface. **Confirmed and
  fixed (PA7.2, 2026-07-14):** each entry is now passed as its own argv token
  (`cmd.extend(["--allowedTools", *allowed_tools])`) — previously `" ".join(allowed_tools)`
  fragmented any entry with an internal space (e.g. `Bash(uv run:*)`) once the CLI re-split the
  joined value on whitespace, so that pre-approval entry silently never registered.
- `alan/claude_cli.py:1154-1157` — the sibling `--allowedTools`/`--disallowedTools` join in
  `_chat_cmd` (chat surfaces, not fleet workers). **Confirmed and fixed (PA7.2, 2026-07-14):**
  `--allowedTools` now emits `*allowed_tools` as discrete tokens, symmetric with `_build_cmd`
  above; `--disallowedTools`'s `" ".join(_WRITE_TOOLS)` join is unchanged (deny surface, out of
  scope — see rec#5/PA7.1 below). See `docs/architecture/claude_cli_streaming.md` and
  `tests/test_claude_cli.py::test_build_cmd_allowed_tools_survive_as_discrete_argv_tokens` /
  `::test_chat_cmd_allowed_tools_survive_intact_with_internal_spaces` for the regression coverage.
- `alan/engine_config.py:121-122` — `base_tools`/`team_tools` schema fields.
- `alan/engine_config.py:273-290` — `_BASE_TOOLS`, the default engine's pre-approval list (no
  `gh`/`npm`/network entries).
- `alan/teams/_base.py:47` — `BASE_ALLOWED_TOOLS = (*_ENGINE_DEFAULT.base_tools,)`.
- `alan/teams/verification.py:1425` — `WorkerOverrides(cwd=cwd)`; no `permission_mode` override
  anywhere in the module, so the team runs at the `_build_cmd` default of `"auto"`.
- `alan/teams/implementation.py:433,622` and `alan/teams/implementation_fix.py:547` — the two
  roles that opt into `permission_mode="acceptEdits"`, which lifts the write-tool hard block.
- CLI: observed against Claude Code CLI v2.1.207.

**Evidence location note:** the raw evidence for this boundary lives in
`~/.alan/workspaces/**/agents/*.stream.jsonl` (per-node event streams, including tool-call and
`tool_result` events) and the paired `*.config.json` (persisted `allowed_tools`/`permission_mode`
per node) — **not** `~/.alan/logs/*.jsonl`, which are outcome summaries (one row per audited
action) and do not carry individual tool-call approve/deny events.

## Back-links

- The prompt audit's assumption that auto-mode permission semantics were established
  empirically, with the exact boundary not yet probed — this document is that probe's output.
- `PA6.3-r2` (allowlist space-join defect — **confirmed and fixed**, PA7.2, 2026-07-14) and
  `PA6.3-r3` (boundary smoke test) — the two routine follow-ups filed from this investigation.

## Owner-decision reference — do not pull, do not implement

The investigation also surfaced two **hard-safety** recommendations. They are owner-gated (weakening or
tightening a permission/deny surface is an explicit owner decision) and are referenced here for
completeness only:

- **rec#4** — the read-role worker's reach is broader than a "read-only reviewer" mental model
  suggests: unrestricted read access plus unblocked network egress (row 10 above) for any command
  the analyzer classifies as read-only, not just the pre-approved list.
- **rec#5** — the only enforced write boundary for a read role is the CLI's own Bash safety
  analyzer; as of this audit alan added no deny-surface of its own beyond the
  `Write`/`Edit`/`NotebookEdit` tool block, so a mutating Bash command was one
  analyzer-classification decision away from being permitted. **Corrected 2026-09-29 (P1.12):**
  alan now adds credential deny rules to every invocation that has tools
  (`docs/architecture/worker_credential_boundary.md`); those cover reads of the credential paths,
  not mutating Bash in general, so the rec#5 concern about mutating commands is unchanged.

**These are reference-only in this document.** Neither is implemented, scheduled, or listed as an opportunistic pull — a permission-model or deny-surface
change requires the owner's explicit decision, not a worker picking it up opportunistically.

## 2026-08-21 addendum — a research-role reachability probe now exists; it does not re-verify row 10

`tests/test_auto_mode_boundary_smoke.py::test_research_role_network_reach_probe` (added by the
`give-research-workers-a-contract-bearing-fetch` measurement workspace) now probes network
reachability in the same harness this document's row 4/6/8 pins already use. It is **not** a
re-verification of row 10 above: row 10 documents the `verification` role reaching the network via
a command the Bash safety analyzer classifies as read-only despite never being pre-approved (`gh
api` / `curl`-style reads, not in `BASE_ALLOWED_TOOLS`); the new probe instead measures the
`research` role reaching the network via a command that **matches its own pre-approved
`Bash(uv run:*)` entry** directly. Different role, different mechanism (pre-approval match vs.
analyzer heuristic). Row 10 stays flagged drifted per the 2026-08-02 addendum above — this probe's
result, once run, neither confirms nor refutes it. See
`docs/audits/research-network-reach-2026-08/README.md` for the new probe's own tool list, exact
command, result, and scope notes.
