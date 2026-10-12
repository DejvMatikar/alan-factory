# Daily digest + inner/outer dead-man rings (P3.2 deliverable B)

## Overview

Deliverable A already wires a
`reconcile_incidents` hook into `TriageTeam` (`alan/digest_sweep.py::reconcile_incidents_sweep`,
constructed at `alan/routes/infra.py`'s composition root) that upserts every fresh incident into
`incident_dispositions_v1` on every ~30-minute sensor sweep. This deliverable extends the **same**
adapter to render and persist a **daily digest** from that store, and adds a dead-man's-switch
signal for when the digest stops appearing. No new thread, timer, or scheduler is introduced — the
digest rides the existing sweep cadence.

## `alan/digest.py` — pure fold

`render_digest(dispositions, incidents, audit_counts, since, now) -> DigestDoc` is a pure function:
no I/O, no clock read (both `since` and `now` are caller-supplied), imports only
`alan.incident.{Incident, IncidentClass, AuditRow}` and `alan.incident_store.DispositionRecord` as
types, plus stdlib. It folds the disposition store + a fresh incident list + pre-bucketed audit-row
counts into a `DigestDoc`:

- `opened: tuple[OpenedEntry, ...]` — count of `DispositionRecord`s whose `opened_at` falls in
  `[since, now]`, grouped by `incident_class`.
- `closed: tuple[ClosedEntry, ...]` — count of records whose `closed_at` falls in the window and
  whose `disposition` is not `None`, grouped by `disposition`.
- `recovery_trend: tuple[RecoveryTrendEntry, ...]` — per incident class, `current_count` vs
  `previous_count` (from `audit_counts`, see below) with a `direction` of `"up"`/`"down"`/`"same"`.
- `suppressions: tuple[SuppressionEntry, ...]` — every record whose suppression is still in force
  (`now < ttl_epoch` and `occurrence_count < recurrence_threshold`), with `remaining_seconds`.
- `residue: tuple[ResidueEntry, ...]` — every currently-fresh `IncidentClass.RESIDUE` incident that
  is neither closed nor suppressed (an "unclassified anomaly" the source registry has no typed
  handler for yet — `alan/incident.py`'s "law 1: no orphans" registry-completeness rule).
- `cost_note: str` — a fixed, deterministic disclosure that this digest costs no LLM tokens to
  produce (unlike `manager_summary`'s per-stop Haiku call — see below), reinforcing "ritual, not a
  daemon, provable not probabilistic."
- `fingerprints: frozenset[str]` — every disposition-store fingerprint "touched" in the window
  (`max(opened_at, closed_at, last_seen, ack_at) ∈ [since, now]`). This is the **conservation**
  invariant: every incident/suppression touched in the window surfaces at least once through the
  digest, by construction — `fingerprints` is computed with the exact same window predicate the
  opened/closed/suppression/residue buckets themselves use, so it is always a superset (in practice
  equal, since every touched record's class of event lands in one of those buckets or was already
  visible before the window).

### Trend is counted from audit rows, never `occurrence_count`

`occurrence_count` on a `DispositionRecord` means "seen for N sweeps" (`reconcile_reopen` bumps it
every ~30-minute sweep an incident persists) — it is **per-observation**, not per-episode, and its
value is load-bearing for suppression's `recurrence_threshold` semantics (unchanged by this PR).
Using it for a trend figure would make "fired 3× today" actually mean "existed across 3 sweeps",
which lies about a single lingering incident as if it recurred three times.

Instead, `bucket_audit_rows(rows, *, since, prior_since) -> AuditWindowCounts` counts **distinct
audit-log events** — append-only rows from `job_failure_audit.jsonl` / `self_heal_audit.jsonl` /
`merge_gate_audit.jsonl` (via `alan.incident.gather_audit_rows`, already the source `derive_incidents`
uses for its own `job_failure`/`self_heal`/`merge_gate` incident classes) — bucketed into the current
window `[since, ∞)` and the immediately-preceding window `[prior_since, since)`, one count per
`incident_class`. Only rows that represent an actual **recovery** are counted: every `self_heal` row
(self-heal only ever writes on success) and every `merge_gate` row whose `recovered` field is `True`
(mechanical merge recovery can also fail; those rows are excluded). `job_failure` rows are excluded
entirely — a failure is not a recovery. This makes the trend figure invariant to how many sweeps an
incident persisted: it counts real, timestamped events, not sweep-derived bookkeeping.

### Serialization

`digest_to_dict(doc) -> dict[str, Any]` / `digest_from_dict(d) -> DigestDoc` round-trip a `DigestDoc`
to/from a plain JSON-able dict for persistence in `queue_meta`. `digest_from_dict` is a total
function over a well-formed `dict` (every field parser tolerates a missing/malformed key by
defaulting — empty string, `0`, `()`, `frozenset()` — mirroring the tolerant-deserialization
convention used elsewhere, e.g. `alan.health.load_health_state`); callers that read arbitrary
persisted JSON (`alan.digest_sweep.load_latest_digest`) guard the `isinstance(parsed, dict)` check
before calling it, the same division of responsibility `alan.incident_store.load_dispositions` uses
around `DispositionRecord.model_validate`: it validates each stored JSON-object record against the
frozen `DispositionRecord` model, whose fields each carry a lenient validator that falls back to a
default instead of rejecting the record. A non-object record is skipped, unknown keys are ignored,
and a malformed suppression becomes none while the record is kept.

## Emission — `alan/digest_sweep.py`, date-key gated

`reconcile_incidents_sweep` (the same function Deliverable A wires into `TriageTeam`) now also
drives digest emission in the same pass:

1. Reads `digest_last_date` from `queue_meta` (a plain date string, e.g. `"2026-07-05"`, in the
   configured digest-day's own local date — see the deadline section below).
2. Resolves today's `deadline` via `alan.health.resolve_digest_deadline(now)`.
3. Passes `digest_last_date`/`now`/`digest_deadline` into `derive_incidents(...)` so a
   `digest_overdue` incident (see below) is upserted into the disposition store on the same pass
   that decides whether to render a new digest.
4. If `digest_overdue(digest_last_date, now, deadline)` is `True` — i.e. `now` is past today's
   deadline and no digest has been recorded for that date yet — renders a fresh `DigestDoc` via
   `render_digest`, persists it to `queue_meta["digest_latest_v1"]`, sets
   `queue_meta["digest_last_date"] = deadline.date().isoformat()`, and pushes it to the outer-ring
   webhook (below). Otherwise it is a no-op for the digest.

This reuses **one** predicate (`digest_overdue`) for two purposes: deciding whether the sweep should
render a new digest, and (fresh per request, see below) raising a dead-man alarm when it hasn't.
Because the compare-and-set is exactly "read `digest_last_date`, maybe write a new one keyed by
today's date", two sweeps landing on the same digest-day produce byte-identical digests — the first
sweep past the deadline renders and stamps `digest_last_date`; every subsequent sweep that day sees a
match and short-circuits before doing any work. This mirrors the existing `dep_sweep_next_run`
compare-and-set precedent (`alan/reconciler.py::seed_dep_sweep`).

`since` for the next render is the **previous** digest's own `generated_at` (read back via
`load_latest_digest`), or the Unix epoch if none has ever been rendered — so the first-ever digest's
window covers all prior history, and every subsequent digest's window is exactly "since the last
digest landed." The prior window for the trend calculation is the same duration immediately
preceding `since`.

## `alan/health.py::resolve_digest_deadline` — tz/hour resolution (shared, cycle-safe)

`ALAN_DIGEST_TZ` (IANA name, default `"UTC"`, falling back to UTC on an unknown zone) and
`ALAN_DIGEST_HOUR` (0–23, default `9`, falling back to `9` on a non-numeric or out-of-range value)
are resolved once per call into `resolve_digest_deadline(now: datetime) -> datetime`: `now` converted
into the configured tz, with `hour` as the day's cutoff (minute/second/microsecond zeroed). This is
the **only** piece of ambient config-reading in the whole feature; both `render_digest` and
`digest_overdue` receive the already-computed `deadline`/`now` as plain arguments and never read an
env var, the clock, or a timezone database themselves.

This function specifically lives in `alan/health.py` — not in `alan/digest.py` or
`alan/incident.py` — because of a real import-linter constraint: `alan/incident.py` already imports
`alan.health` (for `HealthState`/`HealthFinding`, used by its own `_incidents_from_health`
appender), so `alan.health` importing back from `alan.incident` (or from `alan.digest`, which itself
imports `alan.incident`) would be a circular import. `resolve_digest_deadline` has zero dependency on
either module (only `os`/`datetime`/`zoneinfo`), so it can safely live in `alan.health` and be
imported by both `alan.digest_sweep` (sweep-side generation gate) and `alan.routes.infra`
(`_digest_overdue_flag`, the `GET /api/health` read-side alarm), giving both a single
shared source of truth for the day boundary.

## Synthetic-finding folds: removed

`alan/digest.py` used to carry `residue_findings` and `pending_rca_findings`, the module's only consumers of `alan.health.HealthFinding`, called from `alan/routes/infra.py`'s `GET /api/health` handler to fold `unclassified_anomaly` (one per digest residue entry) and `pending_disposition` (one per RCA draft with no recorded human disposition) findings into `blocked_count`. Both functions, `_dispositioned` (their private helper), the `HealthFinding`/`RcaDraft` imports and the `infra.py` call site (`_digest_synthetic_findings` and its imports of `load_latest_digest`, `load_dispositions`, `load_rca_drafts`) are deleted: the two kinds asked the operator to hand-classify anomalies, and the Health page now leads with a read-only observation surface instead (see [health.md](health.md), [unblocker.md](unblocker.md)'s "Activity view"). The digest no longer emits either finding kind, and `blocked_count` drops by exactly the folded findings' count — intended. `DigestDoc.residue` is unchanged and still feeds `/api/health/digest`'s residue tile. `POST /api/health/incidents/{fingerprint}/disposition` and `alan.incident_store.set_disposition` survive: the route simply has no UI caller now.

## Inner ring — `digest_overdue` (`alan/incident.py`)

`digest_overdue(digest_last_date: str | None, now: datetime, deadline: datetime) -> bool` is a pure,
three-argument predicate: `False` while `now < deadline`; otherwise `True` iff
`digest_last_date != deadline.date().isoformat()`. It reads no clock and no queue_meta itself — the
caller resolves `deadline` (via `resolve_digest_deadline`) and reads `digest_last_date` from
`queue_meta`, then passes both in.

`_incidents_from_digest_overdue` wraps a `True` result into a single `Incident` of a new
`IncidentClass.DIGEST_OVERDUE`, appended in `derive_incidents` **alongside** (not inside) the
`_incidents_from_health` call — it is SYSTEM-scoped (`scope_ref="system:digest"`, `workspace_id=None`,
`pr_number=None`), not tied to any one workspace, so it does not belong in the per-workspace
`_SOURCES` registry. `derive_incidents` gained three new optional keyword parameters
(`digest_last_date`, `now`, `digest_deadline`); when `now`/`digest_deadline` are omitted (every
existing caller that doesn't care about the digest feature), no digest-overdue incident is ever
produced, so `alan/incident.py` stays exactly as queue-free and clock-free as before.

The `digest_overdue` incident's fingerprint is constant (`scope_ref` never varies), so once a human
disposes of it (e.g. `root_caused` — "I've restarted the sweep"), `incident_store.reconcile_reopen`'s
existing law-4 recurrence detection reopens it automatically if it genuinely recurs later (a fresh
`last_seen` after the record's `closed_at`), exactly as it does for every other incident class.

`GET /api/health` (`alan/routes/infra.py`) re-derives this signal **fresh per request** — no
persisted state, no background thread. It is now the only HTTP surface that does: the deleted
`GET /api/inbox` used to fold a `digest_overdue` incident through the ordinary incident pipeline as
a `"failure"`-typed `InboxItem` (disposition/suppress/ack lifecycle included); that whole derivation
path is gone with the rest of the inbox HTTP surface (delete-the-inbox-http-surface, 2026-09) and was
not ported — the `alan/health.py`/`alan/routes/health.py`'s `/api/health/digest` endpoint (see
[health.md](health.md)) covers `recovery`/`residue`/`digest_overdue` but carries no per-incident
disposition lifecycle of its own.

`alan/routes/infra.py::_digest_overdue_flag(queue, now)` reads `digest_last_date` from `queue_meta`
and calls `alan.incident.digest_overdue(digest_last_date, now, resolve_digest_deadline(now))`,
folding the plain boolean result into the response as `"digest_overdue"` — a read-only snapshot, so a
boolean flag is enough to make a stalled sweep visible there.

## Outer ring — webhook push

`alan/digest_sweep.py::_push_digest_webhook` is called once per successful digest render. It reads
`ALAN_DIGEST_WEBHOOK_URL` (from `~/.alan/secrets.env` in production, per the deploy doc's existing
secrets convention) and, if unset, logs at INFO and returns — the digest is still recorded to
`queue_meta["digest_latest_v1"]` and readable via `load_latest_digest`, no exception, no new
dependency. If set, it `POST`s the digest's
`digest_to_dict` JSON body via stdlib `urllib.request` only (mirrors
`alan.teams.deploy_verify._probe_health`'s existing urllib usage) with a 10 s timeout; any
`URLError`/`OSError`/`ValueError` is caught and logged at WARNING — the sweep (and the digest's own
persistence to `queue_meta`) has already completed by this point, so a failing webhook never blocks
or fails the sweep.

## Tombstone — harvesting `manager_summary` into inbox recommendations (removed, 2026-09)

`alan/manager_summary.py`'s per-stop Haiku summary (`docs/architecture/manager_summary.md`) used to
be harvested into the deleted `/api/inbox` surface's item recommendations: `_harvested_recommendation`
read `read_manager_summary(ws_path)` and substituted its `text` for a mechanical fallback string at
every recommendation construction site (`_question_item`, `_awaiting_harvest_item`, `_failure_item`,
and conditionally `_unblock_item`). All of that lived in `alan/inbox_items.py` and was deleted with
the rest of the inbox item-derivation layer when it shrank to `alan/proposal_items.py`
(delete-the-inbox-http-surface, 2026-09) — none of the five surviving `proposal_items` symbols
harvest a manager summary. `alan/manager_summary.py` itself, and the prompt-enrichment work below,
are untouched: the summary is still generated on every autonomous terminal/blocked transition, it
just has no reader that folds it into a recommendation string anymore.

### Prompt enrichment: DoD evidence + park reason

`alan/manager_summary.py::_do_generate` now reads `ws_path / "meta.json"` directly — via plain
`json.loads`, not `alan.workspace.Meta` — to pull `park_reason` and every task's
`dod_clauses[].evidence` (paired with each clause's `text`) into the Haiku prompt
(`_build_user_prompt`'s new optional `park_reason`/`dod_evidence` parameters). This is a deliberate
raw-JSON read rather than an `alan.workspace` import: `alan/manager_summary.py` sits **below**
`alan/workspace.py` in `pyproject.toml`'s import-linter layers list, so a real `Meta`/`Workspace`
import would be backwards. Reading the same file as an untyped dict — tolerating a missing file or
malformed JSON by degrading to `(None, [])` — keeps the layering intact while still enriching the
summary with structured signal the model previously never saw. `trigger_manager_summary`'s public
signature is unchanged; every existing dispatcher call site benefits automatically since the read
happens from `ws_path`, which was already a parameter.
