# Clock seam (`alan/clock.py`)

`alan/clock.py` is a small, stdlib-only seam that makes the two time domains
a program reads from the operating system — wall-clock (epoch) time, which
can jump backwards or forwards (NTP adjustment, DST, manual clock set), and
monotonic elapsed time, which cannot — a fact the type checker and a plain
text search can both see, instead of a convention a reader has to remember.
It replaces raw `time.time()` / `time.monotonic()` calls in `alan/queue.py`
(2 epoch sites) and `alan/slot_pool.py` (4 monotonic sites).

## Why it exists: the #327 deadlock

A production pool deadlock traced back to exactly this confusion: code
compared a `time.monotonic()` reading against an epoch-domain threshold.
Both are bare `float`s, so the comparison type-checks and runs — it just
never becomes true, because the two numbers live on unrelated scales (one is
seconds since an arbitrary, per-process start point; the other is seconds
since the Unix epoch). The condition silently never fires, and the pool
never recovers. `tests/test_clock_domain_regression.py` reconstructs this
comparison shape against `alan.clock`'s typed wrappers and pins that the
seam now rejects it outright (`TypeError`) instead of silently never firing.
No production code carries the original broken shape any more — it was
removed when the defect was fixed — so that test exercises the seam
directly, not a live code path.

## Design: distinct types, not distinct methods

`Monotonic` and `Epoch` are separate `@dataclass(frozen=True, slots=True,
order=True)` wrappers around a single `value: float` field, not two methods
on `Clock` that both return bare `float`. Two float-returning methods
(`clock.monotonic_seconds()` / `clock.epoch_seconds()`) would be readable at
the call site but not enforceable — nothing stops a caller from taking the
result of one and comparing or subtracting it against the result of the
other, because both are still just `float` once returned. Distinct types
make that mistake a `mypy`/`pyright` error, not just a naming-convention
violation a reviewer has to catch by eye.

Each type's `__sub__` accepts only its own type and guards at runtime with
`if type(other) is not <Self>: raise TypeError(...)` — never `isinstance`.
The dataclass's own field is inherited by `isinstance` for any duck-typed
lookalike, so the check has to be nominal (`type(...) is`), not structural.
This also sidesteps `pyright`'s `reportUnreachable = "error"` setting
(`pyproject.toml`): a guard that pyright can statically prove always
matches the annotated type appears to have a dead negative branch, which the
gate then flags. `type(other) is not <Self>` keeps the negative branch live
because pyright cannot rule out a subclass instance being passed at runtime,
even though the parameter is annotated as the exact dataclass type.

The `order=True` dataclass flag gives both types `__lt__`/`__le__`/`__gt__`/
`__ge__`/`__eq__` for free. Because their generated comparison methods
return `NotImplemented` for a mismatched class (dataclass-generated
behavior, not custom code), cross-domain `<`/`<=` already raise `TypeError`
without any extra guard, and cross-domain `==` is `False` (Python's default
identity fallback when both sides return `NotImplemented`) rather than a
type-check failure — equality across domains is a meaningful "no" answer,
not a programmer error, so it doesn't need to raise.

## The collision: one monotonic reading, two meanings

`SlotPool._stamp_lease_under_lock` (`alan/slot_pool.py`) returns a single
`Monotonic` reading that serves two different purposes depending on where
it flows:

- **Identity token** — `_release` compares the token a caller presents
  against `self._lease_times[idx]` by **equality**. Whichever code path
  currently holds a slot is defined by "whoever has the token that matches
  what's stored," not by any ordering or duration.
- **Duration input** — `_reclaim_stale_under_lock` **subtracts** a stored
  `Monotonic` from the current reading and compares the resulting `float`
  against `_STALE_LEASE_TIMEOUT_S`. Here the same stored value participates
  in an elapsed-time calculation.

`Monotonic` resolves this without splitting the token into two fields:
**the wrapper always expresses an instant** (structural equality is the
dataclass's default `__eq__`, so token comparison in `_release` is exactly
"are these two instants the same reading"), and **duration is never stored
— it only exists as the transient `float` returned by `Monotonic.__sub__`**
at the point `_reclaim_stale_under_lock` computes it. One representation,
two call sites reading it two different ways; the type itself only ever
claims to be an instant. `_stamp_lease_under_lock` carries the one
permitted invariant comment for this (per `CLAUDE.md`'s concurrency-seam
carve-out): a clock that does not advance between two stamps of the same
slot yields colliding tokens, because the stamp doubles as the lease's
identity. `tests/test_slot_pool_lease_clock.py::
test_non_advancing_fake_clock_yields_colliding_stamps` pins this directly,
and `tests/fake_clock.py`'s `FakeClock` is deliberately explicit-advance-only
(no auto-tick on read) so this hazard can never be silently masked by a
permissive fake clock in a future test.

## Injection: a real default, no singleton

`Queue.__init__` and `SlotPool.__init__` each take a keyword-only `clock:
Clock = SYSTEM_CLOCK` parameter, where `SYSTEM_CLOCK: Final[SystemClock]` is
an immutable module-level instance with no setter and no reassignment path.
This gives every call site a working default (`Queue()` and `SlotPool(...)`
behave exactly as before) without the global swappable time object the
roadmap explicitly forbids (T2-12) — there is nothing to monkeypatch at
module scope; a test that needs deterministic time constructs its own
`FakeClock` (`tests/fake_clock.py`) and passes it in at the same
constructor parameter production code uses.

## Consuming the seam from elsewhere (H6.1b and beyond)

`alan.clock` has zero dependencies on the rest of `alan` — it sits at the
bottom of the `[tool.importlinter]` layered contract in `pyproject.toml`,
below every module migrated onto it (`queue`, `slot_pool`, and — as of
H6.1b — `dispatcher`, `_dispatcher_loop`, `_dispatcher_usage_limit`). Any
future caller, regardless of which file or package it lives in, consumes
the seam the same way: import `Clock`, `Monotonic`/`Epoch`, and
`SYSTEM_CLOCK` from `alan.clock`, accept a keyword-only `clock: Clock =
SYSTEM_CLOCK` parameter at the relevant constructor or entry point, and
read `.monotonic()` / `.now()` instead of `time.monotonic()` /
`time.time()`. Nothing about the contract assumes a particular caller's
file layout, module depth, or whether it is a class, a function, or a set
of module-level functions — H6.1b's dispatcher migration is a worked
example of exactly that: `Dispatcher.__init__` takes the keyword-only
`clock` parameter and stores it on `DispatcherState._clock`, the one
attribute surface all five dispatcher mixins already type-check against;
the module-level `_delay_for_ci_reset` (no instance to hang a clock off)
takes its own keyword-only `clock: Clock = SYSTEM_CLOCK` parameter instead,
passed as `clock=self._clock` from its single call site.

## Structural gate

`tests/test_no_raw_clock_reads.py` AST-walks every module named in
`tests/raw_clock_reads.py::CLOCK_MIGRATED` (`alan/queue.py`,
`alan/slot_pool.py`, `alan/dispatcher.py`, `alan/_dispatcher_loop.py`, and
`alan/_dispatcher_usage_limit.py`) and flags `time.time()` /
`time.monotonic()` / `time.perf_counter()` (and their `_ns` variants) in
both `import time` and `from time import ...` styles, plus
`datetime.now(...).timestamp()`. The exemption list,
`tests/raw_clock_reads.py::RAW_CLOCK_READS`, mirrors the already-proven
`tests/raw_root_resolution.py` allowlist-does-not-grow pattern (a
frozenset checked by a companion `test_raw_clock_reads_allowlist_is_empty`
test) and is empty as of this change. `alan/queue.py::_now_iso`'s
`datetime.now(UTC).isoformat()` and
`alan/dispatcher.py::_format_project_fact_bullet`'s
`datetime.now(UTC).strftime(...)` are deliberately not flagged — both
format a timestamp string for storage/an audit record, never a
lease/timeout/heartbeat comparison, so neither participates in the
monotonic-vs-epoch confusion this seam exists to prevent.

## Boundary rule: reads are migrated, storage/transport values stay plain floats

The seam covers *reads* — every direct `time.time()` / `time.monotonic()`
call becomes a `.now()` / `.monotonic()` call through the injected clock.
Values that flow onward into existing storage or transport structures
(`RouterDecision.eligible_at`, `Job.eligible_at`,
`Queue.requeue_with_server_error_backoff`'s parameter,
`Queue.paused_until()`'s return) stay plain `float` — widening those types
is out of scope for a clock-read migration. `Queue.paused_until()`
returning a bare `float` epoch (rather than an `Epoch`) is the precedent:
callers that need the typed domain re-wrap explicitly at the point they
actually compare, e.g. `alan/_dispatcher_loop.py`'s `Epoch(paused) -
self._clock.now()`. That is also the only one of H6.1b's five sites that
performs a genuine comparison (a subtraction, in this case) rather than
adding a delay to a current reading to produce a value handed to existing
`float`-typed storage — and consequently the only site where the
cross-domain `TypeError` guard can engage, the same shape as the #327
deadlock this seam exists to prevent.

## Known triage/claude_cli time-reading scope drift (not fixed here)

`alan/teams/triage.py:265,267` has 2 epoch reads and `alan/claude_cli.py`
has 6 monotonic reads (`:721`, `:733`, `:740`, `:759`, `:777`, `:801`) in
its stdout-activity watchdog. Neither is touched by H6.1a or H6.1b:
`alan/teams/triage.py`'s reads were out of both items' declared scope, and
`alan/claude_cli.py` is additionally drift-gate load-bearing (a change
there forces a same-PR `CLAUDE.md`/`docs/architecture/**` update, and the
`drift-ok` bypass label is not available to this roadmap). Both remain
open drift belonging to neither H6.1a nor H6.1b; a future item that wants
them re-scopes them explicitly rather than inheriting this note's silence
as permission.
