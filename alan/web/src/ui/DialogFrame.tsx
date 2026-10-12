import type { KeyboardEvent, ReactNode, SyntheticEvent } from "react";

import { handleModalKeyDown } from "../components/modalKeys";
import { useModalLifecycle } from "./useModalLifecycle";

export type DialogFrameSize = "md" | "lg";

const FRAME_HEAD = "fixed inset-0 m-auto w-[90vw] ";
const FRAME_TAIL =
  " rounded-lg border border-border bg-surface-elevated p-0 text-text-primary shadow-lg backdrop:bg-surface/80";

const SIZE_CLASS: Record<DialogFrameSize, string> = {
  md: "max-w-md",
  lg: "max-w-lg",
};

interface DismissHandlers {
  handleKeyDown: (e: KeyboardEvent<HTMLFormElement>) => void;
  handleCancel: (e: SyntheticEvent<HTMLDialogElement>) => void;
}

function noop(): void {
  return undefined;
}

function activeModalDismissHandlers(onClose: () => void, onSubmit: () => void): DismissHandlers {
  const handleKeyDown = (e: KeyboardEvent<HTMLFormElement>): void => {
    handleModalKeyDown(e, { onEscape: onClose, onCtrlEnter: onSubmit });
  };
  const handleCancel = (e: SyntheticEvent<HTMLDialogElement>): void => {
    e.preventDefault();
    onClose();
  };
  return { handleKeyDown, handleCancel };
}

function blockedModalDismissHandlers(): DismissHandlers {
  const handleKeyDown = (e: KeyboardEvent<HTMLFormElement>): void => {
    handleModalKeyDown(e, { onEscape: noop, onCtrlEnter: noop });
  };
  const handleCancel = (e: SyntheticEvent<HTMLDialogElement>): void => {
    e.preventDefault();
  };
  return { handleKeyDown, handleCancel };
}

export interface DialogFrameProps {
  size: DialogFrameSize;
  ariaLabel: string;
  testId?: string | undefined;
  busy?: boolean | undefined;
  onClose: () => void;
  onSubmit: () => void;
  children: ReactNode;
}

export default function DialogFrame({
  size,
  ariaLabel,
  testId,
  busy = false,
  onClose,
  onSubmit,
  children,
}: Readonly<DialogFrameProps>) {
  const dialogRef = useModalLifecycle();
  const { handleKeyDown, handleCancel } = busy
    ? blockedModalDismissHandlers()
    : activeModalDismissHandlers(onClose, onSubmit);

  return (
    <dialog
      ref={dialogRef}
      onCancel={handleCancel}
      aria-label={ariaLabel}
      data-testid={testId}
      className={FRAME_HEAD + SIZE_CLASS[size] + FRAME_TAIL}
    >
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions */}
      <form
        className="flex flex-col gap-4 p-5"
        onSubmit={(e) => {
          e.preventDefault();
        }}
        onKeyDown={handleKeyDown}
      >
        {children}
      </form>
    </dialog>
  );
}
