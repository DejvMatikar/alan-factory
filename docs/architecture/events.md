# Global event channel — `GET /api/events`

`alan/routes/events.py` exposes one app-wide SSE stream that tells clients *that*
something changed, never *what*: each frame is `event: changed` with
`{"kinds": [...]}` where kinds ⊆ `{workspace_list, health, engines}` server-side. Clients
react by refetching the corresponding endpoint — which is cheap post-P1/P2 (the
list is stat-validated) and always
freshness-validated at read time, so a lost or dropped event can never cause
staleness beyond the fallback poll. Events are dirty-hints, not data carriers.

**Inbox kind retired (delete-the-inbox-http-surface, 2026-09):** the entire inbox HTTP surface
(`alan/routes/inbox.py`, `GET /api/inbox`, `GET /api/inbox/count`) is deleted — there is no
`inbox`-scoped read left for any client to refetch. `_INBOX_ONLY_KINDS` and the `"inbox"` entry in
the default fan-out are both gone; the frontend's `ChangeKind` union already excluded `inbox` (from
the earlier remove-inbox-from-web-ui UI removal), so no live subscriber ever loses a signal it was
reading. The surviving project-amendment approve handler (`alan/server.py`'s
`approve_project_amendment`) now publishes its own `project_amendment_approved` kind instead of the
retired `inbox_disposition` — it is not inbox surface (it approves a durable pending-amendment
record, unrelated to the deleted incident-ledger presentation), so it earns its own event kind rather
than either being deleted or continuing to publish an inbox-named event into a system with no inbox.

## Emit surface

`PubSub` (`alan/dispatcher.py`) gained `subscribe_all`/`publish_global`; the keyed
`publish` also fans out to all-listeners, so the dispatcher's existing job/node
lifecycle events and the route-level `workspace_*` publishes reach the global
channel with no new emit points. Gap fills: `workspace_created` and `note_updated`
publishes in `routes/workspaces.py`, `project_amendment_approved` (`publish_global`) from
`approve_project_amendment`, a `health_state_saved` TeamEvent emitted by
`TriageTeam.run` after `_run_sensors`, and the `engines` sites below (R1.7).
Handover saves need no emit — dispatcher routing events always follow them, and
out-of-band writes are covered by read-validation plus the client's fallback poll.

`engines` (R1.7): `routes/engines.py` publishes `engine_created`/`engine_updated`/
`engine_deleted` (create/update/delete) and `engine_posture_changed`
(`POST .../posture`) via `publish_global` after each persist succeeds. `preflight_completed`
(R1.4) is published the same way from `POST /api/engines/{owner}/{repo}/preflight` after
`alan.engine_preflight.run_preflight` returns — see `engine_preflight.md`.
`dispatcher.py` publishes `pool_lifecycle_changed` from `register_pool` (synchronously,
right after the dict insert, before the warm-up thread starts), from `_warm_pool`
(in a `finally`, so a warm-up failure still emits the dirty-hint — that thread
runs on a daemon thread, and `publish_global` copies the listener list under
`PubSub._lock` then calls the module-level `_fanout` (`alan/events.py`) synchronously
after releasing the lock, so no thread-to-loop bridge is needed there; the
asyncio-loop hop back to SSE subscribers happens in `events.py` via
`call_soon_threadsafe`), and from
`dispose_pool` after `finish_dispose()` succeeds. Emission is deliberately
lifecycle-only — CRUD, posture, and pool create/warm/dispose — and never fires on
per-lease `free_count`/`leased_count` churn (acquiring/releasing a slot), because
the client's re-read of `GET /api/engines/{owner}/{repo}/pool` does a full
recursive disk walk (`snapshot()` → `disk_usage()`); emitting per-lease would
mean a recursive walk per pool per dispatch — the read-herd class this item
exists to kill. The boot-time load path (`_load_engines`, which runs
`_import_legacy_engines_once` and then the saved-record boot merge) has no live SSE client yet and deliberately emits
nothing. The frame carries only `{"kinds": ["engines"]}` — no slug, no config, no
secret material — the read endpoints (`GET /api/engines`, `GET
/api/engines/{owner}/{repo}/pool`) are where redaction already lives (`EngineConfig`
is name-only; secrets sit in `secrets_store` keyed by record name). `preflight_completed`
follows the same no-payload-fields discipline — the persisted `PreflightResult` (findings,
overall status) is read back via `alan.engine_preflight.load_last_preflight`, not carried
on the event.

## Classification and flow control

`_kinds_for` maps event kinds → buckets: `stream` (per-token node output) is
dropped, `health_state_saved`/`driver_loop_changed` → `health`, the six
`_ENGINE_KINDS` (`engine_created`/`engine_updated`/`engine_deleted`/
`engine_posture_changed`/`pool_lifecycle_changed`/`preflight_completed`) → `engines`, everything else
(workspace-scoped lifecycle, including `project_amendment_approved`) → `{workspace_list, health}` —
health is included because queue-pause transitions have no event of their own but always coincide
with job lifecycle events (a usage-limit pause fires `job_paused`), and `/api/health` is cheap
post-P1 (30 s-cached quality metrics). This default fan-out dropping `inbox` is its own decision,
separate from deleting `_INBOX_ONLY_KINDS` above: it changes what every *other*, still-unclassified
event kind fans out to, not just the retired one, so it carries its own test
(`test_kinds_for_default_fan_out_excludes_inbox` in `tests/test_routes_events.py`) rather than riding
the inbox-kind deletion's coattails.
The `engines` branch is a dedicated check, not folded into the catch-all — the
catch-all fires on every workspace-scoped event, so appending `engines` there
would trigger a console re-read (and the pool's recursive disk walk) on every
unrelated workspace event, inverting the fd-reduction goal this item exists for.
The generator bridges the threadsafe pubsub callback into a **bounded**
`asyncio.Queue` (256, drop-on-full — safe because events are hints) and
**coalesces server-side**: after the first event it drains further events for
250 ms and emits a single merged frame, so a burst of node transitions costs one
refetch. A `: ping` heartbeat every 15 s keeps proxies from timing the stream out
and lets clients detect dead connections. The per-workspace
`GET /api/workspaces/{id}/events` channel is untouched.
