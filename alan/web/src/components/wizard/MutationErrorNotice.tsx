import type { MutationBlock } from "../../lib/connectFlowErrors";
import ErrorBanner from "../../ui/ErrorBanner";

// AC6: a missing/mismatched control token and a missing repository credential
// are different secrets with different jobs — the copy below never conflates
// them, and every variant is a typed, actionable sentence, never a raw error
// string. Mirrors EngineConfigEditor.tsx's existing "set it from Settings (⚙)
// first" precedent rather than a real route (Settings is a modal opened from
// the Shell header on every page, not a navigable path). no_token and
// token_expired are likewise kept distinct — a header that was never sent and
// one that was sent but is past its deadline call for different remedies
// (paste a token vs. extend/replace the one already pasted).
const COPY_BY_KIND: Record<MutationBlock["kind"], string> = {
  no_token: "Enter your control token in Settings (⚙) to make changes.",
  token_expired:
    "Your control token expired — extend or replace it in Settings (⚙) to make changes.",
  token_mismatch: "Your control token doesn't match the server's — update it in Settings (⚙).",
  not_configured: "The server has no control token configured yet — ask an operator to set one.",
  missing_credential:
    "No credential exists for this repository yet — add one in Settings (⚙) → Credentials.",
  already_registered: "This repository is already registered.",
  proposal_failed: "The automatic stack scan couldn't complete.",
  validation: "That value isn't valid.",
  pool_busy:
    "The worker pool has active leases and couldn't be resized — wait for current work to " +
    "finish, or leave the pool size unchanged.",
  scaffold_failed: "The scaffold pull request couldn't be opened.",
  other: "Something went wrong.",
};

export interface MutationErrorNoticeProps {
  block: MutationBlock;
  testId: string;
}

export default function MutationErrorNotice({ block, testId }: Readonly<MutationErrorNoticeProps>) {
  const detail = "detail" in block ? block.detail : null;
  return (
    <ErrorBanner data-testid={testId}>
      <p data-testid={`${testId}-message`}>{COPY_BY_KIND[block.kind]}</p>
      {detail !== null && (
        <p className="mt-0.5 text-text-muted" data-testid={`${testId}-detail`}>
          {detail}
        </p>
      )}
    </ErrorBanner>
  );
}
