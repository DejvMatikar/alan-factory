import { useState } from "react";

import { api, type EnginePosture } from "../api";
import { errorMessageOf } from "../lib/errorMessage";
import DialogFrame from "../ui/DialogFrame";
import Input from "../ui/Input";
import { useAction } from "../ui/useAction";
import Button from "./Button";

const POSTURE_LABELS: Record<EnginePosture, string> = {
  observe: "Observe — read-only, no autonomous action",
  propose: "Propose — pause for my approval",
  self_merge: "Self-merge — verify and merge autonomously",
  autonomous: "Autonomous — full autonomy",
};

const POSTURE_OPTIONS: { value: EnginePosture; label: string }[] = (
  Object.keys(POSTURE_LABELS) as EnginePosture[]
).map((value) => ({ value, label: POSTURE_LABELS[value] }));

interface PostureInputProps {
  targetPosture: EnginePosture | undefined;
  posture: EnginePosture;
  submitting: boolean;
  onChange: (posture: EnginePosture) => void;
}

// When targetPosture is supplied, the free <select> is not rendered at
// all — not merely disabled — so a caller's greying decision is structural,
// never just decorative.
function PostureInput({ targetPosture, posture, submitting, onChange }: Readonly<PostureInputProps>) {
  if (targetPosture !== undefined) {
    return (
      <p data-testid="posture-confirm-target" className="text-sm text-text-primary">
        {POSTURE_LABELS[targetPosture]}
      </p>
    );
  }
  return (
    <Input
      as="select"
      id="posture-confirm-select"
      data-testid="posture-confirm-select"
      aria-labelledby="posture-confirm-target-label"
      value={posture}
      onChange={(e) => {
        onChange(e.target.value as EnginePosture);
      }}
      disabled={submitting}
    >
      {POSTURE_OPTIONS.map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </Input>
  );
}

export interface PostureConfirmModalProps {
  engineKey: string;
  currentPosture: EnginePosture;
  onClose: () => void;
  // When supplied, the caller has already chosen the target posture
  // elsewhere (e.g. connect wizard step 4's radio column) — the free
  // <select> is not rendered at all, so a posture the caller has marked
  // unavailable can never be reached from inside this dialog either.
  targetPosture?: EnginePosture;
  // Fired after a resolved api.setEnginePosture, before onClose — lets a
  // caller chain further work (e.g. creating/navigating to a project) only
  // once the write has actually succeeded.
  onConfirmed?: () => void;
}

export default function PostureConfirmModal({
  engineKey,
  currentPosture,
  onClose,
  targetPosture,
  onConfirmed,
}: Readonly<PostureConfirmModalProps>) {
  const [posture, setPosture] = useState<EnginePosture>(targetPosture ?? currentPosture);
  const { busy: submitting, run } = useAction("rejection");
  const [error, setError] = useState<string | null>(null);

  const handleConfirm = (): void => {
    if (posture === currentPosture) return;
    setError(null);
    // No optimistic update: the tile's displayed posture only ever changes on
    // the engine_posture_changed SSE re-read, never from this response.
    run(() => api.setEnginePosture(engineKey, posture)).then(
      () => {
        onConfirmed?.();
        onClose();
      },
      (err: unknown) => {
        setError(errorMessageOf(err));
      },
    );
  };

  return (
    <DialogFrame
      size="lg"
      ariaLabel="Change engine posture"
      testId="posture-confirm-dialog"
      busy={submitting}
      onClose={onClose}
      onSubmit={handleConfirm}
    >
      <h3 className="text-base font-semibold text-text-primary">Change posture</h3>
      <p className="text-sm text-text-primary">
        Changing the operating posture for <span className="font-medium">{engineKey}</span>{" "}
        takes effect immediately and governs which teams may act autonomously against this
        engine.
      </p>
      <div className="space-y-1">
        <p className="text-xs font-medium text-text-muted" id="posture-confirm-target-label">
          New posture
        </p>
        <PostureInput
          targetPosture={targetPosture}
          posture={posture}
          submitting={submitting}
          onChange={setPosture}
        />
      </div>
      {error !== null && (
        <p role="alert" data-testid="posture-confirm-error" className="text-sm text-btn-destroy">
          {error}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" type="button" onClick={onClose} disabled={submitting}>
          Cancel
        </Button>
        <Button
          variant="action"
          type="button"
          data-testid="posture-confirm-submit"
          onClick={handleConfirm}
          disabled={submitting || posture === currentPosture}
        >
          {submitting ? "Changing…" : "Confirm posture change"}
        </Button>
      </div>
    </DialogFrame>
  );
}
