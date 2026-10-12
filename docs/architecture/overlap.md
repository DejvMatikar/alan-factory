# Overlap detection

## Purpose

`alan/overlap.py` is the pure comparison core behind the sibling file-overlap failure class: a subtask silently touching files outside the manifest it declared when the task was split (or planned). It is path-normalization + set math only — no I/O, no `TeamEvent` import, no external calls. Callers (verification's manifest check, the post-merge sibling-overlap audit) own emission of findings/events; this module only answers "which files are out of scope."

## Entry points

```python
find_out_of_manifest(diff_files: Sequence[str], manifest: Sequence[str]) -> list[str]
find_sibling_overlap(
    diff_files: Sequence[str], sibling_manifests: Mapping[str, Sequence[str]]
) -> dict[str, list[str]]
```

- `find_out_of_manifest` returns the subset of `diff_files` not covered by any entry in `manifest`. An empty (or entirely-blank) `manifest` is treated as "no declared manifest" and always returns `[]` — the guardrail stays silent rather than flagging every file as out of scope.
- `find_sibling_overlap` maps each sibling task id to the `diff_files` that fall within *that sibling's* declared manifest. Siblings with an empty manifest are skipped; siblings with no overlapping files are omitted from the result.

## Path normalization

Declared manifests can name either a directory (the decomposer's typical output, e.g. `alan/web/src/chooser/`) or a specific file (e.g. `alan/x.py`), while diff files are always concrete file paths (e.g. `alan/web/src/chooser/Foo.tsx`). Both sides are normalized the same way before comparison:

- strip a leading `./`
- strip leading/trailing `/`

A diff file is "covered" by a declared path when it matches exactly, or when it starts with `declared + "/"` — so a declared directory covers every file beneath it regardless of a trailing slash, and naming a folder vs. a file inside it never produces a false alarm or a missed match.

## Callers (not part of this module)

- Verification's manifest check computes `find_out_of_manifest(pr_diff_files, task.files)` and surfaces any hit as a finding the judge weighs — never a deterministic auto-block.
- The post-merge sibling-overlap audit computes `find_sibling_overlap` for a just-merged task's diff against its siblings' manifests, purely advisory (`TeamEvent` + `impl_audit.jsonl`), off the merge-gate hot path.

## Import-linter layer

`overlap` sits between `compression` and `terminal_outcome` in the layered-architecture contract (`pyproject.toml`): below `teams` (verification, which lives under `teams`, imports it) and above `terminal_outcome`/`workspace` (it imports nothing from `alan`, so it could sit anywhere below `teams`; this slot keeps it next to the other small, focused post-processing helpers).
