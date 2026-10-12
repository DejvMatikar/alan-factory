import { useEffect, useRef, type ReactNode } from "react";

import Button from "./Button";
import { handleModalKeyDown } from "./modalKeys";
import { useModalLifecycle } from "../ui/useModalLifecycle";

// Opt-in filter box for callers whose item list is too large (or too
// keyboard-driven) for a fixed shortlist — CommandPalette is the first
// consumer. Rendered as the first focusable element in the dialog so it
// takes focus on open ahead of the close button, without depending on
// jsdom's unreliable native-autofocus emulation (see the effect below).
export interface ListChooserFilter {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  ariaLabel: string;
}

interface Props<T> {
  ariaLabel: string;
  testId: string;
  title: string;
  description?: string | undefined;
  filter?: ListChooserFilter | undefined;
  notice?: ReactNode | undefined;
  items: readonly T[];
  getItemKey: (item: T) => string;
  getItemTestId: (item: T) => string;
  renderItem: (item: T) => ReactNode;
  onSelect: (item: T) => void;
  onClose: () => void;
}

function noop(): void {
  // no confirm step remains to wire onCtrlEnter to
}

export default function ListChooserModal<T>({
  ariaLabel,
  testId,
  title,
  description,
  filter,
  notice,
  items,
  getItemKey,
  getItemTestId,
  renderItem,
  onSelect,
  onClose,
}: Readonly<Props<T>>) {
  const dialogRef = useModalLifecycle();
  const filterInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    filterInputRef.current?.focus();
  }, []);

  const handleContainerKeyDown = (e: React.KeyboardEvent<HTMLDivElement>): void => {
    handleModalKeyDown(e, {
      onEscape: onClose,
      onCtrlEnter: noop,
    });
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
      data-testid={testId}
      className="fixed inset-0 m-0 flex h-screen w-screen items-center justify-center bg-surface/80 p-0"
    >
      {/* eslint-disable-next-line jsx-a11y/no-static-element-interactions */}
      <div
        onKeyDown={handleContainerKeyDown}
        className="w-[90vw] max-w-md rounded-lg border border-border bg-surface-elevated p-6 shadow-xl"
      >
        {filter !== undefined && (
          <input
            ref={filterInputRef}
            type="text"
            data-testid="list-chooser-filter"
            aria-label={filter.ariaLabel}
            placeholder={filter.placeholder}
            value={filter.value}
            onChange={(e) => filter.onChange(e.target.value)}
            className="mb-4 min-h-11 w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60"
          />
        )}
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold text-text-primary">{title}</h2>
          <Button variant="ghost" data-testid="modal-close" aria-label="Close" onClick={onClose}>
            ✕
          </Button>
        </div>
        {description !== undefined && (
          <p className="mb-4 text-xs text-text-muted">{description}</p>
        )}
        {notice !== undefined ? (
          notice
        ) : (
          <ul className="max-h-[60vh] space-y-2 overflow-y-auto">
            {items.map((item) => {
              const key = getItemKey(item);
              return (
                <li key={key}>
                  <button
                    type="button"
                    data-testid={getItemTestId(item)}
                    onClick={() => onSelect(item)}
                    className="min-h-11 w-full cursor-pointer rounded-md border border-border bg-surface-elevated px-3 py-2 text-left hover:ring-1 hover:ring-text-muted"
                  >
                    {renderItem(item)}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </dialog>
  );
}
