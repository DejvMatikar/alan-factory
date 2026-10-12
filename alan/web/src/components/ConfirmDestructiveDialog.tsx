import Button from "./Button";
import type { ButtonVariant } from "./Button";
import DialogFrame from "../ui/DialogFrame";

// Every call site today confirms either an outright deletion ("destroy", the
// default) or a reversible-but-consequential reset ("reset", amber) — never
// a create/action-coloured confirm, so the prop is narrowed to those two
// rather than the full ButtonVariant union.
export type ConfirmDestructiveVariant = Extract<ButtonVariant, "destroy" | "reset">;

export interface ConfirmDestructiveDialogProps {
  title: string;
  body: string;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  testId?: string;
  confirmVariant?: ConfirmDestructiveVariant;
}

export default function ConfirmDestructiveDialog({
  title,
  body,
  confirmLabel,
  onConfirm,
  onCancel,
  testId,
  confirmVariant = "destroy",
}: Readonly<ConfirmDestructiveDialogProps>) {
  return (
    <DialogFrame
      size="md"
      ariaLabel={title}
      testId={testId}
      onClose={onCancel}
      onSubmit={onConfirm}
    >
      <h3 className="text-base font-semibold text-text-primary">{title}</h3>
      <p className="text-sm text-text-primary">{body}</p>
      <div className="flex justify-end gap-2">
        <Button variant="ghost" type="button" onClick={onCancel}>
          Cancel
        </Button>
        <Button variant={confirmVariant} type="button" onClick={onConfirm}>
          {confirmLabel}
        </Button>
      </div>
    </DialogFrame>
  );
}
