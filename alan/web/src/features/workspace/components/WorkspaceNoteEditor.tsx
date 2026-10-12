import { useCallback, useEffect, useId, useRef, useState } from "react";

import { api } from "../../../api";
import Button from "../../../components/Button";
import { errorMessageOf } from "../../../lib/errorMessage";
import ErrorBanner from "../../../ui/ErrorBanner";
import Input from "../../../ui/Input";
import { useAction } from "../../../ui/useAction";

interface WorkspaceNoteEditorProps {
  taskId: string;
  initialNote: string | null | undefined;
}

export default function WorkspaceNoteEditor({
  taskId,
  initialNote,
}: Readonly<WorkspaceNoteEditorProps>) {
  const [text, setText] = useState(initialNote ?? "");
  const [savedText, setSavedText] = useState(initialNote ?? "");
  const [saved, setSaved] = useState(false);
  const { busy: saving, begin, end } = useAction();
  const [error, setError] = useState<string | null>(null);
  const unsavedId = useId();

  const taskIdRef = useRef(taskId);

  useEffect(() => {
    taskIdRef.current = taskId;
  }, [taskId]);

  const handleSave = useCallback(() => {
    const requestTaskId = taskId;
    const trimmed = text.trim();
    begin();
    setSaved(false);
    setError(null);
    api
      .updateWorkspaceNote(requestTaskId, trimmed || null)
      .then(({ note }) => {
        if (taskIdRef.current !== requestTaskId) return;
        setText(note ?? "");
        setSavedText(note ?? "");
        setSaved(true);
        end();
      })
      .catch((err: unknown) => {
        if (taskIdRef.current !== requestTaskId) return;
        setError(errorMessageOf(err));
        end();
      });
  }, [taskId, text, begin, end]);

  const unsaved = text !== savedText;

  return (
    <section className="space-y-2" data-testid="note-editor">
      <h3 className="text-sm font-medium">Personal note</h3>
      <Input
        as="textarea"
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setSaved(false);
        }}
        rows={3}
        aria-label="Personal note"
        aria-describedby={unsaved ? unsavedId : undefined}
        placeholder="Add a private memo…"
        focusRing
        placeholderStyle
      />
      <div className="flex items-center gap-3">
        <Button variant="action" onClick={handleSave} busy={saving} data-testid="note-save-btn">
          {saving ? "Saving…" : "Save note"}
        </Button>
        {unsaved && (
          <span id={unsavedId} className="text-xs text-text-muted">
            Unsaved changes
          </span>
        )}
        <output className="text-xs text-text-muted">
          {saved && <span data-testid="note-saved">Saved</span>}
        </output>
      </div>
      {error !== null && <ErrorBanner data-testid="note-error">{error}</ErrorBanner>}
    </section>
  );
}
