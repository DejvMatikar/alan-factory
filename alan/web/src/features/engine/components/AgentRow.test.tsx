import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  makeAgentEntry,
  makeAgentEntryWithout,
  makeFlag,
  placeholderSegment,
  textSegment,
} from "../../../test/agentFixtures";
import { activateDisclosure } from "../../../test/disclosure";
import type { AgentEntry } from "../../../api";
import AgentRow, { type AgentRowProps } from "./AgentRow";

const ID = "team_role:implementation/implementer";

function renderRow(entry: AgentEntry = makeAgentEntry(), overrides: Partial<AgentRowProps> = {}) {
  const props: AgentRowProps = {
    entry,
    promptMatches: 0,
    term: "",
    open: false,
    onOpenChange: vi.fn(),
    ...overrides,
  };
  render(
    <ul>
      <AgentRow {...props} />
    </ul>,
  );
  return props;
}

describe("AgentRow — summary line", () => {
  it("names the agent with its kind, model and effort", () => {
    renderRow();
    const summary = screen.getByTestId(`agent-row-${ID}-summary`);
    expect(summary).toHaveTextContent("Implementation · implementer");
    expect(summary).toHaveTextContent("Team role");
    expect(summary).toHaveTextContent("sonnet · effort high");
  });

  it("shows CLI default explicitly when alan sets no effort", () => {
    renderRow(makeAgentEntry({ effort: { value: null, display: "CLI default", source: "cli_default" } }));
    expect(screen.getByTestId(`agent-row-${ID}-summary`)).toHaveTextContent("effort CLI default");
  });

  it("marks an overridden prompt and a flagged agent", () => {
    renderRow(makeAgentEntry({ prompt_overridden: true, flags: [makeFlag(), makeFlag({ code: "model_mismatch" })] }));
    const summary = screen.getByTestId(`agent-row-${ID}-summary`);
    expect(summary).toHaveTextContent("overridden");
    expect(summary).toHaveTextContent("2 flagged");
  });

  it("omits the overridden and flagged chips for a plain agent", () => {
    renderRow(makeAgentEntryWithout(["flags"]));
    const summary = screen.getByTestId(`agent-row-${ID}-summary`);
    expect(summary).not.toHaveTextContent("overridden");
    expect(summary).not.toHaveTextContent("flagged");
  });

  it("shows how many times the search term appears in the prompt, singular and plural", () => {
    renderRow(makeAgentEntry(), { term: "write", promptMatches: 1 });
    expect(screen.getByTestId(`agent-row-${ID}-summary`)).toHaveTextContent("1 match in prompt");
  });

  it("pluralises the match count", () => {
    renderRow(makeAgentEntry(), { term: "write", promptMatches: 4 });
    expect(screen.getByTestId(`agent-row-${ID}-summary`)).toHaveTextContent("4 matches in prompt");
  });

  it("shows no match count when there is no search term", () => {
    renderRow(makeAgentEntry(), { term: "  ", promptMatches: 0 });
    expect(screen.getByTestId(`agent-row-${ID}-summary`)).not.toHaveTextContent("match");
  });
});

describe("AgentRow — disclosure", () => {
  it("does not mount the body while closed", () => {
    renderRow();
    expect(screen.queryByTestId(`agent-${ID}-body`)).not.toBeInTheDocument();
    expect(screen.getByTestId(`agent-row-${ID}`)).not.toHaveAttribute("open");
  });

  it("asks to open on keyboard activation when closed", () => {
    const closed = renderRow();
    activateDisclosure(screen.getByTestId(`agent-row-${ID}`), "Enter");
    expect(closed.onOpenChange).toHaveBeenCalledWith(true);
  });

  it("asks to close when open and activated", () => {
    const open = renderRow(makeAgentEntry(), { open: true });
    activateDisclosure(screen.getByTestId(`agent-row-${ID}`), "Space");
    expect(open.onOpenChange).toHaveBeenCalledWith(false);
  });
});

describe("AgentRow — expanded body", () => {
  it("says when the agent runs and chips its scope, tags and id", () => {
    renderRow(makeAgentEntry({ tags: ["eval", "legacy"], scope: "machine" }), { open: true });
    expect(screen.getByTestId(`agent-${ID}-when`)).toHaveTextContent("when an implementation task starts");
    const chips = screen.getByTestId(`agent-${ID}-chips`);
    expect(chips).toHaveTextContent("Same for every repository");
    expect(chips).toHaveTextContent("Eval");
    expect(chips).toHaveTextContent("Legacy");
    expect(chips).toHaveTextContent(`id ${ID}`);
  });

  it("describes the repository and default-engine scopes and tolerates missing tags", () => {
    renderRow(makeAgentEntryWithout(["tags"], { scope: "default_engine" }), { open: true });
    expect(screen.getByTestId(`agent-${ID}-chips`)).toHaveTextContent("Always the default engine");
    renderRow(makeAgentEntry({ scope: "repo", id: "other" }), { open: true });
    expect(screen.getByTestId("agent-other-chips")).toHaveTextContent("Depends on this repository");
  });

  it("shows the appended prompt as plain text and the first user message template", () => {
    renderRow(makeAgentEntry(), { open: true });
    expect(screen.getByTestId(`agent-${ID}-prompt-text`)).toHaveTextContent("You are the implementer.");
    expect(screen.getByTestId(`agent-${ID}-user-message-text`)).toHaveTextContent("Task: {{TASK_TITLE}}");
    expect(screen.getByRole("region", { name: "Appended system prompt" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "First user message template" })).toBeInTheDocument();
  });

  it("titles an overridden prompt as overridden", () => {
    renderRow(makeAgentEntry({ prompt_overridden: true }), { open: true });
    expect(
      screen.getByRole("region", { name: "Appended system prompt (overridden)" }),
    ).toBeInTheDocument();
  });

  it("highlights the search term inside the expanded prompt", () => {
    renderRow(makeAgentEntry({ prompt: [textSegment("Write the code. Write tests.")] }), {
      open: true,
      term: "write",
      promptMatches: 2,
    });
    expect(within(screen.getByTestId(`agent-${ID}-prompt`)).getAllByTestId(`agent-${ID}-prompt-match`)).toHaveLength(2);
    expect(screen.getByTestId(`agent-${ID}-user-message-text`).querySelector("mark")).toBeNull();
  });

  it("shows the flags and the whole launch setup", () => {
    renderRow(makeAgentEntry({ flags: [makeFlag()] }), { open: true });
    expect(screen.getByTestId(`agent-${ID}-flags-ignores_engine_roles`)).toBeInTheDocument();
    const setup = screen.getByTestId(`agent-${ID}-setup`);
    for (const field of ["model", "effort", "turn_limit", "permission_mode", "tools_stored", "tools_effective", "tools_denied", "add_dirs", "session", "working_directory", "timeouts", "environment", "process"]) {
      expect(within(setup).getByTestId(`agent-${ID}-setup-${field}`)).toBeInTheDocument();
    }
  });

  it("shows a resolver error and says there is no prompt when none was built", () => {
    renderRow(makeAgentEntry({ error: "could not resolve", prompt: [] }), { open: true });
    expect(screen.getByTestId(`agent-${ID}-error`)).toHaveTextContent("could not resolve");
    expect(screen.getByTestId(`agent-${ID}-no-prompt`)).toHaveTextContent("No appended system prompt.");
  });

  it("tolerates an entry with no prompt, user message or flags at all", () => {
    renderRow(makeAgentEntryWithout(["prompt", "user_message", "flags", "error"]), { open: true });
    expect(screen.getByTestId(`agent-${ID}-no-prompt`)).toBeInTheDocument();
    expect(screen.queryByTestId(`agent-${ID}-flags`)).not.toBeInTheDocument();
    expect(screen.queryByTestId(`agent-${ID}-error`)).not.toBeInTheDocument();
  });

  it("says there is no first user message template instead of showing an empty viewer", () => {
    renderRow(makeAgentEntry({ user_message: [] }), { open: true });
    expect(screen.getByTestId(`agent-${ID}-no-user-message`)).toHaveTextContent(
      "No first user message template.",
    );
    expect(screen.queryByTestId(`agent-${ID}-user-message`)).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "First user message template" })).not.toBeInTheDocument();
  });

  it("shows the viewer and no empty-state line when a first user message template exists", () => {
    renderRow(makeAgentEntry(), { open: true });
    expect(screen.queryByTestId(`agent-${ID}-no-user-message`)).not.toBeInTheDocument();
  });

  it("describes run-time placeholders in the user message", () => {
    renderRow(
      makeAgentEntry({ user_message: [textSegment("Task: "), placeholderSegment("TASK_TITLE", "The task title")] }),
      { open: true },
    );
    expect(screen.getByTestId(`agent-${ID}-user-message-legend`)).toHaveTextContent("The task title");
  });
});
