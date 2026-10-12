import Input from "../ui/Input";
import SectionHeading from "../ui/SectionHeading";
import Button from "./Button";

export interface ArgvListEditorProps {
  label: string;
  testId: string;
  value: readonly (readonly string[])[];
  onChange: (next: string[][]) => void;
  disabled?: boolean;
}

function commandToText(command: readonly string[]): string {
  return command.join(" ");
}

function textToCommand(text: string): string[] {
  return text.split(/\s+/).filter((token) => token !== "");
}

export default function ArgvListEditor({
  label,
  testId,
  value,
  onChange,
  disabled = false,
}: Readonly<ArgvListEditorProps>) {
  const handleRowChange = (index: number, text: string): void => {
    onChange(value.map((command, i) => (i === index ? textToCommand(text) : [...command])));
  };

  const handleRemove = (index: number): void => {
    onChange(value.filter((_, i) => i !== index).map((command) => [...command]));
  };

  const handleAdd = (): void => {
    onChange([...value.map((command) => [...command]), []]);
  };

  return (
    <div className="space-y-2" data-testid={testId}>
      <SectionHeading level="h4">{label}</SectionHeading>
      {value.length === 0 && (
        <p className="text-sm text-text-muted" data-testid={`${testId}-empty`}>
          No commands.
        </p>
      )}
      <ul className="space-y-2">
        {value.map((command, index) => {
          const rowId = `${testId}-${String(index)}`;
          return (
            <li key={rowId} className="flex items-center gap-2">
              <label className="sr-only" htmlFor={rowId}>
                {label} command {index + 1}
              </label>
              <Input
                as="input"
                id={rowId}
                data-testid={`${testId}-input-${String(index)}`}
                type="text"
                value={commandToText(command)}
                onChange={(e) => {
                  handleRowChange(index, e.target.value);
                }}
                disabled={disabled}
              />
              <Button
                variant="destroy"
                data-testid={`${testId}-remove-${String(index)}`}
                onClick={() => {
                  handleRemove(index);
                }}
                disabled={disabled}
              >
                Remove
              </Button>
            </li>
          );
        })}
      </ul>
      <Button
        variant="action"
        data-testid={`${testId}-add`}
        onClick={handleAdd}
        disabled={disabled}
      >
        Add command
      </Button>
    </div>
  );
}
