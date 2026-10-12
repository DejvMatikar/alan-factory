import { useEffect, useRef, type FormEvent } from "react";

import type { AutoDispatchState } from "../hooks/autoDispatch";
import Button from "../../../components/Button";
import SendOrStopGroup from "./SendOrStopGroup";
import ShortcutHint from "../../../components/ShortcutHint";

interface RightActionsProps {
  onSummarize: () => void;
  summarizing: boolean;
  sessionReady: boolean;
  thinking: boolean;
  onAutoDispatch: () => void;
  autoDispatch: AutoDispatchState;
}

function composerActionDisabled(summarizing: boolean, sessionReady: boolean, thinking: boolean): boolean {
  return summarizing || !sessionReady || thinking;
}

function RightActions({
  onSummarize,
  summarizing,
  sessionReady,
  thinking,
  onAutoDispatch,
  autoDispatch,
}: Readonly<RightActionsProps>) {
  const baseDisabled = composerActionDisabled(summarizing, sessionReady, thinking);
  return (
    <div className="flex items-center gap-2">
      <Button
        variant="action"
        onClick={onSummarize}
        disabled={baseDisabled}
        data-testid="summarize-button"
      >
        {summarizing ? (
          "Summarizing…"
        ) : (
          <>
            Summarize <ShortcutHint glyph="⌃⇧S" spoken="Control Shift S" />
          </>
        )}
      </Button>
      <Button
        variant="create"
        onClick={onAutoDispatch}
        disabled={autoDispatch.running || baseDisabled}
        data-testid="auto-dispatch-button"
      >
        {autoDispatch.running ? "Summarizing & dispatching…" : "Summarize & Dispatch"}
      </Button>
    </div>
  );
}

export interface ChatComposerDeleteAction {
  onRequestDelete: () => void;
  deleting: boolean;
}

export interface ChatComposerProps {
  draft: string;
  setDraft: (v: string) => void;
  onSubmit: (e: FormEvent) => void;
  disabled: boolean;
  onClear: () => void;
  onSummarize: () => void;
  onStop: () => void;
  summarizing: boolean;
  sessionReady: boolean;
  thinking: boolean;
  stopping?: boolean;
  rows?: number;
  deleteAction?: ChatComposerDeleteAction | undefined;
  onAutoDispatch: () => void;
  autoDispatch: AutoDispatchState;
}

export default function ChatComposer({
  draft,
  setDraft,
  onSubmit,
  disabled,
  onClear,
  onSummarize,
  onStop,
  summarizing,
  sessionReady,
  thinking,
  stopping = false,
  rows = 6,
  deleteAction,
  onAutoDispatch,
  autoDispatch,
}: Readonly<ChatComposerProps>) {
  const formRef = useRef<HTMLFormElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (disabled) return;
    if (document.activeElement !== document.body) return;
    const el = textareaRef.current;
    /* v8 ignore next */
    if (el === null) return;
    el.focus();
  }, [disabled]);
  return (
    <form ref={formRef} className="flex flex-col gap-2 mt-4" onSubmit={onSubmit}>
      <textarea
        ref={textareaRef}
        aria-label="Message"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
            e.preventDefault();
            formRef.current?.requestSubmit();
          }
        }}
        rows={rows}
        disabled={disabled}
        className="w-full rounded-md border border-border bg-surface-elevated px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-text-primary/60 disabled:opacity-50"
      />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-2">
          <SendOrStopGroup
            thinking={thinking}
            stopping={stopping}
            disabled={disabled}
            draft={draft}
            onStop={onStop}
            textareaRef={textareaRef}
          />
          <Button
            variant="ghost"
            onClick={onClear}
            disabled={!sessionReady}
            data-testid="clear-button"
          >
            Clear <ShortcutHint glyph="⌃⇧K" spoken="Control Shift K" />
          </Button>
          {deleteAction !== undefined && (
            <Button
              variant="destroy"
              onClick={deleteAction.onRequestDelete}
              disabled={!sessionReady || deleteAction.deleting}
              data-testid="delete-chat-button"
            >
              {deleteAction.deleting ? "Deleting…" : "Delete chat"}
            </Button>
          )}
        </div>
        <RightActions
          onSummarize={onSummarize}
          summarizing={summarizing}
          sessionReady={sessionReady}
          thinking={thinking}
          onAutoDispatch={onAutoDispatch}
          autoDispatch={autoDispatch}
        />
      </div>
    </form>
  );
}
