import { useId, type ReactNode } from "react";

import type { NodeDetailTab } from "../../../api";
import Button from "../../../components/Button";
import HoverReveal from "../../../components/HoverReveal";

function GlyphFrame({ viewBox, children }: Readonly<{ viewBox: string; children: ReactNode }>) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox={viewBox}
      fill="currentColor"
      className="h-4 w-4"
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

function OutputGlyph() {
  return (
    <GlyphFrame viewBox="0 0 16 16">
      <path d="M2 4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V4Zm2-1a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1V4a1 1 0 0 0-1-1H4Z" />
      <path d="M5 7a1 1 0 0 1 1-1h4a1 1 0 1 1 0 2H6a1 1 0 0 1-1-1Zm0 3a1 1 0 0 1 1-1h2a1 1 0 1 1 0 2H6a1 1 0 0 1-1-1Z" />
    </GlyphFrame>
  );
}

function PromptGlyph() {
  return (
    <GlyphFrame viewBox="0 0 20 20">
      <path
        fillRule="evenodd"
        d="M10 2c-2.236 0-4.43.18-6.57.524C1.993 2.755 1 4.014 1 5.426v5.148c0 1.413.993 2.67 2.43 2.902 1.168.188 2.352.327 3.55.414.28.02.521.18.642.413l1.713 3.293a.75.75 0 0 0 1.33 0l1.713-3.293a.783.783 0 0 1 .642-.413 41.102 41.102 0 0 0 3.55-.414c1.437-.231 2.43-1.49 2.43-2.902V5.426c0-1.413-.993-2.67-2.43-2.902A41.289 41.289 0 0 0 10 2ZM6.75 6a.75.75 0 0 0 0 1.5h6.5a.75.75 0 0 0 0-1.5h-6.5Zm0 2.5a.75.75 0 0 0 0 1.5h3.5a.75.75 0 0 0 0-1.5h-3.5Z"
        clipRule="evenodd"
      />
    </GlyphFrame>
  );
}

function ConfigGlyph() {
  return (
    <GlyphFrame viewBox="0 0 16 16">
      <path
        fillRule="evenodd"
        d="M6.955 1.45A.5.5 0 0 1 7.443 1h1.114a.5.5 0 0 1 .487.38l.28 1.117a5.03 5.03 0 0 1 .948.542l1.085-.396a.5.5 0 0 1 .577.205l.557.964a.5.5 0 0 1-.083.608l-.81.794a5.1 5.1 0 0 1 0 1.072l.81.795a.5.5 0 0 1 .083.608l-.557.964a.5.5 0 0 1-.577.205l-1.085-.396a5.03 5.03 0 0 1-.948.542l-.28 1.117a.5.5 0 0 1-.487.38H7.443a.5.5 0 0 1-.488-.38l-.28-1.117a5.03 5.03 0 0 1-.947-.542l-1.086.396a.5.5 0 0 1-.577-.205l-.557-.964a.5.5 0 0 1 .083-.608l.81-.795a5.11 5.11 0 0 1 0-1.072l-.81-.794a.5.5 0 0 1-.083-.608l.557-.964a.5.5 0 0 1 .577-.205l1.086.396a5.03 5.03 0 0 1 .947-.542l.28-1.117ZM8 10a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z"
        clipRule="evenodd"
      />
    </GlyphFrame>
  );
}

interface CardIconSpec {
  readonly tab: NodeDetailTab;
  readonly description: string;
  readonly Glyph: () => ReactNode;
}

const CARD_ICONS: readonly CardIconSpec[] = [
  { tab: "output", description: "See what it produced", Glyph: OutputGlyph },
  { tab: "prompt", description: "See the instructions used", Glyph: PromptGlyph },
  { tab: "config", description: "See the model and settings", Glyph: ConfigGlyph },
];

interface StepCardIconsProps {
  readonly label: string;
  readonly onOpen: (tab: NodeDetailTab) => void;
}

export function StepCardIcons({ label, onOpen }: Readonly<StepCardIconsProps>) {
  const baseId = useId();
  return (
    <span
      data-testid="step-card-icons"
      className="nodrag nopan flex items-center justify-end gap-1 px-2 pb-2 pt-1"
    >
      {CARD_ICONS.map(({ tab, description, Glyph }) => (
        <HoverReveal key={tab} id={`${baseId}-${tab}`} description={description} align="end">
          <Button
            variant="action"
            iconOnly
            density="compact"
            aria-label={`View ${tab} of ${label}`}
            data-testid={`icon-${tab}`}
            onClick={(e) => {
              e.stopPropagation();
              onOpen(tab);
            }}
          >
            <Glyph />
          </Button>
        </HoverReveal>
      ))}
    </span>
  );
}
