import type { BuildChatSessionState, ChatMessage, RunSummary } from "../api";

export function makeRunSummary(overrides: Partial<RunSummary> = {}): RunSummary {
  return { model: "opus", effort: "xhigh", system_prompt: "prompt", ...overrides };
}

export function makeChatMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    role: "assistant",
    content: "reply",
    ts: "2026-06-01T00:00:00Z",
    interrupted: false,
    ...overrides,
  };
}

export function makeChatSession(
  overrides: Partial<BuildChatSessionState> = {},
): BuildChatSessionState {
  return {
    session_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    project_id: null,
    engine_key: null,
    repo_provision: "not_needed",
    created_at: "2026-05-01T00:00:00Z",
    messages: [],
    in_flight_turn: null,
    brief: null,
    run_summary: makeRunSummary(),
    ...overrides,
  };
}
