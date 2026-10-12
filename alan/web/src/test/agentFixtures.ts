import { HttpResponse, http } from "msw";

import type {
  AgentCatalogue,
  AgentCatalogueSummary,
  AgentEntry,
  AgentFlag,
  AgentPromptSegment,
  AgentValue,
  LaunchRecord,
} from "../api";

export function makeValue(
  display: string,
  source: AgentValue["source"] = "builtin",
  sourceDetail: string | null = null,
): AgentValue {
  return {
    value: display === "CLI default" ? null : display,
    display,
    source: display === "CLI default" ? "cli_default" : source,
    source_detail: sourceDetail,
  };
}

export function textSegment(text: string, label = "system_prompt", shared = false): AgentPromptSegment {
  return { kind: "text", label, text, shared };
}

export function placeholderSegment(label: string, description: string): AgentPromptSegment {
  return { kind: "placeholder", label, text: "", description, shared: false };
}

export function makeFlag(overrides: Partial<AgentFlag> = {}): AgentFlag {
  return {
    code: "ignores_engine_roles",
    severity: "warn",
    title: "Ignores the engine's roles",
    detail: "The model is built in although an engine role of the same name exists.",
    ...overrides,
  };
}

export function makeAgentEntry(overrides: Partial<AgentEntry> = {}): AgentEntry {
  return {
    id: "team_role:implementation/implementer",
    name: "Implementation · implementer",
    kind: "team_role",
    tags: [],
    scope: "repo",
    team: "implementation",
    role: "implementer",
    when_runs: "when an implementation task starts",
    model: makeValue("sonnet", "engine_role", "engine role implementer"),
    effort: makeValue("high", "builtin", "derived from the model"),
    prompt: [textSegment("You are the implementer.\nWrite the code.")],
    prompt_overridden: false,
    user_message: [textSegment("Task: ", "user"), placeholderSegment("TASK_TITLE", "The task title")],
    turn_limit: { max_turns: 40, adaptive: false, note: null },
    permission_mode: "acceptEdits",
    tools: { stored: ["Read", "Edit"], effective: ["Read"], denied: ["WebFetch"] },
    add_dirs: ["/srv/docs"],
    conditional_add_dirs: [],
    session: { behaviour: "new", detail: "starts a new session" },
    working_directory: "the leased checkout",
    timeouts: { idle_seconds: 600, absolute_seconds: 7200, detail: null },
    environment: [{ name: "PATH", category: "allowlisted" }],
    environment_note: "Names and categories only; values are never shown.",
    process: { priority_delta: 5, detached: false, detail: null },
    flags: [],
    error: null,
    ...overrides,
  };
}

export function makeAgentEntryWithout(
  omitted: readonly (keyof AgentEntry)[],
  overrides: Partial<AgentEntry> = {},
): AgentEntry {
  const entry: Partial<AgentEntry> = makeAgentEntry(overrides);
  for (const key of omitted) delete entry[key];
  return entry as AgentEntry;
}

export function makeCatalogue(
  entries: readonly AgentEntry[],
  overrides: Partial<AgentCatalogue> = {},
): AgentCatalogue {
  return {
    engine: "acme/widget",
    notes: {
      appended_prompt: "Every prompt is appended to Claude Code's own built-in system prompt.",
      inherited: [
        { name: "User-level settings", note: "Read by Claude Code on this machine; not shown here." },
        { name: "Repository guidance file", note: "Loaded from the working directory." },
      ],
    },
    entries: [...entries],
    flags: [],
    ...overrides,
  };
}

export function makeSampleCatalogue(): AgentCatalogue {
  return makeCatalogue(
    [
      makeAgentEntry({
        id: "team_role:implementation/implementer",
        name: "Implementer",
        prompt: [textSegment("Write the code.\nWrite tests.")],
      }),
      makeAgentEntry({
        id: "team_role:verification/judge",
        name: "Judge",
        model: makeValue("opus", "engine_role"),
        effort: makeValue("CLI default"),
        prompt: [textSegment("Judge the diff.")],
        prompt_overridden: true,
        flags: [makeFlag()],
      }),
      makeAgentEntry({
        id: "chat:build",
        name: "Build chat",
        kind: "chat",
        model: makeValue("haiku"),
        effort: makeValue("CLI default"),
        prompt: [textSegment("Help the operator plan. Write briefly.")],
      }),
    ],
    {
      flags: [
        makeFlag({
          code: "unread_stored_role",
          title: "Unread stored role",
          detail: "remediator is read by no agent.",
        }),
      ],
    },
  );
}

export function makeSummary(overrides: Partial<AgentCatalogueSummary> = {}): AgentCatalogueSummary {
  return {
    engine: "acme/widget",
    total: 3,
    by_model: [
      { name: "opus", count: 1 },
      { name: "sonnet", count: 2 },
    ],
    by_effort: [{ name: "high", count: 3 }],
    by_kind: [{ name: "team_role", count: 3 }],
    warnings: 1,
    infos: 0,
    errors: 0,
    ...overrides,
  };
}

export function makeLaunchRecord(overrides: Partial<LaunchRecord> = {}): LaunchRecord {
  return {
    agent: "team_role:implementation/implementer",
    model: "sonnet",
    effort: "high",
    max_turns: 40,
    permission_mode: "acceptEdits",
    prompt_segments: [
      { label: "system_prompt", text: "You are the implementer.", shared: false },
      { label: "turn_budget", text: "\n\nTurn budget for this run: 40", shared: false },
    ],
    allowed_tools: ["Read", "Edit"],
    denied_tools: ["WebFetch"],
    add_dirs: ["/srv/docs"],
    deny_all_tools: false,
    session_mode: "new",
    cwd: "/work/checkout",
    idle_timeout_seconds: 600,
    abs_timeout_seconds: 7200,
    fleet: true,
    detached: false,
    priority_delta: 5,
    env: [{ name: "PATH", category: "allowlisted" }],
    ...overrides,
  };
}

export function catalogueHandler(owner: string, repo: string, catalogue: AgentCatalogue) {
  return http.get(`/api/engines/${owner}/${repo}/agents`, () => HttpResponse.json(catalogue));
}

export function summaryHandler(owner: string, repo: string, summary: AgentCatalogueSummary) {
  return http.get(`/api/engines/${owner}/${repo}/agents/summary`, () => HttpResponse.json(summary));
}
