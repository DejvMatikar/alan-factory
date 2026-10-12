import Button from "./Button";

export type SourceToggleLabelSet = "source" | "edit";

const LABEL_SETS: Record<SourceToggleLabelSet, readonly [string, string]> = {
  source: ["View source", "View formatted"],
  edit: ["Edit", "Preview"],
};

export function sourceToggleLabel(
  showSource: boolean,
  labelSet: SourceToggleLabelSet = "source",
): string {
  const [formattedShowingLabel, sourceShowingLabel] = LABEL_SETS[labelSet];
  return showSource ? sourceShowingLabel : formattedShowingLabel;
}

export interface MarkdownSourceToggleProps {
  showSource: boolean;
  onToggle: () => void;
  testId: string;
  labelSet?: SourceToggleLabelSet;
}

export default function MarkdownSourceToggle({
  showSource,
  onToggle,
  testId,
  labelSet = "source",
}: Readonly<MarkdownSourceToggleProps>) {
  return (
    <Button
      variant="ghost"
      data-testid={testId}
      aria-pressed={labelSet === "source" ? showSource : undefined}
      onClick={onToggle}
    >
      {sourceToggleLabel(showSource, labelSet)}
    </Button>
  );
}
