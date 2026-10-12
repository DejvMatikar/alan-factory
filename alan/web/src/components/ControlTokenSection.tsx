import { useState } from "react";

import { api, type ControlTokenStatus } from "../api";
import { classifyMutationError, type MutationBlock } from "../lib/connectFlowErrors";
import { formatDate } from "../lib/format";
import { useControlTokenStatus } from "../hooks/useControlTokenStatus";
import Button from "./Button";
import ConfirmDestructiveDialog from "./ConfirmDestructiveDialog";
import MutationErrorNotice from "./wizard/MutationErrorNotice";

export interface ControlTokenSectionProps {
  draft: string;
  showToken: boolean;
  tokenSet: boolean;
  onDraftChange: (value: string) => void;
  onToggleShowToken: () => void;
  onSave: () => void;
  onClear: () => void;
}

const _MS_PER_DAY = 86_400_000;

// Day-granularity countdown computed from serverNowMs — the status
// response's own Date header — never Date.now(): a browser with a wrong
// clock must never disagree with the machine that actually enforces expiry.
// Every null-guard lives here rather than at each call site, since a status
// payload is parsed from the network (a system boundary) and its fields are
// typed nullable independent of what the current lifecycle state implies.
export function remainingLifetimeText(
  deadline: string | null,
  serverNowMs: number | null,
): string | null {
  if (deadline === null || serverNowMs === null) return null;
  const deadlineMs = Date.parse(deadline);
  if (Number.isNaN(deadlineMs)) return null;
  const remainingMs = deadlineMs - serverNowMs;
  if (remainingMs <= 0) return null;
  const days = Math.floor(remainingMs / _MS_PER_DAY);
  if (days < 1) return "expires soon";
  const dayWord = days === 1 ? "day" : "days";
  return `expires in ${days} ${dayWord}`;
}

// Deadlines are operator-facing, so they're rendered through the repo's
// existing locale-aware formatter rather than as the raw ISO 8601 the wire
// carries — a machine-shaped timestamp is fine on the wire, not in prose.
function deadlineOrUnknown(deadline: string | null): string {
  return deadline !== null ? formatDate(deadline) : "unknown";
}

// Kept separate from serverStatusText purely to stay under the lint-enforced
// cyclomatic-complexity cap — the active branch alone has as many decision
// points as the null/revoked/expired branches combined.
function activeStatusText(status: ControlTokenStatus): string {
  const countdown = remainingLifetimeText(status.deadline, status.serverNowMs);
  return countdown !== null
    ? `Server token: active, ${countdown} (${deadlineOrUnknown(status.deadline)}).`
    : `Server token: active, expires ${deadlineOrUnknown(status.deadline)}.`;
}

// The countdown is text derived from the server's own clock, never from the
// browser's — the two machines are never asked to agree on what time it is.
// state/deadline alone (no separate "warning" flag) is all
// GET /api/control-token/status exposes; the 7-day approaching-expiry
// warning itself is the backend-synthesized needs-you item, not something
// this line recomputes.
function serverStatusText(status: ControlTokenStatus | null, error: boolean): string {
  if (status === null) {
    return error
      ? "Could not reach the server to check the token's status."
      : "Checking the server token's status…";
  }
  if (status.state === "revoked") {
    return "Server token: revoked. No token is active — an operator must configure one on the host.";
  }
  if (status.state === "expired") {
    return (
      `Server token: expired (deadline was ${deadlineOrUnknown(status.deadline)}). Replace it, ` +
      "then paste the new value into every browser that used the old one."
    );
  }
  return activeStatusText(status);
}

function LocalTokenControls({
  draft,
  showToken,
  tokenSet,
  onDraftChange,
  onToggleShowToken,
  onSave,
  onClear,
  serverStatusLine,
}: Readonly<ControlTokenSectionProps & { serverStatusLine: string }>) {
  return (
    <div>
      <label htmlFor="control-token-input" className="mb-1 block text-sm font-medium text-text-primary">
        Control token
      </label>
      <div className="mb-2 flex items-center gap-2">
        <input
          id="control-token-input"
          type={showToken ? "text" : "password"}
          value={draft}
          onChange={(e) => onDraftChange(e.target.value)}
          data-testid="control-token-input"
          className="min-h-11 flex-1 rounded-md border border-border bg-surface px-3 py-1.5 text-sm text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60"
        />
        <Button
          variant="ghost"
          aria-label={showToken ? "Hide token" : "Show token"}
          data-testid="toggle-token-visibility"
          onClick={onToggleShowToken}
        >
          {showToken ? "Hide" : "Show"}
        </Button>
      </div>
      <p className="mb-1">
        <span
          className={`inline-block rounded px-2 py-0.5 text-xs ${
            tokenSet ? "bg-green-500/15 text-green-300" : "bg-zinc-500/15 text-zinc-300"
          }`}
          data-testid="control-token-status"
        >
          {tokenSet ? "Token set." : "No token set."}
        </span>
      </p>
      <p
        className="mb-4 text-xs text-text-muted"
        role="status"
        aria-live="polite"
        data-testid="control-token-server-status"
      >
        {serverStatusLine}
      </p>
      <p className="mb-4 text-xs text-text-muted">
        Find the token at ~/.alan/state/control_token on the host running alan.
      </p>
      <div className="flex justify-end gap-2">
        <Button variant="destroy" data-testid="clear-token" onClick={onClear}>
          Clear
        </Button>
        <Button variant="action" data-testid="save-token" onClick={onSave}>
          Save
        </Button>
      </div>
    </div>
  );
}

type LifecycleAction = "extend" | "replace" | "revoke";

const BUSY_LABEL: Record<LifecycleAction, string> = {
  extend: "Extending…",
  replace: "Replacing…",
  revoke: "Revoking…",
};

const IDLE_LABEL: Record<LifecycleAction, string> = {
  extend: "Extend",
  replace: "Replace",
  revoke: "Revoke",
};

function actionAnnouncement(action: LifecycleAction, status: ControlTokenStatus): string {
  if (action === "extend") {
    return `Control token extended. It now expires ${status.deadline ?? "unknown"}.`;
  }
  if (action === "replace") {
    return (
      "Control token replaced. Every other browser is signed out — fetch the new value from " +
      "the host and paste it above."
    );
  }
  return (
    "Control token revoked. No replacement exists — mutating requests will fail until an " +
    "operator configures one on the host."
  );
}

function LifecycleActions({ applyStatus }: Readonly<{ applyStatus: (status: ControlTokenStatus) => void }>) {
  const [busyAction, setBusyAction] = useState<LifecycleAction | null>(null);
  const [actionBlock, setActionBlock] = useState<MutationBlock | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const [pendingConfirm, setPendingConfirm] = useState<"replace" | "revoke" | null>(null);
  const busy = busyAction !== null;

  const runAction = (action: LifecycleAction, call: () => Promise<ControlTokenStatus>): void => {
    setPendingConfirm(null);
    setBusyAction(action);
    setActionBlock(null);
    call().then(
      (next) => {
        setBusyAction(null);
        applyStatus(next);
        setAnnouncement(actionAnnouncement(action, next));
      },
      (err: unknown) => {
        setBusyAction(null);
        setActionBlock(classifyMutationError(err, "control_token"));
      },
    );
  };

  return (
    <div className="space-y-2 border-t border-border pt-3">
      <p className="text-xs text-text-muted">
        Extend keeps today's token value and only pushes its deadline out — no other browser is
        affected. Replace and Revoke both change what the server accepts everywhere, signing out
        every other browser that used the old value.
      </p>
      <div className="flex flex-wrap gap-2">
        <Button
          variant="action"
          data-testid="control-token-extend"
          disabled={busy}
          onClick={() => runAction("extend", () => api.extendControlToken())}
        >
          {busyAction === "extend" ? BUSY_LABEL.extend : IDLE_LABEL.extend}
        </Button>
        <Button
          variant="reset"
          data-testid="control-token-replace"
          disabled={busy}
          onClick={() => setPendingConfirm("replace")}
        >
          {busyAction === "replace" ? BUSY_LABEL.replace : IDLE_LABEL.replace}
        </Button>
        <Button
          variant="destroy"
          data-testid="control-token-revoke"
          disabled={busy}
          onClick={() => setPendingConfirm("revoke")}
        >
          {busyAction === "revoke" ? BUSY_LABEL.revoke : IDLE_LABEL.revoke}
        </Button>
      </div>
      {actionBlock !== null && (
        <MutationErrorNotice block={actionBlock} testId="control-token-action-error" />
      )}
      <p role="status" aria-live="polite" className="sr-only" data-testid="control-token-announcement">
        {announcement}
      </p>
      {pendingConfirm === "replace" && (
        <ConfirmDestructiveDialog
          title="Replace the control token?"
          body={
            "This issues a brand-new token value and immediately invalidates the current one " +
            "everywhere — including this browser. The new value is never sent over the network: " +
            "after replacing, read it from ~/.alan/state/control_token on the host and paste it above."
          }
          confirmLabel="Replace token"
          confirmVariant="reset"
          onConfirm={() => runAction("replace", () => api.rotateControlToken())}
          onCancel={() => setPendingConfirm(null)}
          testId="control-token-replace-confirm"
        />
      )}
      {pendingConfirm === "revoke" && (
        <ConfirmDestructiveDialog
          title="Revoke the control token?"
          body={
            "This immediately kills the current token with no replacement — every browser, " +
            "including this one, loses the ability to make changes, and the service will not " +
            "generate a new one on its own. This cannot be undone from the browser."
          }
          confirmLabel="Revoke token"
          onConfirm={() => runAction("revoke", () => api.revokeControlToken())}
          onCancel={() => setPendingConfirm(null)}
          testId="control-token-revoke-confirm"
        />
      )}
    </div>
  );
}

export default function ControlTokenSection(props: Readonly<ControlTokenSectionProps>) {
  const { status, error, applyStatus } = useControlTokenStatus();
  return (
    <div className="space-y-3">
      <LocalTokenControls {...props} serverStatusLine={serverStatusText(status, error)} />
      <LifecycleActions applyStatus={applyStatus} />
    </div>
  );
}
