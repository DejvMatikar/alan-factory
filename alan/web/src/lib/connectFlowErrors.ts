import { ApiError, ConfigProposalBlockedError } from "../api";
import { errorMessageOf } from "./errorMessage";

export type MutationStage = "register" | "propose" | "secret" | "save" | "control_token" | "scaffold";

export type MutationBlock =
  | { kind: "no_token" }
  | { kind: "token_expired" }
  | { kind: "token_mismatch" }
  | { kind: "not_configured" }
  | { kind: "missing_credential"; detail: string }
  | { kind: "already_registered"; detail: string }
  | { kind: "proposal_failed"; detail: string }
  | { kind: "validation"; detail: string }
  | { kind: "pool_busy"; detail: string }
  | { kind: "scaffold_failed"; detail: string }
  | { kind: "other"; detail: string };

const STATUS_ONLY_BLOCKS: Record<number, MutationBlock> = {
  403: { kind: "token_mismatch" },
  500: { kind: "not_configured" },
};

// alan.control_plane.require_control_token 401s for two different reasons —
// no header at all, or a header that matches but is past its deadline — and
// distinguishes them only by this literal detail string (mirrors
// _EXPIRED_TOKEN_DETAIL). A mutation blocked by each names a different cause
// and remedy, so the split has to happen here rather than collapsing both
// into one "no_token" kind.
const EXPIRED_CONTROL_TOKEN_DETAIL = "control token expired";

function classify401(err: unknown): MutationBlock {
  if (err instanceof ApiError && err.detail === EXPIRED_CONTROL_TOKEN_DETAIL) {
    return { kind: "token_expired" };
  }
  return { kind: "no_token" };
}

// 422 is overloaded across call sites (missing credential record on register,
// an aborted/unrepairable proposal, a blank secret field, an invalid step-2
// save) — the caller states which mutation it came from so a bare status
// code doesn't have to guess.
const STAGE_422_KIND: Record<MutationStage, MutationBlock["kind"]> = {
  register: "missing_credential",
  propose: "proposal_failed",
  secret: "validation",
  save: "validation",
  control_token: "other",
  scaffold: "scaffold_failed",
};

// 409 is likewise overloaded — register's "already registered" and step 2's
// save "pool has active leases" are structurally different conflicts, so
// reusing the same already_registered kind for both would fabricate a cause
// the pool-resize 409 never states. propose/secret/control_token never
// actually 409 today, but a lookup must still total over every stage.
const STAGE_409_KIND: Record<MutationStage, MutationBlock["kind"]> = {
  register: "already_registered",
  save: "pool_busy",
  propose: "other",
  secret: "other",
  control_token: "other",
  scaffold: "other",
};

export function classifyMutationError(err: unknown, stage: MutationStage): MutationBlock {
  // POST .../config-proposal's bespoke fetch throws this domain-specific class
  // (carrying a Finding, no numeric status) for its one Finding-shaped 422
  // case — it is always a proposal failure by construction, so it is
  // classified structurally rather than by a status code that doesn't exist
  // on this error type.
  if (err instanceof ConfigProposalBlockedError) {
    return { kind: "proposal_failed", detail: err.finding.message };
  }
  const status = err instanceof ApiError ? err.status : undefined;
  if (status === 401) {
    return classify401(err);
  }
  if (status !== undefined && status in STATUS_ONLY_BLOCKS) {
    // Verified present via the `in` check above — noUncheckedIndexedAccess
    // still types a `number`-keyed Record access as possibly undefined.
    return STATUS_ONLY_BLOCKS[status]!;
  }
  if (status === 409) {
    return { kind: STAGE_409_KIND[stage], detail: errorMessageOf(err) };
  }
  if (status === 422) {
    return { kind: STAGE_422_KIND[stage], detail: errorMessageOf(err) };
  }
  return { kind: "other", detail: errorMessageOf(err) };
}
