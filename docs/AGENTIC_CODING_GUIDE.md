# The Agentic Coding Codebase — MUST-HAVE Rules & Best-Practices

> A consolidated checklist for building and maintaining a Python + TypeScript
> codebase that is editable **primarily by AI agents** for the long term.
> Synthesized from ten internal research, brainstorming and implementation
> threads (LLM performance, quality guardrails, codebase improvement,
> autonomous-factory architecture, memory drift, an agent performance retro,
> token saving, repository structure, Claude-native conventions and static
> linters). Every claim is tagged: **[verified]** = multiple
> independent sources / controlled studies; **[likely]** = single reputable
> source, sound methodology; **[uncertain]** = single source / extrapolation.

---

## How to Use This Document

Treat each `[ ]` item as a **hard gate**: if it is not yet true of your repo,
either ship the change or write a one-line waiver in your PR description.
Items prefixed **P0** are must-have; **P1** are high-leverage adds; **P2** are
nice-to-have once P0/P1 are in place.

The guide is organized as a pipeline you can walk top-to-bottom. Each section
ends with a short checklist.

- [1. Language & Stack Choice](#1-language--stack-choice)
- [2. Repo Layout & Module Shape](#2-repo-layout--module-shape)
- [3. Naming, Types, Docs, Comments](#3-naming-types-docs-comments)
- [4. Context Files (CLAUDE.md / AGENTS.md)](#4-context-files-claudemd--agentsmd)
- [5. Static Analysis & Linters](#5-static-analysis--linters)
- [6. Tests and Coverage](#6-tests-and-coverage)
- [7. Pre-commit & Local Hooks](#7-pre-commit--local-hooks)
- [8. CI Pipeline (Dark-Factory-Ready)](#8-ci-pipeline-dark-factory-ready)
- [9. Agent Workflow Patterns](#9-agent-workflow-patterns)
- [10. Context Engineering & Token Budgets](#10-context-engineering--token-budgets)
- [11. Memory & Cross-Session State](#11-memory--cross-session-state)
- [12. Claude-Native Conventions](#12-claude-native-conventions)
- [13. Worktree & Concurrency Hygiene](#13-worktree--concurrency-hygiene)
- [14. Dark Factory: GitHub-Native Loop](#14-dark-factory-github-native-loop)
- [15. Quality Gates That Catch LLM Failure Modes](#15-quality-gates-that-catch-llm-failure-modes)
- [16. Anti-Patterns To Eliminate](#16-anti-patterns-to-eliminate)
- [17. Production-Tested Patterns](#17-production-tested-patterns)
- [18. Distilled Research Findings](#18-distilled-research-findings)
- [19. Reference Bibliography](#19-reference-bibliography)
- [20. Quick-Adoption Order](#20-quick-adoption-order)

---

## 1. Language & Stack Choice

**Empirical pass-rates (Multi-SWE-bench 2025, agentic issue resolution)
[verified]:**

| Language | Best Resolution | Notes |
|---|---|---|
| Python | **48.2%** | 2×–10× any non-Python |
| Java | 23.4% | Strong but verbose |
| Rust | 15.9% | Volatile across benchmarks |
| C++ | 14.7% | |
| TypeScript | **11.6%** | Best of the JS family |
| C | 8.6% | |
| Go | 7.5% | |
| JavaScript | **5.1%** | Worst of mainstream languages |

**[verified]** Python dominates because of training-data volume, syntax
regularity, and ecosystem (pytest/ruff produce clean parseable output).
**[verified]** TypeScript ≫ JavaScript for agentic tasks: typed code lets
the compiler give the agent localized, actionable feedback loops.
**[likely]** Rust's compiler messages are agent-navigable (RustAssistant,
ICSE 2025: ~74% repair success), but training-data scarcity caps its score.

### Rules

- [ ] **P0** Pick **Python** for backend, orchestration, scripts, agents
      themselves. (Multi-SWE-bench 48% vs 23% Java vs 11% TS.) [verified]
- [ ] **P0** Pick **TypeScript** (not JavaScript) for any frontend or Node
      tooling. `strict: true` from day one. [verified]
- [ ] **P0** Avoid pure JavaScript in agent-generated code. JS scored 5.1%
      vs TS 11.6% on Multi-SWE-bench 2025. [verified]
- [ ] **P1** Isolate any other-language modules (Go, Rust, C#) behind an
      HTTP/gRPC boundary so agents work in Python/TS day-to-day. [likely]
- [ ] **P2** Greenfield rule: if a feature can be built in Python, build it
      in Python. LLMs default to Python and contradict their own
      "use Go for concurrency" advice 83% of the time. [verified]

---

## 2. Repo Layout & Module Shape

Quantitative thresholds — **the values agents drop quality past:**

| Property | Threshold | Source / Confidence |
|---|---|---|
| Function CCN (hot paths) | ≤ **7** | lizard gate — Alan default `--CCN 7`, stricter than ULT's CCN ≤ 10 [verified direction] |
| Function CCN (support) | rank A (avg ≤ 5) | xenon `--max-modules A --max-average A` [verified direction] |
| Function length | ≤ **60 lines** | lizard `--length 60`; literature 50–100 [likely] |
| File length (hot paths) | ≤ **400 lines** | inferred from context-window economics [likely] |
| File length (utility) | flag > **800 lines** | server.py / workspace.py risk threshold [uncertain] |
| Lines changed per agent task | ≤ **200 across ≤ 5 files** | SWE-bench Pro median 107 LoC across 4.1 files [verified] |
| Test:code ratio | ≥ **1.5 : 1** | Alan runs `fail_under = 100` on branch coverage to prevent weak-test false-positives [verified direction] |
| CLAUDE.md length | **2–6 KB / 60–150 lines** | +20 % gain plateaus, +20 % cost overhead [verified] |
| Charter / skill files | 4–10 KB each | one is read at a time, so size is fine [verified] |

**Layout rules [verified by Aider repo-map / RepoBench]:**

- Flat directory hierarchy beats deep nesting. Agents retrieve by **symbol
  name**, not directory path; deep paths consume tokens for nothing.
- **Symbol-prefix convention** (`_orch_*.py`, `_dispatcher_*.py`) gives you
  namespacing without hierarchy depth.
- Monorepo with strong module boundaries beats both pure mono and pure poly
  for agent use [likely].
- Tests should mirror source paths (`tests/test_<module>.py` next to
  `src/<module>.py`) — RepoBench shows agents find symbols best when
  test-↔-impl name links are obvious [likely].

### Rules

- [ ] **P0** Lizard gate `--CCN 7 --length 60` on agent-edited core files
      (orchestrator, dispatcher, gateway, etc.). Alan default.
- [ ] **P0** Xenon `--max-modules A --max-average A` (avg CCN ≤ 5) on the
      rest of the package. Stricter than the original `--max-absolute B`.
- [ ] **P0** Add an **import-linter** layered contract; gate in CI
      (`uv run lint-imports`). Without this, refactors silently break the
      stratification.
- [ ] **P1** Xenon `--max-modules A` (avg-CCN ≤ 5) on hottest modules.
- [ ] **P1** No file > 400 lines on hot paths; > 800 lines anywhere is a
      flag — split via mixin or extraction.
- [ ] **P0** **Size ratchets over both stacks' ungated zones** (H4.1). A
      test file over 1000 non-blank lines, or an `alan/` source module
      over 800 non-blank lines (the same "> 800 lines anywhere is a flag"
      threshold above, now enforced), needs a legal remedy before it can
      grow further. In Alan this is implemented as
      `tests/test_size_ratchets.py` + `tests/size_baseline.py`: a file
      already over its cap at the H4.1 branch point is **pinned** at its
      measured size in `PINNED_TEST_FILES`/`PINNED_SOURCE_MODULES` —
      shrink-or-delete only, it may never grow again; a file **under** its
      cap at that point is governed by the cap alone and is **never**
      pinned, so it grows freely up to the cap. When a pinned file, or a
      file that has just hit its cap, needs more content, the only two
      legal moves are (1) shrink the file, or (2) put the new content in a
      **new sibling file/module under the cap** — never add the file to
      the pin dict and never raise the cap. Headroom shrinks over time as
      unpinned files grow toward their cap; a snapshot list of "near-cap"
      files here would drift out of date immediately, so check current
      standing directly — run `tests/test_size_ratchets.py`, or read the
      caps (`TEST_FILE_CAP`/`SOURCE_MODULE_CAP`) and pins in
      `tests/size_baseline.py` — rather than trusting stale numbers in this
      guide. Raising a pin, or either
      `PINNED_*_TOTAL`/`PINNED_*_COUNT` aggregate constant, is a
      **NEEDS-OWNER** human decision made by hand-editing
      `tests/size_baseline.py` — no script, flag, or env var anywhere in
      the gate regenerates the baseline from current state, by design. A
      PR that shrinks or splits a pinned file **must** lower that file's
      dict entry, the matching `PINNED_*_TOTAL`/`PINNED_*_COUNT`
      constants, and the hardcoded aggregate literals in
      `tests/test_size_ratchets.py`, all in the same PR — leaving the
      aggregate untouched turns the redundant check into permanent dead
      slack that never fires again. The gate is **loud, not impossible**:
      the backend CI job checks out at depth 1 (no merge-base to diff a
      baseline against) and `CODEOWNERS` already grants global self-merge,
      so nothing stops a determined edit — the failure message and the
      aggregate bound are the only defenses, not a structural wall.
      `scripts/*.py` is deliberately unmeasured (out of scope for H4.1).
- [ ] **P0** **Duplication ratchet over `alan/` Python source** (H8.5). Size
      (raw line count, above) and duplication (content identity, here) are
      two independently-pinned mechanisms — this is a separate gate, not an
      extension of the size ratchet. `tests/test_duplication_ratchet.py`
      scans every `*.py` file under `alan/`, excluding `alan/web/`, with a
      stdlib-`tokenize` clone scan: a duplicate block must be at least
      `BLOCK_MIN_TOKENS` (70) significant tokens (no identifier
      normalization — exact token text) **and** span at least
      `BLOCK_MIN_LINES` (10) source lines, both floors binding
      independently. The pinned unit in `tests/duplication_baseline.py` is
      the **absolute duplicated-line count** (`DUPLICATED_LINE_BUDGET`),
      **never a percentage** — a percentage's denominator moves whenever
      non-duplicated code is deleted, which this repo does routinely, and
      would fail an honest deletion PR. When the gate fires, the only legal
      remedy is **extracting the duplicated block into a shared sibling
      helper module** — never raising `DUPLICATED_LINE_BUDGET`. Raising it
      is a **NEEDS-OWNER** human decision made by hand-editing both
      `tests/duplication_baseline.py` and the redundant literal pin in
      `tests/test_duplication_ratchet.py` — no script, flag, or env var
      anywhere regenerates it, by design. Check current standing by running
      the gate, not by trusting the number in this guide.
- [ ] **P1** **Mixin "method-bag" decomposition** for files that exceed
      threshold but cannot be cleanly split (no `__init__`, mixins are
      pure method containers).
- [ ] **P0** **The extracting PR deletes the facade, in the same PR.** When a
      size-ratchet split moves a symbol cluster out of an over-cap module
      into a new sibling, the old module is left with a set of
      `from new_module import X as X` identity re-exports so existing
      consumers keep working across the split commit. That facade is
      **temporary scaffolding, not the destination** — the same PR (or an
      immediate follow-up) must repoint every consumer to import from the
      new module directly and delete the re-export block from the old one.
      `__all__` is how a module declares a *deliberate*, permanent public
      facade (a name legitimately re-exported through it, e.g.
      `alan/teams/_base.py`'s `UNTRUSTED_CONTENT_NOTICE`); an identity
      re-export that is absent from `__all__` and never used locally is
      dead weight, and `tests/test_reexport_facade.py` (an AST gate,
      allowlist `tests/reexport_facade.py`, ships empty, tightening-only)
      now fails the build on a new one rather than letting it accumulate
      silently. Worked example: `alan/teams/_pr_state_paths.py` extracted
      19 names out of `alan/teams/_pr_branch.py`'s state cluster; the
      follow-up PR repointed every consumer, deleted the 16 unused
      re-export blocks, and collapsed the 3 still-internally-used names
      (`_engine_state_dir`, `_read_json_with_legacy_fallback`, `state_dir`)
      into one plain (non-reexporting) import.
- [ ] **P2** Tests sit next to or mirror source layout 1:1 — `tests/test_X.py`
      imports `<pkg>/X.py`. [likely]

---

## 3. Naming, Types, Docs, Comments

**The strongest single structural finding in 2025 research [verified —
arXiv 2510.03178]:** when names are obfuscated, LLMs collapse from
intent-level reasoning to mechanical line-by-line narration. Names are
load-bearing cognitive scaffolding, not style preference.

### Rules

- [ ] **P0** **Name for intent at every level** (module, class, function,
      variable). Long-but-meaningful > short-and-opaque. Cost of a long
      name is zero; cost of an opaque name is measurable agent confusion.
- [ ] **P0** **Type-annotate every public function signature** in
      agent-edited modules. Internal vars: annotate when type is
      non-obvious from the RHS.
- [ ] **P0** Use a **dependency-injection / providers pattern** for I/O,
      config, clocks, HTTP, API tokens. Enables agents to write tests
      against fakes without touching production wiring. [verified]
- [ ] **P0** **Zero comments / zero docstrings** policy in new code:
      - Names carry the meaning.
      - "Why" lives in the PR description.
      - Allowed exceptions: tooling directives only (`# type: ignore`,
        `# noqa`, `# pragma: no cover`, `// @ts-ignore`, `// eslint-disable`).
      Rationale: comments rot, become misleading, and inflate context cost.
      [likely — backed by naming-substitution research]
- [ ] **P0** **Test code carries the same zero-comments rule, with no exception for
      explaining a scenario.** A test's *name* is its documentation: rename
      `# a cleared brief disables Re-propose` above a test into
      `test_stale_repropose_is_disabled_once_the_brief_is_cleared` and delete the
      comment, rather than keep both. A file that seems to need a module docstring
      to say what it covers needs a better filename instead — the filename carries
      that job. Fixture and scenario tables (parametrize ids, table-driven cases)
      carry their meaning in the case name or id, never in a trailing comment on
      the row. In frontend test code specifically: an empty `catch {}` needs no
      `// ignore` filler — ESLint's `no-empty` runs with `allowEmptyCatch: true`
      there — and an empty callback is written `() => undefined`, never `() => {}`
      with an explanatory comment inside. Alan enforces this structurally
      (`tests/test_no_comments_in_test_code.py`, the `test-comments/no-prose-comments`
      ESLint rule) rather than trusting the convention — see CLAUDE.md's `Don'ts`
      section for the binding rule, the exact scoped paths, and the gate pointer.
      [likely]
- [ ] **P1** Ban `any` in TypeScript (`@typescript-eslint/no-explicit-any:
      "error"`). It is the LLM's primary hallucination escape hatch.
- [ ] **P1** Use TypedDict / Pydantic / `Protocol` (Python) and discriminated
      unions / generics (TS) over freeform dicts. Type signatures double as
      machine-readable docs — agents need fewer round-trips to infer shape.
- [ ] **P2** Ban inline JSON shape comments — express the shape as a type.

---

## 4. Context Files (CLAUDE.md / AGENTS.md)

**The single highest-leverage configuration file for an agentic repo
[verified — arXiv 2602.11988, AGENTS.md eval paper, Feb 2026]:**

| Context-file quality | Effect on issue-resolution rate |
|---|---|
| High-quality, human-written | **+20 %** |
| Developer-provided (average) | +4 % |
| **LLM-generated** | **−3 %** (worse than no file at all) |
| Inference-cost overhead, any file | +20 % |

*Source: AGENTS.md evaluation paper, arXiv 2602.11988, Feb 2026.*

**A bad CLAUDE.md hurts more than no CLAUDE.md.** Write it yourself.

### What "high quality" means [triangulated — Anthropic docs, OpenAI AGENTS.md spec, GitHub blog 2,500-repo analysis, HumanLayer]

1. **Exact runnable commands** (`pytest tests/ -q`, not "run the tests").
2. **Architecture decision rationale**, not what the code does (agents read
   code). Include things agents cannot infer: "code-changing agents use
   git worktrees for isolation".
3. **Anti-patterns / footguns**: "don't do X" beats "do Y" because agents
   have strong priors toward common patterns that may be wrong here.
4. **Concise**: target 60–150 lines, hard cap ~6 KB. Past ~150 lines LLMs
   start ignoring instructions ("Lost-in-the-Middle" effect — Chroma
   "Context Rot" 2025: ~40 % degradation when window fills). [verified]
5. **No duplication of READMEs** — link, don't paste.
6. **No code-style rules** — those live in the linter. Anthropic docs are
   explicit: "Never send an LLM to do a linter's job."
7. **Mention the gates** (CCN limits, coverage floor) — agents should know
   them before writing, not discover them after CI fails.

### Rules

- [ ] **P0** A `CLAUDE.md` (or `AGENTS.md`) at repo root, hand-written,
      60–150 lines, **2–6 KB max**.
- [ ] **P0** Include the *exact* install / test / lint / build / serve
      commands (single highest-value content).
- [ ] **P0** Include an **anti-pattern / "don't"** section.
- [ ] **P0** Include the active **CCN / length / coverage gates**.
- [ ] **P0** **Never autogenerate it** with an LLM. (−3 % impact). [verified]
- [ ] **P1** Adopt **AGENTS.md** as the universal base, mirror or
      `@import` from CLAUDE.md / `.cursor/rules/*.mdc` if you use multiple
      tools. AGENTS.md is now backed by Linux Foundation / AAIF and
      supported by Claude Code, Cursor, Copilot, Gemini CLI, Aider, etc.
- [ ] **P1** **Sub-directory CLAUDE.md** files for big subsystems
      (the nearest-ancestor wins). Composable beats monolithic — Cursor
      explicitly deprecated single-file `.cursorrules` in favor of
      `.cursor/rules/*.mdc` for this reason.
- [ ] **P0** A **`CLAUDE.md` drift gate** on PRs that touch architecturally
      load-bearing modules (orchestrator/dispatcher/workspace). If the
      module gets refactored but CLAUDE.md still describes the old shape,
      block the PR. (Top finding of a memory-drift retrospective.) In Alan
      this is implemented as a `drift-gate` job in
      `.github/workflows/tests.yml` using `dorny/paths-filter` over a
      curated load-bearing path list, with a `drift-ok` PR label as the
      escape hatch.
- [ ] **P2** Charter / SKILL.md per workflow (TDD, code-review, security
      audit) — **invoked on demand**, not loaded every session. Pattern
      from `addyosmani/agent-skills` and Claude Code skills system.

---

## 5. Static Analysis & Linters

The reference stack for Python + TypeScript, layered fast → deep → audit.

```
ruff (pre-commit/CI, ~0.5 s)
  → mypy (CI, ~5 s)
    → bandit (CI, ~3 s)
      → lizard / xenon (CI, ~2 s)
        → vulture (CI, ~3 s)
```

### Python — required rule additions

- [ ] **P0** `ruff select = ["E","F","I","S","B","BLE","RET","PT","ANN"]`.
      Alan ships with `ANN` enabled and per-file-ignores for tests + the
      `__main__` shim in `pyproject.toml`.
      - `BLE001` catches **blind-except** — the documented #1 LLM agent
        problem (`except Exception:` swallowing real errors). [verified]
      - `RET504/505` kills dead else-branches and unused intermediate vars
        that LLMs leave behind. [verified]
      - `PT009/017/018` catches fake test assertions LLMs emit
        (`assert True`, `assert x is not None` when x is always not-None).
      - `ANN` catches missing annotations on agent-touched code (formerly
        listed as P1).
- [ ] **P0** **mypy** with `strict = true` (Alan default — stricter than
      the original "bug-catching subset" recommendation).
- [ ] **P0** `bandit[toml]` standalone, in addition to ruff `S` rules.
      Skip `B603/B607` for known-safe `subprocess.run` sites with
      annotated `# nosec`.
- [ ] **P0** **vulture** in CI (`--min-confidence 80`) — primary detector
      for **LLM refactor residue** (functions an agent "deleted" but left
      callable; classes whose last user disappeared).
- [ ] **P1** Per-module mypy override to enable `disallow_untyped_defs =
      true` everywhere except the FastAPI `server.py` (≈200 handlers).
- [ ] **P0** **`pyright`** with `typeCheckingMode = "strict"` for
      hallucinated-API detection on untyped 3rd-party libs (mypy
      `ignore_missing_imports = true` masks these). Promoted from P2 —
      Alan runs it in CI as `lint-python-pyright`, scoped to `alan/` with
      `reportMissingTypeStubs`, `reportUnknownMemberType`,
      `reportUnknownArgumentType`, and `reportUnknownVariableType` all at
      `"error"`.
- [ ] **Skip:** pylint (ruff covers ~80 % at 10–100× speed), Coverity
      (irrelevant for GC'd languages), CodeQL (overkill unless you
      already pay for GitHub Advanced Security), Biome migration
      (lacks type-aware rules; not worth the churn).

### TypeScript — required rule additions

See §3's test-code comment corollary (the `no-empty`/`allowEmptyCatch` and
`() => undefined` conventions) for the comment-policy rules that apply to
`*.test.ts`/`*.test.tsx` specifically.

- [ ] **P0** `tsconfig`: `"strict": true`, **`"noUnusedLocals": true`**,
      **`"noUnusedParameters": true`**.
- [ ] **P0** ESLint rules at **`"error"`** (not warn):
      - `@typescript-eslint/no-floating-promises` — LLMs routinely drop
        `await`. **The single highest-signal missing rule for async agent
        code.** Requires type-checked ESLint config (`parserOptions`+`project`).
      - `@typescript-eslint/no-unused-vars`
      - `@typescript-eslint/no-explicit-any`
- [ ] **P0** **knip** v6+ in CI for cross-file dead exports / unused deps
      / unused config entries. (Supersedes ts-prune, which is unmaintained.)
- [ ] **P1** ESLint `complexity: ["error", 8]` on the frontend (Alan
      default — stricter than the original `["warn", 15]`).
- [ ] **P1** `dependency-cruiser` blocking (no-circular, not-to-test);
      `jscpd` blocking with threshold `1` (Alan defaults — stricter than
      the original "informational" recommendation).
- [ ] **P2** Biome **format-only** (10–20× faster than Prettier) — keep
      ESLint for type-aware rules.

### Multi-language (audit layer)

- [ ] **P2** **SonarCloud**, optional and **advisory**: never a required or
      authoritative check, and never `qualitygate.wait=true`. Auto-Analysis
      **disabled** so CI uploads coverage. [Sonar 2025: >90 % of
      LLM-generated issues are code smells; severe bugs (resource leaks,
      contract violations) consistent across all models.] alan's own
      two-month audit (2026-09-30) found that findings reached 15% of fix
      runs and 95% were cleared, but they were mostly maintainability rules
      that lizard, xenon and ESLint already bound, plus type false
      positives. While Sonar blocked, it cost 25 extra fix cycles. Decision:
      keep Sonar advisory.
- [ ] **P1** **`gitleaks`** pre-commit + CI (no secrets in diffs).
- [ ] **P1** **`pip-audit`** in CI (`uv run pip-audit`).
- [ ] **P1** GitHub Action **SHA pins** (40-char commit SHA, never
      floating tags) for every workflow `uses:`.
- [ ] **P1** **CODEOWNERS** for `.github/workflows/`, `deploy/`,
      `pyproject.toml`, `vision.yaml`, anything affecting agents'
      autonomy. Highest-blast-radius surface for silent agent edits.

---

## 6. Tests and Coverage

**Test quality is the agent's primary feedback signal — a weak test suite
is a broken compass.** [verified — UTBoost / Rigorous Evaluation, ACL 2025:
**47.93 %** of "resolved" SWE-bench issues were false-positive due to
weak tests.]

### Rules

- [ ] **P0** **pytest** with `pytest-xdist` (`-n 4 --dist=worksteal`,
      CI included since the 2026-08-09 H8 contention retune — the CI-only
      raise was retired). Started on `loadfile`; switched 2026-07-04 after a
      strict `--dist=load` scatter proved the suite order-independent —
      `loadfile` let the slowest file pin one worker (~20 s straggler tail).
- [ ] **P0** Every test runs against an isolated app home directory
      (`monkeypatch.setenv` + `tmp_path`). Providers reset between tests.
- [ ] **P0** **`pytest --cov`**, **branch-coverage**, **`fail_under = 100`**
      for backend (Alan default; original guidance was 90 %). Below 100 %
      branches on a small codebase, agents silently break edge cases.
- [ ] **P0** **`--junit-xml=test-results.xml`** + structured CI artifact
      upload. Agents parse JUnit XML, not stdout scrapes. (Dropped from
      addopts 2026-07-04: nothing ever consumed it — Sonar reads only
      `coverage.xml`. Re-add alongside the `ci-results.json` artifact below
      if that gets built.)
- [ ] **P0** Mock LLM pattern (a `scripted_llm.py` fixture) — implementation
      agents can run full pipeline tests deterministically without live
      API calls.
- [ ] **P0** A **`slow` marker** for long pipeline / e2e tests; `paths-filter`
      splits fast vs full suite per PR.
- [ ] **P1** **Frontend tests:** Vitest + Testing Library + MSW for fetch
      mocking. Smoke-coverage on `api.ts` + every page.
      Playwright headless for one e2e smoke per release.
- [ ] **P1** Frontend **coverage floor**: Alan ships **100 / 95 / 95 / 100**
      (statements / branches / functions / lines) in `vitest.config.ts`.
      For greenfield projects, 30 % line as a starting ratchet, raised
      quarterly, is the original recommendation.
- [ ] **P2** **TDD with dependency-graph context** (TDAD, arXiv 2603.17973,
      2026): +8 pp resolution rate, **−70 %** test regression — but only
      when the agent gets *which* tests are affected. **Naive "tests-first"
      prompting alone *increases* regressions to 9.94 %.** Don't ship plain
      TDD prompts. [verified]
- [ ] **P2** Best-of-N patch sampling on the implementation step: agent
      generates 2–3 diffs, tester runs each, selects the passing one.
      +6–12 pp on SWE-bench (Agentless oracle gap, RTV/PDR 2026). [verified]

---

## 7. Pre-commit & Local Hooks

**Critical interaction with worktree-based agent execution
[blocker]:** pre-commit hooks fire on agent commits
inside `.worktrees/` because `.git/hooks/` is shared. If `ruff --fix`
mutates files and aborts the commit, the agent gets non-zero exit and
**silently moves on** — wasting turns in a fix/fail loop.

### Rules

- [ ] **P0** `.pre-commit-config.yaml` runs **read-only** linters
      (`ruff --no-fix`, `mypy`, `tsc --noEmit`, `eslint --max-warnings 0`).
      No mutating hooks (no `--fix`, no formatter rewrites).
- [ ] **P0** Pre-commit uses **`language: system`** for ruff/mypy/eslint
      to avoid version drift between `.pre-commit-config.yaml` and the
      project's pinned dev tools.
- [ ] **P0** Agent commit path sets `SKIP=mypy,tsc,eslint` env var on
      commits inside `.worktrees/` (worktree path detection in the
      orchestrator's commit step). Only fast read-only `ruff` runs there;
      heavy gates run at PR-time CI.
- [ ] **P0** **`gitleaks`** as the only "fail loud" hook on agent commits.
- [ ] **P1** `pre-commit autoupdate` is run **monthly by a human**, not
      by an agent.
- [ ] **P2** Commit-msg hook validates Conventional Commits (`feat:`,
      `fix:`, `chore:`) — agents copy the format mechanically.

---

## 8. CI Pipeline (Dark-Factory-Ready)

The CI is the agent's **deterministic feedback bus**. Every gate must
have a **stable exit code**, **structured (JSON / JUnit XML) output**,
and a **predictable name**.

### Required jobs

- [ ] **P0** `tests`: `pytest -n 4 --dist=worksteal` (CI runs the same `-n 4`).
- [ ] **P0** `coverage`: branch coverage, fail under 100 % (Alan default;
      original guidance 90 %), upload XML artifact, post to SonarCloud.
- [ ] **P0** `lint-python`: `ruff check`, `mypy <subset>`, `bandit -r`,
      `vulture --min-confidence 80`.
- [ ] **P0** `lint-frontend`: `tsc --noEmit`, `eslint`, `knip`,
      `dependency-cruiser`.
- [ ] **P0** `ccn-gate`: `lizard --CCN 7 --length 60` on hot paths
      (Alan default); `xenon --max-modules A --max-average A` on rest.
- [ ] **P0** `lint-imports`: `uv run lint-imports` (import-linter contract).
- [ ] **P0** `supply-chain-audit`: `pip-audit`, `npm audit
      --omit=dev --audit-level=high`, `gitleaks detect`.
- [ ] **P2** **SonarCloud**, advisory only (see §5 "Multi-language"): it
      uploads an analysis and never blocks a merge.
- [ ] **P1** `e2e-smoke`: post-deploy `curl /api/health` + one
      task-creation round-trip via Playwright or `gh` CLI.
- [ ] **P2** `contracts`: schemathesis / dredd against the OpenAPI spec
      if you have one.

### Required wiring

- [ ] **P0** **Lockfiles everywhere**: `uv.lock`, `package-lock.json`.
      CI uses `uv sync --locked` and `npm ci`. Deploy uses the same.
      Never `pip install` or `npm install` in CI.
- [ ] **P0** **Reproducible Python** via `uv` (`astral-sh/setup-uv`),
      **reproducible Node** via `actions/setup-node` with `cache: 'npm'`.
- [ ] **P0** **Concurrency groups** on every workflow:
      `concurrency: { group: <workflow>-${{ github.ref }}, cancel-in-progress: true }`.
      Without this, two pushes spawn two deploys and the second `git reset
      --hard` destroys the first's work.
- [ ] **P0** **Backup-commit skip:** every job must skip itself when the
      head commit is a `backup: sync workspaces …` commit
      (`if: !startsWith(github.event.head_commit.message, 'backup:')`).
      Otherwise periodic backup commits trigger CI storms.
- [ ] **P0** **`on-failure.yml` rate-limit guard**: skip if a maintenance
      task was already created for the same workflow_run name in the last
      30 min. Without this, a persistent false-positive lint rule
      generates **unbounded** maintenance tasks across branches.
- [ ] **P1** A dedicated macOS deploy runner. Re-render the launch
      configuration (`bash deploy/render_plist.sh`, see
      `docs/architecture/deploy.md`) before `launchctl bootout + bootstrap`,
      **never** `launchctl kickstart -k` — the latter reuses stale plist
      definitions and silently deploys old config.
- [ ] **P1** Health-check loop after deploy (6 × 2 s). On failure,
      auto-roll-back to the previous `deploy-YYYYMMDD_HHMMSS` tag.
- [ ] **P1** Structured `ci-results.json` artifact (parse `test-results.xml`
      + `ruff --output-format=json`) — agents ingest this to fix failures.
- [ ] **P2** `auto-merge.yml` gated on:
      - branch starts with `teams/` AND
      - all required checks green AND
      - Copilot/CodeRabbit review addressed AND
      - no `do-not-merge` label.
      **Agent PRs do NOT auto-merge by default** even with the
      label — explicit `gh pr merge` after CI green + reviewer addressed.

---

## 9. Agent Workflow Patterns

The single largest variance in SWE-bench scores comes from **scaffold
choice**, not model choice. Same model (GPT-5):

| Scaffold | SWE-bench Verified |
|---|---|
| mini-SWE-agent (ReAct) | < 20 % |
| OpenHands CodeAct (Plan+Execute+test) | 50 %+ |

**A 2.5× gap from scaffold alone.** [verified — CodeSOTA 2026]

### Validated patterns [verified]

| Pattern | Status | Evidence |
|---|---|---|
| Plan-then-execute (vs ReAct) | ✅ Use | 12+ pp gain, AutoCodeRover ISSTA 2024 |
| Compiler/linter-in-the-loop feedback | ✅ Use | LLMLOOP ICSME 2025; baseline 42 % error rate without |
| Best-of-N + execution-based selection | ✅ Use | +6–12 pp; RTV +6.7 / PDR +12.2 (Kim 2026) |
| Auto-inject tester after implementer | ✅ Use | Regression prevention validated |
| Extended-thinking budget on capable models | ✅ Use | +6 pp Sonnet 4.5; +20 pp o1→o3 |
| Multi-agent debate (homogeneous models) | ❌ Skip | No consistent gain over single agent |
| Plain TDD prompting (no dep graph) | ❌ Harmful | +regressions to 9.94 % on smaller models |
| Long-context window stuffing | ❌ Skip | "Lost-in-the-Middle" — ~40 % degradation [Chroma] |
| Cascading multi-agent decisions | ⚠️ Risky | 95 % × 5 steps = 77 % — context degrades across handoffs (Cognition) |

### Rules

- [ ] **P0** Architecture is **plan → execute → verify**, not ReAct.
      A planning step generates a JSON / tool-call composition; a separate
      executor loops sequentially with prior outputs seeded in.
- [ ] **P0** **Tester is auto-injected** after every implementation
      agent (reactive form is enough — preemptive failing-test generation
      requires a code-test dependency graph; without it, harmful).
- [ ] **P0** **Compiler / linter / type-check output is filtered, not
      stuffed.** Compress test output (drop passes, keep failures), strip
      ANSI, dedupe lines, truncate stack traces. Aim for ≤ 30 KB tool
      results.
- [ ] **P0** **Verifier / judge agents** use a **smaller, cheaper model**
      (Haiku-class) for binary classification (PASS / FAIL, ON_TRACK /
      STUCK). Save Opus / Sonnet for synthesis and implementation.
- [ ] **P0** **Token budget per session** with explicit cap (e.g., 150K).
      Skip remaining agents when exceeded — better to ship a partial
      result than thrash. Compress context between agents (caveman
      ultra/lite, RTK-style filtering, ~65–75 % output reduction).
- [ ] **P1** **Best-of-N at the implementation step**: implementer emits
      2–3 candidate diffs, tester runs each, picks the passing one.
- [ ] **P1** **Adaptive thinking** (Opus 4.7) or fixed budget (32K)
      Sonnet 4.5 — turn on extended thinking for implementer agents.
- [ ] **P1** **Question budget decay** for principal-proxy / clarification
      flow: 10 max iter 1–2, 5 iter 3, 2 iter 4, 1 iter 5+. Final-answer
      mode when ≤ 3 questions remain. Prevents agents asking the same
      thing twice.
- [ ] **P2** **Auto-PR** on successful execution with no pending
      questions. Push branch, `gh pr create` with synthesis as body.
- [ ] **P2** **Audit log** every proxy decision to `proxy_audit.jsonl`
      (timestamp, task_id, question, action, confidence). Enables
      calibration runs ("agree / disagree" with the human override).

---

## 10. Context Engineering & Token Budgets

> "Context is the new compiler." — Augment Code

**Cognition (Devin), 2025 [verified — operator experience]:**
> *"Context engineering is the #1 engineering job for agentic systems.
> Distributed multi-agent decision-making produces fragile systems
> because context can't be shared across agent boundaries."*

**Common waste sinks in the orchestrator → agent path
[measured, token-saving research]:**

| Source | % of agent tokens |
|---|---|
| File reads (full file contents) | ~40 % |
| Shell command output (tests, grep, ls) | ~20 % |
| Agent output completions | ~22 % |
| System / charter prompt | ~10 % |
| Task prompt | ~8 % |

### Rules

- [ ] **P0** **Reference files with `@path/to/file`**, never paste their
      contents. Claude Code reads lazily from references; pasted content
      stays in context forever.
- [ ] **P0** **`/compact focus on X`** instead of `/clear` when you need
      retention; `/clear` between unrelated tasks.
- [ ] **P0** Compress tool output **before** the agent sees it: ANSI
      strip, dedupe, drop passing tests, truncate stack traces, summarize
      build progress (RTK-style, see `context_filter.py`).
- [ ] **P0** **Smart-truncate by paragraph and section**, never by char.
      Sentence-boundary detection breaks on caveman fragments — split on
      `\n\n`, prioritize sections by `##` headings, fall back to char
      truncation only as a last resort.
- [ ] **P0** **Adaptive proportional budgets** for prior-context handover:
      most-recent 50 %, previous 30 %, oldest 20 % — total ≤ 4 KB.
- [ ] **P0** **Re-inject agent YAML key-findings** between invocations.
      `AGENT_STATUS / CONFIDENCE / COMPLETENESS / GAPS` blocks survive
      — the rest can be summarized. Without this, the next invocation
      rediscovers what the previous one already knew. (Agent
      performance retrospective: this is the structural root of high
      `needs_input` rates.)
- [ ] **P1** **Repomix** (XML output, tree-sitter compression, ~70 %
      token reduction) for one-shot codebase context delivery to a
      research agent.
- [ ] **P1** **PageRank-style symbol graph** (à la Aider repo-map) over
      keyword priority truncation for cross-file implementation tasks.
- [ ] **P1** **Vagueness score** (not length) gates the preprocessor.
      Signals: file paths(-0.25), code blocks(-0.25), error context(-0.2),
      followup(-1.0), length<40(+0.3), ambiguous verbs(+0.2),
      no identifiers(+0.15). Score < 0.3 = skip preprocessor; > 0.6 =
      preprocess.
- [ ] **P1** **Spec completeness score** (objective 0.30, scope 0.20,
      deliverables 0.20, constraints 0.15, acceptance 0.15) — log per
      invocation, ratchet upward over time.
- [ ] **P1** **Provider detection** before setting `response_format`:
      `response_format={"type":"json_object"}` works on **OpenAI**, breaks
      on **Anthropic**. Strengthen the planning prompt with "RESPOND WITH
      VALID JSON ONLY. No markdown fencing, no prose." Use **non-greedy
      regex** for JSON extraction: `r"\[[\s\S]*?\]"` not `r"\[.*\]"`.
- [ ] **P2** **`token-optimizer-mcp`** caches identical file reads across
      agents in a single team run (~10 % saving when ≥3 agents read the
      same orchestrator.py).
- [ ] **P2** **`context-mode`** / equivalent semantic chunker — return
      relevant sections of files instead of full contents (~28 % saving
      on file-read-heavy runs, but 5-day implementation effort).

---

## 11. Memory & Cross-Session State

**The pointer-index pattern [verified — Claude Code's auto-memory design]:**
- `MEMORY.md` is an **index**, not a memory store: one line per record,
  under ~150 chars: `- [Title](file.md) — one-line hook`.
- Each memory is its own `.md` file with YAML frontmatter
  (`name`, `description`, `type`).
- 4 record types: `user` (who they are), `feedback` (rules to follow),
  `project` (current state, time-stamped, decays), `reference` (where
  external info lives).
- Limit: 200 lines / 25 KB before truncation.

### Rules

- [ ] **P0** Per-task **workspace persistence** (`~/.<app>/workspaces/<task_id>/`):
      `meta.yaml`, `history.yaml`, `artifacts/{context,decisions,plan,handover}.md`,
      `teams/<team>/inv_NNN/{state.yaml, agents/<role>.yaml}`,
      `usage.jsonl`. Agents in future invocations need this; the
      truncated synthesis alone is insufficient.
- [ ] **P0** **Load `spec.yaml` in `load_artifacts()`**. (Retrospective
      bug: the file was hardcoded out of the load list,
      making spec injection dead code.)
- [ ] **P0** **Load agent-output YAMLs** when building handover context.
      Re-inject `AGENT_STATUS` blocks from prior invocations. Currently
      most pipelines lose this, forcing rediscovery.
- [ ] **P0** **Memory-citation pinning**: when a memory references a file
      path or function name, store a SHA anchor; rewrite stale ones on
      monthly sweeps. (A retrospective found stale memory files citing
      paths that no longer existed.)
- [ ] **P0** Convert relative dates to absolute when saving memory
      ("Thursday" → "2026-03-05"). Future-you cannot interpret "Thursday".
- [ ] **P1** **Plan-replan loop cap**: `consecutive_plan_failures ≥ N →
      halt`. Without it, agents loop forever on a flawed plan and burn
      the token budget.
- [ ] **P1** **Post-agent git-state check** before declaring "complete".
      Synthesis routinely says "shipped" when the code is uncommitted.
      `git diff --stat` + branch verification before the orchestrator's
      "complete" status flag is set.
- [ ] **P2** Periodic `context.md` summarization (otherwise unbounded
      growth across many invocations on the same task).

### What NOT to save in memory

- Code patterns, conventions, file paths — derivable from current code.
- Git history / who-changed-what — `git log` / `git blame` are authoritative.
- Debugging fixes / recipes — the fix is in the code.
- Anything already in CLAUDE.md.
- Ephemeral task state — that's what tasks/plans are for.

---

## 12. Claude-Native Conventions

If your agents run inside Claude Code (CLI / SDK / harness), align with
its primitives. Custom replacements add token cost AND make Claude fight
itself ("inner Claude fight").

### How Claude Code loads context [verified — Anthropic docs 2025]

**Session-start order:**
1. System prompt (~4,200 tokens)
2. Auto memory / `MEMORY.md` (first 200 lines or 25 KB)
3. Environment info (cwd, platform, git status)
4. MCP tool **names** (deferred — schemas load on demand via `ToolSearch`)
5. Skill descriptions (one-liners only)
6. `~/.claude/CLAUDE.md` (global, user-level)
7. Project `CLAUDE.md` (loaded as user message)
8. First prompt

Auto-compaction fires at **~83.5 %** capacity. After compaction, project-root
`CLAUDE.md` is **re-injected from disk**; subdirectory CLAUDE.md and
conversation-only instructions are **lost**.

### Rules

- [ ] **P0** **System instructions go via `--append-system-prompt`**, not
      XML-in-stdin. `--system-prompt` *replaces* the default Claude Code
      preamble (loses CLAUDE.md, git status, hooks, identity); use
      `--append-system-prompt` to preserve them. Putting system instructions
      in the user-message body causes Claude to treat them as "another
      request" instead of directives.
- [ ] **P0** **Replace `--dangerously-skip-permissions` with
      `--permission-mode auto`** (Opus 4.7 on Max/Teams/Enterprise).
      The auto classifier approves safe commands; the `--dangerously-…`
      flag is a sledgehammer that bypasses every check.
- [ ] **P0** **Omit `--allowedTools` when empty.** Passing `""` may mean
      "zero tools allowed" and silently break agents when MCP is
      unavailable. Omitting = "no filter".
- [ ] **P0** **Use `Agent` tool with `isolation: "worktree"`** for
      code-changing subagents. Auto-cleanup if no changes; otherwise
      returns the path + branch.
- [ ] **P1** Use `Agent` tool `run_in_background: true` for independent
      research / fact-finding queries to **parallelize**. (Don't parallel
      anything that mutates shared state.)
- [ ] **P1** Use `EnterPlanMode` / `ExitPlanMode` for explicit planning
      phases instead of a separate orchestrator call. (Lower latency,
      no inner-Claude fight.)
- [ ] **P1** Use `TodoWrite` for in-session task tracking; persist
      across-session state in your own workspace.
- [ ] **P1** Use `AskUserQuestion` for blocking clarifications. For
      messaging-platform async approval (WhatsApp / Slack), keep your
      `@@answer` / `@@approve` flow.
- [ ] **P1** **MCP tool schemas via `ToolSearch`**, not eagerly loaded.
      Saves session-start tokens.
- [ ] **P2** Skills (`SKILL.md` with YAML frontmatter under
      `~/.claude/skills/<name>/`) for reusable workflows. Loaded
      one-liner-only at session start; full body fetched on `/skill` invoke.
- [ ] **P2** Hooks for **deterministic** behavior changes (where
      CLAUDE.md is advisory). E.g., a `SessionStart` hook injects last
      7 days of git log; a `PreToolUse` hook validates Bash commands.
- [ ] **P2** `/btw` for off-conversation questions; `/compact focus on X`
      to summarize with retention.

---

## 13. Worktree & Concurrency Hygiene

Code-changing agents run in **per-task git worktrees** (`.worktrees/`,
branch `teams/<task_id>`). This isolation has known footguns.

### Rules

- [ ] **P0** **Branch verification before edit.** Every implementation
      agent first runs `git rev-parse --abbrev-ref HEAD` and aborts if
      not on `teams/<task_id>`. Otherwise an agent on `main` will commit
      to `main`.
- [ ] **P0** **Pre-commit hook awareness in worktree**:
      `.git/hooks/` is shared, so worktree commits fire the same hooks.
      See §7. Use `SKIP=` env var on agent commits.
- [ ] **P0** **Concurrency groups** on workflows (see §8) so two agents
      can't simultaneously deploy.
- [ ] **P0** **Never `pip install -e .` from a worktree.** It overwrites
      the global venv's editable mapping → next session in the main
      checkout fails with `ModuleNotFoundError`. Workspaces have memory
      of this incident; codify it in CLAUDE.md.
- [ ] **P0** **Worktree cleanup is automatic when no changes**, but
      always verify after long runs: `git worktree prune`,
      `git branch --merged main | xargs -n1 git branch -d`.
- [ ] **P1** **Tester runs in the same worktree** as the implementer —
      tests must see the new code. Verify the branch matches before each
      `pytest` invocation.
- [ ] **P1** **Cherry-pick / rebase guard**: if the implementer commits
      to the *wrong* branch (e.g., the parent of the intended PR branch),
      the orchestrator detects it (`git log --oneline ...HEAD`) and
      rebases / cherry-picks before opening the PR.
- [ ] **P0** **`ALAN_*` env vars are for state isolation only, never
      user-facing config.** A fleet worker adding a new persistent store
      registers its redirect env var in every state-isolation surface it
      touches (conftest redirects, e2e `serverEnv`, the worker subprocess
      sandbox, CI workflow env, prod plist) in the *same* PR — a missed
      surface leaks test/worker state into production `~/.alan/`. Every
      `ALAN_*` var read by `alan/` Python source must be classified as
      isolation or named excepted-config in `tests/env_var_classification.py`;
      a static pytest (`tests/test_env_var_classification.py`) fails any
      new unclassified var with "classify this var as isolation or
      excepted-config" instead of letting it reach `main` unbucketed.

---

## 14. Dark Factory: GitHub-Native Loop

The factory is a **GitHub-native state machine** driven by labels (or
Projects v2 status columns) on **structured issues**.

### Phased rollout

**Phase 1 — Skeleton (~100 LOC, 2 files):**
- `.github/ISSUE_TEMPLATE/factory-spec.yml`: structured fields —
  Objective, Scope, Constraints, Acceptance Criteria, Team Sequence.
- `.github/workflows/factory-dispatch.yml`: triggers on `issues:
  [opened, labeled]`; validates spec; transitions status; concurrency
  group `factory-${{ issue.number }}`.

**Phase 2 — Agent dispatch:**
- `factory/dispatch.py` Python bridge mapping issue → `run_team()`.
- Per-task attempt counter in `TaskSpec` (prevent infinite re-queue).
- macOS `hermes-bot` user for runner isolation.
- `uv lock` + `pip-audit` quality gate.

**Phase 3 — Full autonomy:**
- Auto-merge on `flag:auto-merge-ok` + green CI + reviewer addressed.
- Constitutional pre-dispatch checker against `vision.yaml`.
- Meta-queue: `factory:` issues that improve the factory itself.

### Rules

- [ ] **P0** **Structured issues**, not free-form. Every factory job
      has Objective / Scope / Constraints / Acceptance / Team-Sequence.
- [ ] **P0** **Status, not chaos.** Either GitHub Projects v2 columns
      (visual Kanban) or `<!-- factory-status: queued -->` HTML comments
      in the issue timeline. Pick one. Don't mix.
- [ ] **P0** **Concurrency groups** per issue: `factory-<issue-number>`
      with `cancel-in-progress: false` (queue, don't drop).
- [ ] **P0** **Acknowledgement comment** posted by the workflow within
      seconds of issue creation — gives the human a visible signal that
      the system saw it.
- [ ] **P0** **A runner you control** for any agent that calls Anthropic
      API (no OAuth tokens on GitHub-hosted runners, per supply-chain
      hygiene).
- [ ] **P0** **`vision.yaml`** at `~/.<app>/vision.yaml` with auto-answer
      regex rules + LLM Haiku fallback for principal-proxy decisions.
      `dry_run: true` until you've calibrated 10–20 tasks.
- [ ] **P1** **WhatsApp / Slack** notifications for proxy decisions
      (not blocking, just visibility).
- [ ] **P1** **Auto-PR** on green-pipeline completion. Branch prefix
      `teams/<task_id>`, body = synthesis.
- [ ] **P1** **Auto-merge label gated**: `flag:auto-merge-ok` AND CI
      green AND reviewer addressed AND no `do-not-merge`. Until you've
      measured agreement-rate ≥ 95 % on dry-run, keep human-in-loop.
- [ ] **P2** **Per-task attempt counter** (`max_retries: 3`), exponential
      backoff. Otherwise a flaky test causes infinite re-queue.
- [ ] **P2** **Constitutional checker**: a Haiku call validates the
      factory's planned action against `vision.yaml` before dispatch.
      Cheap insurance against drift.

---

## 15. Quality Gates That Catch LLM Failure Modes

The most useful single table. **Map each known LLM failure mode to a
detector.**

| Failure mode | Primary detector | Backup |
|---|---|---|
| Hallucinated API (wrong method/sig) | mypy (Python) / `tsc --strict` (TS) | pyright strict |
| Hallucinated API on untyped 3rd-party lib | pyright strict (`reportUnknownMemberType`) | — |
| Silent type coercion (`str + int`) | mypy `strict_optional`; tsc strict | ruff |
| Over-broad `except Exception:` | **ruff `BLE001`** | pylint W0718 |
| Swallowed errors (raise without `from`) | ruff `B904` | bandit |
| Dead else after return | **ruff `RET505`** | Sonar `S1134` |
| Fake test assertions | ruff `PT017/018` | SonarCloud |
| Unused imports / locals | ruff `F401`, `tsc noUnusedLocals`, knip | vulture |
| Dead functions / classes | **vulture** `--min-confidence 80` | SonarCloud |
| Dropped `await` on async (TS) | **`@typescript-eslint/no-floating-promises`** | — |
| `any` as escape hatch | `@typescript-eslint/no-explicit-any: error` | — |
| Unused exports across files | **knip** | tsc `noUnusedLocals` |
| Hardcoded API keys / secrets | bandit `B105/B106`, gitleaks | — |
| Resource leaks (file/socket) | SonarCloud `S2095` | — |
| API contract violations | SonarCloud + mypy | — |
| Refactor residue (callsites not updated) | vulture + knip | grep |
| Hallucinated commit ("done" but uncommitted) | **post-agent `git diff --stat` check** | branch verifier |
| Stale CLAUDE.md after refactor | **CLAUDE.md drift gate** on architecturally-significant PRs | manual review |
| Truncated handover ("agent rediscovers info") | **agent-output YAML re-injection** | smart-truncate by section |
| JSON-parse failure on planning call (23 % rate) | non-greedy regex; provider detection; explicit "no fences" prompt | retry with stricter prompt |

---

## 16. Anti-Patterns To Eliminate

Don't ship these. Each is a documented regression.

- ❌ **LLM-generated CLAUDE.md.** −3 % impact on issue resolution
  (worse than no file). Write it by hand. [verified]
- ❌ **CLAUDE.md > 200 lines.** Past ~150 lines LLMs start ignoring rules
  ("Lost-in-the-Middle"). [verified]
- ❌ **Code-style rules in CLAUDE.md.** "Never send an LLM to do a
  linter's job." Style → ESLint / ruff / Prettier.
- ❌ **Comments / docstrings as a primary doc strategy.** Names carry
  meaning; comments rot. (Allow only tooling directives.)
- ❌ **Multi-agent debate with homogeneous models.** No consistent
  gain over a single well-scaffolded agent. [verified]
- ❌ **Plain TDD prompting** ("write failing test first") without
  code-test dependency context — increases regressions to 9.94 %.
  [verified]
- ❌ **Cascading multi-agent decisions.** 95 %-reliable steps × 5 hops
  = 77 %. Context degrades across handoffs. [Cognition postmortem]
- ❌ **Long-context window stuffing.** Lost-in-the-Middle: ~40 %
  degradation when window fills. [Chroma 2025]
- ❌ **Pasting file contents** instead of `@references`. Pasted content
  stays forever; references are lazy.
- ❌ **`--dangerously-skip-permissions`** in production. Use
  `--permission-mode auto`.
- ❌ **`--system-prompt`** in Claude Code (overwrites default preamble).
  Use `--append-system-prompt`.
- ❌ **XML-in-stdin** prompt construction ("inner Claude fight").
- ❌ **`launchctl kickstart -k`** for plist reload. Re-render
  (`deploy/render_plist.sh`), then `bootout + bootstrap`.
- ❌ **Floating GitHub-Action tags** (`@v6`). Pin 40-char SHA.
- ❌ **`pip install` / `npm install`** in CI. Use `uv sync --locked`,
  `npm ci`.
- ❌ **`pip install -e .` from a worktree.** Hijacks the global venv's
  editable mapping.
- ❌ **`launchctl kickstart -k`** when the plist changed — render first
  (`deploy/render_plist.sh`), then bootout + bootstrap.
- ❌ **Mutating pre-commit hooks (`ruff --fix`)** in worktrees. Agent
  silently fails the commit and moves on.
- ❌ **Raw 1500-char synthesis truncation** for handover. Use smart
  paragraph + section-priority truncation, re-inject agent YAMLs.
- ❌ **Greedy regex `r"\[.*\]"`** for JSON extraction in the planning
  call. Use non-greedy `r"\[[\s\S]*?\]"`.
- ❌ **`response_format={"type":"json_object"}`** on Anthropic
  endpoints. Detect provider first; only use on actual OpenAI.
- ❌ **Tests that mock the database** in integration tests where prod
  uses a real DB. Mocks pass; migrations fail.
- ❌ **Trusting "synthesis says shipped"** without `git diff --stat` +
  branch verification.
- ❌ **Auto-merge on agent PRs** before calibrating proxy decisions
  against human overrides. Track agree/disagree first.

---

## 17. Production-Tested Patterns

These patterns come from running a multi-agent pipeline in production —
operationally proven, often invisible from outside, each addressing a
specific failure mode that **only shows up after months of running
agentic pipelines for real**.

### 17.1 Heartbeat + watchdog separation of "interrupted" vs "retried"

**Pattern.** Workers write a heartbeat row to `active_tasks` every loop
tick. A watchdog thread periodically marks any running task whose
`COALESCE(heartbeat_at, started_at, created_at)` is older than the
cutoff as `failed: watchdog: stale running (no heartbeat)`.
Re-queues from a server restart bump a separate **`interruption_count`**
column — *not* `retry_count`.

**Why.** Without separation, an innocent task survives N server
restarts and gets buried under "max retries exceeded" — even though
it never actually failed. With separation, you can ask "which tasks
get killed by deploys?" vs "which tasks are genuinely flaky?".

### Rules

- [ ] **P1** Every long-running agent loop emits a heartbeat at least
      every 30–60 s. A watchdog flips `running` → `failed` after a
      stale timeout (default ~4 h is conservative; the right number is
      "longer than your slowest agent, shorter than user patience").
- [ ] **P1** **`interruption_count`** column distinct from
      `retry_count`. On startup, recover all `running`/`pending` items:
      increment `interruption_count`, re-queue. Never silently merge
      into `retry_count`.
- [ ] **P1** Watchdog also expires `pending` tasks older than N hours
      (`expire_stale`) — abandoned queues are common when a user
      submits, then forgets, then changes config.

### 17.2 Forced-summary recovery on turn-budget exhaustion

**Pattern.** When an agent hits its max-turns ceiling, do **NOT** error
out. Resume the same `session_id` with a forcing system prompt:
*"TURN BUDGET EXCEEDED. You have N turns left and ZERO tool calls
allowed. Write findings now from what you gathered. End with
AGENT_STATUS: status=partial, completeness=…, GAPS=…"* — passing
`allowed_tools_override=TOOL_POLICY_NO_TOOLS` and
`max_turns=RECOVERY_MAX_TURNS` (typically 3).

**Why.** A 90 %-complete analysis with no output is total loss. The
forced-summary mode salvages the agent's working memory and produces
a structured partial result the next invocation can build on.

### Rules

- [ ] **P0** Turn-exhausted agents enter a **forced-summary mode**
      (3 turns, zero tools) before being marked failed.
- [ ] **P0** The summary block includes `status=partial`, `completeness`,
      and explicit `GAPS:` — these become the next invocation's seed.

### 17.3 Extension judge + deferred topics + topic grounding

**Pattern.** When an agent hits max-turns, a separate cheap judge model
(Haiku) reads the transcript and decides whether to **(a)** extend by
0–15 turns, or **(b)** stop and emit `deferred_topics: [...]` for the
next invocation. The judge response is parsed with **triple-fallback
JSON** (fenced block → first-`{`-to-last-`}` → safe default), and
deferred topics are **grounded in the transcript** — topics whose key
terms don't appear in the transcript are dropped (the judge sometimes
hallucinates uncovered topics).

**Why.** Without an extension judge, you choose at planning time
between "too few turns" (cliff failures) and "too many turns"
(token waste on every successful run). With one, the system is
adaptive *per agent run*. Topic grounding prevents the judge from
inventing work that wasn't actually started.

### Rules

- [ ] **P0** Every "max turns hit" event runs through an extension
      judge before being declared failed. Judge is on a smaller, cheaper
      model than the agent it judges.
- [ ] **P0** Triple-fallback JSON parsing for any judge / planner
      response: fenced block → braces → safe default. Never error on
      unparseable.
- [ ] **P1** **Ground the judge's claimed deferred topics** in the
      transcript. Drop ones whose ≥ 4-letter content terms don't appear.
- [ ] **P1** Cap extensions (`EXTENSION_TURNS_CAP`, e.g., 15). Also
      cap the *number* of extensions per task — otherwise a clever
      judge keeps granting +5.

### 17.4 Backup-commit awareness throughout the pipeline

**Pattern.** Periodic background commits (e.g., `backup: sync workspaces
2026-05-08 12:09 UTC`) are filtered out everywhere they could mislead:
- CI: `if: !startsWith(github.event.head_commit.message, 'backup:')`.
- Phased orchestrator: `_branch_has_only_backup_cron_commits()` — if a
  branch has *only* backup commits, treat it as "no real work yet" and
  skip re-planning.

**Why.** Without filtering, every 30-min backup commit triggers a CI
run, an on-failure dispatch, a re-plan, or a "task already in progress"
guard. The pipeline melts.

### Rules

- [ ] **P0** Define a small set of **machine-generated commit prefixes**
      (`backup:`, `chore(deps):`, `bot:`) up front. Filter them in:
      CI workflow triggers, on-failure dispatch, "is this branch real?"
      checks, auto-merge eligibility.
- [ ] **P1** Do this at the *start* of the project, not after the first
      melt-down. Retrofitting takes weeks of bug reports.

### 17.5 JSON parse method tracked in usage logs

**Pattern.** Plan parsing tries direct JSON first; on failure, falls
back to extracting from `tool_call` blocks; logs which path succeeded
in `usage.jsonl` as `json_parse_method ∈ {"direct", "tool_call",
"retry"}` plus `json_parse_attempts`.

**Why.** The 23 % JSON parse-fail rate identified in an agent
performance retro was only measurable because the pipeline tracked it. *Quality
metrics you don't log are quality metrics you can't improve.*

### Rules

- [ ] **P0** Log per-invocation: `json_parse_attempts`,
      `json_parse_method`, `context_chars_original`,
      `context_chars_filtered`, `vagueness_score`, `spec_completeness`,
      `confidence`, `latency`, `tokens_in`, `tokens_out`.
- [ ] **P0** `usage.jsonl` is append-only, one record per agent call
      AND one summary per invocation. Append-only means agents and
      humans can both grep / aggregate without locking.
- [ ] **P1** Provide `compute_performance_summary(task_id?, hours=24)`
      that aggregates: `json_fail_rate`, `needs_input_rate`,
      `avg_context_compression`, `avg_tokens_per_invocation`,
      `p50_latency`, `p90_latency`, `top_token_consumers`,
      `handover_count`. Surface via a `@@u` / `@@metrics` command.

### 17.6 Handover artifact pattern (vs raw re-injection)

**Pattern.** Cross-team handover writes a dedicated **`handover_context.md`
artifact** with three sections: (a) Prior Synthesis (filtered via
`_filter_handover_synthesis()` — drops Q&A, debug noise, but keeps
recommendations, decisions, blockers); (b) Source-team Findings
(scoped subset of agent YAMLs); (c) Q&A pairs the user actually
answered. The next team reads this artifact, not the raw prior
synthesis.

**Why.** Re-injecting raw synthesis pushes the next team's planner
context past the cliff (Lost-in-the-Middle starts ~40 % degradation).
A pre-digested artifact is **smaller, more relevant, and survives
compaction**.

### Rules

- [ ] **P1** Cross-team handovers produce a separate `handover_context.md`
      artifact, not just an in-memory blob. Persisting it lets future
      invocations of the *same* team re-read it after a context reset.
- [ ] **P1** Handover artifact is filtered: keep recommendations,
      decisions, blockers, file refs; drop chit-chat, intermediate
      tool output, redundant Q&A.
- [ ] **P2** Append a `## Instructions for Target Team` block at the
      bottom — the source team writes one sentence saying what the
      target team should do next. (Eliminates "wait, what was I
      supposed to do?" rediscovery.)

### 17.7 Effort-level suffix syntax on commands

**Pattern.** Operator-tunable cost/quality dial via command suffix:
`~` → fast (Haiku, 1 agent, 5 turns, 60K budget), `!!!` → low,
`!!` → medium, `!` → high, default → xHigh (8 agents, 50 turns, 750K
budget). One frozen `EffortParams` dataclass per profile; profile
controls `max_agents`, `max_turns`, `confidence_threshold`,
`token_budget`.

**Why.** Without a dial, you either over-spend on every dispatch or
hand-edit config per task. The suffix is muscle-memory; the profile
is a single source of truth that prevents drift between knobs.

### Rules

- [ ] **P1** A single immutable **`EffortParams`** dataclass with
      `max_agents, max_turns, confidence_threshold, token_budget,
      [model_override]`. ≤ 5 named profiles.
- [ ] **P1** **Suffix-based dispatch syntax** so operators can dial
      effort without leaving the prompt. Document the syntax in
      CLAUDE.md (this is one of the rare cases where a non-obvious
      convention belongs there).
- [ ] **P2** Token budget is **hard-enforced** in the executor: when
      `session_tokens_used > token_budget`, halt remaining agents and
      synthesize what you have. Better a partial result than a blown
      bill.

### 17.8 Auto-inject tester driven by git diff, not by static planner rule

**Pattern.** Plan validation: if the plan includes an `implementer` but
no `tester`, the validator reads `git log` / `git diff` since the
parent ref to find what changed, then **injects a tester role with a
docstring scoped to the actual changes** ("Cover unit-test gap from
previous implementer(s); diff: <files>"). Not a generic "always add
tester" — a specific "test what the previous step modified".

**Why.** Generic "always add a tester" wastes turns on no-op runs and
under-specifies on real ones. Diff-driven injection makes the tester's
prompt as concrete as a hand-written one.

### Rules

- [ ] **P0** Plan-validation step: if `implementer` ∈ roles and
      `tester` ∉ roles, inject `tester` automatically.
- [ ] **P0** The injected tester's prompt cites the **actual diff**
      (file list + line ranges) since the previous implementation
      step, not a generic "test the changes".
- [ ] **P1** Same pattern for `code_reviewer` injection on PR-creating
      runs: read the PR diff, scope the reviewer's prompt to it.

### 17.9 Confidence tiers per model in the principal-proxy

**Pattern.** Auto-answer thresholds are **per-model**, not global:
`haiku ≥ 0.97`, `sonnet ≥ 0.93`, `opus ≥ 0.88`. Same proxy decision,
different acceptance bars. Below the bar → escalate to human.
(Calibrated against the Sonar 2025 finding that smaller models
hallucinate at higher rates.)

**Why.** A flat 0.9 threshold is too lax for Haiku (false-confident on
edge cases) and too strict for Opus (rejects legitimately confident
answers). Tiered thresholds match each model's calibration curve.

### Rules

- [ ] **P1** Tier confidence thresholds **per model** in any LLM-as-
      classifier path (proxy, judge, watchdog, vagueness scorer).
- [ ] **P1** Recalibrate quarterly: the same model on the same task
      drifts release-to-release.
- [ ] **P2** Audit log every classifier decision (`logs/proxy_audit.jsonl`)
      with timestamp, task_id, question, action, confidence, category.
      The log is what enables recalibration.

### 17.10 SQLite-backed persistent queue (not Redis, not Celery)

**Pattern.** SQLite at `~/.<app>/teams_queue.db` for the work queue,
worker poll loop every ~2 s, schema migrations versioned and applied
on startup, WAL mode, single writer + many readers. Priority FIFO
with retry.

**Why.** A single-machine agent pipeline does **not** need Redis +
Celery + a worker daemon and the YAML to manage them. SQLite gives
you ACID, restart safety, easy CLI inspection (`sqlite3 …
'SELECT * FROM queue_items'`), and zero infra cost. This is one of
those rare cases where the boring choice scales further than the
trendy one.

### Rules

- [ ] **P1** Default to **SQLite** for any work queue / state DB
      until you have *measured* contention. WAL mode, schema-
      versioned, single-writer.
- [ ] **P1** Schema migrations live in code, applied on startup,
      idempotent. Version column in a `_schema_meta` table; never
      `DROP TABLE`.
- [ ] **P2** Provide a read-only `sqlite3` query helper as a CLI
      sub-command — operators love this when debugging at 2 a.m.

### 17.11 `_isolate_home` autouse pytest fixture

**Pattern.** A single autouse fixture in `tests/conftest.py` redirects
the app's home-directory variable to `tmp_path`, exports a dummy
`ANTHROPIC_API_KEY=test-dummy`, and resets all providers
(`init_providers()` against the test paths). Every test gets a clean
home dir and clean DI without touching production wiring.

**Why.** Without this, one test's leaked state breaks the next; with
it, `pytest -n 4` is safe and providers can be swapped freely. This is
the single fixture that makes the entire DI / providers pattern work
under parallelism.

### Rules

- [ ] **P0** **One autouse fixture** per test session that:
      (a) redirects all home-dir / config paths to `tmp_path`,
      (b) sets dummy API keys and tokens,
      (c) re-initializes the providers / DI registry,
      (d) (optionally) clears any process-global caches.
- [ ] **P0** Verify isolation: a single test that mutates global state
      should NOT affect a subsequent test in any order, with `-n 4`.
      If it does, the fixture is incomplete.

### 17.12 Caveman ULTRA / LITE compression modes (output-side)

**Pattern.** Two named compression policies in the agent system prompt:
**CAVEMAN_ULTRA** (inter-agent communication — drop articles,
no full sentences, max-density bullets) and **CAVEMAN_LITE** (synthesis
phase — slightly more readable, still highly compressed). Reported
65–75 % output-token reduction.

**Why.** RTK / context-filter compresses the *input* (tool output);
CAVEMAN compresses the *output* (the agent's own writing). They stack.
Most projects skip the output-side compression and pay 2–3× more on
agent generations.

### Rules

- [ ] **P1** Inter-agent (machine-only) prose runs under an explicit
      compression policy in the system prompt. Drop articles, prefer
      bullets, prefer file:line refs over restated context.
- [ ] **P1** Final-synthesis prose runs under a *lighter* policy — humans
      read it. Keep agent-to-agent and agent-to-human registers
      different.
- [ ] **P2** When using a Claude Code CLI subprocess, ensure the
      compression policy reaches the agent via `--append-system-prompt`,
      not buried in the user-message body. Otherwise the agent treats
      it as advisory and ignores it.

---

## 18. Distilled Research Findings

One-paragraph essence of each source research thread.

### LLM performance
**Empirical synthesis (≈30 papers, 2024-2026).** Python is 2-10× any other
language on agentic benchmarks. Scaffold choice explains more variance
than model choice. Plan-then-execute beats ReAct by 12+ pp; best-of-N
with execution-based filter adds another 6-12 pp; extended thinking adds
+6-22 pp on capable models. Multi-agent debate with homogeneous models
is cargo-cult. Plain TDD prompting is harmful without dep-graph context.
SWE-bench Verified is contaminated above 80 %; switch to SWE-bench Pro
for new measurements.

### Quality guardrails
**Top 3 next-step guardrails (each <2h, blocks a real LLM failure mode):**
(1) Vulture dead-code gate + assertion-density script; (2) `lint-imports`
in CI; (3) GH Action
SHA-pinning + gitleaks pre-commit + pip-audit. PR-A shipped this
config-trifecta; CODEOWNERS for `.github/workflows/`, `vision.yaml`,
`_providers.py` is the next high-value protection.

### Codebase improvement
**Highest-ROI optimizations for an agent-edited Python codebase as it
grows:** (1) migrate planning calls to native Anthropic + `cache_control`
— charter + codebase overview is identical across 8 sequential agent
calls; pure cache-miss waste. (2) Strangler-fig split monolith
orchestrator (>3K LoC, CCN ~100). (3) Populate real `vision.yaml` —
fallback example escalates everything, defeating the proxy.

### Autonomous-factory architecture
**Greenfield-DF stack:** TypeScript app + Python orchestration **beats
.NET**. AI agents produce higher-quality TS than C#; GitHub Actions is
Node-native; agent frameworks (LangChain.js, Vercel AI SDK) stronger.
Architecture is a **GitHub-native state machine** driven by labels on
structured issues. Three-phase rollout: skeleton (issue template +
dispatch workflow, ~100 LoC) → agent dispatch (Python bridge,
worktree isolation) → full autonomy (auto-merge,
constitutional checker).

### Memory and drift
**Three structural problems dominate Claude Code agent performance:**
(1) hallucinated completion claims — synthesis says "shipped" while code
is uncommitted. Add post-agent `git diff` + branch check before
"complete". (2) CLAUDE.md + memory drift — CLAUDE.md describes
pre-refactor monoliths, `project_deploy_gotchas.md` cites stale paths.
Add a CLAUDE.md drift gate on refactor PRs. (3) Plan-replan loop without
a cap — `consecutive_plan_failures ≥ N → halt`. Tool selection itself
is correct (no `Bash grep` when `Grep` is available); the tax is on
**missing context, missing skills, stale documentation**.

### Agent performance retro
**Context handover is structurally broken.** 32 KB synthesis truncated
to 1.5 KB for the next invocation; inter-team handover gets
double-truncated (1500 → 2000 cap); agent YAML outputs in `agents/` are
never re-injected. **23.4 % of orchestrator plans fail JSON parsing**
→ 108 min + 67 K tokens wasted. **41 % needs_input rate is partially
self-inflicted** (preprocessor heuristic gate skips spec enrichment for
messages > 120 chars; truncation forces agents to ask questions already
answered). Fix: smart-truncate by section, re-inject agent YAMLs,
provider-detect before `response_format`, replace length gate with
vagueness score.

### Token saving
**You already have the two biggest wins integrated.** RTK-style filtering
in `context_filter.py` and Caveman ULTRA/LITE compression cover ~70 % of
the achievable reduction. Top 3 incremental: (1) caveman CLI fix —
ensure CAVEMAN_ULTRA reaches the agent's actual system prompt, not the
`<instructions>` block; +3-4 %. (2) `token-optimizer-mcp` for cross-agent
file-read caching; +9-10 %. (3) `context-mode` semantic chunker for
file-read-heavy runs; +28 % at 5-day cost. **Critical: usage tracking
underreports input tokens by 3-10× on the `claude -p` path.** Fix
tracking before measuring further.

### Repository structure for LLMs
**No dedicated agentic-coding language exists.** The wins come from how
you structure existing Python/TS, not from switching. Top resources:
**Repomix** (XML output, tree-sitter ~70 % token reduction);
**awesome-cursorrules** (FastAPI + React+TS rule sets); **AGENTS.md
standard** (Linux Foundation, cross-tool); **Addy Osmani's spec-first
+ example-priming**; **`addyosmani/agent-skills`** (SKILL.md format).
Decompose monoliths via "method-bag" mixins (no `__init__`, pure method
containers; sidesteps cooperative-init traps).

### Claude-native conventions
**The "inner Claude fight" is a prompt-position bug.** Custom
implementations inject system text inside the **user message body** as
XML; Claude treats those as lower-authority requests. Fix:
`--append-system-prompt`. Replace `--dangerously-skip-permissions` with
`--permission-mode auto`. Omit `--allowedTools` when empty. Use the
native `Agent` tool with `isolation: "worktree"`. Prefer `EnterPlanMode`,
`AskUserQuestion`, `TodoWrite` over custom equivalents. MCP tool schemas
deferred via `ToolSearch`. Skills are invoked behaviors; charters are
orchestration patterns — keep both, don't conflate.

### Static linters
**Two CRITICAL pre-commit + worktree blockers must land *before* any
tightening:** (1) `ruff --fix` in pre-commit mutates files inside
`.worktrees/` and aborts agent commits — agent silently moves on,
wastes turns. Drop `--fix`; pre-commit is read-only. Set `SKIP=
mypy,tsc,eslint` on agent worktree commits. (2) `on-failure.yml`
concurrency key is per-SHA → a persistent false-positive lint rule
generates **unbounded** maintenance tasks. Add a 30-min rate-limit guard.
Then ship: coverage gate (baseline − 5 %), Vitest+RTL+MSW, ruff `S`+`B`,
Dependabot, branch-prefix-gated auto-merge.

---

## 19. Reference Bibliography

Every claim above traces to one of these. **Tagged dates are when the
finding was published; some leaderboard numbers move over time.**

### Benchmarks & papers
- **SWE-bench / SWE-bench Verified** — Princeton NLP, ICLR 2024 (arXiv 2310.06770); OpenAI Verified Aug 2024.
- **SWE-bench Pro** — Scale AI, Sep 2025 (arXiv 2509.16941). Gold standard post-Verified contamination findings.
- **Multi-SWE-bench** — ByteDance Seed, Apr 2025 (arXiv 2504.02605). Per-language pass rates across 8 languages.
- **SWE-PolyBench** — Amazon Science, Apr 2025 (arXiv 2504.08703).
- **AGENTS.md evaluation** — arXiv 2602.11988, Feb 2026. +20 % / −3 % quality finding.
- **"When Names Disappear"** — arXiv 2510.03178, 2025. Naming = load-bearing scaffolding.
- **TDAD: Test-Driven Agentic Development** — arXiv 2603.17973, 2026. +8 pp resolution, −70 % regression *with* dep graph; harmful without.
- **Agentless** — UIUC, Jul 2024 (arXiv 2407.01489). Best-of-N + execution-based selection.
- **AutoCodeRover** — ISSTA 2024 (arXiv 2404.05427). AST localize + repair.
- **OpenHands CodeAct 2.1** — Nov 2025. 72 % SWE-bench Verified with extended thinking.
- **Scaling Test-Time Compute (RTV/PDR)** — Kim et al., Apr 2026 (arXiv 2604.16529). +6.7 / +12.2 pp.
- **METR Time Horizon 1.0/1.1** — Mar 2025 / Jan 2026. Task-length doubling time.
- **METR Developer Productivity RCT** — Jul 2025 (arXiv 2507.09089). Experienced devs in large codebases: AI 19 % slower.
- **OpenAI: Why we no longer evaluate SWE-bench Verified** — Feb 2026. 59.4 % of audited problems flawed.
- **Anthropic: Building Effective Agents** — Dec 2024.
- **Cognition: Don't Build Multi-Agents** — Jun 2025.
- **Sonar: Coding Personalities of Leading LLMs** — Aug 2025. >90 % of LLM issues are code smells.
- **Chroma: Context Rot** — 2025. ~40 % degradation when window fills.

### Tools & resources
- [Anthropic Claude Code Best Practices](https://code.claude.com/docs/en/best-practices)
- [AGENTS.md Standard](https://agents.md/) — Linux Foundation / AAIF
- [HumanLayer: Writing a good CLAUDE.md](https://www.humanlayer.dev/blog/writing-a-good-claude-md)
- [Addy Osmani: My LLM coding workflow into 2026](https://addyosmani.com/blog/ai-coding-workflow/)
- [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills)
- [VoltAgent/awesome-agent-skills](https://github.com/VoltAgent/awesome-agent-skills) (16k★)
- [PatrickJS/awesome-cursorrules](https://github.com/PatrickJS/awesome-cursorrules) (39k★)
- [Repomix](https://github.com/yamadashy/repomix) (23k★) — XML output, tree-sitter compression
- [Aider repo-map](https://aider.chat/docs/repomap.html) — PageRank symbol graph
- [Augment Code: Context Engineering](https://www.augmentcode.com/guides/context-engineering-enhancing-agentic-swarm-coding-through-intent-environment-and-system-memory)
- ruff: <https://docs.astral.sh/ruff/>
- mypy: <https://mypy.readthedocs.io>
- bandit: <https://bandit.readthedocs.io>
- vulture: <https://github.com/jendrikseipp/vulture>
- knip: <https://knip.dev>
- lizard: <https://github.com/terryyin/lizard>
- xenon: <https://github.com/rubik/xenon>
- import-linter: <https://import-linter.readthedocs.io>
- gitleaks: <https://github.com/gitleaks/gitleaks>
- pip-audit: <https://github.com/pypa/pip-audit>
- SonarCloud: <https://sonarcloud.io>

---

## 20. Quick-Adoption Order

If you adopt nothing else, ship in **this** order. Each step lands a
gate; together they cover the documented LLM failure modes.

1. **CLAUDE.md** at repo root, hand-written, 60–150 lines, includes
   exact commands + active CCN/coverage gates + `don't` list.
2. **Lockfiles** (`uv.lock`, `package-lock.json`) + CI uses
   `uv sync --locked` / `npm ci`. Concurrency groups on every workflow.
3. **ruff** with `["E","F","I","S","B","BLE","RET","PT","ANN"]`, **mypy
   `strict = true`**, **`pyright` strict**, **bandit**, **vulture**.
4. **TS:** `strict: true`, `noUnusedLocals`, `noUnusedParameters`,
   ESLint `no-floating-promises`/`no-explicit-any`/`no-unused-vars` at
   `error`, complexity `["error", 8]`, **knip**, blocking `jscpd`
   (threshold 1) and `dependency-cruiser`.
5. **pytest -n 4 --dist=worksteal** (CI the same `-n 4`), branch coverage
   `fail_under = 100` (Alan default; original guidance 90 %), isolated
   app home directory per test.
6. **lizard `--CCN 7 --length 60`** on hot paths (Alan default);
   **xenon `--max-modules A --max-average A`** on rest;
   **import-linter** layered contract.
7. **SonarCloud blocking gate** (Auto-Analysis off, coverage uploaded
   from CI).
8. **Pre-commit read-only** + `SKIP=mypy,tsc,eslint` on worktree commits;
   **gitleaks**.
9. **GH Action SHA-pinning** + **CODEOWNERS** + **`pip-audit`**.
10. **Smart-truncate by paragraph + section-priority** for handover;
    **re-inject prior agent YAMLs**; **vagueness score** preprocessor.
11. **`--append-system-prompt`** (not `--system-prompt`),
    **`--permission-mode auto`** (not `--dangerously-skip-permissions`),
    omit `--allowedTools` when empty.
12. **Worktree isolation** for code-changing agents
    (`isolation: "worktree"`); branch verification before edit;
    post-agent `git diff` + "complete" only after a real diff.
13. **Auto-PR** on green pipeline. **No auto-merge** until you've
    calibrated proxy decisions for ≥ 10–20 tasks with `dry_run: true`.
14. **CLAUDE.md drift gate** + **plan-replan loop cap** + **memory-citation
    SHA pinning**.

— end —
