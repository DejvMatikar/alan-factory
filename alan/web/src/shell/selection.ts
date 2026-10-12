export const SELECTION_TEXT_SELECTED_CLASS = "text-text-primary";
export const SELECTION_TEXT_UNSELECTED_CLASS = "text-text-muted";
export const SELECTION_HOVER_CLASS = "hover:bg-surface-elevated";
export const SELECTION_FOCUS_CLASS =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60";
export const SELECTION_INTERACTIVE_CLASS =
  "hover:bg-surface-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60";
export const SELECTION_ROW_SELECTED_CLASS = "bg-surface-elevated";

export function selectionTextClass(selected: boolean): string {
  return selected ? SELECTION_TEXT_SELECTED_CLASS : SELECTION_TEXT_UNSELECTED_CLASS;
}

export function ariaCurrentSelection(selected: boolean): "true" | undefined {
  return selected ? "true" : undefined;
}

export function selectionRowSurfaceClass(selected: boolean): string {
  return selected ? SELECTION_ROW_SELECTED_CLASS : "";
}

export const UNSELECTED_PREVIEW_TEXT_CLASS = "text-text-primary";
