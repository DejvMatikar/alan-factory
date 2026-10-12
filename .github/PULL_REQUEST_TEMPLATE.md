## Summary

<!-- What changed and why. Link the issue or task. -->

## Test plan

- [ ] `uv run pytest` — 100% branch coverage maintained
- [ ] `uv run ruff check . && uv run mypy alan/ tests/`
- [ ] `uv run lizard --CCN 7 --length 60 --languages python --exclude "alan/web/*" alan/`
- [ ] `cd alan/web && npm run lint && npm run test:coverage && npm run e2e`
- [ ] CI green (all required checks)

## Notes for reviewers

<!-- Anything you want to flag: tradeoffs, follow-ups, gates touched. -->
