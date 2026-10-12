import ShortcutHint, { type ShortcutHintProps } from "./ShortcutHint";

interface Props {
  label: string;
  description: string;
  hint?: ShortcutHintProps;
}

export default function ChooserRow({ label, description, hint }: Readonly<Props>) {
  return (
    <>
      <span className="block text-sm font-medium text-text-primary">
        {label}
        {hint !== undefined && <ShortcutHint glyph={hint.glyph} spoken={hint.spoken} />}
      </span>
      <span className="block text-xs text-text-muted">{description}</span>
    </>
  );
}
