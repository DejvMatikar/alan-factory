import type { KeyboardEvent } from "react";

import { matchShortcut } from "../lib/shortcuts";

export interface ModalKeyHandlers {
  onEscape: () => void;
  onCtrlEnter: () => void;
}

export function handleModalKeyDown(
  e: KeyboardEvent<HTMLElement>,
  { onEscape, onCtrlEnter }: ModalKeyHandlers,
): void {
  if (e.key === "Escape") {
    e.stopPropagation();
    e.preventDefault();
    onEscape();
  } else if (matchShortcut(e.nativeEvent, ["modal-confirm"]) !== null) {
    e.stopPropagation();
    e.preventDefault();
    onCtrlEnter();
  }
}
