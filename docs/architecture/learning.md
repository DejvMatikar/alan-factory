# Learning-loop trajectory instrument

Rungs 1–2 of the reward-labeled trajectory store: **measurement-only** infrastructure that records one
reward-labeled trajectory per agent invocation and derives north-star quality metrics offline. Zero
runtime/routing/decision changes — nothing here feeds back into team selection, loop-guard, or dispatch;
it only appends audit rows and reads them back. Fully reversible: delete the audit file and the offline
store and the system is unchanged.

## Rung 1 — durable per-invocation signal

**`num_turns` capture.** `_extract_result_usage` in `alan/claude_cli.py` (factored out of `_extract_stream_usage`
to keep CCN under gate) lifts the top-level `num_turns` integer from the terminal `type=result` stream event
into `usage_raw`, alongside the existing `usage`/`total_cost_usd` keys. It rejects bools (`isinstance(True, int)`
is True). The value flows through `complete_node(usage=…)` into the per-node `usage_raw` on the workflow task
row, which is the durable source for the turn-count measure (see `workflow_visibility.md`).

**`node_turns_audit.jsonl` sink.** `emit_node_turns_audit` in `alan/teams/_base.py` reads the just-completed node's
`usage_raw["num_turns"]` from the graph and appends one row `{task_id, inv_id, team, role, turns?, hit_ceiling?}`
(`turns` omitted when absent or when `hit_ceiling=True`) to `<logs-root>/node_turns_audit.jsonl` via the shared
`append_jsonl_audit` helper. `_complete_node` calls it on every node's success path, so every team/role accumulates
history — not just the two worktree-driven teams. `implementation.py` and `implementation_fix.py` additionally call
it with `hit_ceiling=True` from their `except TurnCeilingReached` handler, so a wedged node still leaves a row (with
`role`/`team` but no `turns`) instead of silently dropping out of the history. `node_turns_audit_dir` (also in
`_base.py`, re-exported into `_pr_handover.py`) honours the `ALAN_LOGS_ROOT` override seam (same pattern as
`verification.py`/`pr_merge.py`) for test isolation.

**Adaptive turn ceiling.** `alan/turn_policy.py` is a pure module (`FLOOR_TURNS`, `MIN_HISTORY_ROWS`,
`role_history_from_turns`, `turn_ceiling`) that derives a per-role ceiling from the p95 of that role's historical
`turns` — never above the role's static cap, never below `FLOOR_TURNS`, and never consulted for a role with fewer
than `MIN_HISTORY_ROWS` rows (falls back to the cap). `_max_turns_for_run` in `alan/teams/_base.py` is the single
choke point: with `ALAN_ADAPTIVE_TURNS` unset/falsy (the default) it returns the role's static cap unchanged and
never reads `node_turns_audit.jsonl` — reproducing today's `implementation`=500 / `implementation_fix`=300 /
flat-default=150 exactly. When the env var is truthy, it reads a cached (mtime/size-keyed, like `workflow.py`'s
`_WORKFLOW_CACHE`) per-role turns map from `node_turns_audit.jsonl` — excluding `hit_ceiling` rows — and delegates
to `turn_policy.turn_ceiling`. Every decision (`static` vs `adaptive`, the raw derived value, and the applied
value) is persisted onto the node's `.config.json` as `turns_source`/`derived_max_turns` alongside `max_turns`
(`NodeConfigDoc` in `alan/workflow.py`) and surfaced verbatim by the `GET .../agents/{node_id}/config` route.

**`inv_id` on verification rows.** `_maybe_post_review` in `alan/teams/verification.py` threads the *implementer's*
`inv_id` — `prior.from_invocation_id`, taken from the inbound handover, **not** the verifier's own invocation — into
every `review_ok` / `review_ok_inline` / `dry_run` audit row written by `_apply_pr_review` /
`_apply_legacy_pr_review` / `_apply_inline_pr_review`. This is the join key that ties a verdict back to the exact
implementation invocation that earned it.

## Rung 2 — offline derivation

`alan/trajectory.py` is pure (no I/O): `derive_trajectories(node_turns_text, verification_text, merge_audit_text,
git_log_text)` returns a list of frozen `TrajectoryRecord(task_id, inv_id, pr_number, team, verdict, reward,
fix_cycles, revisits, turns)`. It:

- parses the three JSONL streams defensively (bad lines, missing keys, and bool-as-int are dropped);
- joins each node-turns row to a `pr_number` + `verdict` by `(task_id, inv_id)` from the collapsed verification
  rows (latest `timestamp_utc` wins);
- labels the **binary terminal reward** by reusing `merge_survived` + `revert_targets_pr` from `alan/eval_metrics.py`
  against the `pr_merge_audit.jsonl` merge rows and `git log` revert history (reward is `True` only if the PR merged
  and survived the revert window; `False` if merged-then-reverted; `None` if never merged);
- derives `fix_cycles` (count of prior `team == "implementation_fix"` invocations on the same PR, by `inv_seq`) and
  `revisits` (all prior invocations on the same PR).

The end-to-end schema is consistent by construction: `emit_node_turns_audit` writes `team`/`turns` and
`_valid_node_turns_row` requires `team` before a row is derived.

## Offline scripts

- `scripts/derive_trajectories.py` — `uv run python scripts/derive_trajectories.py [--out PATH] [--logs-root DIR]
  [--git-log FILE]`. Reads `node_turns_audit.jsonl` / `verification_audit.jsonl` / `pr_merge_audit.jsonl` under
  `--logs-root` (default `~/.alan/logs`) plus `git log --all`, writes one JSON line per `TrajectoryRecord` to the
  store (default `trajectory_store.jsonl`). Idempotent — delete and re-run to reproduce.
- `scripts/learning_report.py` — `uv run python scripts/learning_report.py [--store PATH] [--logs-root DIR]
  [--git-log FILE]`. Prints `METRIC first_pass_approval_rate` (APPROVE fraction of `team=implementation` records
  with a verdict), `METRIC mean_fix_cycles_to_merge` (mean number of fix rounds per merged-and-survived PR, grouped by `task_id + pr_number`; each PR counted once, first-pass contributes 0), and
  `STRUCTURAL merge_survival_rate` (Alan's existing all-merges survival metric, labeled separately so the two
  populations are never conflated). With no `--store`, it derives inline from the audit logs.
