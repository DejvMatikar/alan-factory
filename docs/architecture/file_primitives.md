# File primitives

`alan/store.py` is a public leaf module holding two atomic tmp-then-replace text writers:
`write_text_atomic(path, text, *, mode=None)`, covering the 16 `Path.with_suffix`-shaped call
sites across 13 files, and `write_text_mkstemp(path, text, *, prefix, suffix=".tmp", mode=None)`,
covering the 11 `tempfile.mkstemp`-shaped call sites this second shape now confines to one file.
Together they replace the writer half of the family's duplication. The mtime-cache and JSONL-row
families are covered below, along with the byte-budget tail readers, the environment parsers, the
audit-directory wrappers, and the structural test that keeps all of them folded.

## The mkstemp shape

`write_text_mkstemp` creates its temp file with `tempfile.mkstemp(prefix=prefix, suffix=suffix,
dir=path.parent)` and `os.fchmod`s the raw descriptor before any bytes are written, unlike
`write_text_atomic`'s write-then-`chmod`. That ordering matters for `control_token_store.py`'s
`_write_value`/`_write_meta` (both `mode=_FILE_MODE` / `0o600` secrets): folding both creation
shapes into one writer would mean threading a chmod-timing flag through `write_text_atomic`,
changing its other 16 sites' window guarantee for no benefit. Both `control_token_store.py` sites
pass `suffix=""` (matching their pre-migration calls); the other nine use the default `".tmp"`.

## Layer slot

`store` sits in `pyproject.toml`'s `[tool.importlinter]` layers list immediately above `roots`
(and below `detached_mode`) — not at the bottom of the list. It is a near-foundational layer:
lower than almost every caller, but one level above `roots` because a later phase of this same
consolidation (the audit-directory family) needs `store` to be importable from code that already
depends on `roots.logs_root()`. Every current caller of either writer sits well above `store` in
the list, so each can import it without a layering violation. `store.py` itself imports nothing
from `alan` — it takes a `Path` and an already-serialized `text` string, with no ambient root
lookup. (`alan/teams/_pr_branch.py:53`'s `tempfile.mkstemp` is not a member of this family: it
writes a `gh` scratch payload unlinked in a `finally` block and never `os.replace`d anywhere, so a
bare grep overcounts the writer family by one.)

## The text-in contract

Both writers take already-serialized `text: str`, never an object to serialize. Callers keep their
own `json.dumps(...)`, `yaml.safe_dump(...)`, or `model_dump_json()` call and pass the resulting
string in. `alan/driver_loop_runs.py` and `alan/worker_records.py` changed their prior
`json.dump(data, fh)` stream write to an explicit `json.dumps(data)` passed as `text` — the two
emit byte-identical output, so this is not an observable change. Hoisting serialization out of the
tmp-file window means the only failures that can occur inside either writer are filesystem-shaped
(`OSError`, `UnicodeEncodeError`).

`write_text_mkstemp` extends the same failure-contract convergence: any failure before `os.replace`
succeeds unlinks the tmp file and re-raises. Two of its eleven sites
(`teams.dependency_sweep._save_dependency_sweep_status`, `teams._triage_sweep_status`) already did
this by hand; the other nine leaked a stray `.tmp` on failure and no longer do — a
strictly-improving side effect of confining the mechanism, not a requested behavior change. Every
log-and-swallow caller (those two, plus `engine_preflight._persist`, `repo_scaffold._persist_state`)
keeps its own `try/except OSError` around the call, as `gold_label._truncate_to_cap` does around
`write_text_atomic`.

## Mode is a caller-supplied parameter, not a policy

`mode=None` means no explicit `chmod`/`fchmod` call is issued by `store.py` — but the two writers
start from different baseline permissions, so "no explicit mode" is not the same guarantee for both.
`write_text_atomic` creates its tmp file via `Path.write_text`, so with `mode=None` the file keeps
the process umask default (typically `0o644`, group/other readable). `write_text_mkstemp` creates
its tmp file via `tempfile.mkstemp`, which the standard library always creates readable and writable
only by the creating user ID (`0o600`) regardless of `write_text_mkstemp`'s own `mode` argument — so
`mode=None` on `write_text_mkstemp` still yields a private, owner-only file, not an umask-readable
one. When `mode` is set, `write_text_atomic` chmods after writing and `write_text_mkstemp` fchmods
before writing (see "The mkstemp shape" above for why). `store.py` defines no mode constant of its
own; callers thread their own `_FILE_MODE` / `_RESULT_FILE_MODE` / `_OUTCOMES_FILE_MODE` constants
through, so the pre-existing `0o600`-vs-umask-default split stays observable per call site instead of
being centralized and silently unified.

## Tmp-naming shape deliberately left out

`write_text_atomic` covers the append `.tmp` shape; `write_text_mkstemp` covers the
`tempfile.mkstemp` shape. One call site keeps its own shape, deliberately out of this leaf, as the
item's single acknowledged exception:

- `alan/retention.py` compresses an archived worker-stream file (`*.stream.jsonl`, the largest files
  the system produces) to a `.gz` sibling via its own `_GZ_TMP_SUFFIX = ".gz.tmp"` archive-temp
  shape, streaming through `gzip.open`/`shutil.copyfileobj` rather than materializing the file in
  memory, then unlinks the original. It is not the same primitive as either writer here: it is
  compress-and-swap to a sibling path (`path.gz.tmp` → `path.gz`, then `path` unlinked), not an
  atomic overwrite of the same path — and it is the only binary write in `alan/`.

This is hosted as its own separately-named function rather than folded into `write_text_atomic` as
a behavioral parameter — a writer with tmp-naming, mode, and serialization-shape all as parameters
breaches the repository's CCN cap and defeats the preservation rule this consolidation exists to
enforce: one file, one mode, one failure contract per call site, exactly as before.

`alan/compression.py` and `alan/manager_summary.py`, previously listed here alongside
`alan/retention.py`, now call `write_text_atomic` directly — the former's tmp name changed from a
replaced suffix (`<name>.tmp`) to the appended shape (`<name>.txt.tmp`) as a result.

## The stat-signature cache shape

`stat_cache_hit(cache, key, st)` and `stat_cache_put(cache, key, st, value)` hold the invalidation
rule seven modules previously hand-copied: stat a file, compare a remembered
`(mtime_ns, size, value)` triple against the current `os.stat_result`, and either reuse the
remembered value or recompute and re-store it. `stat_cache_hit` returns `tuple[value] | None` — a
one-element box, never a bare `value | None`. `manager_summary._USAGE_CACHE` legitimately caches
`None` (a stream with no parseable usage events), so an unboxed return would read that cached
`None` as a miss and recompute forever on a hot read path; the box lets `hit is not None` (cache
present) stay distinguishable from `hit[0] is None` (cache present, value is `None`).

The caller owns the mapping: both functions take the cache as a parameter, and every module keeps
its own module-level `dict`. This is not just convention — seven tests isolate themselves by
rebinding that module-level dict wholesale (`monkeypatch.setattr(module, "_X_CACHE", {})`); if the
cache lived inside `store.py` instead, those rebinds would target an attribute nothing reads and
silently stop isolating anything.

Each site keeps its own stat-failure fallback and its own copy-on-hit choice — the shared rule is
only the guard and the store:

| module | function | fallback when the file can't be stat-ed | copy on hit |
|---|---|---|---|
| `handover.py` | `load_handover_cached` | loader (`load_handover(path)`) | `copy.deepcopy` |
| `manager_summary.py` | `read_manager_summary_usage` | fixed-empty (`None`) | `copy.deepcopy` |
| `workspace.py` | `_read_meta_cached` | loader (`_read_meta(ws_path)`) | `copy.deepcopy` |
| `workflow.py` | `load_workflow_cached` | loader (`load_workflow(path)`) | `copy.deepcopy` |
| `workflow.py` | `load_workflow_summary_cached` | derived (`_doc_row_summary(load_workflow(path))`) | raw |
| `teams/_base.py` | `_cached_role_turns` | fixed-empty (`{}`) | raw |
| `incident.py` | `_rows_from_file_cached` | fixed-empty (`[]`) | raw |

`incident.py` keys `_AUDIT_ROWS_CACHE` on `(path, incident_class)` rather than on `path` alone,
because its value — the parsed rows — depends on `incident_class` too, not just on what's on disk
at `path`. Keying on `path` alone is safe only because `_AUDIT_SOURCE_FILES` maps each
`incident_class` to a distinct filename (one-to-one); the tuple key keeps that dependency explicit
in the cache's own type instead of resting on an invariant that lives in a different dict.

Three other `st_mtime`-shaped references in these same files are not part of this family and are
untouched: `handover.py`'s `_CHAIN_INDEX_CACHE` (`:584`/`:605`) is a directory-keyed cache
comparing `mtime` only, with no `size` and a different value shape (a two-element tuple, not
three) — folding it in would mean adding a parameter to a function whose whole value is having
almost none. `workflow.py:353`'s `st_mtime` read feeds `prune_invocation_dirs`' sort key, not a
cache. `workspace_cost.py:118,123` and `workspace_index.py:167,172` are bare signature helpers
whose caching, where it exists, is done by the caller — a later leaf, not this one.

## The JSONL row shape

`parse_jsonl_row(line: str) -> dict[str, Any] | None` and
`parse_jsonl_rows(text: str) -> list[dict[str, Any]]` hold the tolerant-read rule twelve modules
previously hand-copied: strip a line, decode it as JSON, and return the record only if it decoded
to a mapping — a blank or whitespace-only line, malformed JSON, and a non-dict decode (a list, a
scalar, `null`) all collapse to `None`. Like the two writers above, both functions take `str` in and
do no I/O of their own; `store` keeps its existing slot in `pyproject.toml`'s `[tool.importlinter]`
layers list above `roots` — this family adds no new leaf and needs no new layer entry.

`parse_jsonl_row` returns the decoded record whole and unfiltered, never a subset of its keys.
Every migrated caller reads arbitrary keys off the row, and two of them
(`teams/_copilot_resolution_block.py`, `teams/_scenario_rebind_block.py`) recently grew new key
reads after their own hand-copied version of this helper was already written — a filtering or
`TypedDict`-narrowing return would silently break whichever caller reads a key the filter didn't
anticipate. This is also why the return type is a bare `dict[str, Any] | None` rather than a boxed
`tuple[dict[str, Any]] | None` like `stat_cache_hit`: `stat_cache_hit`'s box exists solely to keep a
legitimately cached `None` value distinguishable from a cache miss (see above). `parse_jsonl_row` has
no such ambiguity — `None` always and only means "no row" — so boxing it would add a needless unwrap
at every one of this family's call sites for no distinguishing power.

`parse_jsonl_rows` builds its list eagerly, in the function body, with no `yield`. This is not about
surfacing a decode failure early: `parse_jsonl_row` already catches `JSONDecodeError` and collapses
every blank, malformed, or non-dict line to `None` before it ever reaches `parse_jsonl_rows`, so no
decode failure can propagate out of this helper at all, eager or lazy. The actual constraint is the
concrete type each migrated call site already commits to: `finisher_rate._parse_jsonl` and
`run_summary._read_stream_events` both keep their own `-> list[dict[str, Any]]` signatures, and
`finisher_rate`'s three downstream builders (`_build_failure_index`, `_build_merge_set`,
`_build_spiral_index`) all take `rows: list[dict[str, Any]]`. A generator is duck-type compatible at
every one of today's single-pass call sites, but it is not assignable to a `list[...]`-typed return
or parameter under pyright's strict mode — returning one here would either fail typecheck at those
sites or force each to widen its own annotation to `Iterable`/`Iterator`, which is exactly the kind
of caller-side change this migration is scoped to avoid. Eager construction is the one return shape
that needs no caller edits anywhere.

Nine readers are deliberately excluded from this family and still hand-roll their own parse:

| module | function | why excluded |
|---|---|---|
| `alan/claude_cli.py` | `_parse_stream_line` | raises on malformed input; folding it into a tolerant parser is a semantic change to a reader |
| `alan/teams/_node_attach.py` | `handle` | raises on malformed input, same reason |
| `alan/routes/_deps.py` | `read_stream_events` | logs a warning on a JSON decode failure but stays silent on a non-dict line; a single `dict \| None` result collapses those two cases, which would add or drop a log line depending on direction |
| `alan/teams/_base.py` | `_parse_turns_row` | parses then extracts a `tuple[str, int] \| None`, not the raw record |
| `alan/repo_status.py` | `_parse_relevant_audit_line` | parses then validates/extracts, not the raw record |
| `alan/teams/triage_pregate.py` | `_parse_tally_row` | parses then extracts a `tuple[str, datetime] \| None`, not the raw record |
| `alan/health_repo_metrics.py` | `_bc_row_from_line` | parses then extracts a `BlockCorrectionRow`, not the raw record |
| `alan/teams/triage.py` | `_tail_jsonl_lines` | a row-budgeted backward scan, not a line-parsing concern — deferred with the rest of the tail family |
| `alan/incident.py` | `_tail_text` | byte-budgeted tail read, part of the same deferred tail family |

`alan/teams/_pr_json.py`'s `safe_json` is not a member of this family either, for a different
reason than the table above: it decodes one whole JSON blob (`json.loads` over the entire input),
not a JSONL stream of one-record-per-line, so it has no line-splitting or per-line tolerance
behavior to share with `parse_jsonl_row`.

The row-budgeted backward scan in `alan/teams/triage.py` (`_tail_jsonl_lines`) is never migrated:
it cannot be expressed as a byte-bounded read once the rows sought exceed the budget, since it
walks backward in fixed-size chunks counting newlines rather than seeking to a size-derived offset.

## The environment-parser shape

`store.positive_int_env(raw, default)` and `store.positive_float_env(raw, default)` hold the
algorithm three call sites hand-copied twice for integers and once for floats: missing or blank
input yields the default, unparsable input yields the default, and non-positive input yields the
default. Both take the already-read value (`raw: str | None`), never an environment variable name —
`store.py`'s contract is no ambient state lookup, so the first environment read stays at each call
site. `alan/claude_cli.py`'s `_timeout_from_env` and `_ftimeout_from_env`, and
`alan/_dispatcher_usage_limit.py`'s `_positive_float_env`, survive as one-line delegating shims
(`return store.positive_float_env(os.environ.get(env), default)`) rather than being deleted outright,
because `tests/test_claude_cli.py` (pinned, never touched) imports `_ftimeout_from_env` by name, and
`alan/dispatcher.py` calls the usage-limit parser across a module boundary by its existing name.
`alan/dispatcher.py:91,914` stays pointed at the `_dispatcher_usage_limit` shim rather than
`store.positive_float_env` directly — retargeting it touches a file outside this fold for no net
line saving. `_TRUTHY_ENV_VALUES` (`alan/auto_continue.py`, `alan/teams/_base.py`) is a two-line
truthy-value constant, not a parser, and stays duplicated: it is outside this family's shape.
There is no detector for this family — "read a string, parse it, fall back to a default" has no
distinguishable code shape the way a temp-then-replace write or a size-derived seek does, so the
fold here is enforced by convention and code review, not by `tests/test_file_primitive_families.py`.

## The tail-reader shape

`store.read_tail_text(path, max_bytes)` holds the byte-budget tail read two call sites duplicated:
seek to `max(0, size - max_bytes)` from the end of the file and decode the remainder with
`errors="replace"`, **propagating any `OSError` to the caller unchanged** — neither the seek nor the
read is guarded. `alan/incident.py`'s `_tail_text` and `alan/teams/triage_pregate.py`'s `_tail_bytes`
delegate their callers directly to `store.read_tail_text` and are deleted outright; both callers
already caught `OSError` around the old call, so the propagation change is invisible to them.

Two readers are deliberately left on their own shape:

- `alan/health_repo_metrics.py`'s `read_audit_tail` reads the **whole** file with `path.read_text()`
  when it is under the byte budget, and only seeks-and-replaces when it is over budget. Delegating
  would replace that strict small-file read with `read_tail_text`'s unconditional
  `errors="replace"` decode, changing observable behaviour on a small file that happens to contain
  invalid UTF-8 — from raising to silently substituting the replacement character.
- `alan/teams/triage.py`'s `_tail_jsonl_lines` is a row-budgeted backward scan (see above), a
  different primitive entirely from a byte-budgeted seek.

## The stat-signature shape

`store.stat_signature(path)` holds the `(st_mtime_ns, st_size)` 2-tuple two call sites hand-copied:
stat the path, and return the tuple, or `None` on `OSError`. `alan/workspace_cost.py`'s `_stat_sig`
and `alan/workspace_index.py`'s `_stat_sig` delegate their callers to `store.stat_signature` and are
deleted. This is a different family from the stat-*cache* shape documented above
(`stat_cache_hit`/`stat_cache_put`): a bare signature function returns the tuple directly, with no
cache dict and no cached value to box. `store.stat_signature` **swallows** `OSError` into `None`,
the opposite asymmetry from `read_tail_text`, which propagates — both source pairs behaved that way
before the fold, so the fold preserves each behaviour rather than unifying it.

`workspace_index.py`'s `_dir_mtime` sibling stays in place: it returns a bare `st_mtime_ns` integer,
not a `(mtime, size)` tuple, so it is not a member of this family and needs neither a fold nor a
documented exception. Its one remaining caller feeds `WorkspaceRow.handovers_mtime`; it gates nothing.
`workspace_cost.py`'s copy is deleted along with the directory-timestamp guards it served.

Directory-change detection has its own primitive in the same module, because a directory timestamp
cannot carry it (a coarse-tick filesystem leaves it unchanged for an entry added in the same tick):
`store.list_dir` (a sorted, fully materialised listing, `[]` on `OSError`), `store.listing_entry`
(`(name, st_ino, st_size)` for one path, `None` when it no longer stats or, with `dirs_only`, is not
a directory) and `store.listing_fingerprint` (the tuple of those entries). No timestamp enters the
fingerprint. `workspace_index.py` (handovers) and `workspace_cost.py` (chat, preparation-chat and
cleared-record listings) both call it, and `workflow.list_invocation_ids` lists through
`store.list_dir`; see `workspace_index.md`'s "Change detection without directory timestamps".

## The audit-directory-wrapper shape

Fourteen private, identically-bodied functions across the backend each did nothing but
`return logs_root()`. All fourteen are deleted, and their roughly thirty-nine call sites now call
`logs_root()` (from `alan.roots`) directly — a name every one of those modules already imported for
other reasons. `alan/gold_label.py`'s wrapper additionally called `.resolve()`; both of its call
sites now read `logs_root().resolve()` inline rather than losing that behaviour.

Two callers are kept as deliberate monkeypatch seams, untouched: `alan/teams/_base.py`'s
`node_turns_audit_dir()` and `alan/teams/_pr_handover.py`'s `_node_turns_audit_dir` alias — several
test files patch by those exact names, and they are a distinct audit tree (`node_turns_audit.jsonl`
and friends) from the `logs_root()`-rooted files this fold touches.

## The structural test

`tests/file_primitive_shapes.py` holds four AST-based shape detectors, one per family above (the
environment-parser family excepted, per its section above): a temp-path variable followed by a
`.replace()` call on that same variable for writers; a 2-tuple of `st_mtime_ns`/`st_size` attributes
built from a `.stat()` result for signatures; a single-`str`-parameter function that decodes its
argument with `json.loads` inside a `try`/`except` that neither raises nor extracts a narrower value
for row parsers; and a `.seek()` call whose offset traces back to a `.stat().st_size` read for tail
readers. Detection is by code shape, not by name, so a delegating shim (like the three env-parser
survivors) is invisible to every detector — none of them inspect call expressions, only the shape of
the function that would otherwise duplicate the leaf.

Each detector deliberately does not flag one representative non-member, exercised as a synthetic
in-memory source string in `tests/test_file_primitive_families.py`: `alan/forge_gh.py`'s
`run_gh_api_json_input` (moved there from `alan/teams/_pr_branch.py` in X2.7 PR15) for the writer detector (creates a `tempfile.mkstemp` scratch file but only
ever `.unlink()`s it, never `.replace()`s it); `alan/handover.py`'s `_build_chain_index` and
`workspace_index.py`'s `_dir_mtime` for the signature detector (both read
`.stat()` but return a bare value or a `dict`, never the 2-tuple); `alan/claude_cli_journal.py`'s
`read_err_file` for the tail detector (seeks to a caller-supplied `offset` parameter, never one
derived from a `.stat()` call in the same function); and `alan/teams/_pr_json.py`'s `safe_json` for
the row-parser detector (decodes one whole JSON blob with no per-line splitting or tolerance shape).

At the finished tree, the measured hit set is exactly two functions outside the ten-entry exception
list's members that the detectors themselves can see: `alan/retention.py::_compress_one` (writer
shape: its `.gz.tmp` scratch file is `.replace()`d onto the compressed sibling) and
`alan/health_repo_metrics.py::read_audit_tail` (tail shape: its large-file path seeks from a
`.stat().st_size` read). Both are named, agreed exceptions — see `EXCEPTION_ENTRIES` in
`tests/test_file_primitive_families.py`. The signature and row-parser detectors currently hit
nothing outside `alan/store.py` at all: every prior copy of either shape was already folded by an
earlier change in this programme, and this PR's own exceptions for those two families
(`_parse_stream_line`, `handle`, `_parse_turns_row`, `_parse_relevant_audit_line`, `_parse_tally_row`,
`_bc_row_from_line`, `read_stream_events`) fail the detector's own passthrough/no-raise checks by
construction, so they are listed for documentation and future-proofing rather than because the
detector currently flags them — the assertion in each test is that hits are a *subset* of the
exception list, not that every exception entry is itself a hit.

`EXCEPTION_ENTRIES` (`tests/test_file_primitive_families.py`) is a tightening-only list **by
convention only** — it is not yet registered with the ratchet-direction detector
(`alan/teams/_ratchet_direction.py`) or `MEASURED_BASELINE_PATHS`
(`alan/teams/_pr_freshness.py`). Registering it is a known follow-up: it requires a paired
per-path direction rule in `_ratchet_direction.py`, a rule-table entry `tests/test_ratchet_direction.py`
asserts is in exact-set sync with the registry, and a row in `docs/architecture/ratchet_direction.md`
— out of this PR's scope. `EXCEPTION_ENTRIES` is already shaped for that follow-up: one module-level
dict of `"path::symbol"` string keys to reason strings, the same shape `tests/gh_read_baseline.py`
uses, so a later membership rule can diff BASE against HEAD without a new parser.
