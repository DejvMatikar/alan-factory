export interface ShortcutHintProps {
  readonly glyph: string;
  readonly spoken: string;
}

export default function ShortcutHint({ glyph, spoken }: Readonly<ShortcutHintProps>) {
  return (
    <span>
      <span aria-hidden="true" className="ml-1 opacity-90 text-xs">
        {glyph}
      </span>
      <span className="sr-only">, shortcut {spoken}</span>
    </span>
  );
}
