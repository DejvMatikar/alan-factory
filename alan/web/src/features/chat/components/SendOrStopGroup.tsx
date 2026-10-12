import { useEffect, useRef, type RefObject } from "react";

import { useEscapeToStop } from "../hooks/useEscapeToStop";
import Button from "../../../components/Button";
import ShortcutHint from "../../../components/ShortcutHint";

interface SendOrStopGroupProps {
  thinking: boolean;
  stopping: boolean;
  disabled: boolean;
  draft: string;
  onStop: () => void;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  stopTestId?: string;
}

export default function SendOrStopGroup({
  thinking,
  stopping,
  disabled,
  draft,
  onStop,
  textareaRef,
  stopTestId = "stop-button",
}: Readonly<SendOrStopGroupProps>) {
  const stopWasFocusedRef = useRef(false);
  const wasThinkingRef = useRef(thinking);

  useEffect(() => {
    if (wasThinkingRef.current && !thinking && stopWasFocusedRef.current) {
      textareaRef.current?.focus();
      stopWasFocusedRef.current = false;
    }
    wasThinkingRef.current = thinking;
  }, [thinking, textareaRef]);

  const handleStop = (): void => {
    stopWasFocusedRef.current =
      document.activeElement instanceof HTMLElement &&
      document.activeElement.dataset.testid === stopTestId;
    onStop();
  };

  useEscapeToStop(thinking && !stopping, handleStop);

  if (!thinking) {
    return (
      <Button type="submit" variant="action" disabled={disabled || draft.trim().length === 0}>
        Send <ShortcutHint glyph="⌘/⌃↵" spoken="Command Enter or Control Enter" />
      </Button>
    );
  }
  return (
    <>
      <Button
        type="submit"
        variant="action"
        disabled={draft.trim().length === 0}
        data-testid="queue-button"
      >
        Queue <ShortcutHint glyph="⌘/⌃↵" spoken="Command Enter or Control Enter" />
      </Button>
      <Button
        variant="destroy"
        onClick={handleStop}
        disabled={stopping}
        aria-busy={stopping}
        data-testid={stopTestId}
      >
        {stopping ? (
          "Stopping…"
        ) : (
          <>
            Stop <ShortcutHint glyph="Esc" spoken="Escape" />
          </>
        )}
      </Button>
      <span role="status" aria-live="polite" className="sr-only" data-testid="stop-status">
        {stopping ? "Stopping the current turn…" : ""}
      </span>
    </>
  );
}
