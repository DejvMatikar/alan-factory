import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  makeAgentEntry,
  makeAgentEntryWithout,
  makeLaunchRecord,
  makeValue,
} from "../test/agentFixtures";
import LaunchSetupList, {
  LAUNCH_FIELD_IDS,
  NOT_RECORDED_TEXT,
  RUN_LAUNCH_FIELD_IDS,
  entryLaunchValues,
  notRecordedLaunchValues,
  recordLaunchValues,
} from "./LaunchSetupList";

function field(id: string): HTMLElement {
  return screen.getByTestId(`setup-${id}`);
}

describe("LaunchSetupList", () => {
  it("renders only the fields it was given, in the canonical order, with their labels", () => {
    render(<LaunchSetupList testId="setup" values={{ timeouts: "t", model: "m" }} />);
    const ids = [...screen.getByTestId("setup").children].map((row) => row.getAttribute("data-testid"));
    expect(ids).toEqual(["setup-model", "setup-timeouts"]);
    expect(within(field("model")).getByText("Model")).toBeInTheDocument();
    expect(within(field("timeouts")).getByText("Timeouts")).toBeInTheDocument();
  });

  it("renders nothing for a field whose value is undefined", () => {
    render(<LaunchSetupList testId="setup" values={{ model: undefined }} />);
    expect(screen.queryByTestId("setup-model")).not.toBeInTheDocument();
  });
});

describe("entryLaunchValues", () => {
  it("covers every launch field", () => {
    expect(Object.keys(entryLaunchValues(makeAgentEntry())).sort()).toEqual([...LAUNCH_FIELD_IDS].sort());
  });

  it("shows the model with its source label and detail", () => {
    render(<LaunchSetupList testId="setup" values={entryLaunchValues(makeAgentEntry())} />);
    expect(field("model")).toHaveTextContent("sonnet");
    expect(field("model")).toHaveTextContent("Engine role");
    expect(field("model")).toHaveTextContent("engine role implementer");
    expect(field("effort")).toHaveTextContent("Built-in");
  });

  it("shows an explicit CLI default with no source chip when alan sets none", () => {
    render(
      <LaunchSetupList
        testId="setup"
        values={entryLaunchValues(
          makeAgentEntry({ effort: { ...makeValue("CLI default"), source_detail: null } }),
        )}
      />,
    );
    expect(field("effort")).toHaveTextContent("CLI default");
    expect(within(field("effort")).queryByText("Built-in")).not.toBeInTheDocument();
    expect(within(field("effort")).queryByText("Engine role")).not.toBeInTheDocument();
  });

  it("shows turn limit, adaptive budget and its note", () => {
    render(
      <LaunchSetupList
        testId="setup"
        values={entryLaunchValues(
          makeAgentEntry({ turn_limit: { max_turns: 25, adaptive: true, note: "scales with the plan" } }),
        )}
      />,
    );
    expect(field("turn_limit")).toHaveTextContent("25");
    expect(field("turn_limit")).toHaveTextContent("adaptive budget");
    expect(field("turn_limit")).toHaveTextContent("scales with the plan");
  });

  it("says no cap is set when max turns is null and omits adaptive text and note", () => {
    render(
      <LaunchSetupList
        testId="setup"
        values={entryLaunchValues(makeAgentEntry({ turn_limit: { max_turns: null, adaptive: false } }))}
      />,
    );
    expect(field("turn_limit")).toHaveTextContent("no cap set by alan");
    expect(field("turn_limit")).not.toHaveTextContent("adaptive budget");
  });

  it("lists stored, effective and denied tools separately", () => {
    render(<LaunchSetupList testId="setup" values={entryLaunchValues(makeAgentEntry())} />);
    expect(field("tools_stored")).toHaveTextContent("ReadEdit");
    expect(field("tools_effective")).toHaveTextContent("Read");
    expect(field("tools_effective")).not.toHaveTextContent("Edit");
    expect(field("tools_denied")).toHaveTextContent("WebFetch");
  });

  it("shows none for empty tool lists and a missing permission mode as CLI default", () => {
    render(
      <LaunchSetupList
        testId="setup"
        values={entryLaunchValues(
          makeAgentEntryWithout(["add_dirs", "conditional_add_dirs", "environment"], {
            tools: {},
            permission_mode: null,
          }),
        )}
      />,
    );
    expect(field("tools_stored")).toHaveTextContent("none");
    expect(field("tools_denied")).toHaveTextContent("none");
    expect(field("permission_mode")).toHaveTextContent("CLI default");
    expect(field("add_dirs")).toHaveTextContent("none");
    expect(field("environment")).toHaveTextContent("none set by alan");
  });

  it("shows extra directories with their conditional ones and conditions", () => {
    render(
      <LaunchSetupList
        testId="setup"
        values={entryLaunchValues(
          makeAgentEntry({
            conditional_add_dirs: [{ label: "source conversation workspace", condition: "when refining a brief" }],
          }),
        )}
      />,
    );
    expect(field("add_dirs")).toHaveTextContent("/srv/docs");
    expect(field("add_dirs")).toHaveTextContent("source conversation workspace — when refining a brief");
  });

  it("shows session, working directory, timeouts, environment and process", () => {
    render(
      <LaunchSetupList
        testId="setup"
        values={entryLaunchValues(
          makeAgentEntry({
            session: { behaviour: "per_message", detail: "a fresh session for each message" },
            timeouts: { idle_seconds: 600, absolute_seconds: null, detail: "idle only" },
            process: { priority_delta: -2, detached: true, detail: "survives restarts" },
            environment_note: null,
          }),
        )}
      />,
    );
    expect(field("session")).toHaveTextContent("One session per message");
    expect(field("session")).toHaveTextContent("a fresh session for each message");
    expect(field("working_directory")).toHaveTextContent("the leased checkout");
    expect(field("timeouts")).toHaveTextContent("idle 600s · absolute no limit");
    expect(field("timeouts")).toHaveTextContent("idle only");
    expect(field("environment")).toHaveTextContent("PATH (allowlisted)");
    expect(field("process")).toHaveTextContent("priority -2 · detached");
    expect(field("process")).toHaveTextContent("survives restarts");
  });

  it("says the working directory is not resolved instead of showing a blank value", () => {
    render(
      <LaunchSetupList
        testId="setup"
        values={entryLaunchValues(makeAgentEntry({ working_directory: "", error: "resolver failed" }))}
      />,
    );
    expect(field("working_directory")).toHaveTextContent("not resolved for this agent");
  });

  it("shows a default priority and attached process without a detail", () => {
    render(
      <LaunchSetupList
        testId="setup"
        values={entryLaunchValues(
          makeAgentEntry({
            process: { detached: false },
            timeouts: {},
            session: { behaviour: "none", detail: "no session" },
          }),
        )}
      />,
    );
    expect(field("process")).toHaveTextContent("default priority · attached");
    expect(field("timeouts")).toHaveTextContent("idle no limit · absolute no limit");
    expect(field("session")).toHaveTextContent("No session");
  });

  it("falls back to the raw behaviour for an unknown session value", () => {
    render(
      <LaunchSetupList
        testId="setup"
        values={recordLaunchValues(makeLaunchRecord({ session_mode: "mystery" }))}
      />,
    );
    expect(field("session")).toHaveTextContent("mystery");
  });
});

describe("recordLaunchValues", () => {
  it("covers every run field and omits the stored tools list", () => {
    expect(Object.keys(recordLaunchValues(makeLaunchRecord())).sort()).toEqual(
      [...RUN_LAUNCH_FIELD_IDS].sort(),
    );
    expect(RUN_LAUNCH_FIELD_IDS).not.toContain("tools_stored");
  });

  it("shows the values the CLI actually received", () => {
    render(<LaunchSetupList testId="setup" values={recordLaunchValues(makeLaunchRecord())} />);
    expect(field("model")).toHaveTextContent("sonnet");
    expect(field("effort")).toHaveTextContent("high");
    expect(field("turn_limit")).toHaveTextContent("40");
    expect(field("permission_mode")).toHaveTextContent("acceptEdits");
    expect(field("tools_effective")).toHaveTextContent("ReadEdit");
    expect(field("tools_denied")).toHaveTextContent("WebFetch");
    expect(field("add_dirs")).toHaveTextContent("/srv/docs");
    expect(field("session")).toHaveTextContent("New session");
    expect(field("working_directory")).toHaveTextContent("/work/checkout");
    expect(field("timeouts")).toHaveTextContent("idle 600s · absolute 7200s");
    expect(field("environment")).toHaveTextContent("PATH (allowlisted)");
    expect(field("process")).toHaveTextContent("priority +5 · attached · fleet worker");
  });

  it("shows CLI default for a null model, effort and permission mode and no cap for null turns", () => {
    render(
      <LaunchSetupList
        testId="setup"
        values={recordLaunchValues(
          makeLaunchRecord({
            model: null,
            effort: null,
            permission_mode: null,
            max_turns: null,
            cwd: null,
            priority_delta: null,
            idle_timeout_seconds: null,
            abs_timeout_seconds: null,
            fleet: false,
            detached: true,
            session_mode: "resumed",
          }),
        )}
      />,
    );
    expect(field("model")).toHaveTextContent("CLI default");
    expect(field("effort")).toHaveTextContent("CLI default");
    expect(field("permission_mode")).toHaveTextContent("CLI default");
    expect(field("turn_limit")).toHaveTextContent("no cap set by alan");
    expect(field("working_directory")).toHaveTextContent("inherited from the alan service process");
    expect(field("process")).toHaveTextContent("default priority · detached");
    expect(field("process")).not.toHaveTextContent("fleet worker");
    expect(field("session")).toHaveTextContent("Resumed session");
    expect(field("timeouts")).toHaveTextContent("idle no limit · absolute no limit");
  });

  it("says all tools are denied when the launch denies every tool", () => {
    render(
      <LaunchSetupList
        testId="setup"
        values={recordLaunchValues(makeLaunchRecord({ deny_all_tools: true, allowed_tools: [] }))}
      />,
    );
    expect(field("tools_effective")).toHaveTextContent("all tools denied");
  });
});

describe("notRecordedLaunchValues", () => {
  it("marks every run field as not recorded and never guesses a value", () => {
    render(<LaunchSetupList testId="setup" values={notRecordedLaunchValues()} />);
    for (const id of RUN_LAUNCH_FIELD_IDS) {
      expect(field(id)).toHaveTextContent(NOT_RECORDED_TEXT);
    }
    expect(screen.queryByTestId("setup-tools_stored")).not.toBeInTheDocument();
  });
});
