# Property-based invariant tests (H6.2)

Hypothesis (`hypothesis>=6.140,<7`) joins the dev dependency set in this change and
drives two new sibling test files — `tests/test_queue_properties.py` and
`tests/test_slot_pool_properties.py` — that check the concurrency invariants
example-based tests kept missing: lease exclusivity, no lost jobs, capacity bounds, and
no double slot assignment. `tests/test_slot_pool.py` is pinned at 1637 lines and
shrink-only (`tests/size_baseline.py`), so these live in new siblings rather than
appended to it. See `docs/architecture/clock_seam.md` for the `alan.clock` injection
both files build on (`FakeClock`, `tests/fake_clock.py`) — no test here reads the real
wall clock, which is what makes a threaded harness deterministic at all.

## The load-bearing side effect: one repo-global profile

`tests/conftest.py` registers and loads a Hypothesis settings profile named `"alan"` at
import time. Because Hypothesis has exactly one *default* profile per process, this
governs **every** `@given`/stateful test anywhere in this repo from now on, not just the
two files this item adds — a future contributor adding an unrelated `@given` test
inherits these settings without doing anything, which is intentional but easy to miss
reading only the two new files.

The registered profile:

```python
settings.register_profile(
    "alan",
    derandomize=True,
    database=None,
    deadline=None,
    max_examples=30,
    stateful_step_count=15,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
settings.load_profile("alan")
```

- **`derandomize=True`** — Hypothesis seeds its example generator from a hash of the
  test's own bytecode instead of a random per-run seed, so the exact same inputs are
  tried on every run, on every machine. Without this, a property could pass locally and
  fail in CI (or vice versa) purely because the two runs explored different examples —
  precisely the load/environment-coupled flake class T2-12 exists to close.
- **`database=None`** — disables Hypothesis's default `DirectoryBasedExampleDatabase`
  (a `.hypothesis/` cache of previously-failing examples it would otherwise replay first).
  A database is a *third* source of run-to-run difference beyond derandomization: a
  machine with stale cached failures behaves differently from a fresh checkout. Turning
  it off means every run explores the same fixed sequence, full stop.
- **`deadline=None`** — Hypothesis's default `deadline` fails an example that takes too
  long to execute, measured in **wall-clock time**. That is exactly the load-coupled
  verdict mechanism T2-12 forbids: the slot-pool harness spawns real OS threads, and a
  loaded box can legitimately make one execution slower without the code being wrong.
  `deadline=None` makes "how long did this take" never itself a verdict.
- **`suppress_health_check=[function_scoped_fixture, too_slow]`** — mandatory, not
  optional, given this repo's `conftest.py`: five autouse fixtures
  (`_reset_registry`, `_redirect_alan_logs_root`, `_strip_verifier_gh_token`,
  `_no_real_manager_summary_spawn`,
  `_no_real_investigator_spawn`) are function-scoped, so *every* `@given` in this repo
  trips `HealthCheck.function_scoped_fixture` by construction — Hypothesis is correctly
  warning that the fixture doesn't reset between generated examples, which here is fine
  (nothing in those fixtures is mutated by the properties). `too_slow` is suppressed
  because the slot-pool harness's real thread joins are inherently slower than
  Hypothesis's heuristic expects, and `deadline=None` already covers the actual
  time-based flake risk.

None of this is a downgrade from a "real" random/timed suite — it is what makes the
suite's pass/fail verdict a property of the *code*, not of the machine it happened to run
on, which is the entire point of a CI-facing property test.

## Two search-budget tiers, not one

The shared profile's `max_examples=30` / `stateful_step_count=15` is deliberately modest
— it is what the slot-pool schedule properties need, since every regression they guard
against is pinned as an `@example()` (next section) and does not depend on the random
search budget at all. The queue's `RuleBasedStateMachine`
(`test_queue_invariants_under_generated_operation_sequences`) has no equivalent pinning
mechanism (Hypothesis's stateful testing has no `@example()`-for-state-machines feature),
so one of its three invariants — capacity bounds — only fails on a generated sequence
that happens to enqueue *and* claim three jobs into the *same* workspace, which the
shared 30-example budget does not reliably find. That one test opts into a larger,
local override instead of raising the shared default for every future Hypothesis test in
the repo:

```python
_STATE_MACHINE_SETTINGS = settings(
    settings.get_profile("alan"), max_examples=150, stateful_step_count=30
)
...
run_state_machine_as_test(QueueStateMachine, settings=_STATE_MACHINE_SETTINGS)
```

It inherits `derandomize`/`database`/`deadline`/`suppress_health_check` from the shared
profile — only the two search-budget knobs move.

## Mutant-kill table

Per H6.2's DoD, "properties pass" is not evidence — the evidence is a seeded mutant of
each invariant *failing* the named property, with the property back to green on
unmutated HEAD. Every mutant below was
applied by hand, run against its named node, its failure captured, then reverted via
`git checkout -- <file>` before the next one — `git diff --stat -- alan/` is zero at
every commit in this PR.

| Invariant | Property node | Seeded mutant (one-line) | Shrunk falsifying case | HEAD (unmutated) |
|---|---|---|---|---|
| Lease exclusivity | `test_lease_exclusivity_under_reset_all_interleavings` | `reset_all`: drop the `unleased` filter → `to_clean` walks every slot regardless of live lease (the #1012 shape) | Pinned `@example` fires immediately, no shrink needed: `schedule=["lease","reset_all","lease","release","release"]` — `clean_during_live_lease == [0]` | green |
| No double slot assignment | `test_no_double_slot_assignment_under_generated_schedules` | `reset_all`: move `self._free -= set(to_clean)` outside the `with self._cond:` block | Pinned `@example`: `schedule=["lease","reset_all","lease","lease"]` — `double_assignment == [1]` | green |
| Capacity bounds (pool) | `test_capacity_bounds_hold_after_every_step` | `reset_all`'s `finally`: `self._free \|= set(range(self._size))` instead of `set(to_clean)` | Same pinned `@example` — `max_open_sessions == 3 > size == 2` | green |
| Lease-token identity | `test_lease_tokens_are_unique_when_clock_advances` | `_release`: drop the `== token` guard → `if idx in self._lease_times:` | Pinned `@example(advances=[1.0, 1.0])` — a stale release evicts the new holder: `assert 0 not in {0}` fails | green |
| No lost jobs (queue) | `test_queue_invariants_under_generated_operation_sequences` | `_finish`: silently write `RUNNING` back instead of `FAILED` when `status is JobStatus.FAILED` | Shrunk to 3 steps: `enqueue(ws-a)` → `claim()` → `fail()` — `result.status == RUNNING`, not `FAILED` | green |
| Capacity bounds (queue) | `test_queue_invariants_under_generated_operation_sequences` | claim SQL param (`alan/queue.py`): `self._intra_workspace_cap` → `self._intra_workspace_cap + 1` | Shrunk to 3×`(enqueue(ws-a), claim())`: `capacity_bounds` — "workspace ws-a had 3 running jobs, exceeding cap 2" | green |
| Claim exclusivity (queue) | `test_queue_invariants_under_generated_operation_sequences` | `claim()`: drop the pending-status predicate from the claim `UPDATE`'s `WHERE` clause | Shrunk to 4 steps: `enqueue` → `claim` → `complete` → `claim` — a `COMPLETED` job gets silently re-claimed into `RUNNING` | green |

Full per-mutant diffs and wall-clock timings are in the PR body (this doc names the
methodology; the PR record is the evidence artifact per the DoD).

### A trap the mutant table surfaced: not every real race is realistic

The "no double slot assignment" mutant above only became reliably observable after two
harness fixes, both load-bearing for trusting the harness at all:

1. **Thread `.start()` order is not execution order.** The driver used to spawn a
   `reset_all` step's thread and immediately move on to the next schedule step. On this
   host, a fast-scheduled `lease` thread routinely raced to completion *before*
   `reset_all`'s own thread got any CPU time — silently skipping the interleaving the
   schedule was trying to test. The fix: the driver now waits for `reset_all` to signal
   it has entered its locked section (via the `is_git_clone()` stub, called while the
   lock is still held) before spawning any further steps.
2. **The "no double assignment" window is a handful of bytecodes wide with no I/O.**
   Instruction reordering alone (moving `self._free -= set(to_clean)` outside the lock,
   with no added delay) survived 3000 randomized schedules (~4m17s) of pure OS/GIL
   scheduling luck with zero failures. It only became deterministically observable after
   widening the window with an explicit `time.sleep(0.1)` at the exact point the line
   moved to — a standard fault-injection technique for testing narrow races, and part of
   the *temporary* mutant, never shipped.

Also surfaced, and *not* carried into the shipped properties: an early harness draft
allowed multiple `reset_all()` calls to run concurrently against the same `SlotPool`
within one generated schedule. That interleaving is real in the sense that
`SlotPool.reset_all()` is not written to be safe against concurrent self-invocation, but
it cannot happen in production — `register_pool` (`alan/_dispatcher_loop.py`) always
constructs a brand-new `SlotPool` instance per call, so no call site ever invokes
`reset_all()` twice concurrently on the *same* pool object. The schedule generator
(`_schedules()` in `tests/test_slot_pool_properties.py`) therefore caps at one
`reset_all` per generated schedule — modeling the real concurrency contract (many
concurrent `lease`/`release` racing a single in-flight `reset_all`, exactly the #1012
shape) rather than an interleaving no code path can produce.

## Traps this suite specifically guards against

- **The stale-reclaim trap.** `_reclaim_stale_under_lock` legitimately hands a slot index
  to a second holder once `_STALE_LEASE_TIMEOUT_S` (8h) has elapsed — that is correct,
  intended behavior for a crashed worker's slot, not a bug. If the lease-exclusivity and
  no-double-assignment properties advanced their `FakeClock` by unbounded amounts, they
  would manufacture a *false* double-assignment report purely as a harness artifact. Both
  properties keep their total clock advance strictly under 8h (`reset_all` steps advance
  by `1.0`s each, schedules cap at 24 steps); the legitimate stale-reclaim path is
  covered instead by its own dedicated property,
  `test_stale_reclaim_permits_a_second_holder`, which explicitly models the ≥8h advance.
- **The blocking-claim trap.** `SlotPool._claim()` blocks on a real
  `self._cond.wait(timeout=1.0)` when no slot is free — a genuine wall-clock wait the
  `FakeClock` cannot escape, since it is a `threading.Condition` timeout, not a
  clock-domain read. An oversubscribed schedule (more concurrent `lease` attempts than
  free slots, with no compensating `release`) would make a property spend real seconds
  per generated example, which is the harness's own load-coupling risk. The harness's
  final drain force-releases any dangling open lease sessions the schedule itself never
  released, and every thread join is bounded (`_JOIN_TIMEOUT_S = 5.0` etc.) and raises
  loudly on timeout rather than hanging the suite.

## Wall-time budget

Measured on this host (a live, shared fleet machine — see caveat below), nothing else
intentionally started for the run:

- **(a) The two new files in isolation** (`uv run pytest tests/test_queue_properties.py
  tests/test_slot_pool_properties.py --no-cov -n 0`): **27.7s**, against a target of
  ≤60s.
- **(b) Full-suite delta.** Two full `uv run pytest` runs (with vs. without the two new
  files, via `--ignore`) measured **75.13s** and **87.38s** respectively — the run
  *including* the new files was the *faster* of the two. This is a real measurement, not
  a typo: this machine runs other fleet workers concurrently (`ps aux` at measurement
  time showed multiple concurrent `claude` subprocesses), and the ~12s swing between two
  back-to-back full-suite runs is larger than the ≤30s delta target itself. The honest
  conclusion is that the delta signal is noise-dominated on this shared host, not that
  the new files cost nothing — (a)'s isolated, single-worker measurement is the more
  trustworthy number, and it clears its own target with more than 2× headroom.

Per the plan, if the budget were exceeded the fix is to cut `max_examples`, never an
invariant — see the two-tier budget section above for why the queue state machine
already needed a *local increase* over the shared default rather than the reverse.

## Budget-derivation methodology

The mutant table above is what actually justifies both tiers of `max_examples`/
`stateful_step_count`:

- **Slot-pool tier (shared profile, 30/15):** every seeded mutant is caught by a pinned
  `@example()`, which Hypothesis always runs regardless of the random-search budget.
  30/15 exists purely to give the random search *some* additional exploration budget for
  defense-in-depth (catching a class of bug nobody has imagined a named mutant for yet),
  bounded by wall-time headroom rather than by any specific mutant's needs.
- **Queue tier (local override, 150/30):** empirically derived, not guessed. At the
  shared default (30 examples), the capacity-bounds queue mutant did not reliably die.
  At 60 examples it died on one trial; at 100 examples (this repo's originally-proposed
  starting point) it died reliably across repeated trials. 150 sets roughly 1.5× that
  observed floor as a safety margin, while `stateful_step_count=30` stays comfortably
  above the 6-step sequence the mutant actually shrinks to. A future Hypothesis version
  bump that changes example-generation order should re-run this specific node with the
  seeded mutant reapplied (§ above) and re-derive the floor the same way, rather than
  trusting these numbers indefinitely — they are a measured floor plus margin, not a
  law.

## Cross-references

- `docs/architecture/clock_seam.md` — the `Monotonic`/`Epoch`/`FakeClock` seam both new
  files build on; `_stamp_lease_under_lock`'s identity-vs-duration collision (the reason
  `_release`'s `== token` guard exists, and the mutant that removes it) is the deep
  background for the lease-token-identity property.
