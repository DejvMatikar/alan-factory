import { useState } from "react";

import { api, type Project } from "../api";
import { errorMessageOf } from "../lib/errorMessage";
import DialogFrame from "../ui/DialogFrame";
import Input from "../ui/Input";
import { useAction } from "../ui/useAction";
import Button from "./Button";

export interface NewProjectModalProps {
  onClose: () => void;
  onCreated: (project: Project) => void;
}

export default function NewProjectModal({ onClose, onCreated }: Readonly<NewProjectModalProps>) {
  const [title, setTitle] = useState("");
  const [docRef, setDocRef] = useState("");
  const { busy: submitting, run } = useAction();
  const [error, setError] = useState<string | null>(null);
  const trimmedTitle = title.trim();

  const handleSubmit = (): void => {
    if (trimmedTitle === "") return;
    setError(null);
    const trimmedDocRef = docRef.trim();
    run(() => api.createProject(trimmedTitle, trimmedDocRef === "" ? null : trimmedDocRef))
      .then(onCreated)
      .catch((err: unknown) => {
        setError(errorMessageOf(err));
      });
  };

  return (
    <DialogFrame
      size="md"
      ariaLabel="New project"
      testId="new-project-dialog"
      busy={submitting}
      onClose={onClose}
      onSubmit={handleSubmit}
    >
      <h2 className="text-base font-semibold text-text-primary">New project</h2>
      <div className="space-y-1">
        <label className="text-xs font-medium text-text-muted" htmlFor="new-project-title">
          Title
        </label>
        <Input
          as="input"
          id="new-project-title"
          data-testid="new-project-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          disabled={submitting}
        />
      </div>
      <div className="space-y-1">
        <label className="text-xs font-medium text-text-muted" htmlFor="new-project-doc-ref">
          Doc reference (optional)
        </label>
        <Input
          as="input"
          id="new-project-doc-ref"
          data-testid="new-project-doc-ref"
          value={docRef}
          onChange={(e) => setDocRef(e.target.value)}
          disabled={submitting}
          placeholder="docs/roadmap.md"
          placeholderStyle
        />
      </div>
      {error !== null && (
        <p role="alert" data-testid="new-project-error" className="text-sm text-btn-destroy">
          {error}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" type="button" onClick={onClose} disabled={submitting}>
          Cancel
        </Button>
        <Button
          variant="create"
          type="button"
          data-testid="new-project-submit"
          onClick={handleSubmit}
          disabled={submitting || trimmedTitle === ""}
        >
          {submitting ? "Creating…" : "Create project"}
        </Button>
      </div>
    </DialogFrame>
  );
}
