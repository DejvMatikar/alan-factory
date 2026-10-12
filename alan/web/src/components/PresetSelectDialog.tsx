import { useState } from "react";
import type { KeyboardEvent } from "react";

import Button from "./Button";
import type { ButtonVariant } from "./Button";
import { handleModalKeyDown } from "./modalKeys";
import Input from "../ui/Input";
import { useModalLifecycle } from "../ui/useModalLifecycle";

export interface PresetSelectOption {
  value: string;
  label: string;
}

export interface PresetSelectDialogProps {
  testId: string;
  ariaLabel: string;
  title: string;
  description: string;
  options: readonly PresetSelectOption[];
  confirmLabel: string;
  confirmVariant: ButtonVariant;
  onConfirm: (value: string) => void;
  onClose: () => void;
}

export default function PresetSelectDialog({
  testId,
  ariaLabel,
  title,
  description,
  options,
  confirmLabel,
  confirmVariant,
  onConfirm,
  onClose,
}: Readonly<PresetSelectDialogProps>) {
  const dialogRef = useModalLifecycle();
  const [selected, setSelected] = useState(options[0]?.value ?? "");

  const selectedOption = options.find((option) => option.value === selected);
  const handleConfirm = (): void => {
    if (selectedOption !== undefined) onConfirm(selectedOption.value);
  };
  const handleContainerKeyDown = (e: KeyboardEvent<HTMLDivElement>): void => {
    handleModalKeyDown(e, { onEscape: onClose, onCtrlEnter: handleConfirm });
  };

  return (
    // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-noninteractive-element-interactions -- backdrop click-to-close; Escape is handled separately below
    <dialog
      ref={dialogRef}
      onCancel={(e) => {
        // The native default action would close the dialog itself; leaving
        // that in place double-closes it once useModalLifecycle's cleanup
        // also calls dialog.close() after onClose() unmounts this component.
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        if (e.target === dialogRef.current) onClose();
      }}
      aria-label={ariaLabel}
      data-testid={`${testId}-dialog`}
      className="fixed inset-0 m-0 flex h-screen w-screen items-center justify-center bg-surface/80 p-0"
    >
      {/* eslint-disable-next-line jsx-a11y/no-static-element-interactions */}
      <div
        onKeyDown={handleContainerKeyDown}
        className="w-[90vw] max-w-lg rounded-lg border border-border bg-surface-elevated p-5 shadow-xl"
      >
        <div className="flex flex-col gap-4">
          <h3 className="text-base font-semibold text-text-primary">{title}</h3>
          <p className="text-sm text-text-primary">{description}</p>
          <div className="space-y-1">
            <label className="text-xs font-medium text-text-muted" htmlFor={`${testId}-select`}>
              Preset
            </label>
            <Input
              as="select"
              id={`${testId}-select`}
              data-testid={`${testId}-select`}
              value={selected}
              onChange={(e) => {
                setSelected(e.target.value);
              }}
              disabledStyle={false}
            >
              {options.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Input>
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" type="button" onClick={onClose}>
              Cancel
            </Button>
            <Button
              variant={confirmVariant}
              type="button"
              data-testid={`${testId}-confirm`}
              onClick={handleConfirm}
              disabled={selectedOption === undefined}
            >
              {confirmLabel}
            </Button>
          </div>
        </div>
      </div>
    </dialog>
  );
}
