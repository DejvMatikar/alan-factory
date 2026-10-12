import type { Page } from "@playwright/test";
import { expect } from "@playwright/test";

interface LayeringArgs {
  revealId: string;
  beneathSelector: string;
}

type LayeringReason =
  | "measured"
  | "reveal-missing"
  | "reveal-closed"
  | "no-overlap"
  | "hit-test-empty";

interface LayeringMeasurement {
  reason: LayeringReason;
  revealIndex: number;
  beneathIndex: number;
  beneathHasSticky: boolean;
}

function measureLayering({ revealId, beneathSelector }: LayeringArgs): LayeringMeasurement {
  const unresolved = (reason: LayeringReason): LayeringMeasurement => ({
    reason,
    revealIndex: -1,
    beneathIndex: -1,
    beneathHasSticky: false,
  });
  const revealEl = document.getElementById(revealId);
  if (revealEl === null) return unresolved("reveal-missing");
  if (revealEl.classList.contains("sr-only")) return unresolved("reveal-closed");
  const revealRect = revealEl.getBoundingClientRect();
  const beneathEl = Array.from(document.querySelectorAll(beneathSelector)).find((candidate) => {
    const rect = candidate.getBoundingClientRect();
    return (
      rect.left < revealRect.right &&
      rect.right > revealRect.left &&
      rect.top < revealRect.bottom &&
      rect.bottom > revealRect.top
    );
  });
  if (beneathEl === undefined) return unresolved("no-overlap");
  const beneathRect = beneathEl.getBoundingClientRect();
  const x =
    (Math.max(revealRect.left, beneathRect.left) + Math.min(revealRect.right, beneathRect.right)) / 2;
  const y =
    (Math.max(revealRect.top, beneathRect.top) + Math.min(revealRect.bottom, beneathRect.bottom)) / 2;
  const stack = document.elementsFromPoint(x, y);
  if (stack.length === 0) return unresolved("hit-test-empty");
  return {
    reason: "measured",
    revealIndex: stack.findIndex((el) => el === revealEl || el.closest(`[id="${revealId}"]`) !== null),
    beneathIndex: stack.findIndex((el) => el === beneathEl || el.closest(beneathSelector) !== null),
    beneathHasSticky: beneathEl.classList.contains("sm:sticky"),
  };
}

async function measureOnce(
  page: Page,
  revealId: string,
  beneathSelector: string,
  arm: () => Promise<void>,
): Promise<LayeringMeasurement> {
  await page.evaluate((id) => {
    document.getElementById(id)?.scrollIntoView({ block: "center" });
  }, revealId);
  await arm();
  await expect(page.locator(`[id="${revealId}"]`)).not.toHaveClass(/sr-only/, { timeout: 2_000 });
  return page.evaluate(measureLayering, { revealId, beneathSelector });
}

async function expectLayering(
  page: Page,
  revealId: string,
  beneathSelector: string,
  arm: () => Promise<void>,
  requireSticky: boolean,
): Promise<void> {
  const beneath = page.locator(beneathSelector).first();
  await expect(beneath).toBeVisible();

  let result: LayeringMeasurement | undefined;
  await expect(async () => {
    const attempt = await measureOnce(page, revealId, beneathSelector, arm);
    expect(
      attempt.reason,
      `layering of "${revealId}" over "${beneathSelector}" stalled at "${attempt.reason}"`,
    ).toBe("measured");
    result = attempt;
  }).toPass({ timeout: 20_000, intervals: [100, 250, 500, 1_000] });

  const measured = result!;
  expect(
    measured.revealIndex,
    `reveal "${revealId}" not found in the hit-tested stack`,
  ).toBeGreaterThanOrEqual(0);
  expect(
    measured.beneathIndex,
    `no "${beneathSelector}" element overlaps the reveal - vacuous premise`,
  ).toBeGreaterThanOrEqual(0);
  if (requireSticky) {
    expect(measured.beneathHasSticky, `"${beneathSelector}" match does not carry sm:sticky`).toBe(true);
  }
  expect(
    measured.revealIndex,
    `reveal does not sit above "${beneathSelector}" in the hit-tested stack`,
  ).toBeLessThan(measured.beneathIndex);
}

export async function expectRevealAboveSticky(
  page: Page,
  revealId: string,
  beneathSelector: string,
  arm: () => Promise<void>,
): Promise<void> {
  await expectLayering(page, revealId, beneathSelector, arm, true);
}

export async function expectRevealAbove(
  page: Page,
  revealId: string,
  beneathSelector: string,
  arm: () => Promise<void>,
): Promise<void> {
  await expectLayering(page, revealId, beneathSelector, arm, false);
}
