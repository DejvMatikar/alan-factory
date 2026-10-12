import { fireEvent, waitFor } from "@testing-library/react";
import { expect } from "vitest";

export type DisclosureActivation = "click" | "Enter" | "Space";

export const DISCLOSURE_ACTIVATIONS: readonly DisclosureActivation[] = ["click", "Enter", "Space"];

export function disclosureSummary(details: HTMLElement): HTMLElement {
  const summary = details.querySelector("summary");
  if (summary === null) throw new Error("summary element not found");
  return summary;
}

export function activateDisclosure(details: HTMLElement, activation: DisclosureActivation): void {
  const summary = disclosureSummary(details);
  if (activation === "click") {
    fireEvent.click(summary);
    return;
  }
  fireEvent.keyDown(summary, { key: activation === "Enter" ? "Enter" : " " });
}

export async function settleDisclosure(): Promise<void> {
  await new Promise<void>((resolve) => {
    setTimeout(resolve, 0);
  });
}

export async function expectDisclosureState(details: HTMLElement, open: boolean): Promise<void> {
  const summary = disclosureSummary(details);
  await waitFor(() => {
    if (open) expect(details).toHaveAttribute("open");
    else expect(details).not.toHaveAttribute("open");
    expect(summary.firstElementChild).toHaveTextContent(open ? "▾" : "▸");
  });
  expect(summary.firstElementChild).toHaveAttribute("aria-hidden", "true");
  expect(summary).not.toHaveAttribute("aria-expanded");
}

export async function expectTogglesOnEveryActivation(details: HTMLElement): Promise<void> {
  const startedOpen = details.hasAttribute("open");
  for (const activation of DISCLOSURE_ACTIVATIONS) {
    activateDisclosure(details, activation);
    await expectDisclosureState(details, !startedOpen);
    await settleDisclosure();
    await expectDisclosureState(details, !startedOpen);
    activateDisclosure(details, activation);
    await expectDisclosureState(details, startedOpen);
    await settleDisclosure();
    await expectDisclosureState(details, startedOpen);
  }
  fireEvent.keyDown(disclosureSummary(details), { key: "Tab" });
  await settleDisclosure();
  await expectDisclosureState(details, startedOpen);
}
