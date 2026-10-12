import { useEffect, useRef } from "react";

import MarkdownBody from "../../../components/MarkdownBody";

export interface EditableBriefProps {
  brief: { content: string } | null;
  value: string;
  onChange: (v: string) => void;
  onEdit: (v: string) => void;
  mode?: "edit" | "preview";
  rows?: number;
  focusOnEdit?: boolean;
}

export default function EditableBrief({
  brief,
  value,
  onChange,
  onEdit,
  mode = "edit",
  rows = 20,
  focusOnEdit = false,
}: Readonly<EditableBriefProps>) {
  const isDirty = brief !== null && value !== brief.content;
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (mode === "edit" && focusOnEdit) textareaRef.current?.focus();
  }, [mode, focusOnEdit]);

  return (
    <div className="flex flex-col gap-3">
      {brief === null && (
        <p className="text-sm text-text-muted" data-testid="summary-empty">
          No summary yet — click Summarize to distil the conversation.
        </p>
      )}
      <section aria-label={mode === "preview" ? "Summary" : undefined} data-testid="summary-content">
        {mode === "preview" ? (
          <div
            className="w-full rounded-md border border-border bg-surface-elevated px-3 py-2 text-sm text-text-primary"
            data-testid="summary-preview"
          >
            <MarkdownBody measure="full">{value}</MarkdownBody>
          </div>
        ) : (
          <textarea
            ref={textareaRef}
            aria-label="Summary"
            value={value}
            onChange={(e) => {
              onChange(e.target.value);
            }}
            onBlur={() => {
              if (isDirty) onEdit(value);
            }}
            rows={rows}
            className="field-sizing-content w-full rounded-md border border-border bg-surface-elevated px-3 py-2 text-sm text-text-primary"
            data-testid="summary-textarea"
          />
        )}
      </section>
    </div>
  );
}
