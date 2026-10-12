import type { UnboundReason } from "../features/connect/pages/connectStep4PostureLogic";

const REASON_COPY: Record<UnboundReason, string> = {
  preflight_blocked:
    "This project isn't bound to the repo yet — its readiness check is currently blocked. Fix the blocking check, then revisit the repo's setup to connect it.",
  preflight_unknown:
    "This project isn't bound to the repo yet — its readiness check hasn't passed. Run the check from the repo's setup, then revisit to connect it.",
  bind_rejected:
    "This project isn't bound to the repo yet — the readiness check wasn't passing at the moment binding was attempted. Revisit the repo's setup and try again.",
  bind_conflict:
    "This project isn't bound to the repo yet — it already has workspaces in flight. Wait for them to finish, then try again.",
};

const KNOWN_REASONS: ReadonlySet<string> = new Set<UnboundReason>([
  "preflight_blocked",
  "preflight_unknown",
  "bind_rejected",
  "bind_conflict",
]);

function isUnboundReason(value: unknown): value is UnboundReason {
  return typeof value === "string" && KNOWN_REASONS.has(value);
}

// location.state is history-controlled, not typed by React Router, and
// survives back/forward navigation and manual URL edits — it must never be
// trusted into copy without going through this guard first.
export function readConnectUnboundState(state: unknown): UnboundReason | null {
  if (state === null || typeof state !== "object") return null;
  const reason = (state as { connectUnboundReason?: unknown }).connectUnboundReason;
  return isUnboundReason(reason) ? reason : null;
}

export interface ConnectUnboundNoticeProps {
  reason: UnboundReason;
}

// An explained condition, not an error: the attention tint (not the danger
// tint MutationErrorNotice reuses) because the project itself was created
// successfully — only its bind to the repo is pending.
export default function ConnectUnboundNotice({ reason }: Readonly<ConnectUnboundNoticeProps>) {
  return (
    <output
      data-testid="connect-unbound-notice"
      className="space-y-0.5 rounded-md bg-status-attention-tint/15 px-3 py-2 text-xs text-status-attention-fg"
    >
      <p data-testid="connect-unbound-notice-message">{REASON_COPY[reason]}</p>
    </output>
  );
}
