import type { KeyboardEvent } from "react";

export function handleTablistKeyDown(e: KeyboardEvent<HTMLDivElement>): void {
  if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
  const tabs = Array.from(e.currentTarget.querySelectorAll<HTMLElement>('[role="tab"]'));
  const currentIndex = tabs.findIndex((el) => el.getAttribute("aria-selected") === "true");
  if (currentIndex === -1) return;
  e.preventDefault();
  const delta = e.key === "ArrowRight" ? 1 : -1;
  const nextIndex = Math.min(Math.max(currentIndex + delta, 0), tabs.length - 1);
  const nextTabButton = tabs[nextIndex];
  /* v8 ignore next */
  nextTabButton?.focus();
  /* v8 ignore next */
  nextTabButton?.click();
}
