---
paths:
  - "alan/web/**"
---

- jsdom 29 dispatches `PointerEvent` for `element.click()` — don't assert `instanceof MouseEvent` in unit tests (locked in by `alan/web/src/jsdom-runtime.test.ts`).
- Reuse the shared primitives named in `docs/DESIGN_SYSTEM.md` (`Button`, the shared chat primitives, the modal-key convention) rather than hand-rolling new ones.
- A new top-level route/screen must be added to `alan/web/e2e/mobile-audit/screens.ts`, with its counts seeded in both `alan/web/e2e/mobile-audit/baseline.json` and its pin at `alan/web/e2e/mobile-audit/baseline-high-water.ts`, in the same PR — a screen landing with nonzero counts and no pin entry fails the gate (an absent pin entry defaults to zeros), which is intentional.
