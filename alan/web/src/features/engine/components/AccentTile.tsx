import type { CSSProperties } from "react";
import { forwardRef } from "react";

import type { AccentColor } from "../../../api";
import { accentEdgeHex } from "../../../lib/repoAccent";

const ACCENT_COLOR_LABEL: Record<AccentColor, string> = {
  iris: "Iris",
  violet: "Violet",
  plum: "Plum",
  orchid: "Orchid",
  magenta: "Magenta",
  rose: "Rose",
  ruby: "Ruby",
  crimson: "Crimson",
  orange: "Orange",
  amber: "Amber",
  tan: "Tan",
  green: "Green",
  teal: "Teal",
  blue: "Blue",
  slate: "Slate",
  bone: "Bone",
};

export function accentColorLabel(color: AccentColor | null): string {
  return color === null ? "No accent" : ACCENT_COLOR_LABEL[color];
}

function accentTileAriaLabel(color: AccentColor | null, taken: boolean): string {
  const label = accentColorLabel(color);
  return taken ? `${label} — already used by another repository` : label;
}

function swatchStyle(color: AccentColor | null): CSSProperties | undefined {
  return color === null ? undefined : { backgroundColor: accentEdgeHex(color, "standard") };
}

function edgeBorderClass(selected: boolean): string {
  return selected ? "border-text-primary" : "border-border";
}

function boolAttr(value: boolean): "true" | "false" {
  return value ? "true" : "false";
}

export interface AccentTileProps {
  readonly color: AccentColor | null;
  readonly selected: boolean;
  readonly taken: boolean;
  readonly disabled?: boolean;
  readonly tabIndex?: number;
  readonly onSelect: () => void;
}

const AccentTile = forwardRef<HTMLButtonElement, Readonly<AccentTileProps>>(function AccentTile(
  { color, selected, taken, disabled = false, tabIndex, onSelect },
  ref,
) {
  const testKey = color ?? "none";
  return (
    <button
      ref={ref}
      type="button"
      role="radio"
      aria-checked={selected}
      aria-label={accentTileAriaLabel(color, taken)}
      data-testid={`accent-tile-${testKey}`}
      data-selected={boolAttr(selected)}
      data-taken={boolAttr(taken)}
      disabled={disabled}
      tabIndex={tabIndex}
      onClick={onSelect}
      style={swatchStyle(color)}
      className={`relative flex h-11 w-11 shrink-0 items-center justify-center rounded-full border-2 bg-surface transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60 disabled:opacity-50 disabled:cursor-not-allowed ${edgeBorderClass(selected)}`}
    >
      {color === null && (
        <span aria-hidden="true" className="text-xs font-semibold text-text-muted">
          ∅
        </span>
      )}
      {selected && (
        <span
          aria-hidden="true"
          data-testid={`accent-tile-${testKey}-check`}
          className="absolute -bottom-1 -right-1 flex h-4 w-4 items-center justify-center rounded-full border border-border bg-surface text-[10px] leading-none text-text-primary"
        >
          ✓
        </span>
      )}
      {taken && !selected && (
        <span
          aria-hidden="true"
          data-testid={`accent-tile-${testKey}-taken`}
          className="absolute -top-1 -right-1 h-2.5 w-2.5 rounded-full border border-surface bg-text-muted"
        />
      )}
    </button>
  );
});

export default AccentTile;
