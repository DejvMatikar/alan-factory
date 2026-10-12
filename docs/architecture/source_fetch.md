# Source fetch

`alan/source_fetch.py` gives research-shaped workers a contract-bearing way to
read an HTTP(S) resource's own bytes, invoked as `python3 -m alan.source_fetch
<url>` — a stdlib-only module (no import from `alan`) run under the system
interpreter, not `uv run`. It exists because the only other sanctioned read
path, `WebFetch`, routes the response through a summarization model: a worker
using it cannot tell "the source returned nothing" from "my tooling
summarized it away," and cannot count or parse a machine-readable response.
One wrong, confidently-reported decision already came from this — a
structured endpoint read as capped and empty when raw bytes showed roughly
33x more data.

**This is a contract, not new network reach.** Research-shaped roles already
hold an unguarded general shell primitive (`Bash(uv run:*)` is in
`base_tools` in both tracked configs, reachable by every team including
verification). A worker hand-rolling a fetch with that primitive still cannot
distinguish empty from unreachable from truncated — that is the defect this
module exists to prevent, not a capability gap. Nothing here closes the
worse path: a careless worker can still hand-roll a fetch and reproduce the
exact defect.

## What it reaches

Only `http://` and `https://` URLs. Any other scheme (including `file://`) is
refused before any network attempt. The target host is resolved and every
resolved address is screened; if **any** resolved address is private in the
standard sense — loopback, link-local, unspecified, or RFC1918 — the whole
fetch is refused with `detail="private-address"`, never attempted. IPv6
addresses are unmapped explicitly (`.ipv4_mapped`) before classification
rather than trusted to `ipaddress`'s own `::ffff:0:0/96` blanket rule, so an
IPv4-mapped public address is not over-blocked and an IPv4-mapped private one
is not missed. The reason for the address screen is request forgery against
the fleet's own live API on the loopback interface (`127.0.0.1:8585`) — a
worker talked into fetching a URL that resolves there. Redirects are never
followed: a 3xx response is reported as the `redirect` outcome with its
`location`, and the caller re-issues deliberately if it wants to follow.

**Accepted limitation — DNS time-of-check window.** The module resolves the
host once to screen addresses, then hands the URL (not a pinned address) to
the HTTP layer, which re-resolves it. Closing that window needs
address-pinned connections with SNI and certificate-hostname overrides, which
blows the complexity budget to defend against an attacker who already
controls a name the worker was told to fetch. The real threat this module
stops — a worker talked into hitting the fleet's own API on loopback — is
stopped by the screen regardless.

## The cap and truncation contract

The body is read with a single `read(cap + 1)` (default cap 256 KiB) — one
read, so a timeout costs one wait, not a second full timeout on a probe byte.
`bytes_read` is the body actually returned, capped at `cap`. `total_bytes` is
how much was actually consumed from the read (`cap + 1` when the body did not
fit) — **a lower bound on the true body size once truncated**, not the
server's real Content-Length. `outcome` is `"complete"` when `total_bytes <=
cap`, `"truncated"` when `total_bytes > cap`, and `"empty"` when
`total_bytes == 0`. There is no offset and no range request: an earlier
design considered walking a capped source with an offset, but the incident
that motivated this module needed page two of a paginated source — a
different URL, not a byte-offset walk of one response — and an offset drags
in a snapshot-coherence hole (a server that ignores a Range header forces a
re-fetch-and-skip, silently interleaving two snapshots across two "pages").
Truncation being a visible dead end is the goal; truncation being invisible
is the defect this module exists to prevent.

The outcome vocabulary has seven members: `complete`, `truncated`, `empty`,
`redirect`, `unreachable`, `refused`, `client_rendered`. The last is *named,
never detected* — nothing in this module classifies a response as
client-rendered; a worker that reads a `complete` body that turns out to be
an empty client-side-rendering shell names that outcome itself in its own
report. A capability that silently returns an empty result for several of
these outcomes recreates the original defect.

`Accept-Encoding: identity` is sent explicitly and the response is never
decompressed; `content_encoding` is reported in the envelope so a compressed
body's byte count is never silently misread as decoded text.

## Output shape

Standard output is three parts written back to back: one JSON metadata line
(`outcome`, `url`, `status`, `location`, `content_type`, `content_encoding`,
`bytes_read`, `cap`, `total_bytes`, `detail`), the raw body bytes verbatim
(exactly `bytes_read` bytes, untouched — even if it happens to contain bytes
that look like the terminator), then a fixed terminator line. A caller
trusts the metadata line's `bytes_read` to know how much body follows — it
never scans for the terminator inside the body. The terminator's only job is
end-of-envelope signalling: if it is missing, the harness truncated the
tool's stdout output before the module's own output ended, which is a
different failure than anything `outcome` reports.

The process exits `0` for every well-formed outcome, including `refused` and
`unreachable` — those are successful classifications, not process failures.
It exits `2` only on argv shape errors (missing or multiple URL arguments).

## The four-file grant (team_tools-scoped), and why code alone grants nothing

`render_allowed_tools` (`alan/teams/_base.py`) falls back to the in-code
`_TEAM_TOOLS` lists in `alan/engine_config.py` only when a config's
`base_tools` **and** `team_tools` are both empty. Both tracked engine
configs carry full explicit tool lists, so in production the grant is
**file-authoritative**, not code-authoritative. Landing a `team_tools`-scoped
entry point like this one requires four files changing together:

1. `alan/engine_config.py` — `_WEB_RESEARCH_TOOLS` (fans to `research`,
   `brainstorming`, `opportunity_scout` via `_TEAM_TOOLS`).
2. `tests/owner/engine_config_default.yaml` — the owner golden the
   characterization tests diff the in-code defaults and the booted default
   engine against.
3. `config/engines.d/alan.yaml`
4. `config/engines.d/<repo>.yaml` for every other connected engine

A PR that only changes file 1 passes every unit test and grants nothing in
production. `tests/test_engine_config_files.py` guards against exactly this:
it loads the **real** tracked configs (`config/engines.d/*.yaml`, passed
explicitly — the suite's autouse `_empty_engine_config_dir` fixture redirects
the *default* config directory to an empty path for every other test, so a
call with no directory argument would load nothing and pass vacuously),
asserts the loaded mapping is non-empty before asserting anything about it,
then asserts the entry resolves via `render_allowed_tools` for both tracked
repos' `research`/`brainstorming`/`opportunity_scout` teams and is absent
from `verification`.

**A `base_tools`-scoped grant needs two more files.** `base_tools` is the
part of the grant every team draws on — including `verification`, which
carries no `team_tools` entry of its own — so widening it (e.g. the
read-only staples added alongside the reviewer-evidence change: `head`,
`tail`, `wc`, `diff`, `nl`, and six read-only `git` subcommands) needs the
same four files above plus two CI-enforced mirrors, or the sync gates fail
even though the grant itself would be file-authoritative and correct:

5. `alan/teams/_scenario_runner.py`'s `_ALLOWED_COMMAND_PREFIXES` — asserted
   set-equal to `BASE_ALLOWED_TOOLS`'s Bash prefixes by
   `tests/test_scenario_runner_allowlist_sync.py`, because the same list also
   gates which commands `_check_allowlist` accepts for a scenario's `when`
   field (and, via `implementation_fix`'s `scenario_rebind` channel, which
   commands a fix worker may rebind a scenario to).
6. `tests/test_engine_config_equivalence.py`'s literal `_SNAPSHOT_BASE_TOOLS`
   — a hardcoded snapshot the round-trip test diffs against.

A base-reference fetch (`git fetch origin main`) was considered for this
same grant and rejected: unlike the staples above, it writes to the
checkout's `.git` refs and performs network I/O, which the shared
read-only surface's admission rule excludes regardless of wildcard shape.
`alan/teams/_review_context.py`'s diff-unavailable fallback text tells a
diff_reviewer to proceed and note the limitation instead of fetching
anything itself, for the same reason.

**An engine with a persisted registry record but no tracked `config/engines.d/`
file would go ungranted** — this is a limitation to check against, not
asserted absent anywhere in this codebase today.

## Only the worker phase, only new workspaces

`alan/teams/_orchestrated.py`'s `OrchestratedTeam` gives the *worker* node
`render_allowed_tools(cfg, team_name)` and gives the orchestrator, the
synthesizer, and the verifier node `render_base_tools(cfg)` — the entry
point never reaches the orchestrator/synthesizer/verifier by construction,
verified as a regression in `tests/test_source_fetch_grant.py` rather than
built. `tests/test_engine_config_files.py::test_source_fetch_absent_from_verification`
covers the same fact against the real tracked configs.

A workspace's governing config is resolved from `ws.meta.engine_snapshot`
(`governing_config_for_ws`), pinned at first dispatch — so this grant reaches
**newly created workspaces only**; nothing already in flight is unblocked by
this change.

## Why `python3 -m alan.source_fetch`, not `uv run`

`alan/engine_preflight.py`'s `_bash_allowlist_prefixes` /
`_command_reachable` is this repo's own model of the harness's permission
semantics: `Bash(<prefix>:*)` is a token-prefix match on argv. `Bash(uv
run:*)` is already in `base_tools` in both tracked configs — granted to
*every* team, verification and pr_merge included — so an entry point invoked
as `uv run ...` would already be reachable by every role and the
research-shaped role scoping in this design would be decorative. Granting it
as `Bash(python3 -m alan.source_fetch:*)` in `team_tools`, scoped to exactly
three teams, is what makes the scoping real.

Two facts make `python3 -m alan.source_fetch` work at all, and both are
asserted by tests rather than assumed:

- `alan/__init__.py` is empty, so the module runs under the system
  interpreter with no virtualenv and no `alan` package dependencies beyond
  the stdlib.
- `WorkerOverrides.cwd` is `None` for a worker's process (`_worker_overrides`
  in `alan/teams/_orchestrated.py` sets only `allowed_tools`), so the
  subprocess **inherits the service's working directory**, which the launch
  plist pins to the alan checkout. This is why the entry point works for
  an iOS-preset engine's research runs even though that engine's own repository has
  no Python interpreter or dependencies of its own — an in-repo shell script
  would not have this property.
  `tests/test_source_fetch.py::test_source_fetch_subprocess_offline_refused_smoke`
  asserts this end to end: it spawns `/usr/bin/python3 -m alan.source_fetch`
  with `cwd` set to the repo root and `VIRTUAL_ENV`/`PYTHONPATH` stripped
  from the environment, and asserts a `refused` envelope comes back.
- `_worker_env` (`alan/claude_cli.py`) builds the subprocess environment from
  `**os.environ` and scrubs only slot/worktree/dial vars, registered secret
  names, and the control token — `PATH` is untouched, so `/usr/bin/python3`
  resolves normally.

`Bash(python3:*)` already reaches this module today from `implementation`
and `implementation_fix` (their `team_tools` grant the bare `python3`
prefix) — that reachability is pre-existing, not widened by this change.
"Research-shaped roles only" describes the **new grant**, not the complete
set of roles that can already reach this module by argv prefix.

## What the harness actually enforces

The real permission matcher is Claude Code's own Bash safety analyzer, not
`alan/engine_preflight.py`'s allowlist-prefix model — that function is
evidence of intent and a preflight check, not the enforcement point. A
prefix mismatch between what this document says and what the harness
actually grants surfaces as a **visible denial** at the call site, never a
silent over-grant.

## Out of scope

A workspace that needs both web reading and file-writing capability in the
same run is undispatchable as a single workspace today — research-shaped
teams never enter `acceptEdits` mode. That is a separable finding, tracked
as a limitation, not addressed by this module.
