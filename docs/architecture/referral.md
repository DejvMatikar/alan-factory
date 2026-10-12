# Referral capability — REMOVED (2026-07-10)

**Tombstone.** The cross-chat-family referral capability (`prepare` → `accept`, `alan/referral.py`, `alan/routes/referral.py`, the Explore/Build "Refer to…" affordance, `ChatOrigin` provenance, and the read-only source-session view) was deleted whole by P6.3. Cross-consultant conversation handoff is **retired, with no replacement** — the owner confirmed this contradicts the roadmap's original "retarget" note.

Durable provenance is no longer a per-session `ChatOrigin` back-pointer. It now lives in the project ledger's workspace → project → PR spine (`docs/architecture/projects.md`): every workspace optionally carries a `project_id`, and a project's ledger derives its workspace/PR history from that link — no in-band chat-to-chat pointer is needed.

Durable *context* carry-forward (the part referral's brief-seeding approximated) is served by promote-to-doc instead: a build or explore brief's decision reaches the project's Decision Log via propose-amendment → approve (`docs/architecture/project_doc.md`, `POST /api/projects/{project_id}/amendments` → `.../approve`). This is not a drop-in replacement for referral — there is no automatic hand-off of an in-flight conversation to a new session — it is the durable substitute for the underlying need (a decision surviving past the conversation that produced it).

If session-lineage-on-project-page (e.g. "this session was referred from that one") is ever wanted again, it is a separate, future, explicitly-scoped task — not a resurrection of this file's prior contents.
