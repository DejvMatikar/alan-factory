import type { Ref } from "react";

import Button from "../../../components/Button";
import ShortcutHint from "../../../components/ShortcutHint";
import { shortcutHint } from "../../../lib/shortcuts";

export interface OperatorBarProps {
  readonly ref?: Ref<HTMLFieldSetElement>;
  readonly summary: string;
  readonly endBusy: boolean;
  readonly onClose: () => void;
  readonly onEnd: () => void;
  readonly onReplace: () => void;
}

export default function OperatorBar({
  ref,
  summary,
  endBusy,
  onClose,
  onEnd,
  onReplace,
}: Readonly<OperatorBarProps>) {
  const hint = shortcutHint("operator-escape");
  return (
    <fieldset
      ref={ref}
      aria-label="Session controls"
      data-testid="operator-bar"
      className="min-w-0 space-y-2"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="ghost" data-testid="operator-close" onClick={onClose}>
          Close
        </Button>
        <Button variant="destroy" data-testid="operator-end" busy={endBusy} onClick={onEnd}>
          End session
        </Button>
        <Button variant="ghost" data-testid="operator-replace" onClick={onReplace}>
          Replace session
        </Button>
        <span data-testid="operator-summary" className="text-sm text-text-muted">
          {summary}
        </span>
      </div>
      <p data-testid="operator-escape-hint" className="text-xs text-text-primary">
        Close leaves the session running. End session stops it. Back to these buttons from the
        terminal:
        <ShortcutHint glyph={hint.glyph} spoken={hint.spoken} />
      </p>
    </fieldset>
  );
}
