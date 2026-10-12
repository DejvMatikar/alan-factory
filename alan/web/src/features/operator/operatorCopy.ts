import { ApiError } from "../../api";
import type { OperatorScope, OperatorSession } from "../../api/operator";
import { errorMessageOf } from "../../lib/errorMessage";
import type { UnavailableReason } from "./operatorProtocol";

export const STATUS_UNREACHABLE = "status_unreachable";
export const TICKET_REFUSED = "ticket_refused";

export const STATUS_UNREACHABLE_DETAIL =
  "The service did not answer the request for the operator terminal's status.";
export const TICKET_REFUSED_DETAIL =
  "The service refused the connection ticket twice in a row. Reload the page to try again.";

export const PHONE_NOTICE =
  "The operator terminal needs a larger screen. Open this page on a tablet or desktop browser.";

export const UNAVAILABLE_HEADING = "Operator terminal unavailable";

export const PHASE_COPY = {
  loading: "Checking for a running session…",
  choose: "No operator session is running.",
  connecting: "Connecting to the session…",
  attached: "Attached to the running session.",
  reattaching: "Connection lost. Reattaching to the session…",
  ended: "The session has ended.",
  superseded:
    "This session was taken over by another browser window. Close this page and open it again to attach here.",
} as const;

const REASON_LABELS: Readonly<
  Record<UnavailableReason | typeof STATUS_UNREACHABLE | typeof TICKET_REFUSED, string>
> = {
  detached_mode_off: "Detached worker mode is off",
  tmux_missing: "tmux is not installed",
  claude_missing: "The claude CLI is not installed",
  git_missing: "git is not installed",
  checkout_clone_failed: "The operator checkout could not be cloned",
  status_unreachable: "The service did not answer the status request",
  ticket_refused: "The terminal connection was refused",
};

export type TargetScope = Exclude<OperatorScope, "fleet">;

const SCOPE_LABELS: Readonly<Record<OperatorScope, string>> = {
  fleet: "Global session",
  repo: "Repository session",
  workspace: "Workspace session",
};

export const SCOPE_OPTIONS: Readonly<Record<OperatorScope, { label: string; hint: string }>> = {
  fleet: {
    label: "Global",
    hint: "Opens in the folder holding a checkout of every connected repository, with the fleet's workspaces, logs and state folders available.",
  },
  repo: {
    label: "One repository",
    hint: "Opens in that repository's operator checkout and waits for you to type.",
  },
  workspace: {
    label: "One workspace",
    hint: "Starts analysing that workspace straight away, from its own repository's checkout.",
  },
};

export const TARGET_COPY: Readonly<
  Record<
    TargetScope,
    { label: string; choose: string; loading: string; failed: string; empty: string }
  >
> = {
  repo: {
    label: "Repository",
    choose: "Choose a repository",
    loading: "Loading repositories…",
    failed: "The repository list could not be loaded.",
    empty: "No repository is connected yet.",
  },
  workspace: {
    label: "Workspace",
    choose: "Choose a workspace",
    loading: "Loading workspaces…",
    failed: "The workspace list could not be loaded.",
    empty: "Nothing needs the operator right now.",
  },
};

export function unavailableLabel(reason: string): string {
  const labels: Readonly<Record<string, string | undefined>> = REASON_LABELS;
  return labels[reason] ?? reason;
}

export function describeSession(session: OperatorSession): string {
  return [SCOPE_LABELS[session.scope], session.target].filter((part) => part !== null).join(" · ");
}

export function failureText(error: unknown): string {
  return error instanceof ApiError ? error.detail : errorMessageOf(error);
}

export function updateWarningText(warning: string): string {
  return `The operator checkout could not be brought up to date, so the session started with the checkout as it was: ${warning}`;
}
