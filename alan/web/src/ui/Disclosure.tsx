import { useState, type ReactNode } from "react";

const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60";

const SUMMARY_PRESETS = {
  panel:
    "flex min-h-11 w-fit cursor-pointer list-none items-center gap-2 text-left text-sm font-medium text-text-muted hover:text-text-primary [&::-webkit-details-marker]:hidden",
  panelXs:
    "flex min-h-11 w-fit cursor-pointer list-none items-center gap-2 text-left text-xs font-medium text-text-muted hover:text-text-primary [&::-webkit-details-marker]:hidden",
  markdown:
    "flex min-h-11 cursor-pointer list-none items-center gap-2 text-sm font-medium text-text-primary [&::-webkit-details-marker]:hidden",
  group:
    "flex cursor-pointer list-none items-center gap-2 text-sm font-medium [&::-webkit-details-marker]:hidden",
  badge:
    "inline-flex cursor-pointer list-none [&::-webkit-details-marker]:hidden items-center gap-1",
  row: "cursor-pointer font-medium text-text-primary flex list-none items-baseline gap-1 [&::-webkit-details-marker]:hidden",
  rowMuted:
    "cursor-pointer text-xs text-text-muted flex list-none items-baseline gap-1 [&::-webkit-details-marker]:hidden",
} as const;

export type DisclosurePreset = keyof typeof SUMMARY_PRESETS;

function isDisclosureActivationKey(key: string): boolean {
  return key === "Enter" || key === " ";
}

export interface DisclosureProps {
  readonly preset: DisclosurePreset;
  readonly defaultOpen: boolean;
  readonly open?: boolean | undefined;
  readonly onOpenChange?: ((open: boolean) => void) | undefined;
  readonly label: ReactNode;
  readonly children?: ReactNode;
  readonly className?: string | undefined;
  readonly testId?: string | undefined;
  readonly summaryTestId?: string | undefined;
}

export default function Disclosure({
  preset,
  defaultOpen,
  open,
  onOpenChange,
  label,
  children,
  className,
  testId,
  summaryTestId,
}: Readonly<DisclosureProps>) {
  const [ownOpen, setOwnOpen] = useState(defaultOpen);
  const isOpen = open ?? ownOpen;

  const commit = (next: boolean) => {
    if (next === isOpen) return;
    setOwnOpen(next);
    onOpenChange?.(next);
  };

  return (
    <details
      className={className}
      data-testid={testId}
      open={isOpen}
      onToggle={(event) => {
        commit(event.currentTarget.open);
      }}
    >
      <summary
        className={`${SUMMARY_PRESETS[preset]} ${FOCUS_RING}`}
        data-testid={summaryTestId}
        onKeyDown={(event) => {
          if (!isDisclosureActivationKey(event.key)) return;
          event.preventDefault();
          commit(!isOpen);
        }}
      >
        <span aria-hidden="true">{isOpen ? "▾" : "▸"}</span>
        {label}
      </summary>
      {children}
    </details>
  );
}
