import { useState } from "react";

import { api, type RoadmapItemView } from "../../../api";
import { errorMessageOf } from "../../../lib/errorMessage";
import DialogFrame from "../../../ui/DialogFrame";
import Input from "../../../ui/Input";
import { useAction } from "../../../ui/useAction";
import Button from "../../../components/Button";

type ChangeMode = "reorder" | "edit";

export interface ProposeRoadmapChangesModalProps {
  projectId: string;
  items: readonly RoadmapItemView[];
  onClose: () => void;
  onProposed: () => void;
}

function orderedByPosition(items: readonly RoadmapItemView[]): RoadmapItemView[] {
  return [...items].sort((a, b) => a.order - b.order);
}

// The caller only ever invokes this with an in-range delta (the Up/Down
// buttons are disabled at the first/last position), so no bounds guard here.
function moveItem<T>(list: readonly T[], index: number, delta: number): T[] {
  const next = [...list];
  const moved = next.splice(index, 1)[0] as T;
  next.splice(index + delta, 0, moved);
  return next;
}

interface ReorderPanelProps {
  order: readonly RoadmapItemView[];
  disabled: boolean;
  onMove: (index: number, delta: number) => void;
}

function ReorderPanel({ order, disabled, onMove }: Readonly<ReorderPanelProps>) {
  return (
    <ol className="space-y-2" data-testid="propose-roadmap-reorder-list">
      {order.map((item, i) => (
        <li
          key={item.id}
          className="flex items-center justify-between gap-2 rounded-md border border-border px-2 py-1.5"
        >
          <span className="text-sm text-text-primary">{item.title}</span>
          <div className="flex gap-1">
            <Button
              variant="ghost"
              type="button"
              data-testid={`propose-roadmap-reorder-up-${item.id}`}
              onClick={() => onMove(i, -1)}
              disabled={disabled || i === 0}
            >
              Up
            </Button>
            <Button
              variant="ghost"
              type="button"
              data-testid={`propose-roadmap-reorder-down-${item.id}`}
              onClick={() => onMove(i, 1)}
              disabled={disabled || i === order.length - 1}
            >
              Down
            </Button>
          </div>
        </li>
      ))}
    </ol>
  );
}

interface EditPanelProps {
  items: readonly RoadmapItemView[];
  selectedId: string;
  title: string;
  text: string;
  disabled: boolean;
  onSelect: (id: string) => void;
  onTitleChange: (v: string) => void;
  onTextChange: (v: string) => void;
}

interface ModePanelProps {
  mode: ChangeMode;
  order: readonly RoadmapItemView[];
  items: readonly RoadmapItemView[];
  selectedId: string;
  title: string;
  text: string;
  disabled: boolean;
  onMove: (index: number, delta: number) => void;
  onSelect: (id: string) => void;
  onTitleChange: (v: string) => void;
  onTextChange: (v: string) => void;
}

function ModePanel({
  mode,
  order,
  items,
  selectedId,
  title,
  text,
  disabled,
  onMove,
  onSelect,
  onTitleChange,
  onTextChange,
}: Readonly<ModePanelProps>) {
  if (mode === "reorder") {
    return <ReorderPanel order={order} disabled={disabled} onMove={onMove} />;
  }
  return (
    <EditPanel
      items={items}
      selectedId={selectedId}
      title={title}
      text={text}
      disabled={disabled}
      onSelect={onSelect}
      onTitleChange={onTitleChange}
      onTextChange={onTextChange}
    />
  );
}

function ErrorBanner({ message }: Readonly<{ message: string | null }>) {
  if (message === null) return null;
  return (
    <p role="alert" data-testid="propose-roadmap-error" className="text-sm text-btn-destroy">
      {message}
    </p>
  );
}

interface SubmitButtonProps {
  submitting: boolean;
  disabled: boolean;
  onClick: () => void;
}

function SubmitButton({ submitting, disabled, onClick }: Readonly<SubmitButtonProps>) {
  return (
    <Button
      variant="action"
      type="button"
      data-testid="propose-roadmap-submit"
      onClick={onClick}
      disabled={disabled}
    >
      {submitting ? "Proposing…" : "Propose"}
    </Button>
  );
}

function initialSelection(order: readonly RoadmapItemView[]): {
  id: string;
  title: string;
  text: string;
} {
  const first = order[0];
  return { id: first?.id ?? "", title: first?.title ?? "", text: first?.text ?? "" };
}

function computeCanSubmit(mode: ChangeMode, order: readonly unknown[], title: string): boolean {
  return mode === "reorder" ? order.length > 0 : title.trim() !== "";
}

function EditPanel({
  items,
  selectedId,
  title,
  text,
  disabled,
  onSelect,
  onTitleChange,
  onTextChange,
}: Readonly<EditPanelProps>) {
  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <label
          className="text-xs font-medium text-text-muted"
          htmlFor="propose-roadmap-edit-item-select"
        >
          Item
        </label>
        <Input
          as="select"
          id="propose-roadmap-edit-item-select"
          data-testid="propose-roadmap-edit-item-select"
          value={selectedId}
          onChange={(e) => onSelect(e.target.value)}
          disabled={disabled}
        >
          {items.map((item) => (
            <option key={item.id} value={item.id}>
              {item.title}
            </option>
          ))}
        </Input>
      </div>
      <div className="space-y-1">
        <label className="text-xs font-medium text-text-muted" htmlFor="propose-roadmap-edit-title">
          Title
        </label>
        <Input
          as="input"
          id="propose-roadmap-edit-title"
          data-testid="propose-roadmap-edit-title"
          type="text"
          value={title}
          onChange={(e) => onTitleChange(e.target.value)}
          disabled={disabled}
        />
      </div>
      <div className="space-y-1">
        <label className="text-xs font-medium text-text-muted" htmlFor="propose-roadmap-edit-text">
          Description
        </label>
        <Input
          as="textarea"
          id="propose-roadmap-edit-text"
          data-testid="propose-roadmap-edit-text"
          value={text}
          onChange={(e) => onTextChange(e.target.value)}
          disabled={disabled}
          rows={4}
        />
      </div>
    </div>
  );
}

export default function ProposeRoadmapChangesModal({
  projectId,
  items,
  onClose,
  onProposed,
}: Readonly<ProposeRoadmapChangesModalProps>) {
  const initialOrder = orderedByPosition(items);
  const initial = initialSelection(initialOrder);
  const [mode, setMode] = useState<ChangeMode>("reorder");
  const [order, setOrder] = useState<RoadmapItemView[]>(initialOrder);
  const [selectedId, setSelectedId] = useState<string>(initial.id);
  const [title, setTitle] = useState<string>(initial.title);
  const [text, setText] = useState<string>(initial.text);
  const { busy: submitting, run } = useAction();
  const [error, setError] = useState<string | null>(null);

  // The id always comes from this same `items` list's own <option> values
  // (see EditPanel's select below), so a match is guaranteed.
  const handleSelectItem = (id: string): void => {
    setSelectedId(id);
    const found = items.find((item) => item.id === id)!;
    setTitle(found.title);
    setText(found.text);
  };

  const handleMove = (index: number, delta: number): void => {
    setOrder((current) => moveItem(current, index, delta));
  };

  const canSubmit = computeCanSubmit(mode, order, title);

  const submitChanges = (): void => {
    if (!canSubmit) return;
    setError(null);
    run(() =>
      mode === "reorder"
        ? api.proposeRoadmapReorder(
            projectId,
            order.map((item) => item.id),
          )
        : api.proposeRoadmapEdit(projectId, selectedId, { title: title.trim(), text }),
    )
      .then(onProposed)
      .catch((err: unknown) => {
        setError(errorMessageOf(err));
      });
  };

  return (
    <DialogFrame
      size="lg"
      ariaLabel="Propose roadmap changes"
      testId="propose-roadmap-changes-dialog"
      busy={submitting}
      onClose={onClose}
      onSubmit={submitChanges}
    >
      <h2 className="text-base font-semibold text-text-primary">Propose roadmap changes</h2>
      <div className="space-y-1">
        <label
          className="text-xs font-medium text-text-muted"
          htmlFor="propose-roadmap-mode-select"
        >
          Change type
        </label>
        <Input
          as="select"
          id="propose-roadmap-mode-select"
          data-testid="propose-roadmap-mode-select"
          value={mode}
          onChange={(e) => setMode(e.target.value as ChangeMode)}
          disabled={submitting}
        >
          <option value="reorder">Reorder items</option>
          <option value="edit">Edit an item</option>
        </Input>
      </div>
      <ModePanel
        mode={mode}
        order={order}
        items={items}
        selectedId={selectedId}
        title={title}
        text={text}
        disabled={submitting}
        onMove={handleMove}
        onSelect={handleSelectItem}
        onTitleChange={setTitle}
        onTextChange={setText}
      />
      <ErrorBanner message={error} />
      <div className="flex justify-end gap-2">
        <Button variant="ghost" type="button" onClick={onClose} disabled={submitting}>
          Cancel
        </Button>
        <SubmitButton
          submitting={submitting}
          disabled={submitting || !canSubmit}
          onClick={submitChanges}
        />
      </div>
    </DialogFrame>
  );
}
