import type { KeyboardEvent, ReactNode } from "react";
import { useRef, useState } from "react";

import {
  ACCENT_COLORS,
  ACCENT_INTENSITIES,
  api,
  type AccentChoice,
  type AccentColor,
  type AccentIntensity,
} from "../../../api";
import { errorMessageOf } from "../../../lib/errorMessage";
import { accentEdgeClass, accentTextClass, accentVars, resolveAccents, type ResolvedAccent } from "../../../lib/repoAccent";
import { useAction } from "../../../ui/useAction";
import { useEngineResource } from "../../../hooks/useEngineResource";
import { useEngines } from "../../../hooks/useEngines";
import AccentTile from "./AccentTile";
import Button from "../../../components/Button";

interface Draft {
  readonly automatic: boolean;
  readonly color: AccentColor | null;
  readonly intensity: AccentIntensity;
}

const AUTOMATIC_DRAFT: Draft = { automatic: true, color: null, intensity: "standard" };
const PALETTE: readonly (AccentColor | null)[] = [null, ...ACCENT_COLORS];

const INTENSITY_LABEL: Record<AccentIntensity, string> = {
  soft: "Soft",
  standard: "Standard",
  bold: "Bold",
};

function draftFromAccent(accent: AccentChoice | null): Draft {
  if (accent === null) return AUTOMATIC_DRAFT;
  return { automatic: false, color: accent.color ?? null, intensity: accent.intensity };
}

function draftToAccent(draft: Draft): AccentChoice | null {
  return draft.automatic ? null : { color: draft.color, intensity: draft.intensity };
}

function draftsEqual(a: Draft, b: Draft): boolean {
  if (a.automatic || b.automatic) return a.automatic === b.automatic;
  if (a.color !== b.color) return false;
  return a.color === null || a.intensity === b.intensity;
}

function arrowDelta(key: string): number {
  if (key === "ArrowRight" || key === "ArrowDown") return 1;
  if (key === "ArrowLeft" || key === "ArrowUp") return -1;
  return 0;
}

function previewFor(draft: Draft, automaticPreview: ResolvedAccent | null): ResolvedAccent | null {
  if (draft.automatic) return automaticPreview;
  if (draft.color === null) return null;
  return { color: draft.color, intensity: draft.intensity };
}

function takenColorsExcluding(
  accents: ReadonlyMap<string, ResolvedAccent | null>,
  engineKey: string,
): ReadonlySet<AccentColor> {
  const taken = new Set<AccentColor>();
  for (const [key, resolved] of accents) {
    if (key === engineKey || resolved === null) continue;
    taken.add(resolved.color);
  }
  return taken;
}

function isDirty(committed: AccentChoice | null, draft: Draft): boolean {
  return !draftsEqual(draft, draftFromAccent(committed));
}

function isTileSelected(draft: Draft, color: AccentColor | null): boolean {
  return !draft.automatic && draft.color === color;
}

function isTileTaken(takenColors: ReadonlySet<AccentColor>, color: AccentColor | null): boolean {
  return color !== null && takenColors.has(color);
}

function intensityDisabled(draft: Draft, saving: boolean): boolean {
  return saving || draft.automatic || draft.color === null;
}

function resetDisabled(draft: Draft, saving: boolean): boolean {
  return saving || draft.automatic;
}

function saveDisabled(dirty: boolean, saving: boolean): boolean {
  return saving || !dirty;
}

function saveButtonLabel(saving: boolean): string {
  return saving ? "Saving…" : "Save accent";
}

function AccentPickerShell({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <section className="space-y-3" data-testid="accent-picker">
      <h3 className="text-sm font-medium text-text-primary">Accent colour</h3>
      {children}
    </section>
  );
}

function AccentPickerLoading() {
  return (
    <p className="text-sm text-text-muted" data-testid="accent-picker-loading">
      Loading…
    </p>
  );
}

function AccentPickerError({ message }: Readonly<{ message: string }>) {
  return (
    <p role="alert" className="text-sm text-btn-destroy" data-testid="accent-picker-error">
      {message}
    </p>
  );
}

function AccentPickerUnsaved({ dirty }: Readonly<{ dirty: boolean }>) {
  if (!dirty) return null;
  return (
    <p className="text-xs text-text-muted" data-testid="accent-picker-unsaved">
      Unsaved changes
    </p>
  );
}

function AccentPickerSaveError({ message }: Readonly<{ message: string | null }>) {
  if (message === null) return null;
  return (
    <p role="alert" className="text-sm text-btn-destroy" data-testid="accent-picker-save-error">
      {message}
    </p>
  );
}

interface AccentSwatchGridProps {
  readonly draft: Draft;
  readonly takenColors: ReadonlySet<AccentColor>;
  readonly disabled: boolean;
  readonly onSelect: (color: AccentColor | null) => void;
}

function AccentSwatchGrid({ draft, takenColors, disabled, onSelect }: Readonly<AccentSwatchGridProps>) {
  const tileRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const selectedIndex = PALETTE.findIndex((color) => isTileSelected(draft, color));
  const activeIndex = selectedIndex === -1 ? 0 : selectedIndex;

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    const delta = arrowDelta(event.key);
    if (delta === 0) return;
    event.preventDefault();
    const nextIndex = (activeIndex + delta + PALETTE.length) % PALETTE.length;
    tileRefs.current[nextIndex]?.focus();
    // nextIndex is computed mod PALETTE.length, so it is always in range —
    // noUncheckedIndexedAccess still types the access as possibly undefined.
    onSelect(PALETTE[nextIndex]!);
  };

  return (
    <div
      role="radiogroup"
      aria-label="Accent colour"
      className="flex flex-wrap gap-2"
      data-testid="accent-picker-swatches"
      tabIndex={-1}
      onKeyDown={handleKeyDown}
    >
      {PALETTE.map((color, index) => (
        <AccentTile
          key={color ?? "none"}
          ref={(el) => {
            tileRefs.current[index] = el;
          }}
          color={color}
          selected={isTileSelected(draft, color)}
          taken={isTileTaken(takenColors, color)}
          disabled={disabled}
          tabIndex={index === activeIndex ? 0 : -1}
          onSelect={() => {
            onSelect(color);
          }}
        />
      ))}
    </div>
  );
}

interface IntensityControlProps {
  readonly intensity: AccentIntensity;
  readonly disabled: boolean;
  readonly onChange: (intensity: AccentIntensity) => void;
}

function IntensityControl({ intensity, disabled, onChange }: Readonly<IntensityControlProps>) {
  const buttonRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const activeIndex = ACCENT_INTENSITIES.indexOf(intensity);

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    const delta = arrowDelta(event.key);
    if (delta === 0) return;
    event.preventDefault();
    const nextIndex = (activeIndex + delta + ACCENT_INTENSITIES.length) % ACCENT_INTENSITIES.length;
    buttonRefs.current[nextIndex]?.focus();
    // nextIndex is computed mod ACCENT_INTENSITIES.length, so it is always in
    // range — noUncheckedIndexedAccess still types the access as optional.
    onChange(ACCENT_INTENSITIES[nextIndex]!);
  };

  return (
    <div
      role="radiogroup"
      aria-label="Accent intensity"
      className="inline-flex gap-1"
      data-testid="accent-picker-intensity"
      tabIndex={-1}
      onKeyDown={handleKeyDown}
    >
      {ACCENT_INTENSITIES.map((value, index) => (
        <button
          key={value}
          ref={(el) => {
            buttonRefs.current[index] = el;
          }}
          type="button"
          role="radio"
          aria-checked={intensity === value}
          aria-label={`${INTENSITY_LABEL[value]} intensity`}
          disabled={disabled}
          tabIndex={value === intensity ? 0 : -1}
          data-testid={`accent-picker-intensity-${value}`}
          onClick={() => {
            onChange(value);
          }}
          className={`min-h-11 rounded-md border px-3 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60 disabled:opacity-50 disabled:cursor-not-allowed ${
            intensity === value
              ? "border-text-primary bg-surface-elevated text-text-primary"
              : "border-border text-text-muted hover:bg-surface-elevated"
          }`}
        >
          {INTENSITY_LABEL[value]}
        </button>
      ))}
    </div>
  );
}

function AccentPreview({ resolved }: Readonly<{ resolved: ResolvedAccent | null }>) {
  return (
    <div
      style={accentVars(resolved)}
      data-testid="accent-picker-preview"
      className={`space-y-1 rounded-md border-2 bg-surface p-3 ${accentEdgeClass(resolved)}`}
    >
      <p className="text-xs text-text-muted">Preview</p>
      <h4 className={`text-base font-semibold ${accentTextClass(resolved)}`}>Repository name</h4>
    </div>
  );
}

export interface AccentPickerProps {
  readonly engineKey: string;
}

export default function AccentPicker({ engineKey }: Readonly<AccentPickerProps>) {
  const { state } = useEngineResource("engineConfig", engineKey);
  const { allEngines } = useEngines();
  const [seeded, setSeeded] = useState(false);
  const [committed, setCommitted] = useState<AccentChoice | null>(null);
  const [draft, setDraft] = useState<Draft>(AUTOMATIC_DRAFT);
  const { busy: saving, run } = useAction();
  const [saveError, setSaveError] = useState<string | null>(null);

  if (state.status === "ready" && !seeded) {
    const loaded = state.data.accent ?? null;
    setSeeded(true);
    setCommitted(loaded);
    setDraft(draftFromAccent(loaded));
  }

  if (state.status === "loading") {
    return (
      <AccentPickerShell>
        <AccentPickerLoading />
      </AccentPickerShell>
    );
  }
  if (state.status === "error") {
    return (
      <AccentPickerShell>
        <AccentPickerError message={state.message} />
      </AccentPickerShell>
    );
  }

  const accents = resolveAccents(allEngines);
  const takenColors = takenColorsExcluding(accents, engineKey);
  const automaticPreview = accents.get(engineKey) ?? null;
  const preview = previewFor(draft, automaticPreview);
  const dirty = isDirty(committed, draft);

  const handleSave = (): void => {
    setSaveError(null);
    run(() => api.setEngineAccent(engineKey, draftToAccent(draft))).then(
      (detail) => {
        const next = detail.accent ?? null;
        setCommitted(next);
        setDraft(draftFromAccent(next));
      },
      (err: unknown) => {
        setSaveError(errorMessageOf(err));
        setDraft(draftFromAccent(committed));
      },
    );
  };

  return (
    <AccentPickerShell>
      <AccentSwatchGrid
        draft={draft}
        takenColors={takenColors}
        disabled={saving}
        onSelect={(color) => {
          setDraft((prev) => ({ automatic: false, color, intensity: prev.intensity }));
        }}
      />
      <div className="flex flex-wrap items-center gap-3">
        <IntensityControl
          intensity={draft.intensity}
          disabled={intensityDisabled(draft, saving)}
          onChange={(intensity) => {
            setDraft((prev) => ({ ...prev, intensity }));
          }}
        />
        <Button
          variant="ghost"
          type="button"
          data-testid="accent-picker-reset"
          disabled={resetDisabled(draft, saving)}
          onClick={() => {
            setDraft(AUTOMATIC_DRAFT);
          }}
        >
          Use automatic colour
        </Button>
      </div>
      <AccentPreview resolved={preview} />
      <AccentPickerUnsaved dirty={dirty} />
      <AccentPickerSaveError message={saveError} />
      <Button
        variant="action"
        type="button"
        data-testid="accent-picker-save"
        disabled={saveDisabled(dirty, saving)}
        onClick={handleSave}
      >
        {saveButtonLabel(saving)}
      </Button>
    </AccentPickerShell>
  );
}
