export const CLOSE_SUPERSEDED = 4001;
export const CLOSE_ENDED = 4002;
export const CLOSE_BAD_TICKET = 4401;
export const CLOSE_NO_SESSION = 4404;

export const RESIZE_MESSAGE_TYPE = "resize";

export const OPERATOR_SCOPES = ["fleet", "repo", "workspace"] as const;

export const UNAVAILABLE_REASONS = [
  "detached_mode_off",
  "tmux_missing",
  "claude_missing",
  "git_missing",
  "checkout_clone_failed",
] as const;

export const WORKSPACE_TARGET_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/;

export type UnavailableReason = (typeof UNAVAILABLE_REASONS)[number];

export interface ResizeMessage {
  readonly type: typeof RESIZE_MESSAGE_TYPE;
  readonly cols: number;
  readonly rows: number;
}

export function encodeResize(cols: number, rows: number): string {
  const message: ResizeMessage = { type: RESIZE_MESSAGE_TYPE, cols, rows };
  return JSON.stringify(message);
}
