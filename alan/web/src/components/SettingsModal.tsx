import { useState, type KeyboardEvent } from "react";

import { clearControlToken, readControlToken, writeControlToken } from "../controlToken";
import Button from "./Button";
import ControlTokenSection from "./ControlTokenSection";
import CredentialsSection from "./CredentialsSection";
import { handleModalKeyDown } from "./modalKeys";
import { useModalLifecycle } from "../ui/useModalLifecycle";

interface Props {
  onClose: () => void;
}

export default function SettingsModal({ onClose }: Readonly<Props>) {
  const dialogRef = useModalLifecycle();
  const [draft, setDraft] = useState<string>(() => readControlToken());
  const [showToken, setShowToken] = useState(false);
  const [tokenSet, setTokenSet] = useState<boolean>(() => readControlToken().length > 0);

  const handleSave = (): void => {
    writeControlToken(draft);
    setTokenSet(readControlToken().length > 0);
  };

  const handleClear = (): void => {
    clearControlToken();
    setDraft("");
    setTokenSet(readControlToken().length > 0);
  };

  const handleContainerKeyDown = (e: KeyboardEvent<HTMLDivElement>): void => {
    handleModalKeyDown(e, { onEscape: onClose, onCtrlEnter: handleSave });
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
      aria-label="Settings"
      data-testid="settings-modal"
      className="fixed inset-0 m-0 flex h-screen w-screen items-center justify-center bg-surface/80 p-0"
    >
      {/* eslint-disable-next-line jsx-a11y/no-static-element-interactions */}
      <div
        onKeyDown={handleContainerKeyDown}
        className="max-h-[85vh] w-[90vw] max-w-md overflow-y-auto rounded-lg border border-border bg-surface-elevated p-6 shadow-xl"
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold text-text-primary">Settings</h2>
          <Button variant="ghost" data-testid="settings-modal-close" aria-label="Close" onClick={onClose}>
            ✕
          </Button>
        </div>
        <ControlTokenSection
          draft={draft}
          showToken={showToken}
          tokenSet={tokenSet}
          onDraftChange={setDraft}
          onToggleShowToken={() => setShowToken((prev) => !prev)}
          onSave={handleSave}
          onClear={handleClear}
        />
        <hr className="my-4 border-border" />
        <CredentialsSection />
      </div>
    </dialog>
  );
}
