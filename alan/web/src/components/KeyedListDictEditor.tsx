import Input from "../ui/Input";
import SectionHeading from "../ui/SectionHeading";

export interface KeyedListDictEditorProps {
  label: string;
  testId: string;
  value: Readonly<Record<string, readonly string[]>>;
  onChange: (next: Record<string, string[]>) => void;
  disabled?: boolean;
}

function listToText(list: readonly string[]): string {
  return list.join(", ");
}

function textToList(text: string): string[] {
  return text
    .split(",")
    .map((token) => token.trim())
    .filter((token) => token !== "");
}

// Keys are whatever the loaded config already declares — there is no
// add/remove-key affordance, only each key's value list is editable (down to
// empty). Sorted for a stable render order across saves, independent of the
// source object's (arbitrary) key order.
export default function KeyedListDictEditor({
  label,
  testId,
  value,
  onChange,
  disabled = false,
}: Readonly<KeyedListDictEditorProps>) {
  const rows = Object.entries(value).sort(([a], [b]) => a.localeCompare(b));

  const handleRowChange = (key: string, text: string): void => {
    const next = Object.fromEntries(
      rows.map(([k, list]) => [k, k === key ? textToList(text) : [...list]] as const),
    );
    onChange(next);
  };

  return (
    <div className="space-y-2" data-testid={testId}>
      <SectionHeading level="h4">{label}</SectionHeading>
      {rows.length === 0 && (
        <p className="text-sm text-text-muted" data-testid={`${testId}-empty`}>
          No entries.
        </p>
      )}
      <ul className="space-y-2">
        {rows.map(([key, list]) => {
          const rowId = `${testId}-${key}`;
          return (
            <li key={key} className="space-y-1">
              <label className="text-xs font-medium text-text-muted" htmlFor={rowId}>
                {key}
              </label>
              <Input
                as="input"
                id={rowId}
                data-testid={`${testId}-input-${key}`}
                type="text"
                value={listToText(list)}
                onChange={(e) => {
                  handleRowChange(key, e.target.value);
                }}
                disabled={disabled}
                placeholder="Comma-separated — leave blank for none"
                placeholderStyle
              />
            </li>
          );
        })}
      </ul>
    </div>
  );
}
