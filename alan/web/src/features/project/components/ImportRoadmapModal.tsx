import { useState } from "react";

import { api, type RoadmapPreviewItem } from "../../../api";
import { errorMessageOf } from "../../../lib/errorMessage";
import DialogFrame from "../../../ui/DialogFrame";
import Input from "../../../ui/Input";
import { useAction } from "../../../ui/useAction";
import Button from "../../../components/Button";
import MarkdownPanel from "../../../components/MarkdownPanel";

type Step = "edit" | "preview";

export interface ImportRoadmapModalProps {
  projectId: string;
  onClose: () => void;
  onImported: () => void;
}

interface PreviewListProps {
  items: readonly RoadmapPreviewItem[];
}

function PreviewList({ items }: Readonly<PreviewListProps>) {
  if (items.length === 0) {
    return (
      <p className="text-sm text-text-muted" data-testid="import-roadmap-preview-empty">
        No ## headings found — check your markdown format.
      </p>
    );
  }
  return (
    <ol className="space-y-2" data-testid="import-roadmap-preview-list">
      {items.map((item, i) =>
        item.text.trim() === "" ? (
          <li key={`${String(i)}-${item.title}`} className="space-y-1">
            <p className="text-sm font-medium text-text-primary">{item.title}</p>
          </li>
        ) : (
          <li key={`${String(i)}-${item.title}`} className="space-y-1">
            <MarkdownPanel
              title={item.title}
              ariaLabel={item.title}
              testId={`import-roadmap-preview-item-${String(i)}`}
              contentClassName="text-sm text-text-muted"
            >
              {item.text}
            </MarkdownPanel>
          </li>
        ),
      )}
    </ol>
  );
}

interface EditStepProps {
  markdown: string;
  disabled: boolean;
  onChange: (v: string) => void;
}

function EditStep({ markdown, disabled, onChange }: Readonly<EditStepProps>) {
  return (
    <div className="space-y-1">
      <label className="text-xs font-medium text-text-muted" htmlFor="import-roadmap-markdown">
        Roadmap markdown
      </label>
      <Input
        as="textarea"
        id="import-roadmap-markdown"
        data-testid="import-roadmap-markdown"
        value={markdown}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        rows={10}
        placeholder={"## First item\nDescribe the work.\n\n## Second item\n…"}
        placeholderStyle
      />
      <p className="text-xs text-text-muted">Each ## heading becomes one ordered roadmap item.</p>
    </div>
  );
}

interface StepBodyProps {
  step: Step;
  markdown: string;
  submitting: boolean;
  previewItems: readonly RoadmapPreviewItem[];
  onMarkdownChange: (v: string) => void;
}

function StepBody({ step, markdown, submitting, previewItems, onMarkdownChange }: Readonly<StepBodyProps>) {
  if (step === "edit") {
    return <EditStep markdown={markdown} disabled={submitting} onChange={onMarkdownChange} />;
  }
  return <PreviewList items={previewItems} />;
}

function primaryButtonLabel(step: Step, submitting: boolean): string {
  if (step === "edit") return submitting ? "Loading preview…" : "Preview";
  return submitting ? "Importing…" : "Import";
}

interface PrimaryButtonProps {
  step: Step;
  submitting: boolean;
  disabled: boolean;
  onClick: () => void;
}

function PrimaryButton({ step, submitting, disabled, onClick }: Readonly<PrimaryButtonProps>) {
  const testId = step === "edit" ? "import-roadmap-preview-button" : "import-roadmap-submit";
  return (
    <Button variant="create" type="button" data-testid={testId} onClick={onClick} disabled={disabled}>
      {primaryButtonLabel(step, submitting)}
    </Button>
  );
}

export default function ImportRoadmapModal({
  projectId,
  onClose,
  onImported,
}: Readonly<ImportRoadmapModalProps>) {
  const [markdown, setMarkdown] = useState("");
  const [step, setStep] = useState<Step>("edit");
  const [previewItems, setPreviewItems] = useState<RoadmapPreviewItem[]>([]);
  const { busy: submitting, run } = useAction();
  const [error, setError] = useState<string | null>(null);

  const canPreview = markdown.trim() !== "";

  const handlePreview = (): void => {
    if (!canPreview) return;
    setError(null);
    run(() => api.previewRoadmap(projectId, markdown))
      .then((res) => {
        setPreviewItems(res.items);
        setStep("preview");
      })
      .catch((err: unknown) => {
        setError(errorMessageOf(err));
      });
  };

  const handleImport = (): void => {
    if (previewItems.length === 0) return;
    setError(null);
    run(() => api.importRoadmap(projectId, markdown))
      .then(() => {
        onImported();
      })
      .catch((err: unknown) => {
        setError(errorMessageOf(err));
      });
  };

  const handleBack = (): void => {
    setStep("edit");
    setError(null);
  };

  const handlePrimary = (): void => {
    if (step === "edit") handlePreview();
    else handleImport();
  };

  const primaryDisabled =
    submitting || (step === "edit" ? !canPreview : previewItems.length === 0);

  return (
    <DialogFrame
      size="lg"
      ariaLabel="Import roadmap"
      testId="import-roadmap-dialog"
      busy={submitting}
      onClose={onClose}
      onSubmit={handlePrimary}
    >
      <h2 className="text-base font-semibold text-text-primary">Import roadmap</h2>
      <StepBody
        step={step}
        markdown={markdown}
        submitting={submitting}
        previewItems={previewItems}
        onMarkdownChange={setMarkdown}
      />
      {error !== null && (
        <p role="alert" data-testid="import-roadmap-error" className="text-sm text-btn-destroy">
          {error}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" type="button" onClick={onClose} disabled={submitting}>
          Cancel
        </Button>
        {step === "preview" && (
          <Button
            variant="ghost"
            type="button"
            data-testid="import-roadmap-back"
            onClick={handleBack}
            disabled={submitting}
          >
            Back
          </Button>
        )}
        <PrimaryButton
          step={step}
          submitting={submitting}
          disabled={primaryDisabled}
          onClick={handlePrimary}
        />
      </div>
    </DialogFrame>
  );
}
