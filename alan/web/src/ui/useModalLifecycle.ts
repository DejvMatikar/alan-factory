import { useEffect, useRef, type RefObject } from "react";

export function useModalLifecycle(): RefObject<HTMLDialogElement | null> {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const previouslyFocusedRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    // Chromium doesn't reliably restore focus to the launching control once
    // React unmounts this component's <dialog> on close, so the launching
    // control is captured and refocused explicitly rather than relying on
    // the native showModal()/close() focus-restoration contract.
    previouslyFocusedRef.current = document.activeElement as HTMLElement | null;
    dialog?.showModal();
    return () => {
      dialog?.close();
      /* v8 ignore next */
      previouslyFocusedRef.current?.focus();
    };
  }, []);

  return dialogRef;
}
